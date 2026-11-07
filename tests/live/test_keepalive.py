"""The keep-alive of ADR 0072 on a real Postgres. `idle_session_timeout` ends a session that
sends nothing for its time outside a transaction, as Neon's suspend does after 5 minutes;
it is lowered to 2 seconds here, and a 5-second computation stands for a long stage.
Skipped without Postgres (failed in CI)."""

import threading
import time

import pytest

from trialpulse.cli import RefusedError
from trialpulse.live.keepalive import THREAD_NAME, KeepAliveError, keep_alive

psycopg = pytest.importorskip("psycopg")

IDLE_LIMIT_MS = 2000  # Neon: about 300000 (5 minutes)
COMPUTE_SECONDS = 5.0  # a stage longer than the limit
PING_SECONDS = 0.5


def _cut(scratch: str) -> str:
    from psycopg import conninfo

    dsn = scratch.replace("postgresql+psycopg://", "postgresql://", 1)
    return conninfo.make_conninfo(dsn, options=f"-c idle_session_timeout={IDLE_LIMIT_MS}")


def _threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name == THREAD_NAME]


def test_a_quiet_connection_is_cut_and_the_keep_alive_keeps_it(scratch: str) -> None:
    """The control: a connection quiet for longer than the limit is lost, which is Neon's
    cut. With the keep-alive the same span keeps it, and a write follows."""
    with psycopg.connect(_cut(scratch), autocommit=True) as con:
        assert con.execute("SHOW idle_session_timeout").fetchone() == ("2s",)
        time.sleep(COMPUTE_SECONDS)
        with pytest.raises(psycopg.OperationalError):
            con.execute("SELECT 1")
    with psycopg.connect(_cut(scratch), autocommit=True) as con:
        with keep_alive(con, PING_SECONDS):
            time.sleep(COMPUTE_SECONDS)
        with con.transaction():
            con.execute("CREATE TEMP TABLE kept (n int)")
            con.execute("INSERT INTO kept VALUES (1)")
        assert con.execute("SELECT count(*) FROM kept").fetchone() == (1,)
    assert _threads() == []


def test_the_keep_alive_is_refused_inside_a_transaction(scratch: str) -> None:
    with (
        psycopg.connect(_cut(scratch), autocommit=True) as con,
        con.transaction(),
        pytest.raises(RefusedError, match="no transaction open"),
        keep_alive(con, PING_SECONDS),
    ):
        pass
    with psycopg.connect(_cut(scratch)) as con:  # not autocommit: a read opens a transaction
        con.execute("SELECT 1")
        with pytest.raises(RefusedError, match="no transaction open"), keep_alive(con, 1):
            pass
    assert _threads() == []


def test_a_failed_ping_is_raised_when_the_span_ends_and_the_thread_is_gone(
    scratch: str,
) -> None:
    dsn = scratch.replace("postgresql+psycopg://", "postgresql://", 1)
    with (
        psycopg.connect(dsn, autocommit=True) as con,
        psycopg.connect(dsn, autocommit=True) as other,
    ):
        pid = con.execute("SELECT pg_backend_pid()").fetchone()[0]
        reached: list[bool] = []

        def span() -> None:
            with keep_alive(con, PING_SECONDS):
                other.execute("SELECT pg_terminate_backend(%s)", (pid,))
                time.sleep(4 * PING_SECONDS)
                reached.append(True)  # the span itself ran to its end

        with pytest.raises(KeepAliveError, match="ping failed") as raised:
            span()
        assert reached == [True]
        assert isinstance(raised.value.__cause__, psycopg.Error)
    assert _threads() == []


def test_an_error_of_the_span_wins_and_the_thread_still_stops(scratch: str) -> None:
    with psycopg.connect(_cut(scratch), autocommit=True) as con:
        with pytest.raises(ZeroDivisionError), keep_alive(con, PING_SECONDS):
            _ = 1 / 0
        assert con.execute("SELECT 1").fetchone() == (1,)
    assert _threads() == []
