"""Parity layer 2 of ADR 0038: the daily job through the local Postgres on a feed from the
warehouse, against the offline path on the same feed.

    uv run python -m trialpulse.live.replay_parity --start 2026-08-28 --end 2026-09-25

Laptop only, and the local Postgres only: it refuses on a GitHub runner, and any database
host but localhost or 127.0.0.1 (it never touches Neon). It replaces the live state of the
local database. In order:

1. **The bootstrap at the start date** (`bootstrap.run_bootstrap` dated `--start`, held to
   Step 13's files of that date, which exist for 2026-08-28, 09-04, 09-11, 09-18, 09-24 and
   the cutoff), under a throwaway sponsor key held in memory.
2. **One daily run on each weekday** after the start through the end, but the missed ones
   (`--miss`): the job's own code (`daily.run_daily`), dated by a stand-in for API v2's
   `/version`, with its pull replaced by the feed. On a run from `since` through `day`, the
   feed gives what a delta pull would give: for every trial with a version posted in that
   window, its latest version posted on or before `day` (of several versions on one day,
   the last), as the warehouse holds it, made a compact row as the bootstrap makes them.
3. **On each checkpoint** (after that day's run): the live path's features of every scored
   trial, read from the live tables as a full run reads them (`daily.day_features`),
   against the offline path (Step 13's code, `scoring.features.compute_served`) on the
   warehouse's versions of the feed F: every version posted on or before the start, then
   every record the feed sent through that day (ADR 0038 defines F by what was sent, so a
   record the live path wrongly dropped shows as a difference). The populations must be
   equal (trials, reversal, served landmark index, reason), and every served feature exact.
   The accepted loss, offline on every version against offline on F, is counted apart.

It prints and writes counts only (data/replay_parity/), never a trial id or a name. It passes
when it has at least one checkpoint, every checkpoint scored some trials and has no
difference, and the live path kept every record sent with a post date after the start (a
record of an earlier date is a re-send of the overlap, which the seed already holds).
"""

import argparse
import datetime as dt
import json
import secrets
import time as clock
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import duckdb
import pandas as pd

from trialpulse.cli import RefusedError, run
from trialpulse.cohort.outcomes import build_states as build_cohort_states
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.features.build import FEED_VERSIONS, connect, prepare_sources
from trialpulse.features.registry import model_columns
from trialpulse.ingest.ctgov_api import VERSION_URL
from trialpulse.live import bootstrap, contract, daily, pull, runs, store
from trialpulse.live import features as live_features
from trialpulse.scoring import features as served_features
from trialpulse.scoring.predict import classify
from trialpulse.scoring.score import population as offline_population

OUT_DIR = REPO_ROOT / "data" / "replay_parity"
LOCAL_HOSTS = ("localhost", "127.0.0.1")
PEOPLE_COLUMNS: tuple[str, ...] = ("trial_id", "reversal", "true_index", "k_star", "flagged",
                                   "reason", "sponsor_class")  # fmt: skip


class DayApi:
    """A stand-in for API v2 that only dates a run (`/version`); the feed replaces the pull."""

    def __init__(self, day: dt.date) -> None:
        self.day = day

    def get_json(self, url: str, params: Mapping[str, str | int] | None = None) -> Any:
        if url != VERSION_URL:
            raise RefusedError("the replay feeds the job; it never calls API v2")
        return {"dataTimestamp": f"{self.day.isoformat()}T12:00:00"}


def run_days(start: dt.date, end: dt.date, missed: Iterable[dt.date] = ()) -> list[dt.date]:
    """The weekdays after `start` through `end`, but the missed ones (a weekend run does
    nothing, as in the daily job)."""
    skip = set(missed)
    days = (start + dt.timedelta(days=n) for n in range(1, (end - start).days + 1))
    return [d for d in days if not runs.is_weekend(d) and d not in skip]


def feed_sql(day: dt.date, since: dt.date) -> str:
    """For every trial with a version posted from `since` through `day`: its latest version
    posted on or before `day`, with its titles' texts (the warehouse's `versions` and
    `texts`)."""
    columns = ", ".join(f"v.{c}" for c in (*bootstrap.CANONICAL, "nct_version",
                                            "intervention_types"))  # fmt: skip
    d, s = f"DATE '{day.isoformat()}'", f"DATE '{since.isoformat()}'"
    return f"""WITH touched AS (
          SELECT DISTINCT nct_id FROM versions WHERE effective_date BETWEEN {s} AND {d})
        SELECT {columns}, ot.text AS official_title, bt.text AS brief_title
        FROM versions v JOIN touched USING (nct_id)
        LEFT JOIN texts ot ON ot.text_hash = v.official_title_hash
        LEFT JOIN texts bt ON bt.text_hash = v.brief_title_hash
        WHERE v.effective_date <= {d}
        QUALIFY row_number() OVER (PARTITION BY v.nct_id
          ORDER BY v.effective_date DESC, v.nct_version DESC) = 1
        ORDER BY v.nct_id"""


@dataclass
class Feed:
    """The records a delta pull would give, from the warehouse; `fed` maps each record given,
    by (trial, post date), to its version."""

    warehouse: duckdb.DuckDBPyConnection
    rules: CohortRules
    secret: bytes
    fed: dict[tuple[str, dt.date], int] = field(default_factory=dict)
    sent: int = 0

    def fetch(self, since: dt.date, day: dt.date) -> daily.Fetched:
        cursor = self.warehouse.execute(feed_sql(day, since))
        names = [d[0] for d in cursor.description]
        rows = []
        for record in cursor.fetchall():
            v = dict(zip(names, record, strict=True))
            types = v["intervention_types"]
            official, brief = v["official_title"], v["brief_title"]
            rows.append(contract.compact_row(v, official, brief, types, self.rules, self.secret))
            self.fed[(str(v["nct_id"]), v["effective_date"])] = int(v["nct_version"])
        self.sent += len(rows)
        return daily.Fetched([], pull.Converted(rows=rows), pages=1)


def kept_records(cur: Any, run_id: int) -> list[tuple[str, dt.date]]:
    """The records a run kept (`live.study_versions`)."""
    rows = cur.execute(
        "SELECT nct_id, effective_date FROM live.study_versions WHERE run_id = %s", (run_id,)
    ).fetchall()
    return [(str(n), d) for n, d in rows]


def kept_by_run(dsn: str, run_id: int) -> list[tuple[str, dt.date]]:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as con, con.cursor() as cur:
        return kept_records(cur, run_id)


def live_on(
    dsn: str, cfg: ProjectConfig, rules: CohortRules, day: dt.date, model: daily.Model
) -> daily.DayFeatures:
    """The live path's population and features on `day`, read as a full run reads them."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as con, con.cursor() as cur:
        sponsors = store.load_file(cur)
        return daily.day_features(cur, cfg, rules, day, sponsors, model, full=True)


def differs(entry: Mapping[str, Any]) -> bool:
    """Whether a checkpoint found a difference, or scored nothing to compare."""
    return bool(entry["people"] or entry["features"] or entry["columns_live_not_offline"]
                or not entry["scored"])  # fmt: skip


def frame_differences(live: pd.DataFrame, offline: pd.DataFrame) -> dict[str, int]:
    """Per column, the rows whose values differ (exactly; missing equals missing)."""
    out = {}
    for column in live.columns:
        a, b = live[column].reset_index(drop=True), offline[column].reset_index(drop=True)
        both_missing = a.isna() & b.isna()
        same = (a == b).fillna(False).astype(bool) | both_missing
        if int((~same).sum()):
            out[column] = int((~same).sum())
    return out


def people_differences(live: pd.DataFrame, offline: pd.DataFrame) -> dict[str, int]:
    """The trials on one side only, and per column the trials of both whose values differ."""
    merged = live[list(PEOPLE_COLUMNS)].merge(offline[list(PEOPLE_COLUMNS)], on="trial_id",
                                               how="outer", suffixes=("", "_off"),
                                               indicator=True)  # fmt: skip
    out = {
        "only_live": int((merged["_merge"] == "left_only").sum()),
        "only_offline": int((merged["_merge"] == "right_only").sum()),
    }
    both = merged[merged["_merge"] == "both"]
    for column in PEOPLE_COLUMNS[1:]:
        out[column] = frame_differences(both[[column]], both[[f"{column}_off"]].set_axis(
            [column], axis=1)).get(column, 0)  # fmt: skip
    return {k: v for k, v in out.items() if v}


@dataclass
class Offline:
    """What the offline path gives on a date: the classified population and the served
    features of the given keys."""

    people: pd.DataFrame
    features: pd.DataFrame
    seconds: float


def offline_on(
    cfg: ProjectConfig,
    warehouse: Path,
    interventions: str,
    day: dt.date,
    keys: pd.DataFrame,
    decisions: Mapping[str, Any],
    versions: pd.DataFrame | None,
    memory_limit: str,
) -> Offline:
    """Step 13's population and served features on `day`, on the warehouse's versions (all of
    them, or with `versions`, a frame of nct_id and nct_version, those alone)."""
    started = clock.perf_counter()
    rules = CohortRules.from_config(cfg, cutoff=max(day, cfg.dataset.cutoff or day))
    con = connect()
    try:
        con.execute(f"SET memory_limit = '{memory_limit}'")
        only = None
        if versions is not None:
            con.register("feed_frame", versions)
            con.execute("CREATE TEMP TABLE feed_keys AS SELECT CAST(nct_id AS VARCHAR) AS nct_id, "
                        "CAST(nct_version AS BIGINT) AS nct_version FROM feed_frame")  # fmt: skip
            con.unregister("feed_frame")
            only = "feed_keys"
        prepare_sources(con, warehouse, interventions, rules, only_versions=only)
        build_cohort_states(con, rules)
        source = FEED_VERSIONS if only else "wh.versions"
        people = classify(offline_population(con, rules, day, source=source), day, rules,
                          decisions)  # fmt: skip
        served_features.register_keys(con, keys)
        features = served_features.compute_served(con, rules, cfg)
    finally:
        con.close()
    return Offline(people, features, clock.perf_counter() - started)


def local_dsn() -> str:
    dsn = daily._dsn("local")
    host = urlsplit(dsn.replace("postgresql+psycopg://", "postgresql://")).hostname
    if host not in LOCAL_HOSTS:
        raise RefusedError("the replay runs on the local Postgres only (localhost or 127.0.0.1)")
    return dsn


def run_replay(
    cfg: ProjectConfig,
    start: dt.date,
    end: dt.date,
    missed: Sequence[dt.date],
    checkpoints: Sequence[dt.date],
    dsn: str,
    inputs: bootstrap.Inputs,
    interventions: str,
    memory_limit: str = "4GB",
    say: Any = print,
) -> dict[str, Any]:
    """The replay (see the module text); returns its counts."""

    if cfg.dataset.cutoff is None or end > cfg.dataset.cutoff:
        raise RefusedError("the replay ends on or before the data cutoff")
    if not checkpoints:
        raise RefusedError("the replay needs at least one --checkpoint")
    days = run_days(start, end, missed)
    unknown = sorted(set(checkpoints) - set(days))
    if unknown:
        raise RefusedError(f"a checkpoint is not a run day: {', '.join(map(str, unknown))}")
    secret = secrets.token_bytes(32)  # a throwaway sponsor key, in memory only
    rules = CohortRules.from_config(cfg, cutoff=cfg.dataset.cutoff)
    model = daily.champion_model(cfg, inputs.bundle, inputs.decisions)
    started = clock.perf_counter()
    result: dict[str, Any] = {"start": start.isoformat(), "end": end.isoformat(),
                              "missed": [d.isoformat() for d in missed], "runs": [],
                              "checkpoints": [], "seconds": {}}  # fmt: skip

    tick = clock.perf_counter()
    boot = bootstrap.run_bootstrap(cfg, dsn, secret, True, inputs, say=lambda _: None, day=start)
    result["bootstrap"] = {"seconds": boot["seconds"], "counts": boot["counts"]}
    result["seconds"]["bootstrap"] = round(clock.perf_counter() - tick, 1)
    say(f"bootstrap at {start}: {result['seconds']['bootstrap']} s")

    with bootstrap.open_warehouse(inputs.warehouse) as wh:
        feed = Feed(wh, rules, secret)
        kept: list[tuple[str, dt.date]] = []
        for day in days:
            tick = clock.perf_counter()
            report = daily.run_daily(cfg, dsn, secret, DayApi(day), model, fetch=feed.fetch)  # type: ignore[arg-type]
            seconds = round(clock.perf_counter() - tick, 1)
            kept_now = kept_by_run(dsn, int(report.run_id or 0))
            kept += kept_now
            details = report.counts.get("details", {})
            entry = {
                "day": day.isoformat(), "status": report.status, "seconds": seconds,
                "full": bool(details.get("full")), "kept": len(kept_now),
                "rescored": report.counts.get("trials_rescored"),
            }  # fmt: skip
            result["runs"].append(entry)
            say(f"run {day}: {entry}")
            if report.status != "succeeded":
                raise RefusedError(f"the run of {day} ended {report.status}")
            if day in checkpoints:
                result["checkpoints"].append(
                    checkpoint(cfg, day, dsn, model, inputs, interventions, start, feed,
                               memory_limit)
                )  # fmt: skip
                say(f"checkpoint {day}: {result['checkpoints'][-1]}")
        posted = wh.execute(
            f"SELECT count(*) FROM versions WHERE effective_date > DATE '{start}' "
            f"AND effective_date <= DATE '{end}'"
        ).fetchone()
    after = {(n, v) for (n, d), v in feed.fed.items() if d > start}
    in_window = int(posted[0]) if posted else 0
    kept_set = set(kept)
    sent_after = {key for key in feed.fed if key[1] > start}
    result["feed"] = {
        "records_sent": feed.sent, "records_kept": len(kept),
        "sent_after_the_start_not_kept": len(sent_after - kept_set),
        "kept_not_sent": len(kept_set - set(feed.fed)),
        # The pull's overlap re-sends records the seed already holds, which ingest leaves out.
        "distinct_records_sent_posted_by_the_start": sum(1 for _, d in feed.fed if d <= start),
        "distinct_versions_sent_posted_after_the_start": len(after),
        "versions_posted_after_the_start": in_window,
        # Hidden from the feed: superseded on their day, or within a missed run's window.
        "versions_never_sent": in_window - len(after),
    }  # fmt: skip
    result["seconds"]["total"] = round(clock.perf_counter() - started, 1)
    result["passes"] = bool(
        result["checkpoints"]
        and not any(c["differences"] for c in result["checkpoints"])
        and not result["feed"]["sent_after_the_start_not_kept"]
        and not result["feed"]["kept_not_sent"]
    )
    return result


def feed_versions(
    warehouse: duckdb.DuckDBPyConnection, start: dt.date, day: dt.date, feed: Feed
) -> pd.DataFrame:
    """The versions of the feed F through `day`: every version posted on or before the
    start, then the version of every record the feed sent with a post date through `day`."""
    before = warehouse.execute(
        f"SELECT nct_id, nct_version FROM versions WHERE effective_date <= DATE '{start}'"
    ).df()
    sent = pd.DataFrame(
        [(n, v) for (n, d), v in sorted(feed.fed.items()) if d <= day],
        columns=["nct_id", "nct_version"],
    )
    return pd.concat([before, sent], ignore_index=True).drop_duplicates(ignore_index=True)


def checkpoint(
    cfg: ProjectConfig,
    day: dt.date,
    dsn: str,
    model: daily.Model,
    inputs: bootstrap.Inputs,
    interventions: str,
    start: dt.date,
    feed: Feed,
    memory_limit: str,
) -> dict[str, Any]:
    """The live features of every scored trial on `day` against offline(F), and the accepted
    loss (offline on every version against offline(F)), by count."""
    tick = clock.perf_counter()
    rules = CohortRules.from_config(cfg, cutoff=max(day, cfg.dataset.cutoff or day))
    live = live_on(dsn, cfg, rules, day, model)
    live_seconds = clock.perf_counter() - tick
    keys = live_features.served_keys(live.scored, day, rules, cfg)
    versions = feed_versions(feed.warehouse, start, day, feed)
    on_feed = offline_on(cfg, inputs.warehouse, interventions, day, keys, model.decisions,
                         versions, memory_limit)  # fmt: skip
    on_all = offline_on(cfg, inputs.warehouse, interventions, day, keys, model.decisions, None,
                        memory_limit)  # fmt: skip
    served = [c for c in model_columns() if c in on_feed.features.columns]
    live_frame = live.features.reset_index(drop=True)
    entry: dict[str, Any] = {
        "day": day.isoformat(),
        "scored": len(live.scored),
        "feed_versions": len(versions),
        "served_features": len(served),
        "people": people_differences(live.people, on_feed.people),
        "columns_live_not_offline": sorted(set(live_frame.columns) - set(on_feed.features)),
        "features": frame_differences(live_frame[served], on_feed.features[served]),
        "accepted_loss": {
            "rows_with_a_feature_apart": int(
                (
                    ~pd.DataFrame(
                        {
                            c: (on_all.features[c] == on_feed.features[c])
                            | (on_all.features[c].isna() & on_feed.features[c].isna())
                            for c in served
                        }
                    ).all(axis=1)
                ).sum()
            ),
            "people": people_differences(on_all.people, on_feed.people),
        },
        "seconds": {
            "live": round(live_seconds, 1),
            "offline_feed": round(on_feed.seconds, 1),
            "offline_all": round(on_all.seconds, 1),
        },
    }
    entry["differences"] = differs(entry)
    return entry


def main(argv: Sequence[str] | None = None) -> int:
    from trialpulse.ingest.history import refuse_on_a_runner
    from trialpulse.warehouse.build import interventions_glob

    parser = argparse.ArgumentParser(description="Parity layer 2: the replay (Step 14).")
    parser.add_argument("--start", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--end", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--miss", type=dt.date.fromisoformat, action="append", default=[],
                        help="a run day to skip (repeatable)")  # fmt: skip
    parser.add_argument("--checkpoint", type=dt.date.fromisoformat, action="append",
                        default=[], help="a run day to compare (repeatable)")  # fmt: skip
    parser.add_argument("--memory-limit", default="4GB")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    refuse_on_a_runner()  # it reads the warehouse and Step 13's files (ADR 0040)
    cfg = load_project_config()
    dsn = local_dsn()
    result = run_replay(cfg, args.start, args.end, args.miss, args.checkpoint, dsn,
                        bootstrap.Inputs.default(), interventions_glob(cfg),
                        memory_limit=args.memory_limit,
                        say=lambda line: print(line, flush=True))  # fmt: skip
    args.out_dir.mkdir(parents=True, exist_ok=True)
    name = f"replay_{args.start}_{args.end}_missed{len(args.miss)}.json"
    (args.out_dir / name).write_text(json.dumps(result, indent=2, default=str) + "\n",
                                     encoding="utf-8")  # fmt: skip
    print(json.dumps({k: result[k] for k in ("seconds", "feed", "passes")}, default=str))
    return 0 if result["passes"] else 1


if __name__ == "__main__":
    raise SystemExit(run(main))
