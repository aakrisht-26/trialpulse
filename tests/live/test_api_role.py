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


def test_the_command_makes_a_strong_password_of_its_own() -> None:
    """Review finding 12: the command accepted a typed password (16 characters checked);
    Neon refuses a role made by SQL whose password has less than 60 bits of entropy. It now
    makes 32 random bytes itself."""
    import re

    from trialpulse.live import api_role

    made = {api_role.new_password() for _ in range(20)}
    assert len(made) == 20
    for password in made:
        assert re.fullmatch(r"[A-Za-z0-9_-]+", password)  # needs no quoting in a URL
        assert len(password) * 6 >= 256  # 6 bits a character of the URL-safe alphabet


def test_the_api_url_uses_neons_pooled_host() -> None:
    from trialpulse.live.api_role import api_url

    direct = "postgresql://owner:x@ep-cool-name-123.eu-central-1.aws.neon.tech/neondb"
    assert api_url(direct, "p4ss") == (
        "postgresql://trialpulse_api:p4ss@ep-cool-name-123-pooler.eu-central-1.aws.neon.tech"
        "/neondb?sslmode=require"
    )
    assert api_url("postgresql://u:x@127.0.0.1:15432/trialpulse", "p4ss") == (
        "postgresql://trialpulse_api:p4ss@127.0.0.1:15432/trialpulse"
    )


def test_the_command_grants_and_prints_a_url_that_reads_serving(
    scratch: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trialpulse.live import api_role

    dsn = scratch.replace("postgresql+psycopg://", "postgresql://", 1)
    monkeypatch.setattr("trialpulse.live.daily._dsn", lambda target: dsn)
    try:
        assert api_role.main(["--target", "local"]) == 0
        url = capsys.readouterr().out.strip().splitlines()[-1]
        with psycopg.connect(url, autocommit=True) as con:
            assert con.execute("SELECT count(*) FROM serving.trial_scores_latest").fetchone()
            with pytest.raises(psycopg.Error):
                con.execute("SELECT 1 FROM live.trial_state")
    finally:
        with psycopg.connect(dsn, autocommit=True) as con:
            con.execute(f"DROP OWNED BY {api_role.ROLE}")
            con.execute(f"DROP ROLE {api_role.ROLE}")
