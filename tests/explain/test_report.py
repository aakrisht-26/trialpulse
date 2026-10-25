"""The explanations command (Step 12, ADR 0033): the lock first, the parity with the locked
run's saved scores, the cache, the model of each timeline point, the whole command and the
document. On small M4 models fitted to simulated rows; no locked origin is read."""

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
from trialpulse.dates import add_months
from trialpulse.eval.rows import LandmarkRows
from trialpulse.eval.walkforward import FeatureRows, evaluation_rows, select_origins
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
    # Shuffled, so only the match by key makes the clean comparison exact.
    saved = _saved(m4, probe).sample(frac=1.0, random_state=0).reset_index(drop=True)
    assert report.parity(m4, probe, saved, [12, 24]) == 0.0
    nudged = saved.copy()
    nudged.loc[10, "score_24"] += 1e-6  # one row only: the largest difference, not a mean
    assert report.parity(m4, probe, nudged, [12, 24]) == pytest.approx(1e-6, rel=1e-6)
    with pytest.raises(RefusedError, match="not the rows the locked run scored"):
        report.parity(m4, probe, saved.iloc[1:], [12])
    foreign = saved.copy()
    foreign.loc[0, "trial_id"] = "NCT99999999"  # as many rows, one of another trial
    with pytest.raises(RefusedError, match="not the rows the locked run scored"):
        report.parity(m4, probe, foreign, [12])
    missing = saved.copy()
    missing.loc[0, "score_12"] = np.nan  # would compare as no difference at all
    missing.loc[1, "score_12"] += 0.1
    with pytest.raises(RefusedError, match="not a number"):
        report.parity(m4, probe, missing, [12])


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
                       [tl.Update(dt.date(2017, 3, 1), ("registered as RECRUITING",))],
                       first_post=dt.date(2017, 3, 1))  # fmt: skip
    driver = [drivers.Driver("status", "RECRUITING", 0.3)]
    return report.TimelineExplained(line, [driver, driver])


def test_the_document_says_what_it_is(cfg: ProjectConfig, tmp_path: Path) -> None:
    records = [_record("2018-01-01"), _record("2019-01-01")]
    explained = [_explained(kind, f"NCT0000000{i}") for i, kind in enumerate(tl.KINDS)]
    figures = report.figure_paths(tmp_path)
    text = report.document(cfg, records, explained, COMMIT, figures)
    assert text.startswith("# Explanations of the champion\n\nResearch demo. Not medical advice.")
    assert f"Data as of {cfg.dataset.cutoff}" in text
    assert "[ClinicalTrials.gov](https://clinicaltrials.gov) registry records" in text
    assert "these are not part of ClinicalTrials.gov (ADR 0008)" in text
    assert "an operational statement about a registry record" in text
    assert "## What moves the explained log-odds" in text
    assert "| Top drivers (12-month log-odds) |" in text
    assert "First posted 2017-03-01; lead sponsor class INDUSTRY" in text
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
    args = report.parser().parse_args([])
    assert args.sample == report.SAMPLE_ROWS == 20_000
    assert (args.doc, args.figures, args.cache) == (
        report.DOC_PATH,
        report.FIGURES_DIR,
        report.EXPLAIN_DIR,
    )
    assert args.doc.name == "explanations.md"
    assert args.cache.parts[-2:] == ("data", "explain")
    assert args.unlock_test is False


# Two test origins with a model each, on simulated trials --------------------------------------

CUTOFF = dt.date(2026, 9, 25)
ORIGINS = ("2018-01-01", "2019-01-01")


def _two_origin_rows() -> LandmarkRows:
    """Trials with landmark 2 in the 2018 window and landmark 4 in the 2019 window; trial
    NCT90000000 also has landmark 3 in the 2018 window, and ten trials have a 2018 row only.
    Each trial stops early, completes, or is open at the cutoff, after its last landmark."""
    base, _, _ = simulated_rows(120, seed=11)
    rng = np.random.default_rng(11)
    n = len(base)
    first = np.datetime64("2018-02-01") + rng.integers(0, 120, n).astype("timedelta64[D]")
    second = add_months(first, 12)
    kind = rng.integers(0, 3, n)
    end = np.where(kind == 0, np.datetime64(CUTOFF), second + np.timedelta64(200, "D"))
    ids = np.array([f"NCT9{i:07d}" for i in range(n)])
    only_first = np.arange(n) >= n - 10
    parts = [(np.arange(n), 2, first), (np.flatnonzero(~only_first), 4, second), ([0], 3,
             add_months(first[:1], 6))]  # fmt: skip
    pick = np.concatenate([np.asarray(p[0]) for p in parts])
    index = np.concatenate([np.full(len(p[0]), p[1]) for p in parts])
    dates = np.concatenate([p[2] if len(p[2]) == len(p[0]) else p[2][p[0]] for p in parts])
    features = {k: np.asarray(v)[pick] for k, v in base.features.items()}
    features["landmark_index"] = index.astype(np.float64)
    features["months_since_t0"] = 6.0 * index
    return LandmarkRows(
        ids[pick],
        index.astype(np.int64),
        dates.astype("datetime64[D]"),
        kind[pick].astype(np.int64),
        end[pick].astype("datetime64[D]"),
        features,
    )


def _origin_rows(rows: LandmarkRows, origin: str) -> LandmarkRows:
    return evaluation_rows(rows, np.datetime64(origin, "D"), 12)[0]


def _scores(model: DiscreteTimeBoosting, rows: LandmarkRows) -> pd.DataFrame:
    days = (rows.event_date - rows.landmark_date).astype(np.int64).astype(np.float64)
    return pd.DataFrame(
        {
            "trial_id": rows.trial_id.astype(str),
            "landmark_index": rows.landmark_index,
            "landmark_date": rows.landmark_date.astype("datetime64[D]").astype(object),
            "time": days,
            "event": rows.event,
            "censoring_group": "OTHER",
            "score_12": model.predict_months(rows, 12),
            "score_24": model.predict_months(rows, 24),
        }
    ).sort_values(["trial_id", "landmark_index"])


@pytest.mark.slow  # fits two small models; CI runs every test
def test_each_point_is_explained_by_its_origins_model(
    m4: DiscreteTimeBoosting, m4_other: DiscreteTimeBoosting
) -> None:
    rows = _two_origin_rows()
    by_origin = {o: _origin_rows(rows, o) for o in ORIGINS}
    models = {ORIGINS[0]: m4, ORIGINS[1]: m4_other}
    saved = {o: _scores(models[o], by_origin[o]) for o in ORIGINS}
    scored = tl.scored_rows(saved)
    line = tl.timeline(scored, "NCT90000000", tl.STOPPED)  # landmarks 2 and 3 in 2018, 4 in 2019
    assert [(p.origin, p.landmark_index) for p in line.points] == [
        (ORIGINS[0], 2), (ORIGINS[0], 3), (ORIGINS[1], 4)
    ]  # fmt: skip
    explained = report.explain_timeline(line, models, by_origin, 12)
    for point, found in zip(line.points, explained.drivers, strict=True):
        ev = by_origin[point.origin]
        own = ev.subset(
            (ev.trial_id == line.trial_id) & (ev.landmark_index == point.landmark_index)
        )
        expected = drivers.top_drivers(
            drivers.explain(models[point.origin], own, 12), own.features, 0
        )
        assert found == expected
    swapped = {ORIGINS[0]: m4_other, ORIGINS[1]: m4}
    with pytest.raises(RefusedError, match="does not give NCT90000000"):
        report.explain_timeline(line, swapped, by_origin, 12)


def _warehouse(path: Path, rows: LandmarkRows) -> Path:
    first = pd.DataFrame(
        {"nct_id": rows.trial_id.astype(str), "date": rows.landmark_date, "k": rows.landmark_index}
    )
    first = first.sort_values("k", kind="stable").drop_duplicates("nct_id")
    assert (first["k"] == 2).all()  # every simulated trial's first landmark is landmark 2
    t0 = add_months(first["date"].to_numpy().astype("datetime64[D]"), -12)
    frame = pd.DataFrame(  # noqa: F841 (read by DuckDB below)
        {
            "nct_id": first["nct_id"].to_numpy(),
            "nct_version": 1,
            "effective_date": t0,
            "overall_status": "RECRUITING",
            "last_known_status": None,
            "start_date": t0,
            "primary_completion_date": t0,
            "enrollment_count": 100,
            "enrollment_type": "ESTIMATED",
            "lead_sponsor_class": "OTHER",
            "lead_sponsor_name": "A Person",
            "study_first_post_date": t0,
        }
    )
    import duckdb

    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE versions AS SELECT * FROM frame")
    return path


@pytest.mark.slow  # fits two small models and draws four figures; CI runs every test
def test_the_command_explains_both_origins_and_draws_by_the_declared_rule(
    cfg: ProjectConfig,
    m4: DiscreteTimeBoosting,
    m4_other: DiscreteTimeBoosting,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _two_origin_rows()
    by_year = {2018: m4, 2019: m4_other}
    origins = select_origins(cfg, "test")
    assert [o.date.isoformat() for o in origins] == list(ORIGINS)

    def loader(origin: dt.date, role: str, only: object) -> FeatureRows:
        ev = _origin_rows(rows, origin.isoformat())
        return FeatureRows(ev.trial_id, ev.landmark_index, ev.features)

    monkeypatch.setattr(
        report, "saved_scores",
        lambda cfg, origin, commit: _scores(by_year[origin.date.year],
                                            _origin_rows(rows, origin.date.isoformat())),
    )  # fmt: skip
    monkeypatch.setattr(report, "fit_champion", lambda cfg, day, *rest: by_year[day.year])
    args = report.parser().parse_args(
        ["--unlock-test", "--sample", "60", "--doc", str(tmp_path / "e.md"),
         "--figures", str(tmp_path / "figures"), "--cache", str(tmp_path / "cache")]
    )  # fmt: skip
    assert report.explain_all(
        cfg, origins, COMMIT, args, load_rows=lambda: rows, load_features=loader,
        warehouse=_warehouse(tmp_path / "w.duckdb", rows),
    ) == 0  # fmt: skip
    summary = json.loads((tmp_path / "cache" / "summary.json").read_text(encoding="utf-8"))
    assert [o["model_source"] for o in summary["origins"]] == ["fitted", "fitted"]
    assert sorted(p.name for p in (tmp_path / "cache").glob("m4_*.txt")) == [
        "m4_2018-01-01.txt", "m4_2019-01-01.txt"
    ]  # fmt: skip
    # The pools hold the trials with rows at both origins only, and the draw is the rule's.
    saved = {o: _scores(by_year[int(o[:4])], _origin_rows(rows, o)) for o in ORIGINS}
    pools = tl.eligible(tl.scored_rows(saved), CUTOFF, list(ORIGINS))
    assert sum(summary["candidates"].values()) == 110
    assert summary["candidates"] == {kind: len(ids) for kind, ids in pools.items()}
    assert summary["chosen"] == tl.draw(pools, cfg.seeds.default)
    text = (tmp_path / "e.md").read_text(encoding="utf-8")
    assert "A Person" not in text
    for kind, trial in summary["chosen"].items():
        assert f"### {trial} ({kind})" in text
    assert len(list((tmp_path / "figures").glob("*.png"))) == 4
