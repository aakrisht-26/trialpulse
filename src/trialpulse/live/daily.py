"""The daily job of the live system (CLAUDE.md Step 14, Section 12, ADR 0045).

    uv run python -m trialpulse.live.daily --target local
    uv run python -m trialpulse.live.daily --target neon --bundle data/scoring/champion

A run, in order (ADR 0037 decision 5):

1. **The date d** is API v2's `dataTimestamp`. A Saturday or Sunday does nothing, and so
   does a day that already has a succeeded run (the second cron), unless `--force`.
2. **Ingest** (one transaction): pull from the watermark minus the overlap through d,
   convert, quarantine what fails the contract, stop on the NULL gate or a short overlap,
   apply the records to the wide rows, append the kept records, record outcome events,
   lapse exits and amendment markers.
3. **The sponsor pass** (one transaction): the versions of runs the file has not consumed,
   through d minus one day; the file is written once.
4. **Publication**: the population of d, the scope (every open trial on a full run, which
   is due every 7 days; else the trials whose record, landmark or model changed), features
   through the shared final SELECT, scores and drivers from the champion, and
   `scoring.serving.publish` within the scope. A full run also stores the month's score file.

Failures record the stage, the exception type and the SQLSTATE only (ADR 0040): never a
message, which could hold a row. Quarantined records end the run in failure after
publishing, so the failure issue opens. VACUUM runs at the end, whatever happened.

The job refuses to run under GitHub Actions without SPONSOR_ID_KEY, refuses a database not
at the migration head, and refuses before the bootstrap has run.
"""

import argparse
import datetime as dt
import io
import time as clock
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd

from trialpulse.cli import RefusedError, run
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import ProjectConfig, Secrets, load_project_config
from trialpulse.ingest.ctgov_api import ApiClient
from trialpulse.live import engine, pull, runs, store
from trialpulse.live import features as live_features
from trialpulse.live.state import lapse_events
from trialpulse.scoring import serving
from trialpulse.scoring.predict import Scored, classify

INGEST, SPONSORS, PUBLISH = "ingest", "sponsors", "publish"
QUARANTINED_EXIT = 2
RETENTION_EXIT = 3
# A scorer turns the scored trials of a day and their features into score rows (the columns
# of `scoring.predict.predict`, drivers included) and names its model version.
Scorer = Callable[[pd.DataFrame, pd.DataFrame, dt.date], pd.DataFrame]


@dataclass
class Model:
    version: str
    score: Scorer
    decisions: Mapping[str, Any]


@dataclass
class Report:
    status: str
    day: dt.date | None = None
    run_id: int | None = None
    counts: dict[str, Any] = field(default_factory=dict)
    exit_code: int = 0


class InjectedFailureError(RuntimeError):
    """Raised by the crash tests after a stage commits."""


def _sqlstate(exc: BaseException) -> str:
    return str(getattr(exc, "sqlstate", "") or "")


def _dsn(target: str) -> str:
    secrets = Secrets()
    secret = secrets.neon_database_url if target == "neon" else secrets.database_url
    if secret is None:
        name = "NEON_DATABASE_URL" if target == "neon" else "DATABASE_URL"
        raise RefusedError(f"{name} is not set (environment or .env)")
    return secret.get_secret_value().replace("postgresql+psycopg://", "postgresql://")


def score_file(scores: pd.DataFrame) -> bytes:
    """The month's score file (ADR 0039): risks to 4 decimals, compressed Parquet."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    frame = pd.DataFrame(
        {
            "nct_id": scores["nct_id"].astype(str),
            "prediction_date": pd.to_datetime(scores["as_of_date"]).dt.date,
            "risk_12m": scores["risk_12m"].astype(float).round(4),
            "risk_24m": scores["risk_24m"].astype(float).round(4),
            "sponsor_class": scores["sponsor_class"].astype(str),
            "landmark_index": scores["landmark_index"].astype(int),
            "flagged": scores["flagged"].astype(bool),
            "model_version": scores["model_version"].astype(str),
        }
    ).sort_values("nct_id")
    buffer = io.BytesIO()
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), buffer, compression="zstd")
    return buffer.getvalue()


def run_daily(
    cfg: ProjectConfig,
    dsn: str,
    secret: bytes,
    api: ApiClient,
    model: Model,
    force: bool = False,
    fail_after: str | None = None,
) -> Report:
    """One run of the daily job (see the module text)."""
    import psycopg

    live = cfg.live
    day = pull.data_date(api)
    if runs.is_weekend(day):
        return Report("weekend", day)
    rules = CohortRules.from_config(cfg, cutoff=max(day, cfg.dataset.cutoff or day))
    con = psycopg.connect(dsn, connect_timeout=10)
    report = Report("running", day)
    stage = "start"
    timings: dict[str, float] = {}
    try:
        with con.cursor() as cur:
            store.check_head(cur)
            boot = store.last_run(cur, "bootstrap")
            if boot is None:
                raise RefusedError("the bootstrap has not run: no live state to update")
            if not force and store.day_succeeded(cur, day):
                con.rollback()
                return Report("noop", day)
            previous = store.last_run(cur, "daily") or boot
            last_full = store.last_run(cur, "daily", "full")
            full = runs.full_run_due(last_full["data_timestamp"] if last_full else None, day,
                                     live.full_run_days)  # fmt: skip
            report.run_id = store.start_run(cur, "daily", "full" if full else "daily", day,
                                            live.lease_stale_minutes)  # fmt: skip
            con.commit()

            # 1. Ingest --------------------------------------------------------------------
            stage, tick = INGEST, clock.perf_counter()
            latest = store.latest_post_date(cur) or boot["watermark_to"]
            mark = runs.watermark(latest, day)
            since = runs.pull_since(mark, live.overlap_days)
            pulled = pull.pull_delta(api, since, day, live.page_size)
            converted = pull.convert(pulled.studies, rules, secret)
            records = [r for r in converted.rows if r["effective_date"] <= day]
            trials = {str(r["nct_id"]) for r in converted.rows}
            sponsors = store.load_file(cur)
            memory = engine.Memory(
                store.load_wides(cur, trials), sponsors, store.latest_live(cur, trials),
                store.load_markers(cur, trials),
            )  # fmt: skip
            stored = {(w.nct_id, w.effective_date): w.row for w in memory.wides.values()}
            stored.update({(n, r["effective_date"]): r for n, r in memory.last_live.items()})
            lost = runs.null_gate(records, stored, live.null_gate_share)
            if lost:
                raise runs.GateError(f"fields turned NULL: {sorted(lost)}")
            if previous is not boot:
                newest: dict[str, dt.date] = {}
                for r in converted.rows:
                    newest[r["nct_id"]] = max(newest.get(r["nct_id"], r["effective_date"]),
                                              r["effective_date"])  # fmt: skip
                missing = runs.missing_overlap(store.overlap_rows(cur, since), newest, since)
                if len(missing) > live.max_missing_overlap:
                    raise pull.PullError(f"{len(missing)} stored overlap rows were not returned")
            done = engine.ingest(memory, records, rules, day, live.recent_post_days)
            exits = lapse_events(store.lapsed_between(cur, previous["data_timestamp"], day),
                                 previous["data_timestamp"], day)  # fmt: skip
            store.save_ingest(cur, done, report.run_id)
            store.save_events(cur, exits, report.run_id)
            store.quarantine(cur, converted.quarantined, report.run_id)
            con.commit()
            timings[INGEST] = clock.perf_counter() - tick
            report.counts.update(
                watermark_from=since, watermark_to=mark, records_fetched=len(pulled.studies),
                versions_added=len(done.versions),
                records_quarantined=len(converted.quarantined),
                details={"actions": dict(done.counts), "deferred": len(converted.rows)
                         - len(records), "lapse_exits": len(exits), "pages": pulled.pages},
            )  # fmt: skip
            if fail_after == INGEST:
                raise InjectedFailureError(INGEST)

            # 2. The sponsor pass -----------------------------------------------------------
            stage, tick = SPONSORS, clock.perf_counter()
            sponsors = store.load_file(cur)
            pending = [v for v in store.versions_after(cur, sponsors.last_run_id)
                       if v["run_id"] <= report.run_id]  # fmt: skip
            if pending or sponsors.through_day < day - dt.timedelta(days=1):
                engine.advance(engine.Memory({}, sponsors), pending, day, rules, report.run_id)
                store.save_file(cur, sponsors)  # the pass's one commit point
                con.commit()
            timings[SPONSORS] = clock.perf_counter() - tick
            if fail_after == SPONSORS:
                raise InjectedFailureError(SPONSORS)

            # 3. Publication ----------------------------------------------------------------
            stage, tick = PUBLISH, clock.perf_counter()
            if sponsors.through_day < day - dt.timedelta(days=1):
                raise RefusedError(f"the sponsor pass is behind: through {sponsors.through_day}")
            rescored = publish_day(cur, cfg, rules, day, sponsors, model, full, dsn,
                                   report.run_id)  # fmt: skip
            con.commit()
            timings[PUBLISH] = clock.perf_counter() - tick
            report.counts["trials_rescored"] = rescored
            size = store.database_mb(cur)
            alerts = [mb for mb in live.retention_alert_mb if size >= mb]
            report.counts["details"].update(seconds=timings, database_mb=round(size, 1),
                                            retention_alerts=alerts, full=full)  # fmt: skip
            status = "failed" if converted.quarantined else "succeeded"
            error = (f"QuarantinedRecords: {len(converted.quarantined)}"
                     if converted.quarantined else None)  # fmt: skip
            store.finish_run(cur, report.run_id, status, report.counts, error)
            con.commit()
            report.status = status
            if converted.quarantined:
                report.exit_code = QUARANTINED_EXIT
            elif alerts and max(alerts) == max(live.retention_alert_mb):
                report.exit_code = RETENTION_EXIT
            return report
    except BaseException as exc:
        con.rollback()
        if report.run_id is not None:
            with con.cursor() as cur:
                error = f"{stage}: {type(exc).__name__} {_sqlstate(exc)}".strip()
                store.finish_run(cur, report.run_id, "failed", report.counts, error)
                con.commit()
        raise
    finally:
        con.close()
        _vacuum(dsn)


def publish_day(
    cur: Any,
    cfg: ProjectConfig,
    rules: CohortRules,
    day: dt.date,
    sponsors: Any,
    model: Model,
    full: bool,
    dsn: str,
    run_id: int,
) -> int:
    """Score the run's scope on `day` and publish it; returns the trials rescored."""
    rows = store.population_rows(cur, day, rules.open_statuses, rules.study_type,
                                 rules.min_first_post_date)  # fmt: skip
    people = pd.DataFrame(rows, columns=["trial_id", "t0", "reversal", "content_hash",
                                         "sponsor_class"])  # fmt: skip
    people["t0"] = pd.to_datetime(people["t0"])
    people = classify(people, day, rules, model.decisions)
    latest, not_scored = store.served_state(cur)
    chosen = runs.scope(people, latest, not_scored, model.version, full)
    scored = people[people["trial_id"].isin(chosen.rescore)].sort_values("trial_id")
    scored = scored.reset_index(drop=True)
    wides = store.load_wides(cur, chosen.rescore)
    keys = live_features.served_keys(scored, day, rules, cfg)
    count = store.open_count(cur, day, rules.open_statuses, rules.study_type)
    features = live_features.compute_live(wides, keys, sponsors, count, rules)
    scores = model.score(scored, features, day) if len(scored) else _empty_scores()
    unscored = people[people["reason"].notna() & people["trial_id"].isin(chosen.trials)]
    unscored = unscored[["trial_id", "true_index", "reason", "content_hash"]].rename(
        columns={"trial_id": "nct_id"}
    )
    unscored.insert(1, "as_of_date", np.datetime64(day, "D"))
    serving.publish(Scored(scores, unscored.reset_index(drop=True), {}), cfg, dsn,
                    scope=chosen.trials, run_id=run_id, day=day)  # fmt: skip
    if full and len(scores):
        classes = dict(zip(people["trial_id"], people["sponsor_class"], strict=True))
        with_class = scores.assign(sponsor_class=scores["nct_id"].map(classes))
        store.save_score_file(cur, day, model.version, score_file(with_class), len(scores),
                              run_id)  # fmt: skip
    return len(scored)


def _empty_scores() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["nct_id", "as_of_date", "true_index", "landmark_index", "flagged", "risk_12m",
                 "risk_24m", "content_hash", "model_name", "model_version", "drivers"]
    )  # fmt: skip


def _vacuum(dsn: str) -> None:
    """VACUUM after publication (ADR 0045): plain VACUUM keeps rewritten tables from
    doubling. A failure here is ignored: it never undoes the run."""
    import psycopg

    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as con:
            for table in (
                "live.trial_state",
                "live.sponsor_file",
                "serving.trial_scores_latest",
                "serving.trial_score_history",
                "serving.trials_not_scored",
            ):
                con.execute(f"VACUUM (ANALYZE) {table}")  # fmt: skip
    except Exception:
        return


def champion_model(cfg: ProjectConfig, folder: Path) -> Model:
    """The champion from a bundle folder, with the decisions of ADR 0034 saved beside it."""
    import json

    from trialpulse.scoring.bundle import load_bundle
    from trialpulse.scoring.predict import predict

    bundle = load_bundle(cfg, folder)
    path = folder / "step13_checks.json"
    if not path.is_file():
        raise RefusedError(
            f"{path} is missing: register the ADR 0034 decisions with the champion (laptop)"
        )
    decisions = json.loads(path.read_text(encoding="utf-8"))["decisions"]

    def score(scored: pd.DataFrame, features: pd.DataFrame, day: dt.date) -> pd.DataFrame:
        out, _ = predict(scored, features, bundle, day, with_drivers=True)
        return out

    return Model(bundle.version, score, decisions)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The daily job of the live system (Step 14).")
    parser.add_argument("--target", choices=("local", "neon"), required=True)
    parser.add_argument("--bundle", type=Path, help="the champion's bundle folder")
    parser.add_argument("--force", action="store_true", help="run even if today succeeded")
    args = parser.parse_args(argv)
    cfg = load_project_config()
    key = Secrets().sponsor_id_key
    if key is None:
        raise RefusedError("SPONSOR_ID_KEY is not set (environment, .env or a secret)")
    from trialpulse.scoring.bundle import CHAMPION_DIR

    model = champion_model(cfg, args.bundle or CHAMPION_DIR)
    api = ApiClient(httpx.Client(timeout=60), per_minute=cfg.live.requests_per_minute)
    report = run_daily(cfg, _dsn(args.target), key.get_secret_value().encode("utf-8"), api,
                       model, force=args.force)  # fmt: skip
    print(f"daily run: {report.status} for {report.day} (run {report.run_id})")
    for name, value in report.counts.items():
        print(f"  {name}: {value}")
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(run(main))
