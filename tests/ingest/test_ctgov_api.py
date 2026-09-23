"""The minimal API v2 client: pacing, retries, pagination and the page cache."""

import gzip
from pathlib import Path

import httpx
import pytest
import respx
from tenacity import wait_none

from trialpulse.ingest.ctgov_api import (
    STUDIES_URL,
    ApiClient,
    RetryableStatusError,
    get_path,
    iter_study_pages,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _api(**kwargs: object) -> ApiClient:
    defaults: dict[str, object] = {
        "per_minute": 60_000,
        "wait": wait_none(),
        "sleep": lambda _: None,
    }
    defaults.update(kwargs)
    return ApiClient(httpx.Client(), **defaults)  # type: ignore[arg-type]


@respx.mock
def test_requests_are_paced_at_the_rate_limit() -> None:
    respx.get(STUDIES_URL).mock(return_value=httpx.Response(200, json={}))
    fake = FakeClock()
    api = ApiClient(httpx.Client(), per_minute=40, clock=fake.clock, sleep=fake.sleep)
    for _ in range(41):
        api.get_json(STUDIES_URL)
    assert fake.now >= 60.0  # 41 requests need at least 40 gaps of 1.5 s


@respx.mock
def test_retries_transient_errors_but_not_client_errors() -> None:
    route = respx.get(STUDIES_URL).mock(
        side_effect=[httpx.Response(503), httpx.ConnectError("dns"), httpx.Response(200, json=[1])]
    )
    assert _api().get_json(STUDIES_URL) == [1]
    assert route.call_count == 3

    bad = respx.get(f"{STUDIES_URL}/bad").mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        _api().get_json(f"{STUDIES_URL}/bad")
    assert bad.call_count == 1  # client errors are not retried

    respx.get(f"{STUDIES_URL}/down").mock(return_value=httpx.Response(500))
    with pytest.raises(RetryableStatusError):
        _api(max_attempts=2).get_json(f"{STUDIES_URL}/down")


@respx.mock
def test_pagination_follows_tokens_and_the_cache_prevents_refetching(tmp_path: Path) -> None:
    route = respx.get(STUDIES_URL).mock(
        side_effect=[
            httpx.Response(200, json={"studies": [{"a": 1}], "nextPageToken": "t1"}),
            httpx.Response(200, json={"studies": [{"a": 2}]}),
        ]
    )
    params: dict[str, str | int] = {"pageSize": 1}

    pages = list(iter_study_pages(_api(), tmp_path, params))

    assert pages == [[{"a": 1}], [{"a": 2}]]
    first, second = route.calls
    assert first.request.url.params["countTotal"] == "true"
    assert second.request.url.params["pageToken"] == "t1"
    cached_api = _api()
    assert list(iter_study_pages(cached_api, tmp_path, params)) == pages
    assert cached_api.requests_made == 0


@respx.mock
def test_an_interrupted_pull_resumes_at_the_missing_page(tmp_path: Path) -> None:
    route = respx.get(STUDIES_URL).mock(
        side_effect=[
            httpx.Response(200, json={"studies": [{"a": 1}], "nextPageToken": "t1"}),
            httpx.ConnectError("network down"),
        ]
    )
    params: dict[str, str | int] = {"pageSize": 1}
    with pytest.raises(httpx.ConnectError):
        list(iter_study_pages(_api(max_attempts=1), tmp_path, params))

    route.side_effect = [httpx.Response(200, json={"studies": [{"a": 2}]})]
    resumed = _api()
    assert list(iter_study_pages(resumed, tmp_path, params)) == [[{"a": 1}], [{"a": 2}]]
    assert resumed.requests_made == 1
    last = route.calls[-1].request.url.params
    assert last["pageToken"] == "t1"
    assert "countTotal" not in last


@respx.mock
def test_personal_data_is_removed_before_caching(tmp_path: Path) -> None:
    study = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT1"},
            "contactsLocationsModule": {
                "centralContacts": [{"name": "A Person", "email": "a@example.org"}],
                "locations": [{"country": "France", "contacts": [{"phone": "123"}]}],
            },
        }
    }
    respx.get(STUDIES_URL).mock(return_value=httpx.Response(200, json={"studies": [study]}))

    pages = list(iter_study_pages(_api(), tmp_path, {"pageSize": 1}))

    files = list(tmp_path.rglob("*.json.gz"))
    assert files  # the page was cached
    cached = ""
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            cached += fh.read()
    for secret in ("A Person", "a@example.org", "123", "centralContacts"):
        assert secret not in cached
        assert secret not in repr(pages)
    assert pages[0][0]["protocolSection"]["contactsLocationsModule"]["locations"] == [
        {"country": "France"}
    ]


def test_get_path() -> None:
    record = {"a": {"b": {"c": 3}}}
    assert get_path(record, "a.b.c") == 3
    assert get_path(record, "a.x.c") is None


def test_rate_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        ApiClient(httpx.Client(), per_minute=0)
