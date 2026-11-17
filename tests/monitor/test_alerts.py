"""The alerts of ADR 0081 (`monitor.alerts`) on hand-made snapshots."""

import datetime as dt
from pathlib import Path

import pytest

from trialpulse.config import load_project_config
from trialpulse.monitor import alerts

CFG = load_project_config().alerts
TODAY = dt.date(2026, 11, 16)
QUIET = {"data_date": dt.date(2026, 11, 13), "records_fetched": 6000, "records_quarantined": 60,
         "drift_week": dt.date(2026, 11, 13), "drift_share": 0.20,
         "matured_date": None, "matured_auc": None}  # fmt: skip


def test_the_thresholds_are_the_decided_ones() -> None:
    assert (CFG.watermark_lag_days, CFG.quarantine_share, CFG.drift_feature_share) == (3, 0.01, 0.2)
    assert alerts.registered_auc_floor() == 0.7122  # the lower of the 2018 and 2019 bounds


def test_a_snapshot_at_every_threshold_raises_nothing() -> None:
    assert alerts.check(QUIET, CFG, TODAY, 0.7122) == {}


def test_each_alert_kind_fires_just_past_its_threshold() -> None:
    late = {**QUIET, "data_date": dt.date(2026, 11, 12)}
    assert set(alerts.check(late, CFG, TODAY, 0.7122)) == {"watermark_lag"}
    assert "watermark_lag" in alerts.check({**QUIET, "data_date": None}, CFG, TODAY, 0.7122)
    held = {**QUIET, "records_quarantined": 61}
    assert set(alerts.check(held, CFG, TODAY, 0.7122)) == {"quarantine"}
    drifted = {**QUIET, "drift_share": 0.21}
    assert set(alerts.check(drifted, CFG, TODAY, 0.7122)) == {"drift"}
    low = {**QUIET, "matured_date": dt.date(2027, 1, 1), "matured_auc": 0.7121}
    found = alerts.check(low, CFG, TODAY, 0.7122)
    assert set(found) == {"matured_auc"}
    assert "0.7121" in found["matured_auc"]


def test_every_alert_kind_is_listed() -> None:
    assert set(alerts.KINDS) == {"watermark_lag", "quarantine", "drift", "matured_auc"}


def _seed(dsn: str) -> None:
    import psycopg

    plain = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(plain, autocommit=True) as con:
        for job, status, day, fetched, quarantined in (
            ("daily", "succeeded", "2026-01-13", 6000, 0),  # long ago: the lag alert
            ("daily", "succeeded", "2026-01-14", 5000, 10),
            ("daily", "failed", None, 400, 30),  # the latest run: its counts are read
        ):  # fmt: skip
            con.execute(
                """INSERT INTO monitoring.pipeline_runs (job, status, run_kind, data_timestamp,
                   records_fetched, records_quarantined) VALUES (%s, %s, 'daily', %s, %s, %s)""",
                (job, status, day, fetched, quarantined),
            )
        for week, column, drifted in (("2026-11-06", "a", True), ("2026-11-13", "a", True),
                                      ("2026-11-13", "b", False), ("2026-11-13", "c", False),
                                      ("2026-11-13", "risk_12m", True)):  # fmt: skip
            con.execute(
                """INSERT INTO monitoring.drift (week_date, column_name, kind, method, statistic,
                   threshold, drifted, n_current) VALUES (%s, %s, %s, 'psi', 0.2, 0.1, %s, 9)""",
                (week, column, "score" if column.startswith("risk") else "feature", drifted),
            )
        for source, day, auc in (("live", "2027-11-01", 0.70), ("replay", "2027-12-01", 0.95)):
            con.execute(
                """INSERT INTO monitoring.graded_predictions (prediction_date, horizon_months,
                   landmark_index, row_group, model_version, source, n_rows, n_early_stop,
                   n_completed, n_censored, n_open, mean_risk, auc, matured)
                   VALUES (%s, 12, -1, 'all', 'm/1', %s, 10, 1, 1, 1, 7, 0.1, %s, true)""",
                (day, source, auc),
            )


def test_the_state_is_read_from_the_monitoring_tables(scratch: str) -> None:
    _seed(scratch)
    state = alerts.read_state(scratch.replace("postgresql+psycopg://", "postgresql://", 1))
    assert state["data_date"] == dt.date(2026, 1, 14)  # the latest succeeded run
    assert (state["records_fetched"], state["records_quarantined"]) == (400, 30)  # latest run
    assert state["drift_week"] == dt.date(2026, 11, 13)
    assert state["drift_share"] == pytest.approx(1 / 3)  # features only, the latest week
    assert (state["matured_date"], state["matured_auc"]) == (dt.date(2027, 11, 1), 0.70)


def test_the_command_writes_the_alert_kinds_for_the_workflow(
    scratch: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live AUC 0.70 is below the registered floor, the newer replay grade is never read,
    the quarantine is 7.5% of the run, and a third of the features drifted."""
    _seed(scratch)
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert alerts.main(["--target", "local"]) == 0
    written = output.read_text(encoding="utf-8").strip()
    assert written.startswith("alerts=")
    assert set(written.removeprefix("alerts=").split(",")) == {
        "watermark_lag", "quarantine", "drift", "matured_auc"}  # fmt: skip
