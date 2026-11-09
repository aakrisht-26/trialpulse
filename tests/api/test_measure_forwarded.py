"""scripts/measure_forwarded.py against a stand-in for the API's rate limit (ADR 0065)."""

import importlib.util
from collections import Counter

import httpx

from trialpulse.api.guards import client_address
from trialpulse.config import REPO_ROOT

spec = importlib.util.spec_from_file_location(
    "measure_forwarded", REPO_ROOT / "scripts" / "measure_forwarded.py"
)
assert spec is not None
assert spec.loader is not None
measure_forwarded = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure_forwarded)


def _api(trusted_proxies: int, proxy_appends: bool, limit: int = 5) -> httpx.Client:
    """The API's limit behind one proxy that appends the client's address (or not)."""
    seen: Counter[str] = Counter()

    def handle(request: httpx.Request) -> httpx.Response:
        forwarded = request.headers.get("x-forwarded-for")
        if proxy_appends:
            forwarded = ", ".join(h for h in (forwarded, "203.0.113.9") if h)
        who = client_address(forwarded, "10.0.0.1", trusted_proxies)
        seen[who] += 1
        return httpx.Response(429 if seen[who] > limit else 200)

    return httpx.Client(transport=httpx.MockTransport(handle))


def test_the_right_setting_limits_forged_requests_too() -> None:
    with _api(trusted_proxies=1, proxy_appends=True) as client:
        plain = measure_forwarded.measure(client, "https://api.test", 8, forge=False)
        forged = measure_forwarded.measure(client, "https://api.test", 8, forge=True)
    assert plain == Counter({"200": 5, "429": 3})
    assert forged == Counter({"429": 8})  # the same client: its budget is spent


def test_a_setting_that_trusts_the_forged_entry_shows_no_429() -> None:
    with _api(trusted_proxies=2, proxy_appends=True) as client:
        forged = measure_forwarded.measure(client, "https://api.test", 8, forge=True)
    assert forged == Counter({"200": 8})


def test_a_dropped_connection_or_a_timeout_is_counted_not_raised() -> None:
    calls = Counter[str]()

    def handle(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 2:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response")
        if calls["n"] == 4:
            raise httpx.ReadTimeout("timed out")
        return httpx.Response(200 if calls["n"] < 5 else 429)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        found = measure_forwarded.measure(client, "https://api.test", 6, forge=True)
    assert found == Counter({"200": 2, "429": 2, "error: RemoteProtocolError": 1,
                             "error: ReadTimeout": 1})  # fmt: skip


def test_forged_cloudflare_headers_vary_per_request_and_come_from_a_documentation_range() -> None:
    first = measure_forwarded.forged_headers(0, forge=False, forge_cf=True)
    second = measure_forwarded.forged_headers(1, forge=False, forge_cf=True)
    assert set(first) == {"CF-Connecting-IP", "True-Client-IP"}
    assert first["CF-Connecting-IP"] != second["CF-Connecting-IP"]
    assert all(v.startswith("192.0.2.") for v in (*first.values(), *second.values()))
    assert measure_forwarded.forged_headers(0, forge=False, forge_cf=False) == {}
