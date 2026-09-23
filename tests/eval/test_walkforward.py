"""The walk-forward runner on synthetic landmark rows. Locked origins are only ever
requested to prove they are refused; no locked origin is evaluated."""

from pathlib import Path

import duckdb
import numpy as np
import pytest

from trialpulse.config import ProjectConfig, load_project_config
from trialpulse.eval import walkforward
from trialpulse.eval.lock import TestLockError
from trialpulse.eval.walkforward import (
    LandmarkRows,
    evaluation_rows,
    horizon_days,
    load_landmark_rows,
    run,
    select_origins,
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


def test_training_rows_are_censored_at_the_origin() -> None:
    rows = _rows()
    origin = np.datetime64("2016-01-01")
    train, time, event = training_rows(rows, origin)

    assert np.all(train.landmark_date < origin)
    assert np.all(train.landmark_date + time.astype("timedelta64[D]") <= origin)
    late = train.event_date >= origin
    assert late.any()
    assert np.all(event[late] == 0)


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
    results = run(cfg, "m0", "dev", lambda: _rows(), n_resamples=20)

    assert results["unlock"] is None
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

    with pytest.raises(TestLockError, match="--unlock-test"):
        run(cfg, "m0", spec, must_not_load, unlock_flag=False, repo=tmp_path)
    # Even with the flag, a repository without a completed, tagged registration refuses.
    with pytest.raises(TestLockError):
        run(cfg, "m0", spec, must_not_load, unlock_flag=True, repo=tmp_path)


def test_cli_refuses_locked_origins(capsys: pytest.CaptureFixture[str]) -> None:
    assert walkforward.main(["--model", "m0", "--origins", "test"]) == 2
    assert "refused" in capsys.readouterr().err


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
