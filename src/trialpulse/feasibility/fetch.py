"""HTTP plumbing for the spike: rate limiting, retries, an on-disk cache and privacy scrubbing."""

import gzip
import json
import re
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_random_exponential
from tenacity.wait import wait_base

# Keys that hold names or contact details of people. They are removed before anything
# is cached or written (CLAUDE.md Section 2: never store investigator or contact data).
PERSONAL_DATA_KEYS = frozenset(
    {
        "centralContacts",
        "overallOfficials",
        "contacts",
        "pointOfContact",
        "investigatorFullName",
        "investigatorTitle",
        "investigatorAffiliation",
        "oldNameTitle",
        "email",
        "phone",
        "phoneExt",
    }
)


def scrub_personal_data(obj: Any) -> Any:
    """Return a copy of obj with every personal-data key removed, at any depth."""
    if isinstance(obj, dict):
        return {k: scrub_personal_data(v) for k, v in obj.items() if k not in PERSONAL_DATA_KEYS}
    if isinstance(obj, list):
        return [scrub_personal_data(v) for v in obj]
    return obj


class RateLimiter:
    """Spaces calls at least 60 / per_minute seconds apart."""

    def __init__(
        self,
        per_minute: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute must be positive")
        self.interval = 60.0 / per_minute
        self._clock = clock
        self._sleep = sleep
        self._next_allowed: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._next_allowed is not None and now < self._next_allowed:
            self._sleep(self._next_allowed - now)
            now = self._next_allowed
        self._next_allowed = now + self.interval


class RetryableStatusError(Exception):
    """A 429 or 5xx response, which is worth retrying."""

    def __init__(self, status_code: int, url: str) -> None:
        super().__init__(f"HTTP {status_code} from {url}")
        self.status_code = status_code


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, RetryableStatusError | httpx.TransportError)


class JsonFetcher:
    """GET JSON with a rate limit and exponential backoff plus jitter on 429, 5xx and
    network errors. Other HTTP errors are raised at once."""

    def __init__(
        self,
        client: httpx.Client,
        limiter: RateLimiter,
        max_attempts: int = 5,
        wait: wait_base | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._limiter = limiter
        self._max_attempts = max_attempts
        self._wait = wait if wait is not None else wait_random_exponential(multiplier=2, max=120)
        self._sleep = sleep
        self.requests_made = 0

    def get(self, url: str, params: Mapping[str, str | int] | None = None) -> Any:
        retrying = Retrying(
            retry=retry_if_exception(_is_retryable),
            wait=self._wait,
            stop=stop_after_attempt(self._max_attempts),
            sleep=self._sleep,
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                self._limiter.wait()
                self.requests_made += 1
                response = self._client.get(url, params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    raise RetryableStatusError(response.status_code, url)
                response.raise_for_status()
                return response.json()
        raise AssertionError("unreachable: tenacity reraises the last error")


class JsonCache:
    """Gzipped JSON files under a root directory, one per key. Writes are atomic, so an
    interrupted run never leaves a half-written entry that a rerun would trust."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, key: str) -> Path:
        parts = [re.sub(r"[^A-Za-z0-9._-]", "_", part) for part in key.split("/") if part]
        if not parts:
            raise ValueError("empty cache key")
        return self.root.joinpath(*parts).with_suffix(".json.gz")

    def get(self, key: str) -> Any | None:
        path = self.path(key)
        if not path.is_file():
            return None
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)

    def put(self, key: str, value: Any) -> None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(value, fh)
        tmp.replace(path)
