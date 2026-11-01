"""The dashboard's only way out: GET requests to the TrialPulse API (ADR 0054).

A Hugging Face Space that slept answers nothing, or a 502, 503 or 504 from the platform's
proxy, while its container starts; then, while Neon wakes, the API itself answers 503 with
`Retry-After` and its notice. `Client.get` waits both out within a fixed budget and says why
it waits through `on_wait`; it returns every other answer (200, 404, 422) as it came.

A 200 or 404 whose body is not the API's JSON (every answer of the API carries its notice)
is never returned, so no page reads it and nothing caches it: it is a page of the platform,
or of another site at the address, and is waited out like the service starting (ADR 0063).

This module imports no Streamlit, so its waits are tested on their own.
"""

import datetime as dt
import email.utils
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

ATTEMPT_TIMEOUT_SECONDS = 30.0  # one request
BUDGET_SECONDS = 180.0  # every attempt of one request, waits included
BACKOFF_SECONDS = (5, 10, 20, 30)  # without a Retry-After header; the last one repeats
RETRY_AFTER_BOUNDS = (1, 60)
RETRIED = frozenset({429, 502, 503, 504})

STARTING = "The API service is starting (it sleeps when idle); retrying in {n} seconds."
DATABASE = "The database is waking up; retrying in {n} seconds."
BUSY = "The API is busy (too many requests); retrying in {n} seconds."
NOT_API = (
    "The API's address answered with a page that is not the API (the service may be "
    "starting); retrying in {n} seconds."
)
GAVE_UP = (
    "The API did not answer within about 3 minutes. It may still be starting after a quiet "
    "period; try again in a minute."
)
GAVE_UP_NOT_API = (
    "The API's address kept answering with a page that is not the TrialPulse API. It may "
    "still be starting; if this lasts, the dashboard's API address is wrong."
)

# Seams for the tests: the waits and the clock. Pages never change them.
sleep: Callable[[float], None] = time.sleep
monotonic: Callable[[], float] = time.monotonic


@dataclass(frozen=True)
class Answer:
    """One answer of the API: its status and its JSON body (the API's envelope)."""

    status: int
    body: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == 200

    @property
    def data_as_of(self) -> dt.date | None:
        value = self.body.get("data_as_of")
        return dt.date.fromisoformat(value) if isinstance(value, str) else None


class ApiUnavailableError(Exception):
    """The API did not answer within the budget. The message is for the page, in plain
    words; it never holds the address or a traceback."""


def retry_after(value: str | None, now: dt.datetime | None = None) -> int | None:
    """Seconds to wait from a `Retry-After` header (seconds or an HTTP date), bounded."""
    if value is None:
        return None
    value = value.strip()
    seconds: float
    if value.isdigit():
        seconds = int(value)
    else:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.UTC)
        seconds = (when - (now or dt.datetime.now(dt.UTC))).total_seconds()
    low, high = RETRY_AFTER_BOUNDS
    return int(min(high, max(low, round(seconds))))


def _is_envelope(response: httpx.Response) -> bool:
    """Whether an answer is the API's own (its JSON carries the notice), not the proxy's."""
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and "notice" in body


class Client:
    """GET requests to one API address. `transport` is for tests only."""

    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=ATTEMPT_TIMEOUT_SECONDS,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "trialpulse-dashboard"},
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def get(self, path: str, params: dict[str, Any] | None = None,
            on_wait: Callable[[str], None] | None = None) -> Answer:  # fmt: skip
        """The API's answer to one GET, after waiting out a start, a wake or a 429."""
        start = monotonic()
        attempt = 0
        while True:
            wait: int | None
            try:
                response = self._http.get(path, params=params)
            except httpx.TransportError:  # refused, reset or timed out: the service is starting
                wait, message = None, STARTING
            else:
                if response.status_code in (200, 404) and not _is_envelope(response):
                    wait, message = None, NOT_API  # never returned, so never cached
                elif response.status_code not in RETRIED:
                    return Answer(response.status_code, _body(response))
                else:
                    wait = retry_after(response.headers.get("retry-after"))
                    if response.status_code == 429:
                        message = BUSY
                    elif response.status_code == 503 and _is_envelope(response):
                        message = DATABASE
                    else:
                        message = STARTING
            if wait is None:
                wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
            attempt += 1
            if monotonic() - start + wait > BUDGET_SECONDS:
                raise ApiUnavailableError(GAVE_UP_NOT_API if message == NOT_API else GAVE_UP)
            if on_wait is not None:
                on_wait(message.format(n=wait))
            sleep(wait)


def _body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
