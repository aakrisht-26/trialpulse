"""The API's database access (ADR 0049): read-only, pooled, one retry for a waking compute.

- The URL is `API_DATABASE_URL`, Neon's pooled URL for the read-only role; tests and the
  local image give a local URL instead.
- psycopg's automatic prepared statements are off (`prepare_threshold=None`): a statement
  prepared on one server connection may not exist on the next one a transaction-mode pooler
  hands out, and preparing saves little for these small queries.
- A query that cannot reach the database (a connection failure or a timeout) waits
  `retry_wait_seconds` and tries once more (a compute waking from scale-to-zero); a second
  failure is `Unavailable`, which the API answers with 503 and Retry-After. Any other
  database error (a wrong password, a missing grant, a missing table: ADR 0061) is not
  retried and is `DatabaseRefused`, which the API answers with a JSON 500 and logs by its
  SQLSTATE. No error text is ever passed on: it can hold a row.
- `queries` counts the statements sent, so the tests can show what the cache saves.
"""

import time
from collections.abc import Callable, Mapping
from typing import Any

import psycopg
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError


class Unavailable(RuntimeError):  # noqa: N818 (an answer, not a bug: 503)
    """The database did not answer after the retry."""


class DatabaseRefused(RuntimeError):  # noqa: N818 (an answer, not a bug: 500)
    """The database answered with an error that waiting will not fix: a configuration
    error such as a wrong password, a missing grant or a missing table (ADR 0061). Holds
    the SQLSTATE and the error's class only, never its message."""

    def __init__(self, sqlstate: str, kind: str) -> None:
        super().__init__(f"{kind} {sqlstate}".strip())
        self.sqlstate = sqlstate
        self.kind = kind


def engine_for(url: str, connect_timeout: int) -> Engine:
    if url.startswith(("postgresql://", "postgres://")):
        url = "postgresql+psycopg://" + url.split("://", 1)[1]
    return sa.create_engine(
        url,
        pool_pre_ping=True,
        pool_size=4,
        max_overflow=4,
        pool_recycle=300,
        connect_args={"connect_timeout": connect_timeout, "prepare_threshold": None},
    )


class Database:
    def __init__(self, engine: Engine, retry_wait: float,
                 sleep: Callable[[float], None] = time.sleep) -> None:  # fmt: skip
        self.engine = engine
        self.retry_wait = retry_wait
        self.sleep = sleep
        self.queries = 0

    def rows(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """The rows of one read-only query, as dicts."""
        for attempt in (1, 2):
            try:
                with self.engine.connect() as con:
                    self.queries += 1
                    result = con.execute(sa.text(sql), dict(params or {}))
                    return [dict(r._mapping) for r in result]
            except (DBAPIError, PoolTimeoutError) as exc:
                if not unreachable(exc):
                    raise refused(exc) from None
                if attempt == 2:
                    raise Unavailable("the database did not answer") from None
                self.sleep(self.retry_wait)
        raise AssertionError("unreachable")


# SQLSTATEs of a connection that failed, was lost or timed out (ADR 0061): class 08
# (connection exception), the server shutting down or starting (57P01 to 57P03), a statement
# timeout (57014) and too many connections (53300). Everything else is a refusal.
UNREACHABLE_STATES = frozenset({"57P01", "57P02", "57P03", "57014", "53300"})


# A refusal at connect time has no SQLSTATE in psycopg: libpq gives only the server's text,
# which starts with "FATAL:". Known texts are mapped to their SQLSTATE for the log; transient
# ones (the server busy, starting or stopping) count as unreachable. Neon answers in English.
CONNECT_REFUSALS: tuple[tuple[str, str], ...] = (  # first match wins
    ("permission denied", "42501"),  # for database "x": no CONNECT grant
    ("password authentication failed", "28P01"),
    ("no pg_hba.conf entry", "28000"),
    ("role", "28000"),  # role "x" does not exist
    ("database", "3D000"),  # database "x" does not exist
)
CONNECT_TRANSIENT: tuple[str, ...] = (
    "too many clients", "remaining connection slots", "starting up", "shutting down",
    "in recovery mode", "administrator command", "couldn't connect to compute",
)  # fmt: skip


def _connect_text(exc: BaseException) -> str:
    orig = getattr(exc, "orig", None)
    return str(orig if orig is not None else exc).lower()


def sqlstate(exc: BaseException) -> str:
    """The error's SQLSTATE, or the one a refusal at connect time stands for (see above)."""
    state = str(getattr(getattr(exc, "orig", None), "sqlstate", None) or "")
    if state:
        return state
    text = _connect_text(exc)
    if "fatal:" not in text or any(t in text for t in CONNECT_TRANSIENT):
        return ""
    refusal = text.split("fatal:", 1)[1]
    for pattern, code in CONNECT_REFUSALS:
        if pattern in refusal:
            return code
    return "FATAL"


def unreachable(exc: BaseException) -> bool:
    """A connection that failed, was lost or timed out, as opposed to an error the database
    answered with (a wrong password, a missing grant or table, a bad query)."""
    if isinstance(exc, PoolTimeoutError):  # every pooled connection busy past the timeout
        return True
    if not isinstance(exc, DBAPIError):
        return False
    state = sqlstate(exc)
    if state:
        return state.startswith("08") or state in UNREACHABLE_STATES
    # No SQLSTATE and no refusal: the server never answered (refused, reset, DNS, timeout),
    # or the connection was lost. A client-side error without one is a bug, not a wake-up.
    # isinstance, not the class name: psycopg raises subclasses, such as ConnectionTimeout
    # when the connect timeout fires before a refusal arrives (about 2 s on Windows).
    return bool(exc.connection_invalidated) or isinstance(
        exc.orig, (psycopg.OperationalError, psycopg.InterfaceError)
    )


def refused(exc: BaseException) -> DatabaseRefused:
    orig = getattr(exc, "orig", None)
    return DatabaseRefused(sqlstate(exc), type(orig if orig is not None else exc).__name__)
