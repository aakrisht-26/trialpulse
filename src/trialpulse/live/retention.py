"""The retention step of ADR 0045: export old rows to the laptop, verify the file, then delete.

    uv run python -m trialpulse.live.retention --target neon
    uv run python -m trialpulse.live.retention --target neon --export-only

Run by hand on the laptop at the start of every month (ADR 0069), and whenever the daily job's
alert reports 600 MB; never by the job (at the cap deletes fail too, and nothing is deleted
without a confirmed export). For each table, the rows older than its months before `--as-of`
(default today): `live.retention_months` (12) for score history and
`live.version_retention_months` (6) for versions:

- `serving.trial_score_history`, except each trial's newest row, which the next publication
  compares with (the last history row of an earlier day);
- `live.study_versions`, except each trial's newest row, which ingest compares a record
  with, and except rows the sponsor pass has not consumed (a run after the file's last run).

Each table, under the job lease, renewed at every table and first in the delete (ADR 0060),
so no daily run overlaps it: the rows are selected,
written to `data/live_exports/` as Parquet with a manifest, and read back from disk and
compared with the database rows by count and by a digest of every value, all with no
transaction open (ADR 0058: Neon ends a session idle inside a transaction after 5 minutes).
Only then are they deleted by the same condition, in one short transaction that returns the
deleted rows: they must be exactly the exported rows, value for value, or the transaction is
rolled back and nothing is deleted. The files stay on the laptop (ADR 0045, decision 11's
backups go to the same folder); they are never published.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trialpulse.cli import RefusedError, run
from trialpulse.cohort.rules import add_months
from trialpulse.config import REPO_ROOT, load_project_config
from trialpulse.live import store
from trialpulse.live.daily import _dsn
from trialpulse.live.keepalive import keep_alive

EXPORT_DIR = REPO_ROOT / "data" / "live_exports"


@dataclass(frozen=True)
class Table:
    name: str
    alias: str
    order: str  # a total order of the exported rows
    condition: str  # the rows to export and delete; %(cutoff)s and %(consumed)s are bound


TABLES: tuple[Table, ...] = (
    Table(
        "serving.trial_score_history", "h", "h.nct_id, h.as_of_date, h.history_id",
        """h.as_of_date < %(cutoff)s AND EXISTS (
          SELECT 1 FROM serving.trial_score_history n WHERE n.nct_id = h.nct_id
            AND (n.as_of_date, n.history_id) > (h.as_of_date, h.history_id))""",
    ),
    Table(
        "live.study_versions", "v", "v.nct_id, v.effective_date, v.ingested_at, v.feature_hash",
        """v.effective_date < %(cutoff)s AND v.run_id IS NOT NULL AND v.run_id <= %(consumed)s
          AND EXISTS (SELECT 1 FROM live.study_versions n WHERE n.nct_id = v.nct_id
            AND (n.effective_date, n.ingested_at) > (v.effective_date, v.ingested_at))""",
    ),
)  # fmt: skip


class ExportMismatchError(RefusedError):
    """The file read back is not the rows it was written from: nothing is deleted."""


def _plain(value: Any) -> Any:
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.UTC).isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def digest(rows: Sequence[dict[str, Any]]) -> str:
    """A SHA-256 over every value of the rows, in order, independent of how they were read."""
    out = hashlib.sha256()
    for row in rows:
        out.update(json.dumps({k: _plain(v) for k, v in row.items()}, sort_keys=True).encode())
        out.update(b"\n")
    return out.hexdigest()


def _write(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist([{k: _arrow(v) for k, v in r.items()} for r in rows])
    pq.write_table(table, path, compression="zstd")


def _arrow(value: Any) -> Any:
    return bytes(value) if isinstance(value, memoryview) else value


def _read(path: Path) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    rows: list[dict[str, Any]] = pq.read_table(path).to_pylist()
    return rows


@dataclass
class Exported:
    table: str
    rows: int
    path: Path | None
    deleted: int


VERSIONS = "live.study_versions"


def retain(
    dsn: str,
    as_of: dt.date,
    months: int,
    out_dir: Path,
    export_only: bool = False,
    stale_minutes: int = 120,
    version_months: int | None = None,
    keepalive_seconds: float = 60,
) -> list[Exported]:
    """Export, verify and (unless `export_only`) delete the old rows of every table: those
    older than `months`, or for `live.study_versions` than `version_months` when given."""
    import psycopg

    if os.environ.get("GITHUB_ACTIONS"):
        raise RefusedError("retention exports rows to the laptop; it never runs on a runner")
    cutoff = add_months(as_of, -months)
    cutoffs = {t.name: cutoff for t in TABLES}
    if version_months is not None:
        cutoffs[VERSIONS] = add_months(as_of, -version_months)
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[Exported] = []
    with psycopg.connect(dsn, connect_timeout=10, autocommit=True) as con:
        con.execute("SET TimeZone = 'UTC'")
        with con.cursor() as cur:
            store.check_head(cur)
            with con.transaction():
                run_id = store.start_run(cur, "retention", "retention", as_of, stale_minutes)
        try:
            for table in TABLES:
                results.append(_one(con, table, cutoffs[table.name], run_id, out_dir,
                                    export_only, keepalive_seconds))  # fmt: skip
        except BaseException:
            from trialpulse.live.daily import record_failure

            record_failure(dsn, run_id, {}, "retention failed")
            raise
        with con.cursor() as cur, con.transaction():
            details = {
                r.table: {"rows": r.rows, "deleted": r.deleted,
                          "before": cutoffs[r.table].isoformat()}
                for r in results
            }  # fmt: skip
            store.finish_run(cur, run_id, "succeeded", {"watermark_to": cutoff,
                                                        "details": details})  # fmt: skip
    return results


def _one(con: Any, table: Table, cutoff: dt.date, run_id: int, out_dir: Path,
         export_only: bool, keepalive_seconds: float = 60) -> Exported:  # fmt: skip
    """One table (see the module text). `con` is in autocommit: the export and its check run
    with no transaction open, and the delete is one short transaction."""
    with con.cursor() as cur:
        store.renew(cur, run_id)  # every table renews the lease (ADR 0060)
        row = cur.execute("SELECT last_run_id FROM live.sponsor_file WHERE file_id = 1").fetchone()
        params = {"cutoff": cutoff, "consumed": row[0] if row and row[0] is not None else -1}
        a = table.alias
        cur.execute(
            f"SELECT {a}.* FROM {table.name} {a} WHERE {table.condition} ORDER BY {table.order}",
            params,
        )
        names = [d.name for d in cur.description]
        rows = [dict(zip(names, values, strict=True)) for values in cur.fetchall()]
        if not rows:
            return Exported(table.name, 0, None, 0)
        stem = f"{table.name.replace('.', '_')}_before_{cutoff.isoformat()}_run{run_id}"
        path = out_dir / f"{stem}.parquet"
        with keep_alive(con, keepalive_seconds):  # the file and its check (ADR 0072)
            _write(path, rows)
            expected = digest(rows)
            back = _read(path)
        if len(back) != len(rows) or digest(back) != expected:
            raise ExportMismatchError(
                f"{path.name} does not hold the {len(rows)} rows of {table.name} it was "
                "written from; nothing was deleted"
            )
        manifest = {"table": table.name, "before": cutoff.isoformat(), "rows": len(rows),
                    "rows_sha256": expected,
                    "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "run_id": run_id, "deleted": not export_only}  # fmt: skip
        (out_dir / f"{stem}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        deleted = 0
        if not export_only:
            order = table.order.replace(f"{a}.", "")
            with con.transaction():
                store.renew(cur, run_id)  # a job that lost its lease deletes nothing
                cur.execute(
                    f"WITH gone AS (DELETE FROM {table.name} {a} WHERE {table.condition} "
                    f"RETURNING {a}.*) SELECT * FROM gone ORDER BY {order}",
                    params,
                )
                names = [d.name for d in cur.description]
                gone = [dict(zip(names, values, strict=True)) for values in cur.fetchall()]
                deleted = len(gone)
                if deleted != len(rows) or digest(gone) != expected:
                    raise ExportMismatchError(
                        f"{table.name}: the delete would remove {deleted} rows that are not "
                        f"the {len(rows)} exported; nothing was deleted"
                    )
    return Exported(table.name, len(rows), path, deleted)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export, verify, then delete old live rows.")
    parser.add_argument("--target", choices=("local", "neon"), required=True)
    parser.add_argument("--as-of", type=dt.date.fromisoformat, default=dt.date.today())
    parser.add_argument("--out", type=Path, default=EXPORT_DIR)
    parser.add_argument("--export-only", action="store_true", help="export and verify only")
    args = parser.parse_args(argv)
    cfg = load_project_config()
    for result in retain(_dsn(args.target), args.as_of, cfg.live.retention_months, args.out,
                         args.export_only, cfg.live.lease_stale_minutes,
                         cfg.live.version_retention_months,
                         cfg.live.keepalive_seconds):  # fmt: skip
        where = result.path.name if result.path else "nothing to export"
        print(f"{result.table}: {result.rows} rows exported ({where}), {result.deleted} deleted")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
