"""Load test of /v1/trials/{nct_id}/risk (Step 15 acceptance: p95 under 200 ms).

    uv run python scripts/api_load_test.py --url http://127.0.0.1:7860 --requests 3000

Run against a server whose cache is empty (just started) on a database seeded at the
cutoff's size (tests/api/seed.py: 88,895 scored and 2,704 unscored trials). The cold pass
asks for `--requests` distinct trials once each (every answer read from Postgres); the warm
pass asks for the same trials again (every answer from the cache). `--clients` threads send
requests at once, each request as another client address behind one proxy (the
X-Forwarded-For entry the proxy would append), so the per-client rate limit, which protects
the database from one caller, does not stop a test of many callers.
"""

import argparse
import json
import random
import statistics
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

import httpx

SCORED = 88_892  # the synthetic trials of the seed besides its hand-placed ones


def percentile(values: Sequence[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(p / 100 * len(ordered)) - 1))]


def run_pass(url: str, ids: Sequence[str], clients: int) -> dict[str, float]:
    def one(i: int) -> tuple[float, int]:
        headers = {"X-Forwarded-For": f"10.{i // 65536 % 256}.{i // 256 % 256}.{i % 256}"}
        with httpx.Client(base_url=url, timeout=30) as client:
            start = time.perf_counter()
            answer = client.get(f"/v1/trials/{ids[i]}/risk", headers=headers)
            return (time.perf_counter() - start) * 1000, answer.status_code

    with ThreadPoolExecutor(clients) as pool:
        results = list(pool.map(one, range(len(ids))))
    ms = [r[0] for r in results]
    codes = {str(c): sum(1 for r in results if r[1] == c) for c in {r[1] for r in results}}
    return {"requests": len(ms), "p50_ms": round(percentile(ms, 50), 1),
            "p95_ms": round(percentile(ms, 95), 1), "p99_ms": round(percentile(ms, 99), 1),
            "mean_ms": round(statistics.fmean(ms), 1), "status": codes}  # fmt: skip


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:7860")
    parser.add_argument("--requests", type=int, default=3000)
    parser.add_argument("--clients", type=int, default=8)
    parser.add_argument("--seed", type=int, default=15)
    args = parser.parse_args(argv)
    rng = random.Random(args.seed)
    ids = [f"NCT{10_000_000 + i:08d}" for i in rng.sample(range(SCORED), args.requests)]
    cold = run_pass(args.url, ids, args.clients)
    warm = run_pass(args.url, ids, args.clients)
    print(json.dumps({"cold": cold, "warm": warm}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
