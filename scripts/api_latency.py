"""Warm and cold latency of the deployed API's risk endpoint, measured by hand (Step 18).

    uv run python scripts/api_latency.py
    uv run python scripts/api_latency.py --cold-after-minutes 16

It takes scored trial ids from one sponsor's portfolio (`/v1/sponsors/{name}/portfolio`, a large
organization whose name is shown), sends one warm-up request, then asks
`/v1/trials/{id}/risk` once for each of `--n` different scored ids, one request every
`--interval` seconds (under the API's limit of 60 a minute per client; every request counts,
the portfolio and the warm-up included). Each id is asked once, so no answer comes from the
API's cache of an earlier request in this run. It prints p50, p95 and the maximum of the
round-trip times seen from this machine (network included), and the status of every answer.

With `--cold-after-minutes`, it then sends nothing for that long, so the free Render instance
spins down (after 15 idle minutes), and times one more request to an id not asked before, on
a new connection: the cold start as a visitor meets it. Another visitor in that span makes the
time a warm one; the output says when the request was sent.

These are measurements of one run, not targets (Step 15's 200 ms target is for a local load
test). It is never run by the tests, which are offline.
"""

import argparse
import json
import math
import sys
import time
from collections.abc import Sequence
from typing import Any

URL = "https://trialpulse-api.onrender.com"
HEADERS = {"User-Agent": "trialpulse-latency-check"}


def percentile(values: Sequence[float], q: float) -> float:
    """The nearest-rank percentile (q in 0 to 100) of the values."""
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def main(argv: Sequence[str] | None = None) -> int:
    import httpx

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--url", default=URL)
    parser.add_argument("--sponsor", default="Pfizer")
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--interval", type=float, default=1.2)
    parser.add_argument("--cold-after-minutes", type=float, default=0.0)
    args = parser.parse_args(argv)
    base = args.url.rstrip("/")
    last = [0.0]

    def paced_get(client: Any, path: str) -> tuple[Any, float]:
        wait = last[0] + args.interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        last[0] = time.monotonic()
        started = time.perf_counter()
        response = client.get(base + path)
        return response, (time.perf_counter() - started) * 1000

    out: dict[str, Any] = {"url": base, "interval_s": args.interval}
    with httpx.Client(headers=HEADERS, timeout=180.0) as client:
        health, health_ms = paced_get(client, "/health")
        out["first_health"] = {"status": health.status_code, "ms": round(health_ms)}
        portfolio, _ = paced_get(client, f"/v1/sponsors/{args.sponsor}/portfolio?limit=200")
        portfolio.raise_for_status()
        ids = [t["nct_id"] for t in portfolio.json()["trials"] if t["status"] == "scored"]
        if len(ids) < args.n + 2:
            print(f"only {len(ids)} scored trials in the portfolio; need {args.n + 2}")
            return 1
        warm_up, measured, cold_id = ids[0], ids[1 : args.n + 1], ids[args.n + 1]
        response, ms = paced_get(client, f"/v1/trials/{warm_up}/risk")
        out["warm_up"] = {"status": response.status_code, "ms": round(ms)}
        times, statuses = [], {}
        for nct in measured:
            response, ms = paced_get(client, f"/v1/trials/{nct}/risk")
            body = response.json() if response.status_code == 200 else {}
            key = f"{response.status_code} {body.get('status', '')}".strip()
            statuses[key] = statuses.get(key, 0) + 1
            times.append(ms)
    out["warm"] = {"n": len(times), "answers": statuses,
                   "p50_ms": round(percentile(times, 50)), "p95_ms": round(percentile(times, 95)),
                   "max_ms": round(max(times)), "min_ms": round(min(times))}  # fmt: skip
    print(json.dumps(out, indent=1), flush=True)
    if args.cold_after_minutes > 0:
        time.sleep(args.cold_after_minutes * 60)
        sent = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        with httpx.Client(headers=HEADERS, timeout=300.0) as client:
            started = time.perf_counter()
            response = client.get(f"{base}/v1/trials/{cold_id}/risk")
            ms = (time.perf_counter() - started) * 1000
        out["cold"] = {"after_idle_minutes": args.cold_after_minutes, "sent": sent,
                       "status": response.status_code, "ms": round(ms)}  # fmt: skip
        print(json.dumps({"cold": out["cold"]}, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
