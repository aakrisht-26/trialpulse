"""The alerts of ADR 0081 (`monitor.alerts`) on hand-made snapshots."""

import datetime as dt
from pathlib import Path

import pytest

from trialpulse.config import REPO_ROOT, load_project_config
from trialpulse.monitor import alerts

CFG = load_project_config().alerts
TODAY = dt.date(2026, 11, 16)
QUIET = {"data_date": dt.date(2026, 11, 13), "records_fetched": 6000, "records_quarantined": 60,
         "drift_week": dt.date(2026, 11, 13), "drift_share": 0.20,
         "drifted_features": ["n_versions"], "matured_date": None, "matured_auc": None}  # fmt: skip
TOP = ("enrollment_count", "sponsor_stop_rate", "planned_duration_months", "landmark_index",
       "sponsor_class")  # fmt: skip


def test_the_thresholds_are_the_decided_ones() -> None:
    assert (CFG.watermark_lag_days, CFG.quarantine_share, CFG.drift_feature_share) == (3, 0.01, 0.2)
    assert alerts.registered_auc_floor() == 0.7122  # the lower of the 2018 and 2019 bounds
    assert CFG.drift_top_features == TOP


def _importance_by_12_month_mean() -> list[str]:
    """The features of docs/explanations.md's importance table, largest '12 months, mean'
    first (a tie keeps the table's order)."""
    lines = (REPO_ROOT / "docs" / "explanations.md").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("| Feature | Family |"))
    header = [c.strip() for c in lines[start].strip("|").split("|")]
    column = header.index("12 months, mean")
    rows = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append((cells[0], float(cells[column])))
    assert len(rows) >= 10  # the table (its 15 largest features), not a fragment
    return [name for name, _ in sorted(rows, key=lambda r: -r[1])]


def test_the_top_features_are_the_top_5_of_the_importance_table() -> None:
    ranked = _importance_by_12_month_mean()
    assert list(CFG.drift_top_features) == ranked[:5]


def test_every_top_feature_is_watched_by_a_drift_row() -> None:
    """A top feature has a column in the drift reference, or is a derived feature whose inputs
    all have one (the step compares exactly the reference's columns)."""
    import json

    from trialpulse.monitor import drift

    columns = set(json.loads(drift.REFERENCE_PATH.read_text(encoding="utf-8"))["columns"])
    assert set(alerts.DERIVED_INPUTS) == set(drift.DERIVED)
    for name in CFG.drift_top_features:
        assert name in columns or set(alerts.DERIVED_INPUTS[name]) <= columns, name


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


def test_a_drifted_top_feature_raises_the_drift_alert_below_the_share() -> None:
    """Rule 2 (ADR 0081, amended 2026-11-17): one top feature is enough, at any share."""
    one = {**QUIET, "drift_share": 1 / 52, "drifted_features": ["enrollment_count"]}
    found = alerts.check(one, CFG, TODAY, 0.7122)
    assert set(found) == {"drift"}
    assert found["drift"].endswith("top features drifted: enrollment_count")
    for name in ("landmark_index", "sponsor_class", "planned_duration_months"):
        state = {**QUIET, "drift_share": 1 / 52, "drifted_features": [name]}
        assert set(alerts.check(state, CFG, TODAY, 0.7122)) == {"drift"}


def test_the_sponsor_rate_counts_as_drifted_through_its_inputs() -> None:
    for name in ("sponsor_prior_ended", "sponsor_prior_stopped"):
        state = {**QUIET, "drift_share": 1 / 52, "drifted_features": [name]}
        assert "top features drifted: sponsor_stop_rate" in alerts.check(
            state, CFG, TODAY, 0.7122)["drift"]  # fmt: skip
    both = {**QUIET, "drift_share": 2 / 52, "drifted_features": ["sponsor_class", "x"]}
    assert alerts.check(both, CFG, TODAY, 0.7122)["drift"].endswith(
        "top features drifted: sponsor_stop_rate, sponsor_class")  # fmt: skip


def test_other_features_alone_raise_nothing_at_or_below_the_share() -> None:
    others = ["n_versions", "registration_year", "status", "x"]
    for share in (0.0, 0.2):
        state = {**QUIET, "drift_share": share, "drifted_features": others}
        assert alerts.check(state, CFG, TODAY, 0.7122) == {}
    assert alerts.top_features_drifted(TOP, []) == []
    assert "drift" not in alerts.check(
        {**QUIET, "drift_week": None, "drift_share": None, "drifted_features": []},
        CFG, TODAY, 0.7122)  # fmt: skip


def test_the_share_rule_still_names_no_top_feature() -> None:
    found = alerts.check({**QUIET, "drift_share": 0.21}, CFG, TODAY, 0.7122)
    assert found["drift"] == "21% of the features drifted in the week of 2026-11-13"


def test_states_list_every_kind_with_keys_that_ignore_daily_noise() -> None:
    """A key holds only the details that matter (ADR 0081, comments on change only)."""
    quiet = alerts.states(QUIET, CFG, TODAY, 0.7122)
    assert set(quiet) == set(alerts.KINDS)
    assert all(s == {"raised": False, "key": "", "detail": ""} for s in quiet.values())
    late = {**QUIET, "data_date": dt.date(2026, 11, 12)}
    today, tomorrow = (alerts.states(late, CFG, day, 0.7122)["watermark_lag"]
                       for day in (TODAY, TODAY + dt.timedelta(days=1)))  # fmt: skip
    assert today["raised"]
    assert tomorrow["raised"]
    assert today["key"] == tomorrow["key"] == "data as of 2026-11-12"  # today is not in it
    assert today["detail"] != tomorrow["detail"]
    held = [alerts.states({**QUIET, "records_quarantined": n}, CFG, TODAY, 0.7122)["quarantine"]
            for n in (61, 300)]  # fmt: skip
    assert [h["key"] for h in held] == ["", ""]  # counts of one run are not details


def test_a_drift_key_changes_with_its_rules_and_top_features_only() -> None:
    def key(**change: object) -> str:
        return str(alerts.states({**QUIET, **change}, CFG, TODAY, 0.7122)["drift"]["key"])

    top = key(drift_share=1 / 52, drifted_features=["enrollment_count"])
    assert top == "top features enrollment_count"
    assert key(drift_share=2 / 52, drifted_features=["enrollment_count", "x"],
               drift_week=dt.date(2026, 11, 20)) == top  # fmt: skip
    assert key(drift_share=1 / 52, drifted_features=["sponsor_class"]) != top
    assert key(drift_share=0.3) == "share over the threshold"
    assert key(drift_share=0.3, drifted_features=["landmark_index"]) == (
        "share over the threshold; top features landmark_index"
    )
    assert key(drift_share=0.1) == ""


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
    assert state["drifted_features"] == ["a"]  # not the risk, not the earlier week
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
    kinds_line, states_line = output.read_text(encoding="utf-8").strip().splitlines()
    assert kinds_line.startswith("alerts=")
    assert set(kinds_line.removeprefix("alerts=").split(",")) == {
        "watermark_lag", "quarantine", "drift", "matured_auc"}  # fmt: skip
    import json

    written = json.loads(states_line.removeprefix("alert_states="))
    assert set(written) == set(alerts.KINDS)
    assert all(s["raised"] for s in written.values())
    assert written["matured_auc"]["key"] == "predictions of 2027-11-01"
