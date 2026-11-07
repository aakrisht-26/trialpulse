"""The keep-alive of ADR 0072: a held Neon connection that stays quiet while a command computes.

Neon's Free plan suspends a compute after 5 minutes without a query, and the suspend ends
every open connection, inside a transaction or not (ADR 0058 covered only the first). A
command that holds a connection while it pulls, computes or waits wraps that span in
`keep_alive`: a background thread sends `SELECT 1` on the same connection every
`live.keepalive_seconds` (60) until the span ends.

- It is entered only with no transaction open, and it never pings inside one; the main
  thread does not use the connection inside the span.
- A failed ping stops the thread, and the error is raised when the span ends, so the
  command fails through its usual path (ADR 0058: recorded on a fresh connection). The
  thread always stops before the span returns, on success or error; an error of the span's
  own body takes precedence.
- A ping does not renew the lease (ADR 0060): the lease is renewed per stage, as before.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from psycopg import pq

from trialpulse.cli import RefusedError

THREAD_NAME = "trialpulse-keepalive"


class KeepAliveError(RuntimeError):
    """A ping of the keep-alive failed: the connection was lost during the span."""


def _idle(con: Any) -> bool:
    return bool(con.info.transaction_status == pq.TransactionStatus.IDLE)


@contextmanager
def keep_alive(con: Any, seconds: float) -> Iterator[None]:
    """Ping `con` every `seconds` while the span runs (see the module text)."""
    if seconds <= 0:
        raise ValueError("the keep-alive interval must be positive")
    if not _idle(con):
        raise RefusedError("the keep-alive is entered only with no transaction open (ADR 0072)")
    stop = threading.Event()
    failed: list[BaseException] = []

    def ping() -> None:
        while not stop.wait(seconds):
            try:
                if not _idle(con):  # never inside a transaction
                    raise RefusedError("a transaction opened during the keep-alive's span")
                con.execute("SELECT 1")
            except BaseException as exc:
                failed.append(exc)
                return

    thread = threading.Thread(target=ping, name=THREAD_NAME, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
    if failed:
        raise KeepAliveError(
            f"the keep-alive's ping failed: {type(failed[0]).__name__}"
        ) from failed[0]
