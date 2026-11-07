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
    assert plain == Counter({200: 5, 429: 3})
    assert forged == Counter({429: 8})  # the same client: its budget is spent


def test_a_setting_that_trusts_the_forged_entry_shows_no_429() -> None:
    with _api(trusted_proxies=2, proxy_appends=True) as client:
        forged = measure_forwarded.measure(client, "https://api.test", 8, forge=True)
    assert forged == Counter({200: 8})
