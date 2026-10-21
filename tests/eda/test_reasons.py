"""The reasons addendum of docs/eda.md (CLAUDE.md Step 6), on the made-up world of the EDA
tests with made-up reason labels."""

import json
from dataclasses import replace
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from trialpulse.cohort.audit import MODELING_EDA_BEFORE
from trialpulse.config import load_project_config
from trialpulse.eda import analysis as an
from trialpulse.eda import document, report
from trialpulse.eda.refs import FIGURES, TABLES, figure_keys, table_keys
from trialpulse.eda.results import compute

from .conftest import World

MODEL = "tfidf-logreg n=10 trained 2026-10-21T00:00:00+00:00"


def _stops(world: World) -> pd.DataFrame:
    """The cohort's early stops, as the final cohort's outcomes record them."""
    with duckdb.connect() as con:
        return con.execute(
            f"""SELECT trial_id, year(terminal_date) AS stop_year
            FROM read_parquet('{world.sources.final_outcomes.as_posix()}')
            WHERE event = 1 AND in_window AND ever_in_population AND NOT reversal
            ORDER BY trial_id"""
        ).df()


def _write_reasons(path: Path, stops: pd.DataFrame, model: str = MODEL) -> dict[str, str]:
    """A label for every stop but every fifth, cycling through the taxonomy."""
    labels = {
        t: an.REASON_LABELS[i % len(an.REASON_LABELS)]
        for i, t in enumerate(stops["trial_id"])
        if i % 5 != 4
    }
    frame = pd.DataFrame({"nct_id": list(labels), "label": list(labels.values()), "model": model})
    frame["reason_group"] = "x"
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as con:
        con.register("frame", frame)
        con.execute(f"COPY frame TO '{path.as_posix()}' (FORMAT parquet)")
    return labels


@pytest.fixture(scope="module")
def labeled(world: World, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, str]]:
    path = tmp_path_factory.mktemp("reasons") / "early_stop_reasons.parquet"
    return path, _write_reasons(path, _stops(world))


def test_the_breakdown_counts_every_early_stop_and_shares_the_labeled_ones(
    world: World, labeled: tuple[Path, dict[str, str]]
) -> None:
    path, labels = labeled
    stops = _stops(world)
    assert len(stops) >= 10  # the world has early stops to label
    with an.connect(world.sources) as con:
        found = an.reasons_breakdown(con, path)
    total = found["year"][-1]
    assert total["group"] == an.ALL
    assert total["early_stops"] == len(stops)
    assert total["labeled"] == len(labels)
    for reason in an.REASON_LABELS:
        expected = sum(label == reason for label in labels.values()) / len(labels)
        assert total[reason] == pytest.approx(expected)
    assert total["operational"] == pytest.approx(sum(total[r] for r in an.OPERATIONAL_REASONS))
    # By year: the years of the stops, each counted from the outcomes.
    years = {int(r["group"]): r for r in found["year"][:-1]}
    assert sorted(years) == sorted(set(stops["stop_year"].tolist()))
    for year, row in years.items():
        in_year = stops[stops["stop_year"] == year]["trial_id"]
        assert row["early_stops"] == len(in_year)
        assert row["labeled"] == sum(t in labels for t in in_year)
    # Every grouping counts every stop once.
    for name in ("phase", "sponsor"):
        rows = found[name]
        assert sum(r["early_stops"] for r in rows[:-1]) == len(stops), name


def test_the_addendum_is_rendered_with_its_tables_and_figure(
    world: World, labeled: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _ = labeled
    monkeypatch.setattr(document, "findings", lambda res: [])
    results = compute(replace(world.sources, reasons=path), load_project_config(),
                      MODELING_EDA_BEFORE)  # fmt: skip
    text = document.render(results)
    section = text[text.index("\n## 9. Addendum") : text.index("\n## Not in this report")]
    assert f"`{MODEL}`" in section
    for key in table_keys(True)[-3:]:
        assert f"Table {TABLES.index(key) + 1}. Descriptive only" in section
    figure = FIGURES["reasons"]
    assert f"![Figure {len(figure_keys(True))}: {figure[1]}](figures/{figure[0]})" in section
    bullets = [line for line in section.splitlines() if line.startswith("- ")]
    assert len(bullets) == 4
    assert all(b.startswith("- Descriptive only:") for b in bullets)
    assert "waits for the final Step 6 labels" not in text
    # Without the labels the addendum is left out and said to be.
    without = document.render(replace(results, reasons=None))
    assert "## 9. Addendum" not in without
    assert "waits for the final Step 6 labels" in without


def test_only_the_final_model_s_labels_enter_the_report(world: World, tmp_path: Path) -> None:
    stops = _stops(world)
    reasons, metadata = tmp_path / "reasons.parquet", tmp_path / "distilled.json"
    assert report.final_reasons(reasons, metadata) is None  # nothing yet
    _write_reasons(reasons, stops)

    def meta(training: int, trained: str = "2026-10-21T00:00:00+00:00") -> None:
        metadata.write_text(json.dumps({"training_texts": training, "sample_size": 10,
                                        "trained_at": trained}), encoding="utf-8")  # fmt: skip

    meta(9)  # a provisional model
    assert report.final_reasons(reasons, metadata) is None
    meta(10, "2026-10-22T00:00:00+00:00")  # a final model that did not write these labels
    assert report.final_reasons(reasons, metadata) is None
    meta(10)
    assert report.final_reasons(reasons, metadata) == reasons


def test_the_report_reads_the_paths_the_distillation_writes() -> None:
    from trialpulse.nlp import distill

    assert report.REASONS_PATH == distill.REASONS_DIR / "early_stop_reasons.parquet"
    assert distill.MODEL_PATH.with_suffix(".json") == report.REASON_MODEL_METADATA


def test_the_reason_groups_are_those_of_the_configuration() -> None:
    cfg = load_project_config()
    assert set(an.OPERATIONAL_REASONS) == set(cfg.stop_reasons.operational)
    assert set(an.SCIENTIFIC_REASONS) == set(cfg.stop_reasons.scientific)
