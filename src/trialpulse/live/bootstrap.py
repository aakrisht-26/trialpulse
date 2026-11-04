"""The bootstrap of the live system (CLAUDE.md Section 12, ADRs 0037, 0040, 0043 to 0045):
the live state as of the data cutoff, computed from the warehouse and written once.

    uv run python -m trialpulse.live.bootstrap --target local
    uv run python -m trialpulse.live.bootstrap --target local --rebuild

Laptop only (ADR 0040): it reads the warehouse and refuses under GitHub Actions. It needs
SPONSOR_ID_KEY (ADR 0044) and a database at the migration head, and without `--rebuild` it
refuses over any live row; both are checked first, on a short connection. In order:

1. **The cutoff's scores and drivers**, regenerated through Step 13's code
   (`scoring.score.score`) on the warehouse, and held to Step 13's files of that date: the
   bootstrap stops if a trial, a risk, a driver or a reason differs. No connection is held
   while they are computed (about 5 minutes), since a database such as Neon may end a
   session that sends nothing.
2. **The lease** (`store.start_bootstrap`), on a new connection after the checks are made
   again, in a short transaction of its own; the stages below renew it (ADR 0060), and a
   failure is recorded on a fresh connection.
3. **The seeded state, streamed** (`stream_seed`): one DuckDB query reads the versions in
   (trial, post date, version) order with their titles and intervention types, a batch of
   rows at a time; each becomes a compact row (`contract.compact_row`), and each trial goes
   through `seed.seed_trial` once its last version is read, which keeps its wide row, its
   amendment markers and its daily states (the five fields the sponsor pass reads, as
   tuples of shared values). `seed.sponsor_pass` then runs month by month. A test holds
   the result equal to `seed.seed` on the synthetic registry, and a slow test on a seeded
   5% of the warehouse.
4. **The scores' content hashes** become the feature hashes of the trials' wide rows
   (ADR 0037), which the daily job compares with.
5. **The reason counts** of the early stops through the cutoff (ADR 0050).
6. **The write** (W1): every row prepared with no transaction open
   (`store.prepare_bootstrap`), then sent in one transaction (`store.load_bootstrap`), so
   nothing is computed while it is open.

A rebuild empties the display tables (`serving.trial_display`, `serving.sponsors`), which
only the bulk display pull fills (ADR 0051): run `trialpulse.live.display` after every
bootstrap, and never before the first, since its rows would make the bootstrap refuse.

It prints counts, the seconds of each stage and of the write, the rows and bytes sent per
table, the stored bytes per table, and the process's memory (its peak working set and peak
private bytes, on Windows). Like the daily job (ADR 0040), a failure is reported by its
stage, type and SQLSTATE only, since a failed COPY's message repeats a row. Nothing it
prints names a trial or a sponsor.
"""

import argparse
import datetime as dt
import gc
import json
import sys
import time as clock
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from trialpulse.cli import RefusedError, run
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import REPO_ROOT, ProjectConfig, Secrets, load_project_config
from trialpulse.live import contract, store
from trialpulse.live.seed import State, seed_trial, sponsor_pass, state_of
from trialpulse.live.sponsors import SponsorFile
from trialpulse.live.state import Wide
from trialpulse.scoring.predict import Scored

BATCH_ROWS = 50_000
MEMORY_LIMIT = "2GB"
THREADS = 4
TEMP_DIR = REPO_ROOT / "data" / "duckdb_tmp"
TITLES = "bootstrap_titles"
# The canonical columns a compact row is made from (`contract.compact_row`).
CANONICAL: tuple[str, ...] = (
    "nct_id", *contract.COPIED, "overall_status", "last_known_status", "sponsor_key",
    "sponsor_is_individual",
)  # fmt: skip
SCORE_COLUMNS: tuple[str, ...] = ("as_of_date", "true_index", "landmark_index", "flagged",
                                  "risk_12m", "risk_24m", "model_name", "model_version",
                                  "drivers")  # fmt: skip
UNSCORED_COLUMNS: tuple[str, ...] = ("as_of_date", "true_index", "reason")
UPLINKS_MBPS: tuple[float, ...] = (10.0, 2.0)  # the Neon estimate's uplinks
DISPLAY_NEXT = ("next: the display tables are empty; fill them with uv run python -m "
                "trialpulse.live.display --target <the same target> (ADR 0051)")  # fmt: skip


# Memory -----------------------------------------------------------------------------------


def memory_mb() -> dict[str, int] | None:
    """The process's working set, peak working set, private bytes and peak private bytes in
    MB (GetProcessMemoryInfo); None outside Windows."""
    if sys.platform != "win32":
        return None
    import ctypes
    import ctypes.wintypes as wt

    class Counters(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS_EX
        _fields_ = [
            ("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]  # fmt: skip

    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    kernel = ctypes.WinDLL("kernel32")
    psapi = ctypes.WinDLL("psapi")
    kernel.GetCurrentProcess.restype = wt.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(Counters), wt.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters),
                                      counters.cb):  # fmt: skip
        return None
    mb = 1024 * 1024
    return {
        "working_set": counters.WorkingSetSize // mb,
        "peak_working_set": counters.PeakWorkingSetSize // mb,
        "private": counters.PrivateUsage // mb,
        "peak_private": counters.PeakPagefileUsage // mb,
    }


# The streamed seed ------------------------------------------------------------------------


@dataclass
class Streamed:
    """The seeded state, as `seed.seed` gives it, and what the stream read."""

    wides: dict[str, Wide]
    sponsors: SponsorFile
    markers: dict[str, list[tuple[dt.date, int]]]
    counts: dict[str, int] = field(default_factory=dict)


class _Shared:
    """One object per distinct value of the states' fields, so that millions of states hold
    references to few strings and dates."""

    def __init__(self) -> None:
        self.values: dict[Any, Any] = {}

    def __call__(self, state: State) -> State:
        return tuple(v if v is None else self.values.setdefault(v, v) for v in state)


def stream_sql(day: dt.date, trials: str | None) -> tuple[str, str]:
    """The statement that makes the titles relation, and the streamed query over
    `versions`."""
    only = f"JOIN {trials} USING (nct_id)" if trials else ""
    titles = f"""CREATE OR REPLACE TEMP TABLE {TITLES} AS
        SELECT text_hash, text FROM texts WHERE text_hash IN (
          SELECT official_title_hash FROM versions {only}
          UNION SELECT brief_title_hash FROM versions {only})"""
    columns = ", ".join(f"v.{c}" for c in (*CANONICAL, "nct_version", "intervention_types"))
    stream = f"""SELECT {columns}, ot.text AS official_title, bt.text AS brief_title
        FROM versions v {only}
        LEFT JOIN {TITLES} ot ON ot.text_hash = v.official_title_hash
        LEFT JOIN {TITLES} bt ON bt.text_hash = v.brief_title_hash
        WHERE v.effective_date <= DATE '{day.isoformat()}'
        ORDER BY v.nct_id, v.effective_date, v.nct_version"""
    return titles, stream


def stream_seed(
    con: duckdb.DuckDBPyConnection,
    day: dt.date,
    rules: CohortRules,
    secret: bytes,
    keep_days: int,
    alias_min_trials: int,
    alias_wait_days: int,
    batch_rows: int = BATCH_ROWS,
    trials: str | None = None,
    tick: Callable[[], None] | None = None,
) -> Streamed:
    """`seed.seed` of every version posted on or before `day`, streamed from the relations
    `versions` and `texts` of `con` (the warehouse's), `batch_rows` rows at a time; with
    `trials`, a relation of `nct_id`, only those trials. `tick`, if given, is called after
    each batch (the command renews its lease there)."""
    titles, stream = stream_sql(day, trials)
    con.execute(titles)
    cursor = con.execute(stream)
    names = [d[0] for d in cursor.description]
    wides: dict[str, Wide] = {}
    markers: dict[str, list[tuple[dt.date, int]]] = {}
    states: list[State] = []
    shared = _Shared()
    counts = {"versions": 0, "trials": 0, "batches": 0}

    def finish(history: list[dict[str, Any]]) -> None:
        part = seed_trial(history, day, rules, keep_days)
        states.extend(shared(state_of(s)) for s in part.states)
        counts["trials"] += 1
        if part.wide is not None and part.markers is not None:
            wides[part.wide.nct_id] = part.wide
            markers[part.wide.nct_id] = part.markers

    history: list[dict[str, Any]] = []
    while batch := cursor.fetchmany(batch_rows):
        counts["batches"] += 1
        for record in batch:
            canonical = dict(zip(names, record, strict=True))
            row = contract.compact_row(canonical, canonical["official_title"],
                                       canonical["brief_title"],
                                       canonical["intervention_types"], rules, secret)  # fmt: skip
            row["nct_version"] = canonical["nct_version"]
            if history and history[-1]["nct_id"] != row["nct_id"]:
                finish(history)
                history = []
            history.append(row)
        counts["versions"] += len(batch)
        if tick is not None:
            tick()
    if history:
        finish(history)
    con.execute(f"DROP TABLE IF EXISTS {TITLES}")
    counts["states"] = len(states)
    sponsors = sponsor_pass(states, day, rules, alias_min_trials, alias_wait_days)
    counts["wides"] = len(wides)
    return Streamed(wides, sponsors, markers, counts)


def open_warehouse(path: Path) -> duckdb.DuckDBPyConnection:
    """The warehouse, read-only, with DuckDB held to MEMORY_LIMIT and THREADS (it spills to
    data/duckdb_tmp beyond them)."""
    if not path.is_file():
        raise RefusedError(f"no warehouse at {path}; build it with: "
                           "uv run python -m trialpulse.warehouse.build")  # fmt: skip
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(
        str(path), read_only=True,
        config={"memory_limit": MEMORY_LIMIT, "threads": THREADS,
                "temp_directory": TEMP_DIR.as_posix()},
    )  # fmt: skip


# Scores against a reference ---------------------------------------------------------------


def _same(a: Any, b: Any) -> bool:
    if a is None or (isinstance(a, float) and pd.isna(a)):
        return b is None or (isinstance(b, float) and pd.isna(b))
    return bool(a == b)


def _frame_counts(new: pd.DataFrame, reference: pd.DataFrame,
                  columns: Sequence[str]) -> dict[str, Any]:  # fmt: skip
    """Rows in both, in one only, and per column the matched rows equal and different (for
    a risk, also the largest absolute difference)."""
    merged = new.merge(reference, on="nct_id", how="outer", suffixes=("", "_ref"),
                       indicator=True)  # fmt: skip
    both = merged[merged["_merge"] == "both"]
    out: dict[str, Any] = {
        "rows": {
            "both": len(both),
            "only_new": int((merged["_merge"] == "left_only").sum()),
            "only_reference": int((merged["_merge"] == "right_only").sum()),
        },
        "columns": {},
    }
    for column in columns:
        equal = sum(_same(a, b) for a, b in zip(both[column], both[f"{column}_ref"], strict=True))
        entry: dict[str, Any] = {"equal": int(equal), "different": len(both) - int(equal)}
        if column.startswith("risk_") and len(both):
            entry["max_abs_difference"] = float(
                (both[column].astype(float) - both[f"{column}_ref"].astype(float)).abs().max()
            )
        out["columns"][column] = entry
    return out


def compare_scores(new: Scored, scores: pd.DataFrame, unscored: pd.DataFrame) -> dict[str, Any]:
    """`new` against reference scores and unscored trials of the same date (Step 13's
    files): the trials of each side, and every column equal or different, by count. The
    content hashes are counted too, but apart (`differences` leaves them out): Step 13's
    are canonical hashes, the live path's are feature hashes (ADR 0037)."""

    def prepared(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
        out = frame[["nct_id", *columns, "content_hash"]].copy()
        out["as_of_date"] = pd.to_datetime(out["as_of_date"]).dt.date
        return out

    return {
        "scores": _frame_counts(prepared(new.scores, SCORE_COLUMNS),
                                prepared(scores, SCORE_COLUMNS),
                                [*SCORE_COLUMNS, "content_hash"]),
        "unscored": _frame_counts(prepared(new.unscored, UNSCORED_COLUMNS),
                                  prepared(unscored, UNSCORED_COLUMNS),
                                  [*UNSCORED_COLUMNS, "content_hash"]),
    }  # fmt: skip


def differences(compared: Mapping[str, Any]) -> list[str]:
    """What differs in a comparison, the content hashes aside: rows on one side only, and
    the columns with differing rows, by count."""
    out = []
    for part in ("scores", "unscored"):
        rows = compared[part]["rows"]
        if rows["only_new"] or rows["only_reference"]:
            out.append(f"{part}: {rows['only_new']} rows only new, {rows['only_reference']} "
                       "only in the reference")  # fmt: skip
        for column, entry in compared[part]["columns"].items():
            if column != "content_hash" and entry["different"]:
                out.append(f"{part}.{column}: {entry['different']} differ")
    return out


# The command ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Inputs:
    """What the bootstrap reads on the laptop."""

    warehouse: Path
    bundle: Path
    decisions: Path
    outcomes: Path
    scores_dir: Path

    @classmethod
    def default(cls) -> "Inputs":
        from trialpulse.cohort.build import OUTCOMES_PATH
        from trialpulse.scoring import checks
        from trialpulse.scoring.bundle import CHAMPION_DIR
        from trialpulse.scoring.score import SCORES_DIR
        from trialpulse.warehouse.build import WAREHOUSE_PATH

        return cls(WAREHOUSE_PATH, CHAMPION_DIR, checks.RESULT_PATH, OUTCOMES_PATH, SCORES_DIR)

    def step13_files(self, day: dt.date) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Step 13's scores and unscored trials of `day`, which the regenerated scores are
        held to (ADR 0040, item 2)."""
        paths = [self.scores_dir / f"{name}_{day.isoformat()}.parquet"
                 for name in ("scores", "unscored")]  # fmt: skip
        missing = [p.name for p in paths if not p.is_file()]
        if missing:
            raise RefusedError(f"Step 13's files of {day} are not in {self.scores_dir}: "
                               f"{', '.join(missing)}")  # fmt: skip
        return pd.read_parquet(paths[0]), pd.read_parquet(paths[1])


def neon_estimate(sent_bytes: int, write_seconds: float) -> dict[str, dict[str, float]]:
    """The write to Neon at each uplink of UPLINKS_MBPS: the transfer alone, and the transfer
    added to the local write's time (the server's work, as measured on the laptop)."""
    out = {}
    for mbps in UPLINKS_MBPS:
        transfer = sent_bytes * 8 / (mbps * 1e6)
        out[f"{mbps:g}_mbps"] = {
            "transfer_seconds": round(transfer, 1),
            "with_local_write_seconds": round(transfer + write_seconds, 1),
        }
    return out


def _stored(cur: Any, tables: Sequence[str]) -> dict[str, dict[str, int]]:
    out = {}
    for table in tables:
        rows = cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        size = cur.execute("SELECT pg_total_relation_size(%s::regclass)", (table,)).fetchone()[0]
        out[table] = {"rows": int(rows), "bytes": int(size)}
    return out


def run_bootstrap(
    cfg: ProjectConfig,
    dsn: str,
    secret: bytes,
    rebuild: bool,
    inputs: Inputs,
    say: Callable[[str], None] = print,
    day: dt.date | None = None,
) -> dict[str, Any]:
    """The bootstrap (see the module text); returns its report. It is dated the data cutoff;
    the replay of parity layer 2 (`live.replay_parity`) dates it earlier, on a day whose
    Step 13 files exist, so that the daily job can run forward to the cutoff."""
    import psycopg

    from trialpulse.live import daily, reasons
    from trialpulse.scoring import score as offline
    from trialpulse.scoring.bundle import load_bundle

    if cfg.dataset.cutoff is None:
        raise RefusedError("config/project.yaml records no data cutoff")
    day = day or cfg.dataset.cutoff
    if day > cfg.dataset.cutoff:
        raise RefusedError(f"the bootstrap cannot be dated after the data cutoff ({day})")
    rules = CohortRules.from_config(cfg, cutoff=day)
    started = clock.perf_counter()
    report: dict[str, Any] = {"release": day.isoformat(), "seconds": {}, "memory_mb": {}}
    reference = inputs.step13_files(day)
    bundle = load_bundle(cfg, inputs.bundle)
    decisions = offline.served_decisions(inputs.decisions)

    def mark(stage: str, since: float) -> None:
        report["seconds"][stage] = round(clock.perf_counter() - since, 1)
        report["memory_mb"][stage] = memory_mb()
        say(f"{stage}: {report['seconds'][stage]} s, memory {report['memory_mb'][stage]}")

    def connect() -> Any:
        try:
            return psycopg.connect(dsn, connect_timeout=10, autocommit=True)
        except psycopg.Error as exc:
            raise daily.JobFailedError(daily.failure_line("connect", exc),
                                       type(exc).__name__) from None  # fmt: skip

    def check(cur: Any) -> None:
        store.check_head(cur)
        occupied = store.occupied_tables(cur)
        if occupied and not rebuild:
            raise RefusedError(f"live state exists in {len(occupied)} tables; start it "
                               "again with --rebuild")  # fmt: skip

    with connect() as first, first.cursor() as cur:  # refuse before computing anything
        check(cur)
    stage, tick = "scores", clock.perf_counter()
    try:
        scored = offline.score(cfg, day, bundle, decisions, warehouse=inputs.warehouse)
    except Exception as exc:  # no lease is held yet, so there is no run row to fail
        raise daily.JobFailedError(daily.failure_line(stage, exc), type(exc).__name__) from None
    compared = compare_scores(scored, *reference)
    report["held_to_step13"] = compared
    found = differences(compared)
    if found:
        raise RefusedError("the regenerated cutoff scores differ from Step 13's: "
                           + "; ".join(found))  # fmt: skip
    mark(stage, tick)

    con = connect()
    run_id: int | None = None
    stage = "start"
    try:
        with con.cursor() as cur:
            check(cur)  # again: the database may have changed while the scores were made
            with con.transaction():
                run_id = store.start_bootstrap(cur, day, cfg.live.lease_stale_minutes)
            report["run_id"] = run_id
            lease = run_id

            def renew() -> None:
                store.renew(cur, lease)

            stage, tick = "seed", clock.perf_counter()
            with open_warehouse(inputs.warehouse) as wh:
                streamed = stream_seed(
                    wh, day, rules, secret, cfg.live.recent_post_days,
                    cfg.features.sponsor_alias_min_trials, cfg.features.sponsor_alias_wait_days,
                    tick=renew,
                )  # fmt: skip
            report["counts"] = {**streamed.counts, "markers": len(streamed.markers),
                                "sponsor_keys": len(streamed.sponsors.key_numbers),
                                "sponsor_trials": len(streamed.sponsors.thin),
                                "queued_states": len(streamed.sponsors.queue)}  # fmt: skip
            mark(stage, tick)

            stage, tick = "hashes", clock.perf_counter()
            hashes = {n: str(w.row["feature_hash"]) for n, w in streamed.wides.items()}
            for frame in (scored.scores, scored.unscored):
                missing = set(frame["nct_id"]) - set(hashes)
                if missing:
                    raise RefusedError(f"{len(missing)} trials of the cutoff's population have "
                                       "no wide row")  # fmt: skip
                frame["content_hash"] = frame["nct_id"].map(hashes)
            counts = reasons.counts_from_files(inputs.warehouse, inputs.outcomes)
            report["counts"].update(scored=len(scored.scores), unscored=len(scored.unscored),
                                    reason_count_rows=len(counts),
                                    labeled_early_stops=sum(c[3] for c in counts))  # fmt: skip
            renew()
            mark(stage, tick)

            stage, tick = "prepare", clock.perf_counter()
            rows = store.prepare_bootstrap(cur, run_id, day, streamed.wides, streamed.sponsors,
                                           streamed.markers, scored, counts)  # fmt: skip
            del streamed, scored, hashes
            gc.collect()
            renew()
            mark(stage, tick)

            stage, tick = "write", clock.perf_counter()
            with con.transaction():
                store.load_bootstrap(cur, rows, rebuild=rebuild)
            write_seconds = clock.perf_counter() - tick
            mark(stage, tick)
            sizes = rows.sizes()
            sent = sum(s["bytes"] for s in sizes.values())
            report["sent"] = {"tables": sizes, "total_bytes": sent}
            report["write_seconds"] = round(write_seconds, 2)
            report["stored"] = _stored(cur, [*sizes, "monitoring.pipeline_runs"])
            report["neon_estimate"] = neon_estimate(sent, write_seconds)
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt | SystemExit):
            raise
        if run_id is not None and not daily.lost_lease(exc):
            daily.record_failure(dsn, run_id, {}, f"{stage}: {type(exc).__name__} "
                                 f"{getattr(exc, 'sqlstate', '') or ''}".strip())  # fmt: skip
        if isinstance(exc, RefusedError):
            raise
        raise daily.JobFailedError(daily.failure_line(stage, exc), type(exc).__name__) from None
    finally:
        con.close()
    report["seconds"]["total"] = round(clock.perf_counter() - started, 1)
    report["memory_mb"]["end"] = memory_mb()
    report["next"] = DISPLAY_NEXT
    say(DISPLAY_NEXT)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    from trialpulse.ingest.history import refuse_on_a_runner

    parser = argparse.ArgumentParser(description="Bootstrap the live system (Step 14).")
    parser.add_argument("--target", choices=("local", "neon"), required=True)
    parser.add_argument("--rebuild", action="store_true",
                        help="clear the live state and start it again")  # fmt: skip
    args = parser.parse_args(argv)
    refuse_on_a_runner()  # ADR 0040: the warehouse never reaches a GitHub runner
    key = Secrets().sponsor_id_key
    if key is None:
        raise RefusedError("SPONSOR_ID_KEY is not set (environment or .env)")
    from trialpulse.live.daily import _dsn

    cfg = load_project_config()
    report = run_bootstrap(cfg, _dsn(args.target), key.get_secret_value().encode("utf-8"),
                           args.rebuild, Inputs.default(),
                           say=lambda line: print(line, flush=True))  # fmt: skip
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
