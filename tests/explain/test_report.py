"""The explanations command (Step 12, ADR 0033): the lock first, the parity with the locked
run's saved scores, the cache and the document. On a small M4 fitted to simulated rows; no
locked origin is read."""

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from trialpulse.cli import RefusedError
from trialpulse.config import Origin, ProjectConfig
from trialpulse.eval.rows import LandmarkRows
from trialpulse.explain import drivers, report
from trialpulse.explain import timelines as tl
from trialpulse.models.discrete_time import DiscreteTimeBoosting

from .conftest import simulated_rows

COMMIT = "c" * 40


@pytest.fixture(scope="module")
def probe() -> LandmarkRows:
    rows, _, _ = simulated_rows(400, seed=9)
    return rows


def _saved(model: DiscreteTimeBoosting, rows: LandmarkRows) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trial_id": rows.trial_id.astype(str),
            "landmark_index": rows.landmark_index,
            "score_12": model.predict_months(rows, 12),
            "score_24": model.predict_months(rows, 24),
        }
    ).sort_values(["trial_id", "landmark_index"])


def test_the_lock_comes_before_any_data(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("data was read before the lock")

    monkeypatch.setattr(report, "explain_all", forbidden)
    monkeypatch.setattr(report, "load_landmark_rows", forbidden)
    with pytest.raises(RefusedError, match="--unlock-test"):
        report.main([])


@pytest.mark.slow  # fits the small M4; CI runs every test
def test_the_parity_is_the_largest_difference_from_the_saved_scores(
    m4: DiscreteTimeBoosting, probe: LandmarkRows
) -> None:
    saved = _saved(m4, probe)
    assert report.parity(m4, probe, saved, [12, 24]) == 0.0
    nudged = saved.assign(score_24=saved["score_24"] + 1e-6)
    assert report.parity(m4, probe, nudged, [12, 24]) == pytest.approx(1e-6, rel=1e-6)
    with pytest.raises(RefusedError, match="not the rows the locked run scored"):
        report.parity(m4, probe, saved.iloc[1:], [12])


@pytest.mark.slow
def test_a_cached_model_gives_the_same_scores_to_the_last_digit(
    cfg: ProjectConfig, m4: DiscreteTimeBoosting, probe: LandmarkRows, tmp_path: Path
) -> None:
    day = dt.date(2018, 1, 1)
    assert report.load_cached(cfg, day, tmp_path) is None
    report.save_cached(m4, day, tmp_path)
    cached = report.load_cached(cfg, day, tmp_path)
    assert cached is not None
    for months in (12, 24):
        assert np.array_equal(
            cached.predict_months(probe, months), m4.predict_months(probe, months)
        )
    assert cached.summary["rounds"] == m4.summary["rounds"]


@pytest.mark.slow
def test_the_champion_comes_from_the_cache_only_when_it_reproduces_the_saved_scores(
    cfg: ProjectConfig, m4: DiscreteTimeBoosting, probe: LandmarkRows, tmp_path: Path
) -> None:
    day = dt.date(2018, 1, 1)
    saved = _saved(m4, probe)
    fits: list[int] = []

    def fit() -> DiscreteTimeBoosting:
        fits.append(1)
        return m4

    _, source, gap = report.champion_for(cfg, day, probe, saved, tmp_path, fit)
    assert (source, gap, len(fits)) == ("fitted", 0.0, 1)  # no cache yet: fitted and cached
    _, source, gap = report.champion_for(cfg, day, probe, saved, tmp_path, fit)
    assert (source, len(fits)) == ("cache", 1)
    other = saved.assign(score_12=saved["score_12"] * 1.01)  # scores of another model
    with pytest.raises(RefusedError, match="would not be of the registered model"):
        report.champion_for(cfg, day, probe, other, tmp_path, fit)
    assert len(fits) == 2  # the cache failed, so it was fitted again, and that failed too


def test_the_saved_scores_must_be_the_locked_runs(
    cfg: ProjectConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results, predictions = tmp_path / "walkforward", tmp_path / "predictions"
    (predictions / "m4_2018").mkdir(parents=True)
    scores = predictions / "m4_2018" / "origin_2018-01-01.parquet"
    pd.DataFrame({"trial_id": ["NCT1"], "landmark_index": [0]}).to_parquet(scores)
    record: dict[str, Any] = {
        "tracking": {"git_commit": COMMIT, "git_dirty": "no"},
        "origins": [
            {
                "origin": "2018-01-01",
                "n_eval_rows": 1,
                "predictions": {
                    "path": "m4_2018/origin_2018-01-01.parquet",
                    "sha256": hashlib.sha256(scores.read_bytes()).hexdigest(),
                },
            }
        ],
    }
    results.mkdir()
    monkeypatch.setattr(report, "RESULTS_DIR", results)
    monkeypatch.setattr(report, "PREDICTIONS_DIR", predictions)
    origin = Origin(date=dt.date(2018, 1, 1), role="test")

    def written(content: dict[str, Any]) -> None:
        (results / "m4_2018.json").write_text(json.dumps(content), encoding="utf-8")

    written(record)
    assert len(report.saved_scores(cfg, origin, COMMIT)) == 1
    with pytest.raises(RefusedError, match="not made from the tagged commit"):
        report.saved_scores(cfg, origin, "d" * 40)
    written({**record, "tracking": {"git_commit": COMMIT, "git_dirty": "yes"}})
    with pytest.raises(RefusedError, match="not made from the tagged commit"):
        report.saved_scores(cfg, origin, COMMIT)
    record["origins"][0]["predictions"]["sha256"] = "0" * 64
    written(record)
    with pytest.raises(RefusedError, match="not the scores file the locked run recorded"):
        report.saved_scores(cfg, origin, COMMIT)


def test_the_sample_is_seeded_and_keeps_the_row_order(probe: LandmarkRows) -> None:
    one = report.sample_rows(probe, 50, np.random.default_rng(42))
    two = report.sample_rows(probe, 50, np.random.default_rng(42))
    assert len(one) == 50
    assert list(one.trial_id) == list(two.trial_id)
    positions = [list(probe.trial_id).index(t) for t in one.trial_id]
    assert positions == sorted(positions)
    assert report.sample_rows(probe, 10_000, np.random.default_rng(1)) is probe


def _record(origin: str) -> report.OriginExplanation:
    names = ["status", "enrollment_count", "sponsor_class"]
    record = report.OriginExplanation(origin, 1000, 500, 0.0, "fitted", 300)
    record.importance = {12: dict(zip(names, (0.3, 0.2, 0.1), strict=True)),
                         24: dict(zip(names, (0.25, 0.2, 0.05), strict=True))}  # fmt: skip
    record.direction = {"enrollment_count": "higher lowers"}
    record.agreement = {12: 0.95, 24: 0.9}
    record.additivity = {12: 1e-15, 24: 2e-15}
    return record


def _explained(kind: str, trial: str) -> report.TimelineExplained:
    points = [
        tl.Point("2018-01-01", 2, dt.date(2018, 3, 1), 0.031, 0.062),
        tl.Point("2019-01-01", 4, dt.date(2019, 3, 1), 0.052, 0.101),
    ]
    end = dt.date(2026, 9, 25) if kind == tl.OPEN else dt.date(2019, 7, 1)
    line = tl.Timeline(kind, trial, points, end, "INDUSTRY",
                       [tl.Update(dt.date(2017, 3, 1), ("registered as RECRUITING",))])  # fmt: skip
    driver = [drivers.Driver("status", "RECRUITING", 0.3)]
    return report.TimelineExplained(line, [driver, driver], dt.date(2017, 3, 1))


def test_the_document_says_what_it_is_and_shows_no_name(cfg: ProjectConfig, tmp_path: Path) -> None:
    records = [_record("2018-01-01"), _record("2019-01-01")]
    explained = [_explained(kind, f"NCT0000000{i}") for i, kind in enumerate(tl.KINDS)]
    figures = report.figure_paths(tmp_path)
    text = report.document(cfg, records, explained, COMMIT, figures)
    assert text.startswith("# Explanations of the champion\n\nResearch demo. Not medical advice.")
    assert f"Data as of {cfg.dataset.cutoff}" in text
    assert "an operational statement about a registry record" in text
    assert "| 2018-01-01 | 1,000 | 500 | 300 rounds (fitted) | 0.0e+00 | 0.950 | 0.900 |" in text
    assert "| status | amendments | 0.300 | 0.300 | 0.300 | 0.250 |  |" in text
    assert "| enrollment_count | design | 0.200 | 0.200 | 0.200 | 0.200 | higher lowers |" in text
    assert "### NCT00000002 (open at the data cutoff)" in text
    assert "still open at the data cutoff (2026-09-25)" in text
    assert "| 4 | 2019-03-01 | 2019 | 5.2% | 10.1% | status (RECRUITING) raises |" in text
    assert "lead sponsor class INDUSTRY" in text
    assert "![explain_timeline_1](figures/explain_timeline_1.png)" in text
    assert chr(0x2014) not in text  # no em dash
    assert chr(0x2013) not in text  # no en dash


@pytest.mark.slow  # draws four figures; CI runs every test
def test_the_figures_are_drawn_the_same_twice(tmp_path: Path) -> None:
    records = [_record("2018-01-01"), _record("2019-01-01")]
    explained = [_explained(kind, f"NCT0000000{i}") for i, kind in enumerate(tl.KINDS)]
    report.draw_figures(records, explained, dt.date(2026, 9, 25), tmp_path)
    first = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert sorted(first) == sorted(p.name for p in report.figure_paths(tmp_path))
    report.draw_figures(records, explained, dt.date(2026, 9, 25), tmp_path)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == first


def test_the_command_needs_a_recorded_cutoff(cfg: ProjectConfig) -> None:
    assert report.cutoff_of(cfg) == cfg.dataset.cutoff
    empty = cfg.model_copy(update={"dataset": cfg.dataset.model_copy(update={"cutoff": None})})
    with pytest.raises(RefusedError, match="no data cutoff"):
        report.cutoff_of(empty)


def test_the_arguments_default_to_the_documented_paths() -> None:
    parser_args = argparse.Namespace(sample=report.SAMPLE_ROWS)
    assert parser_args.sample == 20_000
    assert report.DOC_PATH.name == "explanations.md"
    assert report.EXPLAIN_DIR.parts[-2:] == ("data", "explain")
