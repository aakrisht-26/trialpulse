"""Measure how the deployed API sees its clients behind Render's proxy (ADR 0065, decision 3).

    uv run python scripts/measure_forwarded.py --url https://<service>.onrender.com
    uv run python scripts/measure_forwarded.py --url https://<service>.onrender.com --forge
    uv run python scripts/measure_forwarded.py --url https://<service>.onrender.com --forge-cf

The API limits each client address to `api.rate_limit_per_minute` requests a minute on
/v1/ paths, keyed by the X-Forwarded-For entry `api.trusted_proxies` places from the end
(ADR 0049). This sends `--requests` requests to /v1/model within a minute and counts the
answers by status code:

- without `--forge`: the laptop's own requests. 429s after the limit show that the key is
  stable for one client;
- with `--forge`: each request carries its own made-up X-Forwarded-For entry (from the
  documentation range 198.51.100.0/24). 429s after the limit show that the key is not the
  entry a client writes, so the setting is right. No 429 at all shows that the key is the
  forged entry: anyone could dodge the limit, and `api.trusted_proxies` must change.

- with `--forge-cf`: each request carries its own made-up CF-Connecting-IP and
  True-Client-IP (from 192.0.2.0/24). If Cloudflare in front of Render overwrites them, a key
  read from CF-Connecting-IP stays the same and the limit holds; the API's temporary
  diagnostic (`api.forwarded_diagnostic`) says whether the header stayed the same.

Run it once without and once with `--forge`, a minute apart, and once more from a second
network at the same time as the first (a phone's hotspot): if the two networks share one
budget, the key is a proxy's address, not the client's. It prints counts only: per status
code, and per error class for a request that got no answer (a dropped connection or a
timeout), which is counted, not raised.
"""

import argparse
import time
from collections import Counter
from collections.abc import Sequence

import httpx

PATH = "/v1/model"


def forged_headers(n: int, forge: bool, forge_cf: bool) -> dict[str, str]:
    """Made-up client headers for request n, from documentation address ranges."""
    headers = {"X-Forwarded-For": f"198.51.100.{n % 254 + 1}"} if forge else {}
    if forge_cf:
        made_up = f"192.0.2.{n % 254 + 1}"
        headers |= {"CF-Connecting-IP": made_up, "True-Client-IP": made_up}
    return headers


def measure(client: httpx.Client, url: str, requests: int, forge: bool,
            forge_cf: bool = False) -> Counter[str]:  # fmt: skip
    """The outcome of each of `requests` requests to the API's model statement: its status
    code, or "error: <class>" when no answer came (a dropped connection, a timeout)."""
    found: Counter[str] = Counter()
    for n in range(requests):
        headers = forged_headers(n, forge, forge_cf)
        try:
            found[str(client.get(url.rstrip("/") + PATH, headers=headers).status_code)] += 1
        except httpx.TransportError as exc:  # no answer: count its class and go on
            found[f"error: {type(exc).__name__}"] += 1
    return found


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--url", required=True, help="the deployed API's address")
    parser.add_argument("--requests", type=int, default=70)
    parser.add_argument("--forge", action="store_true")
    parser.add_argument("--forge-cf", action="store_true")
    args = parser.parse_args(argv)
    started = time.monotonic()
    with httpx.Client(timeout=90.0) as client:
        client.get(args.url.rstrip("/") + "/health")  # wakes a service that spun down
        found = measure(client, args.url, args.requests, args.forge, args.forge_cf)
    seconds = time.monotonic() - started
    kind = "+".join(k for k, on in (("forged", args.forge), ("forged-cf", args.forge_cf)) if on)
    print(f"{kind or 'plain'} requests: {args.requests} in {seconds:.0f} s; "
          f"outcomes: {dict(sorted(found.items()))}")  # fmt: skip
    if seconds > 60:
        print("note: the requests took over a minute, so the limit may not have been reached")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
