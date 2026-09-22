"""Rate limiting, retries, the on-disk cache and privacy scrubbing."""

from pathlib import Path

import httpx
import pytest
import respx

from trialpulse.feasibility.fetch import (
    JsonCache,
    JsonFetcher,
    RateLimiter,
    RetryableStatusError,
    scrub_personal_data,
)

URL = "https://example.test/api"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_rate_limiter_spaces_calls() -> None:
    fake = FakeClock()
    limiter = RateLimiter(20, clock=fake.clock, sleep=fake.sleep)

    limiter.wait()
    limiter.wait()
    fake.now += 10
    limiter.wait()

    # The second call waits 3 s (20 per minute); after a 10 s gap the third needs no wait.
    assert fake.sleeps == [pytest.approx(3.0)]


def test_rate_limiter_never_exceeds_the_rate() -> None:
    fake = FakeClock()
    limiter = RateLimiter(40, clock=fake.clock, sleep=fake.sleep)
    for _ in range(41):
        limiter.wait()
    # 41 calls need at least 40 intervals of 1.5 seconds.
    assert fake.now >= 60.0


def test_rate_limiter_rejects_a_non_positive_rate() -> None:
    with pytest.raises(ValueError, match="positive"):
        RateLimiter(0)


@respx.mock
def test_fetcher_retries_429_and_5xx_then_succeeds(fast_fetcher: JsonFetcher) -> None:
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    assert fast_fetcher.get(URL) == {"ok": True}
    assert route.call_count == 3
    assert fast_fetcher.requests_made == 3


@respx.mock
def test_fetcher_retries_network_errors(fast_fetcher: JsonFetcher) -> None:
    route = respx.get(URL).mock(
        side_effect=[httpx.ConnectError("dns"), httpx.Response(200, json=[1])]
    )
    assert fast_fetcher.get(URL) == [1]
    assert route.call_count == 2


@respx.mock
def test_fetcher_gives_up_after_max_attempts(fast_fetcher: JsonFetcher) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(500))
    with pytest.raises(RetryableStatusError, match="HTTP 500"):
        fast_fetcher.get(URL)
    assert route.call_count == 3


@respx.mock
def test_fetcher_does_not_retry_client_errors(fast_fetcher: JsonFetcher) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        fast_fetcher.get(URL)
    assert route.call_count == 1


@respx.mock
def test_fetcher_sends_params(fast_fetcher: JsonFetcher) -> None:
    route = respx.get(URL, params={"pageSize": "5"}).mock(return_value=httpx.Response(200, json={}))
    fast_fetcher.get(URL, {"pageSize": 5})
    assert route.called


def test_cache_round_trip(tmp_path: Path) -> None:
    cache = JsonCache(tmp_path)
    assert cache.get("a/b") is None

    cache.put("a/b", {"x": [1, 2]})

    assert cache.get("a/b") == {"x": [1, 2]}
    assert cache.path("a/b") == tmp_path / "a" / "b.json.gz"
    assert not list(tmp_path.rglob("*.tmp"))


def test_cache_key_is_sanitized(tmp_path: Path) -> None:
    cache = JsonCache(tmp_path)
    assert cache.path("x/../y?z").is_relative_to(tmp_path)
    with pytest.raises(ValueError, match="empty"):
        cache.path("/")


def test_scrub_removes_personal_data_at_any_depth() -> None:
    record = {
        "protocolSection": {
            "contactsLocationsModule": {
                "centralContacts": [{"name": "A Person", "email": "a@example.org"}],
                "overallOfficials": [{"name": "B Person"}],
                "locations": [{"country": "France", "contacts": [{"phone": "123"}]}],
            },
            "sponsorCollaboratorsModule": {
                "responsibleParty": {"type": "PRINCIPAL_INVESTIGATOR", "investigatorFullName": "C"}
            },
        }
    }

    clean = scrub_personal_data(record)

    text = repr(clean)
    for secret in ("A Person", "B Person", "a@example.org", "123", "investigatorFullName"):
        assert secret not in text
    assert clean["protocolSection"]["contactsLocationsModule"]["locations"] == [
        {"country": "France"}
    ]
    assert "centralContacts" in record["protocolSection"]["contactsLocationsModule"]
