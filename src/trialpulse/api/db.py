"""The API's database access (ADR 0049): read-only, pooled, one retry for a waking compute.

- The URL is `API_DATABASE_URL`, Neon's pooled URL for the read-only role; tests and the
  local image give a local URL instead.
- psycopg's automatic prepared statements are off (`prepare_threshold=None`): a statement
  prepared on one server connection may not exist on the next one a transaction-mode pooler
  hands out, and preparing saves little for these small queries.
- A query that cannot reach the database waits `retry_wait_seconds` and tries once more (a
  compute waking from scale-to-zero); a second failure is `Unavailable`, which the API
  answers with 503 and Retry-After. No error text is ever passed on: it can hold a row.
- `queries` counts the statements sent, so the tests can show what the cache saves.
"""

import time
from collections.abc import Callable, Mapping
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError


class Unavailable(RuntimeError):  # noqa: N818 (an answer, not a bug: 503)
    """The database did not answer after the retry."""


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
            except (OperationalError, DBAPIError) as exc:
                if not _unreachable(exc):
                    raise
                if attempt == 2:
                    raise Unavailable("the database did not answer") from None
                self.sleep(self.retry_wait)
        raise AssertionError("unreachable")


def _unreachable(exc: DBAPIError) -> bool:
    """A connection that failed or was lost, as opposed to a bad query."""
    if exc.connection_invalidated or isinstance(exc, OperationalError):
        return True
    state = getattr(getattr(exc, "orig", None), "sqlstate", None) or ""
    return state.startswith("08") or state == "57P01"  # connection exceptions, admin shutdown
