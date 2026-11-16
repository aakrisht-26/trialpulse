"""The alerts of ADR 0081 (`monitor.alerts`) on hand-made snapshots."""

import datetime as dt

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
