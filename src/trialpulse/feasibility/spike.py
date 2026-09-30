"""Step 2 feasibility spike, runnable per part.

    uv run python -m trialpulse.feasibility.spike --part all
    uv run python -m trialpulse.feasibility.spike --part g
    uv run python -m trialpulse.feasibility.spike --part a --pin-latest

Parts: a download, b profile, c cohort counts, d automated check against API v2,
e manual check checklist, f stability audit of list fields, g API v2 check, i version
history against the official change logs (from the part f cache, no request), h report.
"all" runs them in dependency order: a, b, c, g, d, e, f, i, h.

Every part writes data/spike/results/part_<x>.json with a status ("done", "blocked",
"pending_manual" or "failed") and its numbers. A blocked part does not stop the others.
API responses and downloads are cached under data/, so a rerun skips finished work.
"""

import argparse
import csv
import datetime as dt
import gzip
import json
import logging
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import httpx

from trialpulse.config import (
    PROJECT_CONFIG_PATH,
    REPO_ROOT,
    ProjectConfig,
    Secrets,
    load_project_config,
)
from trialpulse.feasibility import ctgov_v2
from trialpulse.feasibility.dataset import (
    BlockedError,
    cohort_counts,
    cohort_ids,
    download_core,
    parquet_glob,
    pin_cutoff_in_config,
    profile,
    write_data_dictionary,
)
from trialpulse.feasibility.fetch import JsonCache, JsonFetcher, OfflineFetcher, RateLimiter

log = logging.getLogger("trialpulse.feasibility")

DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw" / "history"
SPIKE_DIR = DATA_DIR / "spike"
RESULTS_DIR = SPIKE_DIR / "results"
CACHE_DIR = SPIKE_DIR / "cache"
LOG_DIR = SPIKE_DIR / "logs"
CURRENT_FIELDS_PARQUET = SPIKE_DIR / "current_fields.parquet"
MANUAL_CHECKLIST = SPIKE_DIR / "part_e_checklist.csv"
DOCS_DIR = REPO_ROOT / "docs"
MANUAL_RESULTS_CSV = DOCS_DIR / "feasibility_manual_check.csv"
REPORT_PATH = DOCS_DIR / "feasibility_report.md"
DATA_DICTIONARY_PATH = DOCS_DIR / "data_dictionary_history.md"

PART_ORDER = ("a", "b", "c", "g", "d", "e", "f", "i", "h")

# Sample sizes and windows from the Step 2 specification (CLAUDE.md Section 18).
AUTOMATED_CHECK_SAMPLE = 200
MANUAL_CHECK_SAMPLE = 10
STABILITY_SAMPLE = 150
MANUAL_STABILITY_FALLBACK_SAMPLE = 20
DELTA_WINDOW_DAYS = 7
# The version 0 fields Aakrisht compares with the Record History tab in part e. The version's
# submitted date identifies the matching row on that tab.
MANUAL_CHECK_FIELDS: tuple[str, ...] = (
    "last_update_submit_date",
    "overall_status",
    "study_type",
    "study_first_post_date",
    "start_date",
    "start_date_type",
    "primary_completion_date",
    "primary_completion_date_type",
    "enrollment_count",
    "enrollment_type",
    "lead_sponsor_class",
)


@dataclass
class Context:
    cfg: ProjectConfig
    hf_token: str | None
    pin_latest: bool = False
    offline: bool = False
    config_path: Path = PROJECT_CONFIG_PATH
    _client: httpx.Client | None = field(default=None, repr=False)

    @property
    def client(self) -> httpx.Client:
        # httpx's default User-Agent; ClinicalTrials.gov's edge rejected a custom one.
        if self._client is None:
            self._client = httpx.Client(timeout=120, follow_redirects=True)
        return self._client

    def fetcher(self, per_minute: float) -> JsonFetcher:
        # 8 attempts: backoff reaches the 2-minute cap, riding out short DNS outages.
        return JsonFetcher(self.client, RateLimiter(per_minute), max_attempts=8)


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def result_path(part: str) -> Path:
    return RESULTS_DIR / f"part_{part}.json"


def save_result(part: str, status: str, data: dict[str, Any]) -> dict[str, Any]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"part": part, "status": status, "finished_at": _now(), **data}
    result_path(part).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


def load_result(part: str) -> dict[str, Any] | None:
    path = result_path(part)
    if not path.is_file():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _require_done(part: str, needed_for: str) -> dict[str, Any]:
    result = load_result(part)
    if result is None or result.get("status") != "done":
        raise BlockedError(f"part {part} must be done before {needed_for}")
    return result


def _dataset_glob(ctx: Context) -> str:
    a = _require_done("a", "this part (the dataset is not downloaded)")
    pinned = ctx.cfg.dataset.revision
    if pinned and str(a["revision"]) != pinned:
        raise BlockedError(
            f"the downloaded revision {a['revision']} is not the pinned {pinned}; rerun part a"
        )
    return parquet_glob(RAW_DIR, str(a["revision"]), ctx.cfg.dataset.config_name)


# Part a -----------------------------------------------------------------------------


def part_a(ctx: Context) -> dict[str, Any]:
    if not ctx.hf_token:
        raise BlockedError("HF_TOKEN is not set in .env")
    return download_core(ctx.cfg, RAW_DIR, ctx.hf_token, ctx.pin_latest)


# Parts b and c ----------------------------------------------------------------------


def part_b(ctx: Context) -> dict[str, Any]:
    glob = _dataset_glob(ctx)
    a = _require_done("a", "part b")
    with duckdb.connect() as con:
        prof = profile(con, glob)
    write_data_dictionary(prof, str(a["revision"]), DATA_DICTIONARY_PATH)
    return prof


def record_cutoff(cfg: ProjectConfig, config_path: Path, cutoff: str) -> str:
    """Write the data cutoff into project.yaml the first time it is computed. A recorded
    cutoff that disagrees with the pinned revision's data is an error, never overwritten."""
    measured = dt.date.fromisoformat(cutoff[:10])
    if cfg.dataset.cutoff is None:
        pin_cutoff_in_config(config_path, measured)
        return "recorded"
    if cfg.dataset.cutoff != measured:
        raise ValueError(
            f"config cutoff {cfg.dataset.cutoff} differs from the pinned revision's {measured}"
        )
    return "already recorded"


def part_c(ctx: Context) -> dict[str, Any]:
    glob = _dataset_glob(ctx)
    with duckdb.connect() as con:
        result = cohort_counts(con, glob, ctx.cfg)
    if result["data_cutoff"]:
        result["cutoff_in_config"] = record_cutoff(ctx.cfg, ctx.config_path, result["data_cutoff"])
    return result


# Part g -----------------------------------------------------------------------------


def _pull(
    fetcher: JsonFetcher,
    cache: JsonCache,
    name: str,
    params: dict[str, str | int],
    on_page: Callable[[list[dict[str, Any]]], None],
) -> dict[str, Any]:
    prefix = ctgov_v2.query_cache_prefix(name, params)
    start = time.monotonic()
    pages = cached_pages = records = 0
    total: int | None = None
    for page in ctgov_v2.iter_pages(fetcher, cache, prefix, params):
        pages += 1
        cached_pages += int(page.from_cache)
        records += len(page.studies)
        if page.index == 0:
            total = page.total_count
        on_page(page.studies)
        if pages % 20 == 0:
            log.info("%s: %d pages, %d records (of %s)", name, pages, records, total)
    return {
        "filter": params.get("filter.advanced"),
        "total_count": total,
        "pages": pages,
        "pages_from_cache": cached_pages,
        "records": records,
        "elapsed_seconds_this_run": round(time.monotonic() - start, 1),
        "requests_this_run": pages - cached_pages,
    }


def _write_bulk_parquet(ndjson: Path, out: Path) -> None:
    columns = ", ".join(f"'{k}': '{v}'" for k, v in ctgov_v2.BULK_COLUMNS.items())
    tmp = out.with_suffix(".tmp.parquet")
    with duckdb.connect() as con:
        con.execute(
            f"""COPY (
                SELECT * FROM read_json('{ndjson.as_posix()}', format = 'newline_delimited',
                                        columns = {{{columns}}})
                QUALIFY row_number() OVER (
                    PARTITION BY nct_id ORDER BY last_update_post_date DESC NULLS LAST) = 1
                ORDER BY nct_id
            ) TO '{tmp.as_posix()}' (FORMAT parquet, COMPRESSION zstd)"""
        )
    tmp.replace(out)


def _bulk_summary(cfg: ProjectConfig, parquet: Path) -> dict[str, Any]:
    early = ", ".join(f"'{s}'" for s in cfg.statuses.early_stop)
    with duckdb.connect() as con:
        row = con.execute(
            f"""SELECT count(*),
                avg(CASE WHEN len(phases) > 0 THEN 1 ELSE 0 END),
                avg(CASE WHEN len(conditions) > 0 THEN 1 ELSE 0 END),
                avg(CASE WHEN len(browse_branches) > 0 THEN 1 ELSE 0 END),
                avg(CASE WHEN len(mesh_terms) > 0 THEN 1 ELSE 0 END),
                avg(CASE WHEN len(mesh_ancestors) > 0 THEN 1 ELSE 0 END),
                avg(CASE WHEN n_interventions > 0 THEN 1 ELSE 0 END),
                avg(CASE WHEN n_arm_groups > 0 THEN 1 ELSE 0 END),
                avg(CASE WHEN n_locations > 0 THEN 1 ELSE 0 END),
                sum(CASE WHEN overall_status IN ({early}) THEN 1 ELSE 0 END),
                sum(CASE WHEN overall_status IN ({early})
                         AND nullif(trim(why_stopped), '') IS NOT NULL THEN 1 ELSE 0 END)
            FROM read_parquet('{parquet.as_posix()}')"""
        ).fetchone()
    assert row is not None
    trials, phases, conds, branches, mesh, ancestors, interv, arms, locs, stops, reasons = row
    return {
        "trials": int(trials),
        "share_with_phases": float(phases),
        "share_with_conditions": float(conds),
        "share_with_browse_branches": float(branches),
        "share_with_mesh_terms": float(mesh),
        "share_with_mesh_ancestors": float(ancestors),
        "share_with_interventions": float(interv),
        "share_with_arm_groups": float(arms),
        "share_with_locations": float(locs),
        "current_early_stops": int(stops),
        "current_why_stopped_coverage": int(reasons) / int(stops) if stops else None,
        "file_bytes": parquet.stat().st_size,
    }


def part_g(ctx: Context) -> dict[str, Any]:
    fetcher = ctx.fetcher(ctgov_v2.REQUESTS_PER_MINUTE)
    cache = JsonCache(CACHE_DIR / "api_v2")
    version = fetcher.get(ctgov_v2.VERSION_URL)

    # 1. Delta pull: the last 7 days of updates, with every field TrialPulse will ingest.
    since = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=DELTA_WINDOW_DAYS)
    delta_params: dict[str, str | int] = {
        "filter.advanced": ctgov_v2.last_update_filter(since),
        "fields": ctgov_v2.fields_param(ctgov_v2.FIELD_PATHS),
        "pageSize": ctgov_v2.MAX_PAGE_SIZE,
    }
    delta_studies: list[dict[str, Any]] = []
    delta = _pull(fetcher, cache, "delta", delta_params, delta_studies.extend)
    delta["since"] = since.isoformat()
    delta["unique_nct_ids"] = len(
        {ctgov_v2.get_path(s, "protocolSection.identificationModule.nctId") for s in delta_studies}
    )
    presence = ctgov_v2.field_presence(delta_studies)

    # 2. Bulk pull of current-record fields for the whole cohort, into Parquet.
    bulk_params: dict[str, str | int] = {
        "filter.advanced": ctgov_v2.cohort_filter(
            ctx.cfg.population.study_type, ctx.cfg.population.min_first_post_date
        ),
        "fields": ctgov_v2.fields_param(ctgov_v2.BULK_FIELD_NAMES),
        "pageSize": ctgov_v2.MAX_PAGE_SIZE,
    }
    SPIKE_DIR.mkdir(parents=True, exist_ok=True)
    ndjson = SPIKE_DIR / "current_fields.ndjson.gz"
    with gzip.open(ndjson, "wt", encoding="utf-8") as out:

        def write_rows(studies: list[dict[str, Any]]) -> None:
            for study in studies:
                out.write(json.dumps(ctgov_v2.normalize_bulk_record(study)) + "\n")

        bulk = _pull(fetcher, cache, "cohort_bulk", bulk_params, write_rows)
    _write_bulk_parquet(ndjson, CURRENT_FIELDS_PARQUET)
    ndjson.unlink()
    bulk["parquet"] = CURRENT_FIELDS_PARQUET.relative_to(REPO_ROOT).as_posix()
    bulk["summary"] = _bulk_summary(ctx.cfg, CURRENT_FIELDS_PARQUET)
    return {
        "api_version": version,
        "delta": delta,
        "field_presence": presence,
        "bulk": bulk,
    }


# Part d -----------------------------------------------------------------------------


def check_records_after_cutoff(version: dict[str, Any], cutoff: dt.date) -> str:
    """The API data timestamp of the part d records, refused if it predates the dataset
    cutoff: older records would miss versions posted before the cutoff, and part d would
    count them as mismatches."""
    stamp = str(version.get("dataTimestamp") or "")
    if not stamp or dt.date.fromisoformat(stamp[:10]) < cutoff:
        raise BlockedError(f"API records dated {stamp or 'unknown'} predate the cutoff {cutoff}")
    return stamp


def _api_rows(
    nct_ids: list[str], fetcher: JsonFetcher, cache: JsonCache
) -> dict[str, dict[str, Any]]:
    """Official current records for the given trials: one cached API v2 call per trial."""
    rows: dict[str, dict[str, Any]] = {}
    fields = ctgov_v2.fields_param(ctgov_v2.BULK_FIELD_NAMES)
    for nct_id in nct_ids:
        study = cache.get(nct_id)
        if study is None:
            try:
                study = fetcher.get(f"{ctgov_v2.STUDIES_URL}/{nct_id}", {"fields": fields})
            except httpx.HTTPStatusError as exc:
                log.warning("no official record for %s: %s", nct_id, exc)
                continue
            cache.put(nct_id, study)
        rows[nct_id] = ctgov_v2.normalize_bulk_record(study)
    return rows


def part_d(ctx: Context) -> dict[str, Any]:
    from trialpulse.feasibility.checks import compare_trial, seeded_sample, summarize_comparisons
    from trialpulse.feasibility.dataset import latest_rows

    glob = _dataset_glob(ctx)
    c = _require_done("c", "part d")
    cutoff = dt.date.fromisoformat(str(c["data_cutoff"])[:10])
    with duckdb.connect() as con:
        cohort_counts(con, glob, ctx.cfg)
        sample = seeded_sample(cohort_ids(con), AUTOMATED_CHECK_SAMPLE, ctx.cfg.seeds.default)
        dataset_rows = latest_rows(con, glob, sample)
    # One cached API v2 call per sampled trial, per pinned revision, never the part g bulk
    # pull: its cached pages keep their original date whatever the run's timestamp says.
    a = _require_done("a", "part d")
    fetcher = ctx.fetcher(ctgov_v2.REQUESTS_PER_MINUTE)
    single = JsonCache(CACHE_DIR / "api_v2" / f"single_{a['revision']}")
    version = single.get("_api_version")  # the timestamp of the first fetch, kept on reruns
    if version is None:
        version = fetcher.get(ctgov_v2.VERSION_URL)
        single.put("_api_version", version)
    source = {
        "kind": "one API v2 call per sampled trial, fetched after the cutoff",
        "data_timestamp": check_records_after_cutoff(version, cutoff),
    }
    api_rows = _api_rows(sample, fetcher, single)
    comparisons = [
        compare_trial(
            nct_id, dataset_rows.get(nct_id), api_rows.get(nct_id), cutoff, ctx.cfg.statuses.open
        )
        for nct_id in sample
    ]
    summary = summarize_comparisons(comparisons)
    summary["sample"] = sample
    summary["data_cutoff"] = cutoff.isoformat()
    summary["api_source"] = source
    return summary


# Part e -----------------------------------------------------------------------------


def _at_precision(value: Any, precision: Any) -> str | None:
    """A date as the registry shows it: '2009', '2009-03' or '2009-03-14'."""
    if value is None:
        return None
    text = str(value)[:10]
    keep = {"year": 4, "month": 7}.get(str(precision or "day").casefold(), 10)
    return text[:keep]


def checklist_row(row: dict[str, Any]) -> dict[str, Any]:
    """One trial of the part e checklist: its Record History link and its version 0 values,
    with dates at the precision the dataset records."""
    out: dict[str, Any] = {
        "nct_id": row["nct_id"],
        "record_history_url": f"https://clinicaltrials.gov/study/{row['nct_id']}?tab=history",
    }
    for name in MANUAL_CHECK_FIELDS:
        value = row.get(name)
        if name in ("start_date", "primary_completion_date"):
            out[name] = _at_precision(value, row.get(f"{name}_precision"))
        else:
            out[name] = None if value is None else str(value)[:10] if "date" in name else str(value)
    return out


def write_manual_results(path: Path, chosen: list[str]) -> None:
    """The committed file where Aakrisht records pass or fail, one row per chosen trial. It is
    rewritten while no result is recorded; recorded results for other trials are never
    overwritten."""
    if path.is_file():
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if [r["nct_id"] for r in rows] == chosen:
            return
        if any((r.get("result") or "").strip() for r in rows):
            raise BlockedError(
                f"{path.name} holds recorded results for other trials; move it aside first"
            )
    with path.open("w", newline="", encoding="utf-8") as fh:
        results = csv.writer(fh)
        results.writerow(["nct_id", "result", "notes"])
        for nct_id in chosen:
            results.writerow([nct_id, "", ""])


def part_e(ctx: Context) -> dict[str, Any]:
    """Prepare Aakrisht's manual check: 10 of the part d trials, their dataset version 0
    values (kept under data/, which is gitignored) and a committed results file with
    ids only, where he records pass or fail."""
    from trialpulse.feasibility.checks import seeded_sample

    d = _require_done("d", "part e")
    glob = _dataset_glob(ctx)
    chosen = seeded_sample(d["sample"], MANUAL_CHECK_SAMPLE, ctx.cfg.seeds.default)
    ids = ", ".join(f"'{i}'" for i in chosen)
    with duckdb.connect() as con:
        cur = con.execute(
            f"""SELECT nct_id, {", ".join(MANUAL_CHECK_FIELDS)},
                start_date_precision, primary_completion_date_precision
            FROM read_parquet('{glob}') WHERE nct_version = 0 AND nct_id IN ({ids})
            ORDER BY nct_id"""
        )
        names = [d_[0] for d_ in cur.description]
        rows = [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    checklist = [checklist_row(row) for row in rows]
    MANUAL_CHECKLIST.parent.mkdir(parents=True, exist_ok=True)
    with MANUAL_CHECKLIST.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["nct_id", "record_history_url", *MANUAL_CHECK_FIELDS]
        )
        writer.writeheader()
        writer.writerows(checklist)
    write_manual_results(MANUAL_RESULTS_CSV, chosen)
    return {
        "status_override": "pending_manual",
        "trials": chosen,
        "fields": list(MANUAL_CHECK_FIELDS),
        "rows": checklist,
        "checklist": MANUAL_CHECKLIST.relative_to(REPO_ROOT).as_posix(),
        "results_file": MANUAL_RESULTS_CSV.relative_to(REPO_ROOT).as_posix(),
    }


# Part f -----------------------------------------------------------------------------


def _stability_pool(ctx: Context) -> tuple[list[str], str]:
    a = load_result("a")
    if a is not None and a.get("status") == "done":
        with duckdb.connect() as con:
            cohort_counts(con, _dataset_glob(ctx), ctx.cfg)
            return cohort_ids(con), "dataset cohort"
    if CURRENT_FIELDS_PARQUET.is_file():
        with duckdb.connect() as con:
            ids = con.execute(
                f"SELECT nct_id FROM read_parquet('{CURRENT_FIELDS_PARQUET.as_posix()}')"
            ).fetchall()
        return [str(r[0]) for r in ids], "part g bulk pull (API v2 cohort)"
    raise BlockedError("no cohort to sample from: run part a or part g first")


def part_f(ctx: Context) -> dict[str, Any]:
    from trialpulse.feasibility.checks import seeded_sample
    from trialpulse.feasibility.history_api import run_stability_audit

    pool, source = _stability_pool(ctx)
    sample = seeded_sample(pool, STABILITY_SAMPLE, ctx.cfg.seeds.default)
    fallback = seeded_sample(sample, MANUAL_STABILITY_FALLBACK_SAMPLE, ctx.cfg.seeds.default)
    previous = load_result("f")
    result = run_stability_audit(
        ctx.client,
        JsonCache(CACHE_DIR / "history"),
        sample,
        manual_fallback=fallback,
        fetcher=OfflineFetcher() if ctx.offline else None,
    )
    result["sample_source"] = source
    result["offline"] = ctx.offline
    # A cache-only rerun keeps the cost of the run that actually fetched the data.
    if ctx.offline and previous is not None and previous.get("status") == "done":
        result["original_run"] = previous.get("original_run") or {
            key: previous.get(key)
            for key in ("requests_this_run", "elapsed_minutes_this_run", "finished_at")
        }
    return result


# Part i -----------------------------------------------------------------------------


def part_i(ctx: Context) -> dict[str, Any]:
    """The dataset's versions against the official change logs that part f cached: version
    lists, each version's submitted date and each version's status. Reads the cache only; it
    never makes a request."""
    from trialpulse.feasibility.checks import (
        compare_version_history,
        summarize_version_histories,
    )

    glob = _dataset_glob(ctx)
    history = JsonCache(CACHE_DIR / "history")
    logs: dict[str, list[dict[str, Any]]] = {}
    if history.root.is_dir():
        for folder in sorted(p for p in history.root.iterdir() if p.is_dir()):
            log = history.get(f"{folder.name}/changes")
            if log is not None:
                logs[folder.name] = list(log)
    if not logs:
        raise BlockedError("the part f cache holds no change logs")
    ids = ", ".join(f"'{i}'" for i in logs)
    with duckdb.connect() as con:
        rows = con.execute(
            f"""SELECT nct_id, nct_version, last_update_submit_date, overall_status
            FROM read_parquet('{glob}', union_by_name = true) WHERE nct_id IN ({ids})"""
        ).fetchall()
    versions: dict[str, list[dict[str, Any]]] = {}
    for nct_id, version, submitted, status in rows:
        versions.setdefault(str(nct_id), []).append(
            {"version": version, "submitted": submitted, "status": status}
        )
    per_trial = {
        nct_id: compare_version_history(versions.get(nct_id, []), log, ctx.cfg.statuses.open)
        for nct_id, log in logs.items()
    }
    summary = summarize_version_histories(per_trial)
    summary["trials_missing_from_dataset"] = sorted(set(logs) - set(versions))
    return summary


# Part h -----------------------------------------------------------------------------


def part_h(ctx: Context) -> dict[str, Any]:
    from trialpulse.feasibility.report import read_manual_results, render_report

    results = {part: load_result(part) for part in PART_ORDER if part != "h"}
    manual = read_manual_results(MANUAL_RESULTS_CSV)
    REPORT_PATH.write_text(render_report(results, manual, ctx.cfg), encoding="utf-8")
    return {"report": REPORT_PATH.relative_to(REPO_ROOT).as_posix()}


PARTS: dict[str, Callable[[Context], dict[str, Any]]] = {
    "a": part_a,
    "b": part_b,
    "c": part_c,
    "d": part_d,
    "e": part_e,
    "f": part_f,
    "g": part_g,
    "h": part_h,
    "i": part_i,
}


def run_part(part: str, ctx: Context) -> dict[str, Any]:
    log.info("part %s: start", part)
    try:
        data = PARTS[part](ctx)
    except BlockedError as exc:
        log.warning("part %s: blocked: %s", part, exc)
        return save_result(part, "blocked", {"reason": str(exc)})
    except Exception as exc:  # recorded and reported; the remaining parts still run
        log.exception("part %s: failed", part)
        return save_result(part, "failed", {"error": f"{type(exc).__name__}: {exc}"})
    status = str(data.pop("status_override", "done"))
    log.info("part %s: %s", part, status)
    return save_result(part, status, data)


def _parse_parts(value: str) -> list[str]:
    if value == "all":
        return list(PART_ORDER)
    parts = [p.strip() for p in value.split(",") if p.strip()]
    unknown = [p for p in parts if p not in PARTS]
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown part(s): {', '.join(unknown)}")
    return parts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--part", type=_parse_parts, required=True, help="a..i, a list, or all")
    parser.add_argument(
        "--pin-latest",
        action="store_true",
        help="part a: pin the latest dataset tag in config/project.yaml if none is pinned",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="part f: use cached responses only; any request raises an error",
    )
    args = parser.parse_args(argv)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(LOG_DIR / "spike.log", encoding="utf-8"),
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request
    secrets = Secrets()
    token = secrets.hf_token.get_secret_value() if secrets.hf_token else None
    ctx = Context(
        cfg=load_project_config(),
        hf_token=token,
        pin_latest=args.pin_latest,
        offline=args.offline,
    )
    statuses = {part: run_part(part, ctx)["status"] for part in args.part}
    for part, status in statuses.items():
        print(f"part {part}: {status}")
    return 1 if "failed" in statuses.values() else 0


if __name__ == "__main__":
    raise SystemExit(main())
