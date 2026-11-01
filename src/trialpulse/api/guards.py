"""The API's data clock, its cache and its rate limit (ADR 0049).

- `DataClock` reads the data-as-of date at most once every `refresh` seconds, never per
  request: a cached answer must not wake Neon. Between reads it answers from memory; when a
  read fails it keeps the last date it had.
- `AnswerCache` keeps answers keyed by the data-as-of date and the request, so a new day's
  data never serves yesterday's answer, least recently used first out.
- `RateLimiter` allows `per_minute` requests per client address. Behind Hugging Face Spaces
  every request comes from the proxy, which appends the client's address to
  X-Forwarded-For; with `trusted_proxies` proxies in front, the client is the entry that
  many places from the end. Entries before it can be written by anyone and are ignored.
"""

import contextlib
import datetime as dt
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any


class DataClock:
    def __init__(self, read: Callable[[], dt.date | None], refresh_seconds: float,
                 now: Callable[[], float] = time.monotonic) -> None:  # fmt: skip
        self._read = read
        self._refresh = refresh_seconds
        self._now = now
        self._value: dt.date | None = None
        self._read_at: float | None = None
        self.last_read_failed = False
        self._lock = threading.Lock()

    def peek(self) -> dt.date | None:
        """The last date read, without reading (for /health and error answers)."""
        return self._value

    def current(self) -> dt.date | None:
        with self._lock:
            now = self._now()
            if self._read_at is None or now - self._read_at >= self._refresh:
                self._read_at = now  # one read per period, even when it fails
                self.last_read_failed = True
                with contextlib.suppress(Exception):  # a failed read keeps the last date
                    self._value = self._read()
                    self.last_read_failed = False
            return self._value


class AnswerCache:
    def __init__(self, entries: int) -> None:
        self._entries = entries
        self._data: OrderedDict[Hashable, Any] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0

    def get_or(self, key: Hashable, compute: Callable[[], Any]) -> Any:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self.hits += 1
                return self._data[key]
        value = compute()
        with self._lock:
            self._data[key] = value
            while len(self._data) > self._entries:
                self._data.popitem(last=False)
        return value


def client_address(forwarded_for: str | None, peer: str | None, trusted_proxies: int) -> str:
    """The client's address: the entry the nearest trusted proxy appended (see above)."""
    if trusted_proxies and forwarded_for:
        hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
        if len(hops) >= trusted_proxies:
            return hops[-trusted_proxies]
    return peer or "unknown"


class RateLimiter:
    def __init__(self, per_minute: int, now: Callable[[], float] = time.monotonic) -> None:
        self._rate = per_minute / 60.0
        self._burst = float(per_minute)
        self._now = now
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, client: str) -> float:
        """0 if the request may go on, else the seconds until it may."""
        with self._lock:
            now = self._now()
            tokens, last = self._buckets.get(client, (self._burst, now))
            tokens = min(self._burst, tokens + (now - last) * self._rate)
            if tokens >= 1:
                self._buckets[client] = (tokens - 1, now)
                return 0.0
            self._buckets[client] = (tokens, now)
            if len(self._buckets) > 100_000:  # forget idle clients rather than grow forever
                self._buckets = {k: v for k, v in self._buckets.items() if now - v[1] < 120}
            return (1 - tokens) / self._rate
