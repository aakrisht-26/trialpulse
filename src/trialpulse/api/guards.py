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
        self.last_error: BaseException | None = None  # what the last failed read raised
        self._lock = threading.Lock()

    def peek(self) -> dt.date | None:
        """The last date read, without reading (for /health and error answers)."""
        return self._value

    def current(self) -> dt.date | None:
        with self._lock:
            now = self._now()
            if self._read_at is None or now - self._read_at >= self._refresh:
                self._read_at = now  # one read per period, even when it fails
                try:  # a failed read keeps the last date
                    self._value = self._read()
                except Exception as exc:
                    self.last_read_failed, self.last_error = True, exc
                else:
                    self.last_read_failed, self.last_error = False, None
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


def _hops(forwarded_for: str | None) -> list[str]:
    return [h.strip() for h in (forwarded_for or "").split(",") if h.strip()]


class ForwardedShape:
    """A temporary diagnostic of the proxy chain in front of the API (ADR 0065, measured):
    one line per request with the shape of its headers and nothing else. It never writes an
    address or a hash of one; the previous request's values are kept in memory only, to say
    whether each position stayed the same."""

    POSITIONS = 4  # X-Forwarded-For positions compared, counted from the end

    def __init__(self) -> None:
        self._previous: tuple[list[str], str | None, str | None] | None = None
        self._lock = threading.Lock()

    def describe(self, forwarded_for: str | None, cf_connecting_ip: str | None,
                 true_client_ip: str | None, peer: str | None) -> str:  # fmt: skip
        hops = _hops(forwarded_for)
        cf = (cf_connecting_ip or "").strip() or None
        position = next((k for k in range(1, len(hops) + 1) if hops[-k] == cf), None)
        with self._lock:
            before, self._previous = self._previous, (hops, cf, peer)

        old_hops, old_cf, old_peer = before if before else ([], None, None)

        def same(now: str | None, then: str | None) -> str:
            """yes, no, or - when either side is missing (or there was no request before)."""
            if before is None or now is None or then is None:
                return "-"
            return "yes" if now == then else "no"

        def at(values: list[str], k: int | None) -> str | None:
            return values[-k] if k and len(values) >= k else None

        stable = ",".join(f"{k}:{same(at(hops, k), at(old_hops, k))}"
                          for k in range(1, self.POSITIONS + 1))  # fmt: skip
        return (f"forwarded shape: xff_entries={len(hops)} "
                f"cf_connecting_ip={'yes' if cf else 'no'} "
                f"true_client_ip={'yes' if (true_client_ip or '').strip() else 'no'} "
                f"cf_position_from_end={position or 'none'} "
                f"cf_position_same_as_previous={same(at(hops, position), at(old_hops, position))} "
                f"cf_same_as_previous={same(cf, old_cf)} "
                f"peer_same_as_previous={same(peer, old_peer)} "
                f"xff_same_as_previous_from_end={stable}")  # fmt: skip


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
