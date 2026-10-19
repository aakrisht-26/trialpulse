"""Paired differences between two models (Step 10), on made-up prediction files whose
answers are known. No real data, no model."""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pytest

from trialpulse import tracking
from trialpulse.cli import RefusedError
from trialpulse.config import ProjectConfig, load_project_config
from trialpulse.eval import compare
from trialpulse.eval.bootstrap import (
    BootstrapError,
    cluster_bootstrap,
    cluster_bootstrap_many,
    resamples,
)
from trialpulse.eval.metrics import ipcw_auc, ipcw_brier
from trialpulse.eval.walkforward import predictions_path

ORIGINS = (dt.date(2016, 1, 1), dt.date(2017, 1, 1))


@pytest.fixture(scope="module")
def cfg() -> ProjectConfig:
    return load_project_config()


def _frame(origin: dt.date, n_trials: int = 1500, seed: int = 0) -> pd.DataFrame:
    """Evaluation rows of one origin: two landmarks per trial, a latent risk that drives the
    early stops, and three scores. `good` follows the risk, `fair` follows it with noise,
    `noise` knows nothing."""
    rng = np.random.default_rng(seed + origin.year)
    risk = rng.normal(size=n_trials)
    stop = rng.exponential(1.0 / (0.0006 * np.exp(0.9 * risk)))
    complete = rng.exponential(1.0 / 0.0012, n_trials)
    censor = rng.exponential(2500.0, n_trials)
    end = np.minimum(np.minimum(stop, complete), censor)
    event = np.where(censor <= np.minimum(stop, complete), 0, np.where(stop < complete, 1, 2))
    rows = []
    for k in (0, 1):
        open_at = end > 182 * k + 1
        landmark = np.datetime64(origin, "D") + np.timedelta64(30 + 182 * k, "D")
        rows.append(
            pd.DataFrame(
                {
                    # Some trials of 2016 are evaluated again in 2017: one cluster.
                    "trial_id": [
                        f"NCT{i + (0 if origin.year == 2016 else 1000):08d}"
                        for i in np.flatnonzero(open_at)
                    ],
                    "landmark_index": k,
                    "landmark_date": landmark,
                    "time": np.floor(end[open_at] - 182 * k) + 1.0,
                    "event": event[open_at],
                    "censoring_group": np.where(risk[open_at] > 0, "INDUSTRY", "OTHER"),
                    "risk": risk[open_at],
                }
            )
        )
    frame = pd.concat(rows, ignore_index=True)
    noise = np.random.default_rng(seed + 7).normal(size=len(frame))
    for months in (12, 24):
        base = 1.0 / (1.0 + np.exp(-(frame["risk"].to_numpy() - 2.5 + months / 24.0)))
        frame[f"good_{months}"] = base
        frame[f"fair_{months}"] = 1.0 / (
            1.0 + np.exp(-(frame["risk"].to_numpy() + 1.5 * noise - 2.5 + months / 24.0))
        )
        frame[f"noise_{months}"] = 1.0 / (1.0 + np.exp(-(noise - 2.5)))
    return frame.sort_values(["trial_id", "landmark_index"]).reset_index(drop=True)


def _save(folder: Path, model: str, origin: dt.date, frame: pd.DataFrame, score: str,
          only: tuple[int, ...] | None = None) -> None:  # fmt: skip
    chosen = frame if only is None else frame[frame["landmark_index"].isin(only)]
    out = chosen[["trial_id", "landmark_index", "landmark_date", "time", "event",
                  "censoring_group"]].copy()  # fmt: skip
    for months in (12, 24):
        out[f"score_{months}"] = chosen[f"{score}_{months}"].to_numpy()
    path = predictions_path(folder, model, "dev", origin)
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as con:
        con.execute(
            f"""COPY (SELECT * REPLACE (CAST(landmark_date AS DATE) AS landmark_date) FROM out)
            TO '{path.as_posix()}' (FORMAT parquet)"""
        )


@pytest.fixture(scope="module")
def frames() -> dict[dt.date, pd.DataFrame]:
    return {origin: _frame(origin) for origin in ORIGINS}


@pytest.fixture
def folder(tmp_path: Path, frames: dict[dt.date, pd.DataFrame]) -> Path:
    """m4 follows the risk, m3 follows it with noise, m2 knows nothing; m1 is m3's score at
    landmark 0 only."""
    for origin, frame in frames.items():
        _save(tmp_path, "m4", origin, frame, "good")
        _save(tmp_path, "m3", origin, frame, "fair")
        _save(tmp_path, "m2", origin, frame, "noise")
        _save(tmp_path, "m1", origin, frame, "fair", only=(0,))
    return tmp_path


@pytest.mark.slow  # over half a second; CI runs every test
def test_the_difference_is_the_mean_over_the_origins_of_the_difference_at_each(
    cfg: ProjectConfig, folder: Path, frames: dict[dt.date, pd.DataFrame]
) -> None:
    result = compare.compare_pair(cfg, folder, "m4", "m2", True, ORIGINS, 60)
    assert result["landmark_indices"] == [0, 1]
    assert set(result["horizons"]) == {"12", "24"}
    part = result["horizons"]["12"][compare.ALL]
    assert set(result["horizons"]["12"]) == {compare.ALL, "0", "1"}
    aucs: dict[int, list[float]] = {12: [], 24: []}
    for months in (12, 24):  # each horizon with its own labels, weights and scores
        slice_ = result["horizons"][str(months)][compare.ALL]
        deltas, briers = [], []
        for origin, frame in frames.items():
            time, event = frame["time"].to_numpy(), frame["event"].to_numpy()
            groups = frame["censoring_group"].to_numpy()
            # Each row's horizon in days, by the calendar and without the project's helpers.
            landmark = pd.DatetimeIndex(frame["landmark_date"])
            horizon = np.asarray(
                ((landmark + pd.DateOffset(months=months)) - landmark).days, dtype=float
            )
            assert set(horizon.tolist()) <= {365.0, 366.0, 730.0, 731.0}
            good = frame[f"good_{months}"].to_numpy()
            noise = frame[f"noise_{months}"].to_numpy()
            here = slice_["by_origin"][origin.isoformat()]
            assert here["n_rows"] == len(frame)
            assert here["auc_model"] == pytest.approx(ipcw_auc(time, event, good, horizon, groups))
            assert here["auc_baseline"] == pytest.approx(
                ipcw_auc(time, event, noise, horizon, groups)
            )
            assert here["brier_model"] == pytest.approx(
                ipcw_brier(time, event, good, horizon, groups)
            )
            assert here["brier_baseline"] == pytest.approx(
                ipcw_brier(time, event, noise, horizon, groups)
            )
            assert here["delta_auc"] == pytest.approx(here["auc_model"] - here["auc_baseline"])
            deltas.append(here["delta_auc"])
            briers.append(here["brier_model"] - here["brier_baseline"])
            aucs[months].append(here["auc_model"])
        assert slice_["delta_auc"]["estimate"] == pytest.approx(np.mean(deltas))
        assert slice_["delta_brier"]["estimate"] == pytest.approx(np.mean(briers))
    # The two horizons are two questions: the made-up rows give them different answers.
    assert all(abs(a - b) > 1e-3 for a, b in zip(aucs[12], aucs[24], strict=True))
    assert part["n_rows"] == sum(len(f) for f in frames.values())
    # A model that follows the risk beats one that knows nothing, and the interval says so.
    assert part["delta_auc"]["estimate"] > 0.1
    assert part["delta_auc"]["ci_low"] > 0.05
    assert (
        part["delta_auc"]["ci_low"] < part["delta_auc"]["estimate"] < part["delta_auc"]["ci_high"]
    )
    assert part["delta_auc"]["resamples"] == 60
    first = result["horizons"]["12"]["0"]
    assert first["n_rows"] == sum(int((f["landmark_index"] == 0).sum()) for f in frames.values())


@pytest.mark.slow  # over half a second; CI runs every test
def test_a_trial_evaluated_at_two_origins_is_one_cluster(
    cfg: ProjectConfig,
    folder: Path,
    frames: dict[dt.date, pd.DataFrame],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR 0025, decision 6: the origins are resampled together. A trial with evaluation
    rows at both origins is drawn, or left out, with all of them."""
    handed: list[np.ndarray[Any, Any]] = []
    real = compare.cluster_bootstrap_many

    def recording(clusters: Any, *args: Any) -> Any:
        handed.append(np.asarray(clusters))
        return real(clusters, *args)

    monkeypatch.setattr(compare, "cluster_bootstrap_many", recording)
    rows = compare.load_pair(folder, "m4", "m3", ORIGINS, (12, 24))
    part = compare.compare_slice(rows, 12, ORIGINS, cfg, 20)
    (clusters,) = handed
    first, second = (set(frame["trial_id"]) for frame in frames.values())
    assert len(first & second) > 400  # the trials the two made-up origins share
    assert clusters.tolist() == rows.trial_id.tolist()
    assert set(clusters.tolist()) == first | second
    assert len(set(clusters.tolist())) == len(first) + len(second) - len(first & second)
    assert part["n_trials"] == len(first | second)


@pytest.mark.slow  # over half a second; CI runs every test
def test_a_model_against_itself_differs_by_nothing(
    cfg: ProjectConfig, tmp_path: Path, frames: dict[dt.date, pd.DataFrame]
) -> None:
    for origin, frame in frames.items():
        _save(tmp_path, "m4", origin, frame, "good")
        _save(tmp_path, "m3", origin, frame, "good")
    part = compare.compare_pair(cfg, tmp_path, "m4", "m3", False, ORIGINS, 40)["horizons"]["24"]
    for metric in ("delta_auc", "delta_brier"):
        assert part[compare.ALL][metric] == {
            "estimate": 0.0, "ci_low": 0.0, "ci_high": 0.0, "resamples": 40,
            "valid_resamples": 40, "invalid_resamples": 0,
        }  # fmt: skip


@pytest.mark.slow  # over half a second; CI runs every test
def test_the_paired_interval_is_much_narrower_than_two_separate_ones(
    cfg: ProjectConfig, tmp_path: Path, frames: dict[dt.date, pd.DataFrame]
) -> None:
    """Two models that agree on most rows: each AUC moves a lot from resample to resample,
    their difference hardly at all. Scoring both on the same resampled trials shows it."""
    origin = ORIGINS[0]
    frame = frames[origin].copy()
    nudge = np.random.default_rng(3).normal(scale=0.05, size=len(frame))
    for months in (12, 24):
        logit = np.log(frame[f"good_{months}"] / (1 - frame[f"good_{months}"])) + nudge
        frame[f"near_{months}"] = 1 / (1 + np.exp(-logit))
    _save(tmp_path, "m4", origin, frame, "good")
    _save(tmp_path, "m3", origin, frame, "near")
    rows = compare.load_pair(tmp_path, "m4", "m3", (origin,), (12, 24))
    paired = compare.compare_slice(rows, 12, (origin,), cfg, 200)["delta_auc"]
    separate = cluster_bootstrap(
        rows.trial_id,
        lambda idx: ipcw_auc(rows.time[idx], rows.event[idx], rows.model[12][idx],
                             rows.horizon[12][idx], rows.groups[idx]),
        200, cfg.seeds.default, cfg.evaluation.confidence_level,
    )  # fmt: skip
    width = paired["ci_high"] - paired["ci_low"]
    assert width < 0.25 * (separate["ci_high"] - separate["ci_low"])
    assert paired["ci_low"] < 0.0 < paired["ci_high"]  # no difference to find


def test_several_statistics_share_the_resamples_of_one(cfg: ProjectConfig) -> None:
    rng = np.random.default_rng(5)
    clusters = rng.integers(0, 80, 400)
    x, y = rng.normal(size=400), rng.normal(size=400)
    many = cluster_bootstrap_many(
        clusters, lambda idx: {"x": float(x[idx].mean()), "y": float(y[idx].mean())}, 150, 9, 0.9
    )
    for name, values in (("x", x), ("y", y)):
        alone = cluster_bootstrap(clusters, lambda idx, v=values: float(v[idx].mean()), 150, 9, 0.9)
        assert many[name] == alone  # the same draws, the same interval


def test_a_statistic_that_is_not_finite_is_left_out_of_its_own_interval_only() -> None:
    """Fifty clusters of one row. "rare" is not finite in a resample that draws none of the
    first five clusters (about one resample in 200), "steady" is always finite, and "often"
    is not finite whenever cluster 0 is missing (about one in three)."""
    clusters = np.arange(50)
    n, seed = 600, 3
    missing = sum(1 for idx in resamples(clusters, n, seed) if idx.min() >= 5)
    assert 1 <= missing <= 6  # some resamples, and under the 1% limit

    def statistics(idx: Any) -> dict[str, float]:
        return {"steady": float(idx.mean()), "rare": float("nan") if idx.min() >= 5 else 1.0}

    out = cluster_bootstrap_many(clusters, statistics, n, seed, 0.9)
    assert (out["rare"]["valid_resamples"], out["rare"]["invalid_resamples"]) == (
        n - missing, missing,
    )  # fmt: skip
    assert (out["rare"]["ci_low"], out["rare"]["ci_high"]) == (1.0, 1.0)
    assert (out["steady"]["valid_resamples"], out["steady"]["invalid_resamples"]) == (n, 0)
    assert out["steady"]["ci_low"] < out["steady"]["estimate"] < out["steady"]["ci_high"]
    with pytest.raises(BootstrapError, match="not finite"):
        cluster_bootstrap_many(
            clusters,
            lambda idx: {"often": float("inf") if idx.min() > 0 else 1.0},
            n, seed, 0.9,
        )  # fmt: skip


@pytest.mark.slow  # over half a second; CI runs every test
def test_a_model_of_landmark_0_is_compared_at_landmark_0(cfg: ProjectConfig, folder: Path) -> None:
    result = compare.compare_pair(cfg, folder, "m4", "m1", False, ORIGINS, 40)
    assert result["landmark_indices"] == [0]
    assert set(result["horizons"]["12"]) == {"0"}
    assert result["horizons"]["12"]["0"]["delta_auc"]["estimate"] > 0.0


@pytest.mark.slow  # over half a second; CI runs every test
def test_files_that_do_not_hold_the_same_rows_are_refused(
    cfg: ProjectConfig, tmp_path: Path, frames: dict[dt.date, pd.DataFrame]
) -> None:
    origin = ORIGINS[0]
    frame = frames[origin]
    _save(tmp_path, "m4", origin, frame, "good")
    # A row with another follow-up time: not the same evaluation rows.
    other = frame.copy()
    other.loc[5, "time"] += 1.0
    _save(tmp_path, "m3", origin, other, "fair")
    with pytest.raises(RefusedError, match="not scored on the same evaluation rows"):
        compare.load_pair(tmp_path, "m4", "m3", (origin,), (12, 24))
    # A row missing from one file, at a landmark index both score.
    _save(tmp_path, "m3", origin, frame.drop(index=7), "fair")
    with pytest.raises(RefusedError, match="not scored on the same evaluation rows"):
        compare.load_pair(tmp_path, "m4", "m3", (origin,), (12, 24))
    # Another censoring group for one row.
    other = frame.copy()
    other.loc[9, "censoring_group"] = "NIH"
    _save(tmp_path, "m3", origin, other, "fair")
    with pytest.raises(RefusedError, match="not scored on the same evaluation rows"):
        compare.load_pair(tmp_path, "m4", "m3", (origin,), (12, 24))
    # Another event for one row, with the same follow-up: a completion where the other
    # file holds a censoring.
    other = frame.copy()
    censored = other.index[other["event"] == 0][0]
    other.loc[censored, "event"] = 2
    _save(tmp_path, "m3", origin, other, "fair")
    with pytest.raises(RefusedError, match="not scored on the same evaluation rows"):
        compare.load_pair(tmp_path, "m4", "m3", (origin,), (12, 24))
    # Another landmark date for one row: its horizon would be another day.
    other = frame.copy()
    other.loc[11, "landmark_date"] += np.timedelta64(1, "D")
    _save(tmp_path, "m3", origin, other, "fair")
    with pytest.raises(RefusedError, match="not scored on the same evaluation rows"):
        compare.load_pair(tmp_path, "m4", "m3", (origin,), (12, 24))
    # Two files that share no landmark index hold no row to compare.
    _save(tmp_path, "m3", origin, frame, "fair", only=(1,))
    _save(tmp_path, "m1", origin, frame, "fair", only=(0,))
    with pytest.raises(RefusedError, match="not scored on the same evaluation rows"):
        compare.load_pair(tmp_path, "m3", "m1", (origin,), (12, 24))
    # The same rows again: accepted.
    _save(tmp_path, "m3", origin, frame, "fair")
    assert len(compare.load_pair(tmp_path, "m4", "m3", (origin,), (12, 24))) == len(frame)
    with pytest.raises(FileNotFoundError, match=r"--model m2 --origins dev"):
        compare.load_pair(tmp_path, "m4", "m2", (origin,), (12, 24))


@pytest.mark.slow  # over half a second; CI runs every test
def test_the_command_compares_every_pair_that_has_files(
    folder: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], tracked_runs: list[Any]
) -> None:
    out = tmp_path / "compare.json"
    args = ["--predictions-dir", str(folder), "--out", str(out), "--resamples", "30"]
    assert compare.main(args) == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["origins"] == ["2016-01-01", "2017-01-01"]
    assert saved["bootstrap_resamples"] == 30
    assert [(p["model"], p["baseline"]) for p in saved["pairs"]] == [
        ("m3", "m2"), ("m4", "m3"), ("m4", "m1"),
    ]  # fmt: skip
    assert {"model": "m2", "baseline": "m0"} in saved["skipped"]  # no file for M0 here
    by_pair = {(p["model"], p["baseline"]): p for p in saved["pairs"]}
    # The rungs of the ladder are compared at every landmark index too.
    assert set(by_pair["m3", "m2"]["horizons"]["12"]) == {"all", "0", "1"}
    assert set(by_pair["m4", "m1"]["horizons"]["24"]) == {"0"}
    assert by_pair["m4", "m3"]["what"].startswith("the sponsor record and competition")
    printed = capsys.readouterr().out
    assert "m3 minus m2: AUC +" in printed
    assert "at 12 months" in printed
    (logged,) = tracked_runs
    assert logged["name"] == "compare-dev"
    assert logged["artifacts"] == [str(out)]
    assert logged["tags"] == {"step": "10", "kind": "comparison", "roles": "dev"}
    assert logged["state"] == tracking.git_state()
    part = by_pair["m4", "m3"]["horizons"]["12"]["all"]
    assert logged["metrics"]["delta_auc_12m_all_m4_minus_m3"] == part["delta_auc"]["estimate"]
    assert (
        logged["metrics"]["delta_brier_24m_l0_m4_minus_m1"]
        == (by_pair["m4", "m1"]["horizons"]["24"]["0"]["delta_brier"]["estimate"])
    )
    assert "delta_auc_12m_all_m4_minus_m1" not in logged["metrics"]  # landmark 0 only
    # One pair on request, not logged, and the same numbers again.
    again = tmp_path / "again.json"
    assert compare.main([*args[:2], "--out", str(again), "--resamples", "30", "--pairs", "m4:m3",
                         "--no-track"]) == 0  # fmt: skip
    only = json.loads(again.read_text(encoding="utf-8"))
    assert [(p["model"], p["baseline"]) for p in only["pairs"]] == [("m4", "m3")]
    assert only["pairs"][0]["horizons"] == by_pair["m4", "m3"]["horizons"]
    assert "tracking" not in only
    assert len(tracked_runs) == 1


def test_without_any_prediction_file_the_command_names_the_run_to_make(tmp_path: Path) -> None:
    with pytest.raises(RefusedError, match=r"trialpulse\.eval\.walkforward --model m4"):
        compare.main(["--predictions-dir", str(tmp_path), "--out", str(tmp_path / "x.json")])


@pytest.mark.slow  # over half a second; CI runs every test
def test_a_tracking_failure_keeps_the_comparison(
    folder: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    def refused(*args: object, **kwargs: object) -> dict[str, str]:
        raise tracking.TrackingError("the server answered 404")

    monkeypatch.setattr(tracking, "log_run", refused)
    out = tmp_path / "compare.json"
    code = compare.main(["--predictions-dir", str(folder), "--out", str(out), "--resamples", "10",
                         "--pairs", "m4:m3"])  # fmt: skip
    assert code == tracking.FAILED_EXIT_CODE
    assert "not logged to MLflow (the server answered 404)" in capsys.readouterr().err
    assert len(json.loads(out.read_text(encoding="utf-8"))["pairs"]) == 1
