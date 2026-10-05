"""Build the offline warehouse, data/warehouse.duckdb, from the pinned dataset revision
(CLAUDE.md Step 3).

    uv run python -m trialpulse.warehouse.build

Tables:

- raw_versions: the Parquet rows with each loaded column typed explicitly. Only columns that a
  planned feature, label or audit needs are loaded; no personal-data column, no free text and
  no sponsor name is written.
- texts: every distinct normalized text (trialpulse.contracts.text), keyed by its SHA-256.
- versions: the canonical version rows (trialpulse.contracts.versions) that pass validation.
- versions_quarantine: the rows that fail it, with the reasons. Not used downstream.
- trials: one row per trial: time zero, first and last version, latest status.
- build_info: the dataset revision and the build's counts.

The build writes a new file and replaces the old one only when it is complete, so a failed
build never leaves a half-written warehouse. Every table is deterministic: building twice from
the same revision gives the same row counts and checksums, and the command says whether they
match the previous build.
"""

import argparse
import datetime as dt
import json
import logging
import os
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import Executor, Future, ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import astuple
from itertools import pairwise
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa

from trialpulse.cli import RefusedError, run
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.contracts.sponsor import sponsor
from trialpulse.contracts.text import EMAIL_MARKER, is_html
from trialpulse.contracts.versions import (
    COLUMN_TYPES,
    ENUM_ALIASES,
    FIRST_INVALID_DATE,
    FIRST_VALID_DATE,
    PRECISION_DATES,
    TEXT_FIELDS,
    TEXT_SCHEMA,
    age_in_years,
    content_hashes,
    hash_text,
    split_valid,
    typed_frame,
)

log = logging.getLogger(__name__)

WAREHOUSE_PATH = REPO_ROOT / "data" / "warehouse.duckdb"
BUILD_LOG_PATH = REPO_ROOT / "data" / "warehouse_build.json"
RAW_DIR = REPO_ROOT / "data" / "raw" / "history"
TEMP_DIR = REPO_ROOT / "data" / "duckdb_tmp"
SCHEMA_VERSION = "2"  # 2: last_known_status (ADR 0014)
TABLES: tuple[str, ...] = (
    "raw_versions",
    "texts",
    "versions",
    "versions_quarantine",
    "trials",
    "build_info",
)
TEXT_BATCH = 5_000  # texts per worker task
TRIALS_PER_CHUNK = 30_000  # about 220,000 versions per validation chunk

# Columns loaded into raw_versions: name -> (explicit type, planned use).
RAW_COLUMNS: dict[str, tuple[str, str]] = {
    "nct_id": ("VARCHAR", "identity"),
    "nct_version": ("BIGINT", "identity and version order"),
    "last_update_post_date": ("DATE", "version clock (Section 6)"),
    "last_update_post_date_type": ("VARCHAR", "ESTIMATED post-date share (Step 5)"),
    "last_update_submit_date": ("DATE", "version order audit"),
    "overall_status": ("VARCHAR", "outcomes (Step 4)"),
    "last_known_status": ("VARCHAR", "submitted status behind UNKNOWN (Step 4, ADR 0014)"),
    "status_verified_date": ("DATE", "UNKNOWN rule (Step 4), amendment signals (Step 7)"),
    "study_type": ("VARCHAR", "population (Step 4)"),
    "study_first_post_date": ("DATE", "time zero (Section 6)"),
    "study_first_post_date_type": ("VARCHAR", "ESTIMATED post-date share (Step 5)"),
    "study_first_submit_date": ("DATE", "registration lag (Step 5)"),
    "start_date": ("DATE", "design and amendment features (Step 7)"),
    "start_date_precision": ("VARCHAR", "date precision flag"),
    "start_date_type": ("VARCHAR", "start actual or anticipated (Step 7)"),
    "primary_completion_date": ("DATE", "planned duration, date slips (Step 7)"),
    "primary_completion_date_precision": ("VARCHAR", "date precision flag"),
    "primary_completion_date_type": ("VARCHAR", "date slips (Step 7)"),
    "completion_date": ("DATE", "date slips (Step 7), UNKNOWN rule (Step 4)"),
    "completion_date_precision": ("VARCHAR", "date precision flag"),
    "completion_date_type": ("VARCHAR", "date slips (Step 7)"),
    "allocation": ("VARCHAR", "design (Step 7)"),
    "intervention_model": ("VARCHAR", "design (Step 7)"),
    "primary_purpose": ("VARCHAR", "design (Step 7)"),
    "masking": ("VARCHAR", "design (Step 7)"),
    "healthy_volunteers": ("BOOLEAN", "design (Step 7)"),
    "sex": ("VARCHAR", "design (Step 7)"),
    "minimum_age": ("VARCHAR", "age limits (Step 7)"),
    "maximum_age": ("VARCHAR", "age limits (Step 7)"),
    "enrollment_count": ("BIGINT", "enrollment target and changes (Step 7)"),
    "enrollment_type": ("VARCHAR", "enrollment type (Step 7)"),
    "lead_sponsor_class": ("VARCHAR", "sponsor (Step 7)"),
    "organization_class": ("VARCHAR", "sponsor (Step 7)"),
    # Columns whose Parquet type differs between files: typed here, used by the audit only.
    "disp_first_submit_qc_date": ("DATE", "schema drift audit"),
    "is_ppsd": ("BOOLEAN", "schema drift audit"),
    "fdaaa801_violation": ("BOOLEAN", "schema drift audit"),
}
DRIFT_COLUMNS: tuple[str, ...] = ("disp_first_submit_qc_date", "is_ppsd", "fdaaa801_violation")
# Read during the build but never written as raw values.
DERIVED_FROM: dict[str, str] = {
    **dict.fromkeys(TEXT_FIELDS, "normalized into texts, referenced by hash"),
    "lead_sponsor_name": "reduced to a name and key, both empty for individuals",
}
# Personal-data columns of the core config: never read.
PERSONAL_COLUMNS: tuple[str, ...] = (
    "responsible_party_investigator_full_name",
    "responsible_party_investigator_title",
    "responsible_party_investigator_affiliation",
    "responsible_party_old_name_title",
)


def dataset_glob(cfg: ProjectConfig, raw_dir: Path = RAW_DIR) -> str:
    revision = cfg.dataset.revision
    if not revision:
        raise RefusedError("no dataset revision is pinned in config/project.yaml")
    folder = raw_dir / revision / cfg.dataset.config_name
    if not any(folder.glob("*.parquet")):
        raise RefusedError(
            f"the pinned revision {revision} is not downloaded to {folder}; "
            "run: uv run python -m trialpulse.feasibility.spike --part a"
        )
    return (folder / "*.parquet").as_posix()


def _source(glob: str, extra: str = "") -> str:
    return f"read_parquet('{glob}', union_by_name=true{extra})"


def _sql_enum(column: str) -> str:
    """SQL twin of contracts.versions.canonical_enum (tested against it)."""
    value = f"upper(trim({column}))"
    aliases = " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in ENUM_ALIASES.items())
    return f"(CASE {value} {aliases} WHEN '' THEN NULL ELSE {value} END)"


def _sql_valid(column: str) -> str:
    """SQL twin of contracts.versions.valid_date."""
    return (
        f"(CASE WHEN {column} >= DATE '{FIRST_VALID_DATE}' "
        f"AND {column} < DATE '{FIRST_INVALID_DATE}' THEN {column} END)"
    )


# Raw rows --------------------------------------------------------------------------------


def load_raw(con: duckdb.DuckDBPyConnection, glob: str) -> None:
    """raw_versions (persistent, typed) and row_keys (temporary: per-row text keys and the raw
    sponsor name, which never reaches the file)."""
    columns = ",\n  ".join(f"CAST({c} AS {kind}) AS {c}" for c, (kind, _) in RAW_COLUMNS.items())
    origin = "regexp_extract(filename, '[^/\\\\]+$') AS source_file, file_row_number AS source_row"
    src = _source(glob, ", filename=true, file_row_number=true")
    con.execute(
        f"""CREATE TABLE raw_versions AS SELECT {columns},
          {origin}
        FROM {src} ORDER BY nct_id, nct_version, source_file, source_row"""
    )
    keys = ", ".join(f"md5_number({f}) AS {f}_key" for f in TEXT_FIELDS)
    con.execute(
        f"""CREATE TEMP TABLE row_keys AS SELECT {origin}, {keys},
          lead_sponsor_name AS raw_sponsor_name, lead_sponsor_class AS raw_sponsor_class
        FROM {src}"""
    )


# Texts -----------------------------------------------------------------------------------


def normalize_batch(batch: pa.Table) -> pa.Table:
    """Normalize a batch of raw texts (columns rid, raw): the normalized text and its hash
    (both empty when nothing is left) and whether the raw text was HTML."""
    rids = batch.column("rid").to_pylist()
    raws = batch.column("raw").to_pylist()
    normalized = [hash_text(raw) for raw in raws]
    return pa.table(
        {
            "rid": pa.array(rids, pa.int64()),
            "text_hash": pa.array([key for _, key in normalized], pa.string()),
            "text": pa.array([clean for clean, _ in normalized], pa.string()),
            "html": pa.array([is_html(raw) for raw in raws], pa.bool_()),
        }
    )


class _InlineExecutor(Executor):
    """Runs each task at once in this process (workers=1, and the tests)."""

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        future: Future[Any] = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as error:
            future.set_exception(error)
        return future


@contextmanager
def _executor(workers: int) -> Iterator[Executor]:
    pool: Executor = ProcessPoolExecutor(max_workers=workers) if workers > 1 else _InlineExecutor()
    try:
        yield pool
    finally:
        pool.shutdown()


def _ordered(
    pool: Executor, fn: Callable[[Any], Any], items: Iterable[Any], window: int
) -> Iterator[Any]:
    """fn over items in the pool, in order, with at most `window` items in flight (so a
    generator of large frames is never loaded all at once)."""
    pending: deque[Future[Any]] = deque()
    for item in items:
        pending.append(pool.submit(fn, item))
        if len(pending) >= window:
            yield pending.popleft().result()
    while pending:
        yield pending.popleft().result()


def build_texts(
    con: duckdb.DuckDBPyConnection, glob: str, pool: Executor, workers: int
) -> dict[str, int]:
    """Normalize every distinct raw text once. Fills the temporary text_keys (field, raw key,
    text hash) and the persistent texts table."""
    con.execute("CREATE TEMP TABLE text_keys (field VARCHAR, raw_key UHUGEINT, text_hash VARCHAR)")
    con.execute("CREATE TEMP TABLE text_stage (text_hash VARCHAR, text VARCHAR)")
    stats: dict[str, int] = {}
    for field in TEXT_FIELDS:
        con.execute(
            f"""CREATE OR REPLACE TEMP TABLE raw_text AS
            SELECT row_number() OVER (ORDER BY raw_key) AS rid, raw_key, raw FROM (
              SELECT md5_number({field}) AS raw_key, any_value({field}) AS raw
              FROM {_source(glob)} WHERE {field} IS NOT NULL GROUP BY raw_key)"""
        )
        total = _scalar(con, "SELECT count(*) FROM raw_text")
        counts = {"distinct": total, "html": 0, "email_removed": 0, "empty": 0}
        batches = (
            con.execute(
                "SELECT rid, raw FROM raw_text WHERE rid > ? AND rid <= ? ORDER BY rid",
                [low, low + TEXT_BATCH],
            ).to_arrow_table()
            for low in range(0, total, TEXT_BATCH)
        )
        for normalized in _ordered(pool, normalize_batch, batches, 2 * max(workers, 1)):
            con.register("normalized", normalized)
            html, empty, email = con.execute(
                f"""SELECT count(*) FILTER (WHERE html), count(*) FILTER (WHERE text_hash IS NULL),
                  count(*) FILTER (WHERE contains(text, '{EMAIL_MARKER}')) FROM normalized"""
            ).fetchone() or (0, 0, 0)
            counts["html"] += html
            counts["empty"] += empty
            counts["email_removed"] += email
            con.execute(
                f"""INSERT INTO text_keys SELECT '{field}', r.raw_key, n.text_hash
                FROM normalized n JOIN raw_text r USING (rid) WHERE n.text_hash IS NOT NULL"""
            )
            con.execute(
                """INSERT INTO text_stage SELECT text_hash, text FROM normalized
                WHERE text_hash IS NOT NULL"""
            )
            con.unregister("normalized")
        stats.update({f"texts.{field}.{name}": value for name, value in counts.items()})
        log.info("texts: %s, %s distinct", field, f"{total:,}")
    con.execute("DROP TABLE raw_text")
    con.execute(
        """CREATE TABLE texts AS SELECT text_hash, any_value(text) AS text
        FROM text_stage GROUP BY text_hash ORDER BY text_hash"""
    )
    con.execute("DROP TABLE text_stage")
    _validate_texts(con, pool, workers)
    stats["texts.rows"] = _scalar(con, "SELECT count(*) FROM texts")
    return stats


def check_texts(table: pa.Table) -> int:
    TEXT_SCHEMA.validate(table.to_pandas(), lazy=True)  # a failure here is a bug: raise
    return int(table.num_rows)


def _validate_texts(con: duckdb.DuckDBPyConnection, pool: Executor, workers: int) -> None:
    # 256 chunks by the hash's first byte; texts is sorted by hash, so a range reads only
    # its own row groups.
    bounds = [f"{i:02x}" for i in range(256)] + ["g"]
    frames = (
        con.execute(
            "SELECT text_hash, text FROM texts WHERE text_hash >= ? AND text_hash < ?",
            [low, high],
        ).to_arrow_table()
        for low, high in pairwise(bounds)
    )
    for _ in _ordered(pool, check_texts, frames, max(workers, 1)):
        pass
    log.info("texts: validated")


# Sponsors and ages -----------------------------------------------------------------------


def build_maps(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Temporary lookup tables: raw sponsor name and class -> stored sponsor fields (no name
    for an individual), and raw age text -> years."""
    pairs = con.execute(
        "SELECT DISTINCT raw_sponsor_name, raw_sponsor_class FROM row_keys"
    ).fetchall()
    sponsors = pd.DataFrame(
        [(n or "", c or "", *astuple(sponsor(n, c))) for n, c in pairs],
        columns=["name_key", "class_key", "name", "key", "is_individual"],
    ).astype({"name": object, "key": object, "is_individual": bool})
    con.register("sponsor_frame", sponsors)
    con.execute("CREATE TEMP TABLE sponsor_map AS SELECT * FROM sponsor_frame")
    con.unregister("sponsor_frame")
    ages = [
        r[0]
        for r in con.execute(
            """SELECT minimum_age FROM raw_versions WHERE minimum_age IS NOT NULL
            UNION SELECT maximum_age FROM raw_versions WHERE maximum_age IS NOT NULL"""
        ).fetchall()
    ]
    age_frame = pd.DataFrame({"raw": ages, "years": [age_in_years(a) for a in ages]}).astype(
        {"raw": object, "years": "Float64"}
    )
    con.register("age_frame", age_frame)
    con.execute("CREATE TEMP TABLE age_map AS SELECT * FROM age_frame")
    con.unregister("age_frame")
    return {
        "ages.distinct_texts": len(ages),
        "ages.unparsed_texts": int(age_frame["years"].isna().sum()),
        "sponsors.distinct_pairs": len(pairs),
    }


# Versions --------------------------------------------------------------------------------


def _stage_sql() -> str:
    select = [
        "c.chunk AS _chunk",
        "r.nct_id",
        "r.nct_version",
        "'history' AS source",
        f"{_sql_valid('r.last_update_post_date')} AS effective_date",
        f"{_sql_enum('r.last_update_post_date_type')} AS effective_date_type",
        f"{_sql_valid('r.last_update_submit_date')} AS submitted_date",
        f"{_sql_enum('r.overall_status')} AS overall_status",
        f"{_sql_enum('r.last_known_status')} AS last_known_status",
        f"{_sql_enum('r.study_type')} AS study_type",
        f"{_sql_valid('r.study_first_post_date')} AS study_first_post_date",
        f"{_sql_enum('r.study_first_post_date_type')} AS study_first_post_date_type",
        f"{_sql_valid('r.study_first_submit_date')} AS study_first_submit_date",
        f"{_sql_valid('r.status_verified_date')} AS status_verified_date",
    ]
    for name in PRECISION_DATES:
        valid = _sql_valid(f"r.{name}")
        select += [
            f"{valid} AS {name}",
            f"CASE WHEN {valid} IS NOT NULL THEN lower(trim(r.{name}_precision)) END "
            f"AS {name}_precision",
            f"CASE WHEN {valid} IS NOT NULL THEN {_sql_enum(f'r.{name}_type')} END AS {name}_type",
        ]
    select += [f"{_sql_enum(f'r.{c}')} AS {c}" for c in ("allocation", "intervention_model")]
    select += [f"{_sql_enum(f'r.{c}')} AS {c}" for c in ("primary_purpose", "masking")]
    select += [
        "r.healthy_volunteers",
        f"{_sql_enum('r.sex')} AS sex",
        "amin.years AS minimum_age_years",
        "amax.years AS maximum_age_years",
        "r.enrollment_count",
        f"{_sql_enum('r.enrollment_type')} AS enrollment_type",
        f"{_sql_enum('r.lead_sponsor_class')} AS lead_sponsor_class",
        f"{_sql_enum('r.organization_class')} AS organization_class",
        "s.name AS lead_sponsor_name",
        "s.key AS sponsor_key",
        "coalesce(s.is_individual, false) AS sponsor_is_individual",
    ]
    joins = [
        "JOIN trial_chunks c ON c.nct_id = r.nct_id",
        "JOIN row_keys k USING (source_file, source_row)",
        "LEFT JOIN age_map amin ON amin.raw = r.minimum_age",
        "LEFT JOIN age_map amax ON amax.raw = r.maximum_age",
        "LEFT JOIN sponsor_map s ON s.name_key = coalesce(k.raw_sponsor_name, '') "
        "AND s.class_key = coalesce(k.raw_sponsor_class, '')",
    ]
    for field in TEXT_FIELDS:
        select.append(f"t_{field}.text_hash AS {field}_hash")
        joins.append(
            f"LEFT JOIN text_keys t_{field} ON t_{field}.field = '{field}' "
            f"AND t_{field}.raw_key = k.{field}_key"
        )
    select.append("CAST(NULL AS VARCHAR) AS content_hash")
    return (
        "CREATE TEMP TABLE version_stage AS SELECT\n  "
        + ",\n  ".join(select)
        + "\nFROM raw_versions r\n"
        + "\n".join(joins)
        # Sorted by chunk, so each chunk's query reads only its own row groups.
        + "\nORDER BY c.chunk, r.nct_id, r.nct_version, r.last_update_post_date"
    )


def _create_table(con: duckdb.DuckDBPyConnection, name: str, extra: str = "") -> None:
    columns = ", ".join(f"{c} {kind}" for c, kind in COLUMN_TYPES.items())
    con.execute(f"CREATE TABLE {name} ({columns}{extra})")


def _insert(con: duckdb.DuckDBPyConnection, table: str, rows: pa.Table) -> None:
    if rows.num_rows == 0:
        return
    columns = [f"CAST({c} AS {kind}) AS {c}" for c, kind in COLUMN_TYPES.items()]
    if "reasons" in rows.column_names:
        columns.append("CAST(reasons AS VARCHAR) AS reasons")
    con.register("chunk", rows)
    con.execute(f"INSERT INTO {table} SELECT {', '.join(columns)} FROM chunk")
    con.unregister("chunk")


def prepare_chunk(raw: pa.Table) -> tuple[pa.Table, pa.Table]:
    """Typed canonical rows with content hashes, split into valid and quarantined rows. Runs
    in a worker: Arrow in and out, so the main process never converts rows to pandas."""
    frame = typed_frame(raw.to_pandas(date_as_object=False))
    frame["content_hash"] = content_hashes(frame)
    valid, failing = split_valid(frame)
    return (
        pa.Table.from_pandas(valid, preserve_index=False),
        pa.Table.from_pandas(failing, preserve_index=False),
    )


def build_versions(con: duckdb.DuckDBPyConnection, pool: Executor, workers: int) -> dict[str, int]:
    """Canonical rows, content hashes and validation, in chunks of whole trials (hashing and
    validation run in the worker pool; rows are inserted in chunk order)."""
    con.execute(
        f"""CREATE TEMP TABLE trial_chunks AS
        SELECT nct_id, (row_number() OVER (ORDER BY nct_id) - 1) // {TRIALS_PER_CHUNK} AS chunk
        FROM (SELECT DISTINCT nct_id FROM raw_versions)"""
    )
    con.execute(_stage_sql())
    log.info("versions: staged")
    _create_table(con, "versions")
    _create_table(con, "versions_quarantine", ", reasons VARCHAR")
    # One sorted read of the whole stage, then a compact copy per chunk: re-scanning the
    # stage once per chunk kept the main process busy while the workers waited.
    ordered = con.execute(
        "SELECT * FROM version_stage ORDER BY _chunk, nct_id, nct_version, effective_date"
    ).to_arrow_table()
    con.execute("DROP TABLE version_stage")
    chunk_ids = ordered.column("_chunk").to_numpy()
    bounds = [0, *(np.flatnonzero(np.diff(chunk_ids)) + 1).tolist(), len(chunk_ids)]
    body = ordered.drop_columns(["_chunk"])
    chunks = len(bounds) - 1
    frames = (  # take() copies just the chunk's rows; a slice would pickle the whole table
        body.take(pa.array(np.arange(start, end))) for start, end in pairwise(bounds)
    )
    for done, (valid, failing) in enumerate(
        _ordered(pool, prepare_chunk, frames, max(workers, 1)), start=1
    ):
        _insert(con, "versions", valid)
        _insert(con, "versions_quarantine", failing)
        log.info("versions: chunk %d of %d", done, chunks)
    del ordered, body
    for table in ("trial_chunks", "row_keys", "text_keys"):
        con.execute(f"DROP TABLE {table}")
    return {
        "versions.rows": _scalar(con, "SELECT count(*) FROM versions"),
        "versions.quarantined": _scalar(con, "SELECT count(*) FROM versions_quarantine"),
    }


def build_trials(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """CREATE TABLE trials AS
        WITH q AS (SELECT nct_id, count(*) AS n FROM versions_quarantine GROUP BY nct_id)
        SELECT v.nct_id,
          arg_min(v.study_first_post_date, v.nct_version) AS t0,
          min(v.nct_version) AS first_version,
          max(v.nct_version) AS last_version,
          count(*) AS n_versions,
          min(v.effective_date) AS first_effective_date,
          max(v.effective_date) AS last_effective_date,
          arg_max(v.overall_status, v.nct_version) AS latest_status,
          arg_max(v.study_type, v.nct_version) AS latest_study_type,
          CAST(coalesce(any_value(q.n), 0) AS BIGINT) AS quarantined_versions
        FROM versions v LEFT JOIN q USING (nct_id)
        GROUP BY v.nct_id ORDER BY v.nct_id"""
    )


def build_info(con: duckdb.DuckDBPyConnection, cfg: ProjectConfig, stats: dict[str, Any]) -> None:
    info = {
        "schema_version": SCHEMA_VERSION,
        "dataset.repo_id": cfg.dataset.repo_id,
        "dataset.config_name": cfg.dataset.config_name,
        "dataset.revision": str(cfg.dataset.revision),
        "dataset.cutoff": str(cfg.dataset.cutoff),
        **{key: str(value) for key, value in stats.items()},
    }
    frame = pd.DataFrame(sorted(info.items()), columns=["key", "value"])
    con.register("info_frame", frame)
    con.execute("CREATE TABLE build_info AS SELECT key, value FROM info_frame ORDER BY key")
    con.unregister("info_frame")


# Build and checksums ---------------------------------------------------------------------


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = con.execute(sql).fetchone()
    return int(row[0]) if row else 0


def build(
    glob: str, cfg: ProjectConfig, out_path: Path, workers: int, temp_dir: Path = TEMP_DIR
) -> dict[str, Any]:
    """Build the warehouse into out_path (replaced only when the build completes)."""
    building = out_path.with_name(out_path.name + ".building")
    for leftover in (building, building.with_name(building.name + ".wal")):
        leftover.unlink(missing_ok=True)  # from an interrupted build
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    stats: dict[str, Any] = {}
    with duckdb.connect(str(building)) as con:
        con.execute(f"SET temp_directory = '{temp_dir.as_posix()}'")
        stats["parquet.files"] = len(
            con.execute(
                f"SELECT DISTINCT filename FROM {_source(glob, ', filename=true')}"
            ).fetchall()
        )
        load_raw(con, glob)
        stats["raw.rows"] = _scalar(con, "SELECT count(*) FROM raw_versions")
        with _executor(workers) as pool:
            stats.update(build_texts(con, glob, pool, workers))
            stats.update(build_maps(con))
            log.info("sponsor and age maps: built")
            stats.update(build_versions(con, pool, workers))
        build_trials(con)
        stats["trials.rows"] = _scalar(con, "SELECT count(*) FROM trials")
        build_info(con, cfg, stats)
        con.execute("CHECKPOINT")
    building.replace(out_path)
    return stats


def table_checksums(path: Path) -> dict[str, dict[str, str | int]]:
    """Row count and an order-independent checksum (sum of row hashes) of every table."""
    out: dict[str, dict[str, str | int]] = {}
    with duckdb.connect(str(path), read_only=True) as con:
        for table in TABLES:
            row = con.execute(
                f"SELECT count(*), coalesce(sum(hash(t)::HUGEINT), 0) FROM {table} t"
            ).fetchone()
            assert row is not None
            out[table] = {"rows": int(row[0]), "checksum": str(row[1])}
    return out


def compare_with_previous(current: dict[str, dict[str, str | int]], log_path: Path) -> bool | None:
    """True when every table matches the previous build, None when there is none."""
    if not log_path.is_file():
        return None
    previous = json.loads(log_path.read_text(encoding="utf-8")).get("tables")
    return bool(previous == current)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--workers", type=int, default=min(8, max(1, (os.cpu_count() or 2) - 2)))
    parser.add_argument("--no-audit", action="store_true", help="skip docs/data_audit.md")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_project_config()
    glob = dataset_glob(cfg)
    started = time.monotonic()
    stats = build(glob, cfg, WAREHOUSE_PATH, args.workers)
    tables = table_checksums(WAREHOUSE_PATH)
    identical = compare_with_previous(tables, BUILD_LOG_PATH)
    if not args.no_audit:
        from trialpulse.warehouse.audit import AUDIT_PATH, write_audit

        write_audit(WAREHOUSE_PATH, glob, AUDIT_PATH)
    record = {
        "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "duration_seconds": round(time.monotonic() - started, 1),
        "dataset_revision": cfg.dataset.revision,
        "workers": args.workers,
        "identical_to_previous": identical,
        "tables": tables,
        "stats": stats,
    }
    BUILD_LOG_PATH.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    for table, values in tables.items():
        print(f"{table}: {values['rows']:,} rows, checksum {values['checksum']}")
    verdict = {True: "yes", False: "NO", None: "no previous build"}[identical]
    print(f"Identical to the previous build: {verdict}")
    print(f"Built in {record['duration_seconds']} s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
