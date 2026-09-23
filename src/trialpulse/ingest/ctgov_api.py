"""ClinicalTrials.gov API v2 client: rate limited, retrying, cursor-paginated, cached.

A minimal first version of the Step 14 client (CLAUDE.md Section 16). Step 6 uses it to
pull the current records of the cohort's early stops for the gold set.

- Requests stay at 40 per minute or less; 429, 5xx and network errors are retried with
  exponential backoff and jitter.
- Every page is cached on disk with its nextPageToken, so a rerun makes no requests and an
  interrupted pull resumes.
- Callers request only the fields they need through the fields parameter, and personal-data
  keys are also removed before a page is cached, so nothing cached holds names or contact
  details of people (CLAUDE.md Section 2).
"""

import gzip
import hashlib
import json
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_random_exponential
from tenacity.wait import wait_base

API_BASE = "https://clinicaltrials.gov/api/v2"
STUDIES_URL = f"{API_BASE}/studies"
VERSION_URL = f"{API_BASE}/version"
MAX_PAGE_SIZE = 1000  # larger values are silently clamped by the API
REQUESTS_PER_MINUTE = 40  # guidance is about 50 per minute per IP (CLAUDE.md Section 7)

# Keys that hold names or contact details of people. They are removed before any page is
# cached, even when the fields parameter already excludes them (CLAUDE.md Section 2).
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
    """A copy of obj with every personal-data key removed, at any depth."""
    if isinstance(obj, dict):
        return {k: scrub_personal_data(v) for k, v in obj.items() if k not in PERSONAL_DATA_KEYS}
    if isinstance(obj, list):
        return [scrub_personal_data(v) for v in obj]
    return obj


class RetryableStatusError(Exception):
    """A 429 or 5xx response, which is worth retrying."""

    def __init__(self, status_code: int, url: str) -> None:
        super().__init__(f"HTTP {status_code} from {url}")
        self.status_code = status_code


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, RetryableStatusError | httpx.TransportError)


class ApiClient:
    """GET JSON at most per_minute times a minute, retrying transient failures."""

    def __init__(
        self,
        client: httpx.Client,
        per_minute: float = REQUESTS_PER_MINUTE,
        max_attempts: int = 8,
        wait: wait_base | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute must be positive")
        self._client = client
        self._interval = 60.0 / per_minute
        self._max_attempts = max_attempts
        self._wait = wait if wait is not None else wait_random_exponential(multiplier=2, max=120)
        self._sleep = sleep
        self._clock = clock
        self._next_allowed: float | None = None
        self.requests_made = 0

    def _pace(self) -> None:
        now = self._clock()
        if self._next_allowed is not None and now < self._next_allowed:
            self._sleep(self._next_allowed - now)
            now = self._next_allowed
        self._next_allowed = now + self._interval

    def get_json(self, url: str, params: Mapping[str, str | int] | None = None) -> Any:
        retrying = Retrying(
            retry=retry_if_exception(_is_retryable),
            wait=self._wait,
            stop=stop_after_attempt(self._max_attempts),
            sleep=self._sleep,
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                self._pace()
                self.requests_made += 1
                response = self._client.get(url, params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    raise RetryableStatusError(response.status_code, url)
                response.raise_for_status()
                return response.json()
        raise AssertionError("unreachable: tenacity reraises the last error")


def get_path(record: Any, path: str) -> Any:
    """Follow a dotted path; missing keys give None."""
    current = record
    for key in path.split("."):
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _read(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(value, fh)
    tmp.replace(path)


def query_dir(cache_root: Path, params: Mapping[str, str | int]) -> Path:
    """One cache folder per distinct query, so different queries never share pages."""
    digest = hashlib.sha1(json.dumps(dict(params), sort_keys=True).encode()).hexdigest()[:12]
    return cache_root / digest


def iter_study_pages(
    api: ApiClient, cache_root: Path, params: Mapping[str, str | int]
) -> Iterator[list[dict[str, Any]]]:
    """Yield the studies of every page of a /studies query, following nextPageToken.

    Pages are cached with their token, so a rerun reads the cache and an interrupted pull
    resumes at the first missing page. A stale token on resume raises; delete the query's
    cache folder to start again."""
    folder = query_dir(cache_root, params)
    index = 0
    token: str | None = None
    while True:
        path = folder / f"page_{index:05d}.json.gz"
        if path.is_file():
            page = _read(path)
        else:
            request = dict(params)
            if index == 0:
                request["countTotal"] = "true"
            elif token:
                request["pageToken"] = token
            else:
                raise RuntimeError(f"missing page token to resume at page {index}")
            data = api.get_json(STUDIES_URL, request)
            page = {
                "studies": scrub_personal_data(data.get("studies", [])),
                "nextPageToken": data.get("nextPageToken"),
                "totalCount": data.get("totalCount"),
            }
            _write(path, page)
        yield list(page["studies"])
        token = page.get("nextPageToken")
        if not token:
            return
        index += 1
