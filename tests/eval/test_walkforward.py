"""The walk-forward runner on synthetic landmark rows. Locked origins are only ever
requested to prove they are refused; no locked origin is evaluated."""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pytest

from trialpulse import tracking
from trialpulse.config import ProjectConfig, load_project_config
from trialpulse.eval import walkforward
from trialpulse.eval.bootstrap import BootstrapError
from trialpulse.eval.lock import TestLockError
from trialpulse.eval.walkforward import (
    LandmarkRows,
    evaluation_rows,
    horizon_days,
    load_landmark_rows,
    run,
    select_origins,
    training_path,
    training_rows,
)


@pytest.fixture
def cfg() -> ProjectConfig:
    return load_project_config()


def _rows(n_trials: int = 400, seed: int = 0) -> LandmarkRows:
    """Synthetic trials first posted 2013 to 2017, two landmarks each, two strata with
    different early-stop hazards."""
    rng = np.random.default_rng(seed)
    t0 = np.datetime64("2013-01-01") + rng.integers(0, 5 * 365, n_trials).astype("timedelta64[D]")
    stratum = rng.choice(["phase2", "phase3"], n_trials)
    stop = rng.exponential(np.where(stratum == "phase2", 900, 3000))
    complete = rng.exponential(1200, n_trials)
    cutoff = np.datetime64("2026-05-15")
    end_days = np.minimum(stop, complete)
    end = t0 + end_days.astype("timedelta64[D]")
    event = np.where(stop < complete, 1, 2)
    event = np.where(end > cutoff, 0, event)
    end = np.minimum(end, cutoff)
    ids, ks, ls, evs, eds, strata = [], [], [], [], [], []
    for i in range(n_trials):
        for k in range(2):
            landmark = walkforward.add_months(np.array([t0[i]]), 6 * k)[0]
            if end[i] <= landmark:
                continue  # not open at the landmark
            ids.append(f"NCT{i:08d}")
            ks.append(k)
            ls.append(landmark)
            evs.append(event[i])
            eds.append(end[i])
            strata.append(stratum[i])
    return LandmarkRows(
        np.array(ids),
        np.array(ks, dtype=np.int64),
        np.array(ls, dtype="datetime64[D]"),
        np.array(evs, dtype=np.int64),
        np.array(eds, dtype="datetime64[D]"),
        {"stratum": np.array(strata)},
    )


def test_select_origins(cfg: ProjectConfig) -> None:
    assert [o.date.year for o in select_origins(cfg, "dev")] == [2016, 2017]
    assert [o.role for o in select_origins(cfg, "2017")] == ["dev"]
    with pytest.raises(ValueError, match="unknown origin year"):
        select_origins(cfg, "2015")


def _as_of(rows: LandmarkRows, origin: dt.date) -> LandmarkRows:
    """A stand-in for the cohort built as of an origin, for synthetic rows that have no
    lapse and no reversal: landmarks before the origin, events on or after it not known."""
    t = np.datetime64(origin, "D")
    before = rows.subset(rows.landmark_date < t)
    late = before.event_date >= t
    return LandmarkRows(
        before.trial_id,
        before.landmark_index,
        before.landmark_date,
        np.where(late, 0, before.event).astype(np.int64),
        np.where(late, t, before.event_date).astype("datetime64[D]"),
        before.features,
    )


def _training(origin: dt.date) -> LandmarkRows:
    return _as_of(_rows(), origin)


def test_training_rows_take_the_rows_built_as_of_the_origin() -> None:
    origin = dt.date(2016, 1, 1)
    rows = _as_of(_rows(), origin)
    train, time, event = training_rows(rows, np.datetime64(origin, "D"))

    assert train is rows
    assert np.array_equal(event, rows.event)
    assert np.array_equal(time, (rows.event_date - rows.landmark_date).astype(np.float64))
    assert (event == 0).any()
    assert (event == 1).any()


@pytest.mark.parametrize(
    ("landmark", "event", "event_date"),
    [
        ("2016-01-01", 0, "2016-01-01"),  # a landmark on the origin
        ("2015-06-01", 1, "2016-01-01"),  # an event dated on the origin
        ("2015-06-01", 2, "2016-03-01"),  # an event after the origin
        ("2015-06-01", 0, "2016-01-02"),  # censored after the origin
    ],
)
def test_training_rows_refuse_anything_dated_after_the_origin(
    landmark: str, event: int, event_date: str
) -> None:
    """Rows from the final cohort know what happened later. The harness does not truncate
    them, as it did before ADR 0016: it refuses them."""
    good = _as_of(_rows(), dt.date(2016, 1, 1))
    bad = LandmarkRows(
        np.append(good.trial_id, "NCT99999999"),
        np.append(good.landmark_index, 0),
        np.append(good.landmark_date, np.datetime64(landmark)),
        np.append(good.event, event),
        np.append(good.event_date, np.datetime64(event_date)),
        {"stratum": np.append(good.features["stratum"], "phase2")},
    )
    with pytest.raises(ValueError, match=r"1 training rows for origin 2016-01-01 .*ADR 0016"):
        training_rows(bad, np.datetime64("2016-01-01"))


def test_evaluation_rows_cover_one_year_from_the_origin() -> None:
    rows = _rows()
    origin = np.datetime64("2016-01-01")
    ev, time, event = evaluation_rows(rows, origin, 12)

    assert np.all(ev.landmark_date >= origin)
    assert np.all(ev.landmark_date < np.datetime64("2017-01-01"))
    assert np.array_equal(event, ev.event)
    assert np.all(time >= 0)


def test_horizon_days_use_calendar_months() -> None:
    dates = np.array(["2019-03-01", "2020-01-15"], dtype="datetime64[D]")
    assert horizon_days(dates, 12).tolist() == [366.0, 366.0]
    assert horizon_days(dates, 24).tolist() == [731.0, 731.0]


def test_m0_runs_end_to_end_on_development_origins(cfg: ProjectConfig) -> None:
    results = run(cfg, "m0", "dev", lambda: _rows(), _training, n_resamples=20)

    assert results["unlock"] is None
    assert results["training_labels"] == "as of each origin (ADR 0016)"
    assert [o["n_train_rows"] for o in results["origins"]] == [
        len(_training(dt.date(2016, 1, 1))),
        len(_training(dt.date(2017, 1, 1))),
    ]
    assert [o["origin"] for o in results["origins"]] == ["2016-01-01", "2017-01-01"]
    pooled = results["origins"][0]["horizons"]["12"]["pooled"]
    assert pooled["n_rows"] > 0
    # Strata differ in hazard, so M0 should beat chance on this synthetic data.
    assert pooled["auc"]["estimate"] > 0.55
    assert pooled["auc"]["valid_resamples"] > 0
    assert set(results["origins"][0]["horizons"]) == {"12", "24"}
    assert "0" in results["origins"][0]["horizons"]["12"]["by_landmark_index"]


@pytest.mark.parametrize("spec", ["test", "stress", "all", "2018", "2019", "2020"])
def test_locked_origins_are_refused_before_any_data_is_read(
    cfg: ProjectConfig, tmp_path: Path, spec: str
) -> None:
    def must_not_load() -> LandmarkRows:
        raise AssertionError("data was loaded for a locked origin")

    def no_training(origin: dt.date) -> LandmarkRows:
        raise AssertionError("training rows were loaded for a locked origin")

    with pytest.raises(TestLockError, match="--unlock-test"):
        run(cfg, "m0", spec, must_not_load, no_training, unlock_flag=False, repo=tmp_path)
    # Even with the flag, a repository without a completed, tagged registration refuses.
    with pytest.raises(TestLockError):
        run(cfg, "m0", spec, must_not_load, no_training, unlock_flag=True, repo=tmp_path)


def test_a_slice_without_cases_fails_with_its_name(cfg: ProjectConfig) -> None:
    rows = _rows()
    in_2016 = (rows.landmark_date >= np.datetime64("2016-01-01")) & (
        rows.landmark_date < np.datetime64("2017-01-01")
    )
    no_stops = in_2016 & (rows.landmark_index == 1)
    rows.event[no_stops & (rows.event == 1)] = 2  # no early stop left in that slice

    with pytest.raises(BootstrapError) as info:
        run(cfg, "m0", "2016", lambda: rows, _training, n_resamples=50)

    message = str(info.value)
    assert "origin 2016-01-01, horizon 12 months, landmark index 1, metric auc" in message
    assert "50 of 50 bootstrap resamples" in message


def test_cli_reports_a_bootstrap_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing_run(*args: object, **kwargs: object) -> dict[str, object]:
        raise BootstrapError("origin 2016-01-01, horizon 12 months, pooled, metric auc: too few")

    monkeypatch.setattr(walkforward, "run", failing_run)
    assert walkforward.main(["--model", "m0", "--origins", "dev"]) == 3
    assert "failed: origin 2016-01-01" in capsys.readouterr().err


def test_cli_refuses_locked_origins(capsys: pytest.CaptureFixture[str]) -> None:
    assert walkforward.main(["--model", "m0", "--origins", "test"]) == 2
    assert "refused" in capsys.readouterr().err


def test_cli_reads_one_training_file_per_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The evaluation rows come from --input and the training rows of each origin from its
    own file under --training-dir. Without that file the run is refused."""

    def write(rows: LandmarkRows, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame(
            {
                "trial_id": rows.trial_id,
                "landmark_index": rows.landmark_index,
                "landmark_date": rows.landmark_date,
                "event": rows.event,
                "event_date": rows.event_date,
                "stratum": rows.features["stratum"],
            }
        )
        with duckdb.connect() as con:
            con.register("frame", frame)
            con.execute(
                f"""COPY (SELECT trial_id, landmark_index, CAST(landmark_date AS DATE)
                  AS landmark_date, event, CAST(event_date AS DATE) AS event_date, stratum
                FROM frame) TO '{path.as_posix()}' (FORMAT parquet)"""
            )

    final, training_dir = tmp_path / "landmarks.parquet", tmp_path / "training"
    write(_rows(), final)
    args = ["--model", "m0", "--origins", "2016", "--resamples", "20", "--input", str(final),
            "--training-dir", str(training_dir)]  # fmt: skip
    monkeypatch.setattr(walkforward, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(walkforward, "PREDICTIONS_DIR", tmp_path / "predictions")
    assert walkforward.main(args) == 2
    assert "origin_2016-01-01" in capsys.readouterr().err

    origin = dt.date(2016, 1, 1)
    assert training_path(training_dir, origin) == (
        training_dir / "origin_2016-01-01" / "landmarks.parquet"
    )
    write(_training(origin), training_path(training_dir, origin))
    assert walkforward.main(args) == 0
    saved = json.loads((tmp_path / "results" / "m0_2016.json").read_text(encoding="utf-8"))
    assert saved["origins"][0]["n_train_rows"] == len(_training(origin))
    # The scores are saved too, in the predictions folder (the test's own, not data/results).
    assert walkforward.predictions_path(tmp_path / "predictions", "m0", "2016", origin).is_file()

    write(_rows(), training_path(training_dir, origin))  # the final rows, not built as of T
    assert walkforward.main(args) == 2
    assert "ADR 0016" in capsys.readouterr().err


def test_load_landmark_rows(tmp_path: Path) -> None:
    path = tmp_path / "landmarks.parquet"
    with duckdb.connect() as con:
        con.execute(
            f"""COPY (SELECT 'NCT1' AS trial_id, 0 AS landmark_index,
                DATE '2016-02-01' AS landmark_date, 1 AS event, DATE '2016-09-01' AS event_date,
                'phase2' AS stratum) TO '{path.as_posix()}' (FORMAT parquet)"""
        )
    rows = load_landmark_rows(path)
    assert len(rows) == 1
    assert rows.features["stratum"].tolist() == ["phase2"]
    assert set(rows.features) == {"stratum"}  # the keys and the label are not features
    with pytest.raises(FileNotFoundError, match="Step 4"):
        load_landmark_rows(tmp_path / "missing.parquet")


# Models that need features, and models of some landmark indices only -----------------------------


class _FeatureModel:
    """A stand-in for M1: scored at landmark 0 only, and it predicts from a feature that
    exists only in the feature matrix."""

    name = "probe"
    landmark_indices: tuple[int, ...] | None = (0,)
    feature_matrix = True

    def __init__(self, cfg: ProjectConfig) -> None:
        self.summary = {"fitted_on": 0}

    def fit_rows(
        self, rows: LandmarkRows, time: Any, event: Any, origin: dt.date
    ) -> "_FeatureModel":
        assert set(rows.landmark_index.tolist()) == {0}
        assert "risk" in rows.features
        assert len(time) == len(event) == len(rows)
        self.summary = {"fitted_on": len(rows), "origin": origin.isoformat()}
        return self

    def predict_months(self, rows: LandmarkRows, months: int) -> Any:
        return np.clip(rows.features["risk"] * months / 24.0, 0.01, 0.99)


def _feature_loader(rows: LandmarkRows, calls: list[Any]) -> walkforward.FeatureLoader:
    """Features for whichever rows the harness asks for: a risk that is higher for the
    stratum that stops early more often."""

    def load(origin: dt.date, role: str, only: tuple[int, ...] | None) -> walkforward.FeatureRows:
        calls.append((origin, role, only))
        t = np.datetime64(origin, "D")
        if role == "training":
            chosen = _training(origin)
        else:
            chosen, _, _ = evaluation_rows(rows, t, 12)
        if only is not None:
            chosen = chosen.subset(np.isin(chosen.landmark_index, only))
        risk = np.where(chosen.features["stratum"] == "phase2", 0.4, 0.1)
        return walkforward.FeatureRows(chosen.trial_id, chosen.landmark_index, {"risk": risk})

    return load


def test_a_feature_model_gets_its_rows_with_features_at_its_landmark_indices(
    cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(walkforward.MODELS, "probe", _FeatureModel)
    rows, calls = _rows(), []
    results = run(
        cfg, "probe", "2016", lambda: rows, _training, n_resamples=20,
        load_features=_feature_loader(rows, calls),
    )  # fmt: skip
    origin = results["origins"][0]
    day = dt.date(2016, 1, 1)
    assert calls == [(day, "training", (0,)), (day, "evaluation", (0,))]
    ev, _, _ = evaluation_rows(rows, np.datetime64(day, "D"), 12)
    first = ev.landmark_index == 0
    assert origin["n_eval_rows"] == int(first.sum()) < len(ev)
    assert origin["n_train_rows"] == int((_training(day).landmark_index == 0).sum())
    assert origin["landmark_indices"] == [0]
    assert origin["model_summary"] == {"fitted_on": origin["n_train_rows"], "origin": "2016-01-01"}
    for months in cfg.horizons_months:
        written = origin["horizons"][str(months)]
        assert set(written["by_landmark_index"]) == {"0"}
        assert written["pooled"]["n_rows"] == origin["n_eval_rows"]
        assert written["pooled"]["auc"]["estimate"] > 0.55  # the feature carries the ranking
    m0 = run(cfg, "m0", "2016", lambda: rows, _training, n_resamples=20)["origins"][0]
    assert (
        origin["censoring_groups"]
        == m0["horizons"]["12"]["by_landmark_index"]["0"]["censoring_groups"]
    )
    assert m0["landmark_indices"] == "all"
    assert m0["model_summary"] is None


def test_the_censoring_groups_are_decided_before_the_landmark_filter(
    cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0017: a class gets a censoring curve of its own if it has enough rows among all
    the evaluation rows of the origin. A model of landmark 0 only must keep those groups,
    although at landmark 0 alone the same classes are too small: only then do its rows carry
    the weights M0's rows carry there. (On the real rows this is FED, NIH and NETWORK.)"""
    monkeypatch.setitem(walkforward.MODELS, "probe", _FeatureModel)
    rows = _rows()
    ev, _, _ = evaluation_rows(rows, np.datetime64("2016-01-01"), 12)
    overall = {
        str(name): int(n)
        for name, n in zip(*np.unique(ev.features["stratum"], return_counts=True), strict=True)
    }
    first = ev.landmark_index == 0
    at_zero = {
        str(name): int(n)
        for name, n in zip(*np.unique(ev.features["stratum"][first], return_counts=True),
                           strict=True)
    }  # fmt: skip
    # A threshold between the two counts of every class: large enough overall, too small at
    # landmark 0.
    threshold = max(at_zero.values()) + 1
    assert threshold <= min(overall.values())
    tight = cfg.model_copy(
        update={"evaluation": cfg.evaluation.model_copy(update={"censoring_min_rows": threshold})}
    )
    origin = run(
        tight, "probe", "2016", lambda: rows, _training, n_resamples=20,
        load_features=_feature_loader(rows, []),
    )["origins"][0]  # fmt: skip
    assert origin["censoring_groups"] == at_zero  # each class under its own name, none pooled
    m0 = run(tight, "m0", "2016", lambda: rows, _training, n_resamples=20)["origins"][0]
    for months in tight.horizons_months:
        ours = origin["horizons"][str(months)]["pooled"]
        theirs = m0["horizons"][str(months)]["by_landmark_index"]["0"]
        assert ours["censoring_groups"] == theirs["censoring_groups"] == at_zero
        # The same rows with the same weights: the observed rate of each group is the same
        # number under both models.
        assert [(g["group"], g["n"], g["observed"]) for g in ours["calibration_by_group"]] == [
            (g["group"], g["n"], g["observed"]) for g in theirs["calibration_by_group"]
        ]


def test_a_feature_model_is_refused_without_features_or_with_those_of_other_rows(
    cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(walkforward.MODELS, "probe", _FeatureModel)
    rows = _rows()
    with pytest.raises(ValueError, match="needs the feature matrix"):
        run(cfg, "probe", "2016", lambda: rows, _training, n_resamples=5)

    def one_row_short(origin: dt.date, role: str, only: Any) -> walkforward.FeatureRows:
        full = _feature_loader(rows, [])(origin, role, only)
        return walkforward.FeatureRows(
            full.trial_id[1:], full.landmark_index[1:], {"risk": full.features["risk"][1:]}
        )

    with pytest.raises(ValueError, match="does not hold the training rows"):
        run(cfg, "probe", "2016", lambda: rows, _training, n_resamples=5,
            load_features=one_row_short)  # fmt: skip

    def other_order(origin: dt.date, role: str, only: Any) -> walkforward.FeatureRows:
        full = _feature_loader(rows, [])(origin, role, only)
        back = slice(None, None, -1)
        return walkforward.FeatureRows(
            full.trial_id[back], full.landmark_index[back], {"risk": full.features["risk"][back]}
        )

    with pytest.raises(ValueError, match="or other keys"):
        run(cfg, "probe", "2016", lambda: rows, _training, n_resamples=5,
            load_features=other_order)  # fmt: skip

    def other_landmark(origin: dt.date, role: str, only: Any) -> walkforward.FeatureRows:
        """The same trials in the same order, but their features at the next landmark."""
        full = _feature_loader(rows, [])(origin, role, only)
        return walkforward.FeatureRows(full.trial_id, full.landmark_index + 1, full.features)

    with pytest.raises(ValueError, match="or other keys"):
        run(cfg, "probe", "2016", lambda: rows, _training, n_resamples=5,
            load_features=other_landmark)  # fmt: skip


def test_the_command_logs_each_run_unless_told_not_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tracked_runs: list[Any]
) -> None:
    def write(rows: LandmarkRows, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame(  # noqa: F841 (read by DuckDB below)
            {"trial_id": rows.trial_id, "landmark_index": rows.landmark_index,
             "landmark_date": rows.landmark_date, "event": rows.event,
             "event_date": rows.event_date, "stratum": rows.features["stratum"]}
        )  # fmt: skip
        with duckdb.connect() as con:
            con.execute(
                f"""COPY (SELECT trial_id, landmark_index, CAST(landmark_date AS DATE)
                  AS landmark_date, event, CAST(event_date AS DATE) AS event_date, stratum
                FROM frame) TO '{path.as_posix()}' (FORMAT parquet)"""
            )

    final, training_dir = tmp_path / "landmarks.parquet", tmp_path / "training"
    origin = dt.date(2016, 1, 1)
    write(_rows(), final)
    write(_training(origin), training_path(training_dir, origin))
    monkeypatch.setattr(walkforward, "RESULTS_DIR", tmp_path / "results")
    args = ["--model", "m0", "--origins", "2016", "--resamples", "20", "--input", str(final),
            "--training-dir", str(training_dir),
            "--predictions-dir", str(tmp_path / "predictions")]  # fmt: skip
    assert walkforward.main([*args, "--no-track"]) == 0
    out = tmp_path / "results" / "m0_2016.json"
    assert "tracking" not in json.loads(out.read_text(encoding="utf-8"))
    assert tracked_runs == []
    assert walkforward.main(args) == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["tracking"]["run_name"] == "m0-2016"
    (logged,) = tracked_runs
    assert logged["name"] == "m0-2016"
    assert logged["local"] is False
    assert logged["state"] == tracking.git_state()  # read by the command, handed to the store
    assert logged["artifacts"] == [str(out)]
    assert logged["params"]["model"] == "m0"
    assert logged["params"]["origin_dates"] == "2016-01-01"
    assert logged["params"]["bootstrap_resamples"] == 20
    assert logged["tags"] == {"kind": "walk-forward", "roles": "dev", "test_lock": "locked"}
    pooled = saved["origins"][0]["horizons"]["12"]["pooled"]
    assert logged["metrics"]["auc_12m_2016"] == pooled["auc"]["estimate"]
    assert logged["metrics"]["auc_12m_mean"] == pooled["auc"]["estimate"]
    assert logged["metrics"]["calibration_slope_12m_2016"] == pooled["calibration_slope"]


def test_a_tracking_failure_keeps_the_results_and_fails_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Twelve minutes of bootstrap must not be lost to a network error at the last step."""
    results = {"model": "m0", "origins_spec": "2016", "origins": []}

    def broken(*args: object, **kwargs: object) -> dict[str, str]:
        raise tracking.TrackingError("the server did not answer")

    monkeypatch.setattr(walkforward, "run", lambda *a, **k: dict(results))
    monkeypatch.setattr(walkforward, "track", broken)
    monkeypatch.setattr(walkforward, "RESULTS_DIR", tmp_path)
    assert walkforward.main(["--model", "m0", "--origins", "2016"]) == 5
    message = capsys.readouterr().err
    assert "not logged to MLflow" in message
    assert "the server did not answer" in message
    assert json.loads((tmp_path / "m0_2016.json").read_text(encoding="utf-8")) == results


def test_the_run_is_credited_to_the_commit_it_started_from_and_can_go_to_the_local_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tracked_runs: list[Any]
) -> None:
    """The git state is read before the work, not when the run is logged: a commit made
    during a 40-minute run must not be named as the code that produced it."""
    order: list[str] = []
    started = {"git_commit": "started-here", "git_dirty": "no"}

    def state() -> dict[str, str]:
        order.append("state")
        return dict(started)

    def run(*args: object, **kwargs: object) -> dict[str, object]:
        order.append("run")
        return {"model": "m0", "origins_spec": "2016", "origins": [], "bootstrap_resamples": 20,
                "training_labels": "as of each origin (ADR 0016)"}  # fmt: skip

    monkeypatch.setattr(tracking, "git_state", state)
    monkeypatch.setattr(walkforward, "run", run)
    monkeypatch.setattr(walkforward, "RESULTS_DIR", tmp_path)
    assert walkforward.main(["--model", "m0", "--origins", "2016", "--local-tracking"]) == 0
    assert order == ["state", "run"]
    # What the command line asked for reaches the store, through track: not only main.
    assert [(r["local"], r["state"]) for r in tracked_runs] == [(True, started)]
    assert walkforward.main(["--model", "m0", "--origins", "2016"]) == 0
    assert [(r["local"], r["state"]) for r in tracked_runs] == [(True, started), (False, started)]
    with pytest.raises(SystemExit):  # one or the other, not both
        walkforward.main(["--model", "m0", "--origins", "2016", "--no-track", "--local-tracking"])


def test_a_run_of_a_locked_origin_would_be_tagged_with_its_unlock(
    cfg: ProjectConfig, tmp_path: Path, tracked_runs: list[Any]
) -> None:
    """Section 10: the registration that unlocked a test run is recorded with the run in
    MLflow. No locked origin is evaluated here: `track` is handed a results record as the
    harness would write it at Step 11."""
    results: dict[str, Any] = {
        "model": "m4", "origins_spec": "test", "bootstrap_resamples": 1000,
        "training_labels": "as of each origin (ADR 0016)", "origins": [],
        "unlock": {"tag": "prereg-v1", "commit": "abc1234", "registered": "2026-11-01"},
    }  # fmt: skip
    walkforward.track(results, cfg, tmp_path / "m4_test.json")
    assert tracked_runs[-1]["tags"] == {
        "kind": "walk-forward", "roles": "", "test_lock": "unlocked", "unlock_tag": "prereg-v1",
        "unlock_commit": "abc1234", "unlock_registered": "2026-11-01",
    }  # fmt: skip
    results["unlock"] = None
    walkforward.track(results, cfg, tmp_path / "m4_test.json")
    assert tracked_runs[-1]["tags"] == {"kind": "walk-forward", "roles": "", "test_lock": "locked"}


def test_rows_take_feature_columns_of_their_own_length_only() -> None:
    rows = _rows(n_trials=20)
    more = rows.with_features({"risk": np.arange(len(rows), dtype=np.float64)})
    assert set(more.features) == {"stratum", "risk"}
    assert more.features["risk"][-1] == len(rows) - 1
    assert "risk" not in rows.features  # the rows handed in are not changed
    # A loaded column replaces one of the same name: the feature build is the source.
    renamed = rows.with_features({"stratum": np.full(len(rows), "from the feature build")})
    assert set(renamed.features["stratum"].tolist()) == {"from the feature build"}
    with pytest.raises(ValueError, match=f"risk: {len(rows) - 1} values for {len(rows)} rows"):
        rows.with_features({"risk": np.zeros(len(rows) - 1)})


# Step 10: saved predictions, the registration year, tuning before a run ---------------------------


def test_the_registration_year_is_the_landmark_less_its_index() -> None:
    """Landmark k lies 6k months after the first post date."""
    rows = LandmarkRows(
        np.array(["a", "b", "c", "d"]),
        np.array([0, 1, 2, 6], dtype=np.int64),
        np.array(["2016-03-10", "2016-03-10", "2016-12-31", "2019-01-01"], dtype="datetime64[D]"),
        np.zeros(4, dtype=np.int64),
        np.array(["2020-01-01"] * 4, dtype="datetime64[D]"),
        {"stratum": np.array(["x"] * 4)},
    )
    # 2016-03-10; 2015-09-10; 2015-12-31; 2016-01-01.
    assert walkforward.registration_years(rows, 6).tolist() == [2016, 2015, 2015, 2016]


def test_every_slice_reports_calibration_by_registration_year(cfg: ProjectConfig) -> None:
    rows = _rows()
    origin = run(cfg, "m0", "2016", lambda: rows, _training, n_resamples=20)["origins"][0]
    ev, _, _ = evaluation_rows(rows, np.datetime64("2016-01-01"), 12)
    years = walkforward.registration_years(ev, cfg.landmarks.spacing_months)
    for months in cfg.horizons_months:
        pooled = origin["horizons"][str(months)]["pooled"]
        table = pooled["calibration_by_registration_year"]
        assert [row["group"] for row in table] == [str(y) for y in sorted(set(years.tolist()))]
        assert {row["group"] for row in table} == {"2015", "2016"}  # landmarks 0 and 1 of 2016
        assert sum(row["n"] for row in table) == pooled["n_rows"]
        for row in table:
            assert row["n"] == int((years == int(row["group"])).sum())
            assert 0.0 <= row["mean_predicted"] <= 1.0
        first = origin["horizons"][str(months)]["by_landmark_index"]["0"]
        assert {r["group"] for r in first["calibration_by_registration_year"]} == {"2016"}


def test_the_harness_saves_the_scores_of_every_evaluation_row(
    cfg: ProjectConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(walkforward.MODELS, "probe", _FeatureModel)
    rows = _rows()
    run(cfg, "m0", "2016", lambda: rows, _training, n_resamples=10, predictions_dir=tmp_path)
    run(cfg, "probe", "2016", lambda: rows, _training, n_resamples=10,
        load_features=_feature_loader(rows, []), predictions_dir=tmp_path)  # fmt: skip
    day = dt.date(2016, 1, 1)
    path = walkforward.predictions_path(tmp_path, "m0", "2016", day)
    assert path == tmp_path / "m0_2016" / "origin_2016-01-01.parquet"
    assert walkforward.predictions_path(tmp_path, "m4", "2016,2017", day).parent.name == (
        "m4_2016-2017"
    )
    with duckdb.connect() as con:
        saved = con.execute(f"SELECT * FROM '{path.as_posix()}'").df()
        other = walkforward.predictions_path(tmp_path, "probe", "2016", day)
        probe = con.execute(f"SELECT * FROM '{other.as_posix()}'").df()
    ev, ev_time, ev_event = evaluation_rows(rows, np.datetime64(day, "D"), 12)
    order = np.lexsort((ev.landmark_index, ev.trial_id))
    assert list(saved.columns) == [
        "trial_id", "landmark_index", "landmark_date", "time", "event", "censoring_group",
        "score_12", "score_24",
    ]  # fmt: skip
    assert saved["trial_id"].tolist() == ev.trial_id[order].tolist()
    assert saved["landmark_index"].tolist() == ev.landmark_index[order].tolist()
    assert saved["time"].tolist() == ev_time[order].tolist()
    assert saved["event"].tolist() == ev_event[order].tolist()
    assert set(saved["censoring_group"]) == {"POOLED"}  # two classes, both under 200 rows
    # The scores are the model's predictions for those rows.
    train, time, event = training_rows(_training(day), np.datetime64(day, "D"))
    from trialpulse.models.aalen_johansen import AalenJohansenModel

    model = AalenJohansenModel().fit_rows(train, time, event, day)
    assert saved["score_12"].to_numpy() == pytest.approx(model.predict_months(ev, 12)[order])
    assert saved["score_24"].to_numpy() == pytest.approx(model.predict_months(ev, 24)[order])
    # A model of landmark 0 only saves its rows only.
    assert set(probe["landmark_index"]) == {0}
    assert len(probe) == int((ev.landmark_index == 0).sum())
    # Without a folder nothing is written.
    run(cfg, "m0", "2016", lambda: rows, _training, n_resamples=10)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m0_2016", "probe_2016"]


def test_the_ladder_and_the_ablation_rungs_are_models_of_the_harness() -> None:
    assert set(walkforward.MODELS) == {
        "m0", "m1", "m2", "m3", "m4", "m4-design", "m4-amendments", "m4-sponsor",
    }  # fmt: skip
    cfg = load_project_config()
    for name in ("m2", "m3", "m4", "m4-sponsor"):
        model = walkforward.MODELS[name](cfg)
        assert model.name == name
        assert model.feature_matrix
        assert model.landmark_indices is None


def test_tuning_runs_before_the_model_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trialpulse.models import tuning

    order: list[Any] = []
    codes = iter([0, tracking.FAILED_EXIT_CODE])

    def tune(argv: list[str]) -> int:
        order.append(("tune", argv))
        return next(codes)

    def fake_run(*args: object, **kwargs: object) -> dict[str, object]:
        order.append("run")
        return {"model": "m4", "origins_spec": "dev", "origins": [], "bootstrap_resamples": 5,
                "training_labels": "as of each origin (ADR 0016)"}  # fmt: skip

    monkeypatch.setattr(tuning, "main", tune)
    monkeypatch.setattr(walkforward, "run", fake_run)
    monkeypatch.setattr(walkforward, "RESULTS_DIR", tmp_path)
    args = ["--model", "m4", "--origins", "dev", "--tune", "--local-tracking",
            "--features-dir", str(tmp_path / "features")]  # fmt: skip
    assert walkforward.main(args) == 0
    (first, argv), second = order
    assert (first, second) == ("tune", "run")
    # The tuning reads the rows the model run reads, and is logged where it is logged.
    assert argv == [
        "--model", "m4", "--input", str(walkforward.LANDMARKS_PATH),
        "--training-dir", str(walkforward.TRAINING_DIR),
        "--features-dir", str(tmp_path / "features"), "--local-tracking",
    ]  # fmt: skip
    # A tuning that fails stops the command before the model runs.
    order.clear()
    assert walkforward.main(args) == tracking.FAILED_EXIT_CODE
    assert [step if isinstance(step, str) else step[0] for step in order] == ["tune"]
    # Without --tune nothing is tuned.
    order.clear()
    assert walkforward.main([*args[:4], "--no-track"]) == 0
    assert order == ["run"]
    # Other folders and "do not log" are passed on too.
    order.clear()
    codes = iter([0])
    elsewhere = ["--model", "m3", "--origins", "2016", "--tune", "--no-track",
                 "--input", str(tmp_path / "rows.parquet"),
                 "--training-dir", str(tmp_path / "training"),
                 "--features-dir", str(tmp_path / "features")]  # fmt: skip
    assert walkforward.main(elsewhere) == 0
    assert order[0] == (
        "tune",
        ["--model", "m3", "--input", str(tmp_path / "rows.parquet"),
         "--training-dir", str(tmp_path / "training"),
         "--features-dir", str(tmp_path / "features"), "--no-track"],
    )  # fmt: skip


def test_tune_refuses_before_an_hour_of_tuning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A model without settings of its own, a locked origin and an unknown origin are
    refused before the tuning starts, and a refusal of the tuning itself is one line with
    exit code 2, as every other refusal of this command is."""
    from trialpulse.cli import RefusedError
    from trialpulse.models import tuning

    calls: list[Any] = []

    def tune(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    def fake_run(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append("run")
        return {"model": "m4", "origins_spec": "dev", "origins": []}

    monkeypatch.setattr(tuning, "main", tune)
    monkeypatch.setattr(walkforward, "run", fake_run)
    monkeypatch.setattr(walkforward, "RESULTS_DIR", tmp_path)
    for name in ("m4-design", "m0", "m1"):
        assert walkforward.main(["--model", name, "--origins", "dev", "--tune", "--no-track"]) == 2
        message = capsys.readouterr().err
        assert message.startswith("refused: --tune is for m2, m3, m4")
        assert f"{name} has no settings of its own" in message
    # The lock is checked first, as it is before any data is read.
    assert walkforward.main(["--model", "m4", "--origins", "test", "--tune", "--no-track"]) == 2
    assert "refused: locked origins need the --unlock-test flag" in capsys.readouterr().err
    assert walkforward.main(["--model", "m4", "--origins", "2016,2019", "--tune"]) == 2
    assert "--unlock-test" in capsys.readouterr().err
    assert walkforward.main(["--model", "m4", "--origins", "2031", "--tune", "--no-track"]) == 2
    assert "unknown origin year" in capsys.readouterr().err
    assert calls == []

    def refusing(argv: list[str]) -> int:
        raise RefusedError("the features of origin 2016-01-01\n are not built")

    monkeypatch.setattr(tuning, "main", refusing)
    assert walkforward.main(["--model", "m4", "--origins", "dev", "--tune", "--no-track"]) == 2
    assert capsys.readouterr().err == ("refused: the features of origin 2016-01-01 are not built\n")
    assert calls == []  # the model does not run after a refused tuning


def test_a_tuned_run_is_credited_to_the_tree_it_read_its_settings_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tracked_runs: list[Any]
) -> None:
    """The tuning rewrites config/tuned/<model>.json and the model then reads it. So the git
    state of the model run is read again after the tuning: settings that differ from the
    committed ones show as uncommitted changes, not as a clean commit that does not hold
    them."""
    from trialpulse.models import tuning

    order: list[str] = []
    states = iter(
        [{"git_commit": "c1", "git_dirty": "no"}, {"git_commit": "c1", "git_dirty": "yes"}]
    )

    def state() -> dict[str, str]:
        order.append("state")
        return next(states)

    def tune(argv: list[str]) -> int:
        order.append("tune")
        return 0

    def fake_run(*args: object, **kwargs: object) -> dict[str, object]:
        order.append("run")
        return {"model": "m4", "origins_spec": "dev", "origins": [], "bootstrap_resamples": 5,
                "training_labels": "as of each origin (ADR 0016)"}  # fmt: skip

    monkeypatch.setattr(tracking, "git_state", state)
    monkeypatch.setattr(tuning, "main", tune)
    monkeypatch.setattr(walkforward, "run", fake_run)
    monkeypatch.setattr(walkforward, "RESULTS_DIR", tmp_path)
    assert walkforward.main(["--model", "m4", "--origins", "dev", "--tune"]) == 0
    assert order == ["state", "tune", "state", "run"]
    (logged,) = tracked_runs
    assert logged["state"] == {"git_commit": "c1", "git_dirty": "yes"}
