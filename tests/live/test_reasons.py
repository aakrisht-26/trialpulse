"""Reason counts of ADR 0050 from synthetic warehouse tables, and their load."""

import datetime as dt

import duckdb
import pytest

from trialpulse.live import reasons, store

psycopg = pytest.importorskip("psycopg")
D = dt.date


def _tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""CREATE TABLE outcomes AS SELECT * FROM (VALUES
        ('NCT1', DATE '2020-04-03', 1, false, true, true),
        ('NCT2', DATE '2020-04-20', 1, false, true, true),
        ('NCT3', DATE '2020-05-02', 1, false, true, true),
        ('NCT4', DATE '2020-05-02', 1, true, true, true),
        ('NCT5', DATE '2020-05-09', 2, false, true, true),
        ('NCT6', DATE '2019-01-09', 1, false, false, true)
      ) t(trial_id, terminal_date, event, reversal, in_window, ever_in_population)""")
    con.execute("""CREATE TABLE versions AS SELECT * FROM (VALUES
        ('NCT1', 0, DATE '2019-01-01', 'INDUSTRY'), ('NCT1', 1, DATE '2020-04-03', 'INDUSTRY'),
        ('NCT2', 0, DATE '2020-04-20', 'OTHER'), ('NCT2', 1, DATE '2020-04-20', 'NIH'),
        ('NCT3', 0, DATE '2020-05-02', NULL), ('NCT4', 0, DATE '2020-05-02', 'OTHER'),
        ('NCT5', 0, DATE '2020-05-09', 'OTHER'), ('NCT6', 0, DATE '2019-01-09', 'OTHER')
      ) t(nct_id, nct_version, effective_date, lead_sponsor_class)""")
    con.execute("""CREATE TABLE stop_reasons AS SELECT * FROM (VALUES
        ('NCT1', 'covid19'), ('NCT2', 'covid19'), ('NCT3', 'accrual'), ('NCT4', 'accrual'),
        ('NCT5', 'business'), ('NCT6', 'funding')) t(nct_id, label)""")


def test_counts_are_by_month_reason_and_the_class_on_the_stop_day() -> None:
    """Reversals, completions and trials outside the cohort are left out; the class is the
    one of the last version posted on the stop's day."""
    with duckdb.connect() as con:
        _tables(con)
        counts = reasons.reason_counts(con)
    assert counts == [
        (D(2020, 4, 1), "covid19", "INDUSTRY", 1),
        (D(2020, 4, 1), "covid19", "NIH", 1),
        (D(2020, 5, 1), "accrual", "MISSING", 1),
    ]


def test_loading_replaces_the_counts_and_sets_the_labels_date(scratch: str) -> None:
    dsn = scratch.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        reasons.load(cur, [(D(2020, 4, 1), "covid19", "OTHER", 3)], D(2026, 9, 25))
        reasons.load(cur, [(D(2020, 5, 1), "accrual", "OTHER", 2)], D(2026, 9, 25))
        rows = cur.execute("SELECT month, reason, n FROM serving.reason_counts").fetchall()
        assert rows == [(D(2020, 5, 1), "accrual", 2)]
        run = store.start_run(cur, "daily", "daily", D(2026, 10, 30), 120)
        cur.execute(
            """INSERT INTO live.outcome_events (nct_id, from_status, to_status, effective_date,
              kind, run_id) VALUES ('NCT9', 'RECRUITING', 'TERMINATED', '2026-10-02', 'status',
              %s), ('NCT8', 'RECRUITING', 'COMPLETED', '2026-10-03', 'status', %s),
              ('NCT7', 'RECRUITING', 'WITHDRAWN', '2026-09-20', 'status', %s)""",
            (run, run, run),
        )
        store.set_data_status(cur, D(2026, 10, 30), "m/1", ("TERMINATED", "WITHDRAWN"))
        status = cur.execute(
            "SELECT data_as_of, labeled_through, early_stops_after_labels FROM serving.data_status"
        ).fetchone()
        assert status == (D(2026, 10, 30), D(2026, 9, 25), 1)
