"""The day-0 check of the live path (ADR 0038, parity layer 3): from the live state the
bootstrap wrote, the cutoff's scores and drivers, computed as the daily job computes them,
equal Step 13's.

    uv run python -m trialpulse.live.dayzero --target local

Laptop only: it reads Step 13's files in data/ and refuses under GitHub Actions. It writes
nothing. In order:

1. The bootstrap's run must have succeeded, dated the data cutoff, with the sponsor file
   through the day before.
2. **The live path's scores of the cutoff**: `daily.score_day` on a full run, so every open
   trial is scored from `live.trial_state` and the sponsor file exactly as the daily job's
   publication stage scores (the population, the classes, the live features through the
   shared final SELECT, the champion's risks and drivers), with nothing published.
3. **Against Step 13's stored output** (`data/scoring/scores/scores_<cutoff>.parquet` and
   `unscored_<cutoff>.parquet`), never against the rows the bootstrap regenerated: the
   trials on each side, and every column of the scores (the risks and the drivers exact,
   with no tolerance) and of the unscored trials (the reason), equal or different, by
   count. The content hashes differ by design (Step 13's are canonical hashes, the live
   path's feature hashes); they are counted apart, and the live path's are also held to
   the hashes of the served rows the bootstrap wrote.

It prints the counts as JSON, and fails (exit code 1) on any difference but the content
hashes. Nothing it prints names a trial.
"""

import argparse
import datetime as dt
import json
from collections.abc import Sequence
from typing import Any

import pandas as pd

from trialpulse.cli import RefusedError, run
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import ProjectConfig, load_project_config
from trialpulse.live import bootstrap, daily, store
from trialpulse.scoring.predict import Scored

FAILED_EXIT = 1


def live_unscored(people: pd.DataFrame, day: Any) -> pd.DataFrame:
    """Every open trial of the population that is not scored, in the columns of Step 13's
    unscored file (the scope of a run leaves out those whose served row is current)."""
    out = people[people["reason"].notna()][["trial_id", "true_index", "reason", "content_hash"]]
    out = out.rename(columns={"trial_id": "nct_id"}).copy()
    out.insert(1, "as_of_date", pd.Timestamp(day))
    return out.sort_values("nct_id").reset_index(drop=True)


def served_hashes(cur: Any) -> dict[str, str]:
    """The content hashes of the served rows (latest scores and unscored trials)."""
    rows = cur.execute(
        """SELECT nct_id, encode(content_hash, 'hex') FROM serving.trial_scores_latest
        UNION ALL SELECT nct_id, encode(content_hash, 'hex') FROM serving.trials_not_scored"""
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def check_cutoff(
    cfg: ProjectConfig,
    dsn: str,
    model: daily.Model,
    reference: tuple[pd.DataFrame, pd.DataFrame],
) -> dict[str, Any]:
    """The live path's cutoff scores against the reference files (see the module text)."""
    import psycopg

    if cfg.dataset.cutoff is None:
        raise RefusedError("config/project.yaml records no data cutoff")
    day = cfg.dataset.cutoff
    rules = CohortRules.from_config(cfg, cutoff=day)
    with psycopg.connect(dsn, connect_timeout=10, autocommit=True) as con, con.cursor() as cur:
        store.check_head(cur)
        boot = store.last_run(cur, "bootstrap")
        if boot is None or boot["data_timestamp"] != day:
            raise RefusedError(f"no succeeded bootstrap dated {day}: run the bootstrap first")
        sponsors = store.load_file(cur)
        if sponsors.through_day != day - dt.timedelta(days=1):
            raise RefusedError(f"the sponsor file is through {sponsors.through_day}, not the "
                               "day before the cutoff")  # fmt: skip
        done = daily.score_day(cur, cfg, rules, day, sponsors, model, full=True)
        served = served_hashes(cur)
    unscored = live_unscored(done.people, day)
    compared = bootstrap.compare_scores(Scored(done.scores, unscored, {}), *reference)
    live_hashes = dict(zip(done.scores["nct_id"], done.scores["content_hash"], strict=True))
    live_hashes.update(zip(unscored["nct_id"], unscored["content_hash"], strict=True))
    same = sum(1 for n, h in live_hashes.items() if served.get(n) == h)
    return {
        "day": day.isoformat(),
        "bootstrap_run": boot["run_id"],
        "live_scored": len(done.scores),
        "live_unscored": len(unscored),
        "compared": compared,
        "content_hash_against_served_rows": {
            "equal": same,
            "different": len(live_hashes) - same,
            "served_rows": len(served),
        },
        "differences": bootstrap.differences(compared),
    }


def main(argv: Sequence[str] | None = None) -> int:
    from trialpulse.ingest.history import refuse_on_a_runner
    from trialpulse.live.daily import _dsn

    parser = argparse.ArgumentParser(description="The day-0 check of the live path (Step 14).")
    parser.add_argument("--target", choices=("local", "neon"), required=True)
    args = parser.parse_args(argv)
    refuse_on_a_runner()  # Step 13's files are on the laptop only
    cfg = load_project_config()
    if cfg.dataset.cutoff is None:
        raise RefusedError("config/project.yaml records no data cutoff")
    inputs = bootstrap.Inputs.default()
    reference = inputs.step13_files(cfg.dataset.cutoff)
    model = daily.champion_model(cfg, inputs.bundle, inputs.decisions)
    result = check_cutoff(cfg, _dsn(args.target), model, reference)
    print(json.dumps(result, indent=2, default=str))
    if result["differences"]:
        print(f"failed: {len(result['differences'])} differences (content hashes aside)")
        return FAILED_EXIT
    print("day-0 check: the live path equals Step 13 on every trial, risk and driver")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
