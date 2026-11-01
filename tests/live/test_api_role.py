"""The API's role reads the serving schema only (ADR 0049)."""

import uuid

import pytest

from trialpulse.live.api_role import grant_read_only

psycopg = pytest.importorskip("psycopg")
from psycopg import conninfo  # noqa: E402


def test_the_role_reads_serving_and_nothing_else(scratch: str) -> None:
    dsn = scratch.replace("postgresql+psycopg://", "postgresql://", 1)
    role, password = f"api_test_{uuid.uuid4().hex[:8]}", uuid.uuid4().hex
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        grant_read_only(cur, password, role)
        grant_read_only(cur, password, role)  # a second run only updates the password
        con.commit()
    as_role = conninfo.make_conninfo(dsn, user=role, password=password)
    try:
        with psycopg.connect(as_role, autocommit=True) as con:
            assert con.execute("SELECT count(*) FROM serving.trial_scores_latest").fetchone()
            denied_statements = (
                "SELECT 1 FROM live.trial_state",
                "SELECT 1 FROM monitoring.pipeline_runs",
                "INSERT INTO serving.data_status (status_id) VALUES (1)",
                "CREATE TABLE public.x (a int)",
            )
            for denied in denied_statements:
                with pytest.raises(psycopg.Error):
                    con.execute(denied)
    finally:
        with psycopg.connect(dsn, autocommit=True) as con:
            con.execute(f"DROP OWNED BY {role}")
            con.execute(f"DROP ROLE {role}")
