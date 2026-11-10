"""The monthly live grading (`monitor.grade`, ADR 0077) on a scratch database: a score file and
outcome events in, aggregates with source 'live' out, and the failure on purpose."""

import datetime as dt
import io

import numpy as np
import pandas as pd
import psycopg
import pytest

from trialpulse.monitor import grade

DAY = dt.date(2026, 11, 6)


def _plain(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _seed(dsn: str, n: int = 300) -> None:
    rng = np.random.default_rng(5)
    frame = pd.DataFrame({
        "nct_id": [f"NCT{i:08d}" for i in range(n)], "prediction_date": DAY,
        "risk_12m": rng.random(n).round(4), "risk_24m": rng.random(n).round(4),
        "sponsor_class": "INDUSTRY", "landmark_index": rng.integers(0, 3, n),
        "flagged": rng.random(n) < 0.2, "model_version": "trialpulse-m4/1",
    })  # fmt: skip
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    with psycopg.connect(dsn, autocommit=True) as con:
        run = con.execute(
            """INSERT INTO monitoring.pipeline_runs (job, status, run_kind, data_timestamp)
               VALUES ('daily', 'succeeded', 'full', '2027-12-01') RETURNING run_id"""
        ).fetchone()
        assert run is not None
        con.execute(
            """INSERT INTO monitoring.score_files (month, as_of_date, model_version, rows, sha256,
               content, run_id) VALUES ('2026-11-01', %s, 'trialpulse-m4/1', %s, 'x', %s, %s)""",
            (DAY, n, buffer.getvalue(), run[0]),
        )
        for i in range(0, n, 10):  # every tenth trial stops, the next completes
            con.execute(
                """INSERT INTO live.outcome_events (nct_id, to_status, effective_date, kind, run_id)
                   VALUES (%s, 'TERMINATED', '2027-03-01', 'status', %s),
                          (%s, 'COMPLETED', '2027-05-01', 'status', %s)""",
                (f"NCT{i:08d}", run[0], f"NCT{i + 1:08d}", run[0]),
            )


def test_the_live_grades_are_written_as_aggregates(scratch: str) -> None:
    dsn = _plain(scratch)
    _seed(dsn)
    result = grade.grade_live(dsn)
    assert result["score_files"] == 1
    assert result["predictions"] == 300
    with psycopg.connect(dsn) as con:
        rows = con.execute(
            """SELECT horizon_months, matured, n_rows, n_early_stop, source
               FROM monitoring.graded_predictions
               WHERE landmark_index = -1 AND row_group = 'all' ORDER BY horizon_months"""
        ).fetchall()
    assert rows == [(12, True, 300, 30, "live"), (24, False, 300, 30, "live")]
    again = grade.grade_live(dsn)  # a second run replaces the rows
    assert again["written"] == result["written"]


def test_a_failure_on_purpose_raises_after_reading(scratch: str) -> None:
    dsn = _plain(scratch)
    _seed(dsn)
    with pytest.raises(grade.ForcedFailureError, match="on purpose"):
        grade.grade_live(dsn, fail_on_purpose=True)
    with psycopg.connect(dsn) as con:
        assert con.execute("SELECT count(*) FROM monitoring.graded_predictions").fetchone() == (0,)
