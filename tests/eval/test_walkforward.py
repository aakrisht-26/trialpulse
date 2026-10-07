"""The walk-forward runner on synthetic landmark rows. Locked origins are only ever
requested to prove they are refused; no locked origin is evaluated."""

import datetime as dt
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

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
    with pytest.raises(FileNotFoundError, match="Step 4"):
        load_landmark_rows(tmp_path / "missing.parquet")
