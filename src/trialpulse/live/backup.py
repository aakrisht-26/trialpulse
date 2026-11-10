"""Weekly backups of the live system (ADR 0045, decision 11) and their restore on the laptop.

    uv run python -m trialpulse.live.backup backup --target neon
    uv run python -m trialpulse.live.backup restore --from data/backups/2026-11-10

**Backup.** Every table of the `live`, `serving` and `monitoring` schemas, read from one
consistent snapshot (one `REPEATABLE READ, READ ONLY` transaction), is written to
`data/backups/<UTC date>/` as Postgres's own COPY text format, gzipped, one file per table,
each table's rows in the order of its primary key. The session fixes every setting the text
form depends on (UTC, ISO dates, hex bytea, shortest exact floats), so the same rows always
give the same bytes, and Neon and the laptop's Postgres (both 16) give the same text:

- the digest of a table is the SHA-256 of its COPY stream; its rows are the stream's lines
  (COPY's text format escapes every newline inside a value), checked against `count(*)` in
  the same snapshot;
- after the snapshot ends, each file is read back from disk and its digest and lines are
  checked again; only then is `manifest.json` written and the folder renamed from
  `<date>.partial` into place, so a folder without a manifest is never a backup;
- the last `live.backup_keep` (8) backups are kept; older ones are deleted, never a
  partial one of a run that is still writing, and never the one just written.

The connection is held only inside the snapshot, where it is never idle: each chunk is written
to disk as the next is read. ADR 0072's keep-alive cannot ping inside a transaction (it would
interleave with the COPY), so the span it guards, a held connection that is quiet, does not
occur; the read-back runs after the connection is closed. A folder for today that already
exists is never overwritten.

**Restore.** Only into the laptop's Postgres (`DATABASE_URL`, localhost or 127.0.0.1, as
ADR 0062), never Neon: a fresh database (default `trialpulse_restore`, dropped first if it
exists; only a name starting with `trialpulse_restore` is accepted) is migrated to the
backup's Alembic revision, each table is loaded with COPY in an order that respects the
foreign keys, identity sequences are moved past the loaded ids, and every table is dumped
again in the backup's form: its rows and digest must equal the manifest's.

Nothing here prints a connection string, a password or a row: counts, sizes and digests only.
The backups stay in `data/` on the laptop (gitignored) and are never published.
"""

import argparse
import datetime as dt
import gzip
import hashlib
import json
import re
import shutil
import time as clock
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from psycopg import sql

from trialpulse.cli import RefusedError, run
from trialpulse.config import REPO_ROOT, Secrets, load_project_config

BACKUP_DIR = REPO_ROOT / "data" / "backups"
SCHEMAS = ("live", "serving", "monitoring")
LOCAL_HOSTS = ("localhost", "127.0.0.1")
RESTORE_PREFIX = "trialpulse_restore"
FOLDER = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Every setting the COPY text form depends on, the same on both sides of a backup.
SESSION = (
    "SET TIME ZONE 'UTC'",
    "SET DateStyle TO 'ISO, YMD'",
    "SET IntervalStyle TO 'postgres'",
    "SET bytea_output TO 'hex'",
    "SET extra_float_digits TO 1",
    "SET client_encoding TO 'UTF8'",
)


class BackupError(RefusedError):
    """A table's file is not what the database sent: the backup is not kept."""


@dataclass(frozen=True)
class TableInfo:
    schema: str
    name: str
    key: tuple[str, ...]  # the primary key, the order of the rows
    identities: tuple[str, ...]  # identity columns, whose sequences a restore moves on

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"

    def ident(self) -> sql.Composed:
        return sql.SQL(".").join([sql.Identifier(self.schema), sql.Identifier(self.name)])


@dataclass
class Saved:
    table: str
    rows: int
    sha256: str
    bytes: int  # the gzipped file
    file: str


def _session(con: Any) -> None:
    for statement in SESSION:
        con.execute(statement)


def tables(con: Any) -> list[TableInfo]:
    """Every table of the three schemas, with its primary key and identity columns."""
    rows = con.execute(
        """SELECT n.nspname, c.relname,
             (SELECT array_agg(a.attname::text ORDER BY array_position(i.indkey::int2[], a.attnum))
                FROM pg_index i JOIN pg_attribute a
                  ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
               WHERE i.indrelid = c.oid AND i.indisprimary),
             (SELECT array_agg(a.attname::text ORDER BY a.attnum) FROM pg_attribute a
               WHERE a.attrelid = c.oid AND a.attidentity <> '' AND NOT a.attisdropped)
           FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE n.nspname = ANY (%s) AND c.relkind IN ('r', 'p')
          ORDER BY 1, 2""",
        (list(SCHEMAS),),
    ).fetchall()
    found = []
    for schema, name, key, identities in rows:
        if not key:
            raise BackupError(f"{schema}.{name} has no primary key: its rows have no order")
        found.append(TableInfo(schema, name, tuple(key), tuple(identities or ())))
    return found


def copy_out(table: TableInfo) -> sql.Composed:
    order = sql.SQL(", ").join(sql.Identifier(k) for k in table.key)
    return sql.SQL("COPY (SELECT * FROM {} ORDER BY {}) TO STDOUT").format(table.ident(), order)


def _value(con: Any, query: Any) -> Any:
    row = con.execute(query).fetchone()
    if row is None:
        raise BackupError("a query of the snapshot returned no row")
    return row[0]


def _stream(con: Any, table: TableInfo, write: Callable[[bytes], object]) -> tuple[int, str]:
    """Send a table's COPY stream to `write`; its lines and SHA-256."""
    digest, lines = hashlib.sha256(), 0
    with con.cursor().copy(copy_out(table)) as copy:
        for chunk in copy:
            data = bytes(chunk)
            digest.update(data)
            lines += data.count(b"\n")
            write(data)
    return lines, digest.hexdigest()


def read_back(path: Path) -> tuple[int, str]:
    """A backup file's lines and the SHA-256 of its uncompressed bytes."""
    digest, lines = hashlib.sha256(), 0
    with gzip.open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
            lines += chunk.count(b"\n")
    return lines, digest.hexdigest()


def _local_dsn() -> str:
    secret = Secrets().database_url
    if secret is None:
        raise RefusedError("DATABASE_URL is not set (environment or .env)")
    return secret.get_secret_value().replace("postgresql+psycopg://", "postgresql://", 1)


def _dsn(target: str) -> str:
    from trialpulse.live.daily import _dsn as live_dsn

    return live_dsn(target)


def backup(dsn: str, out_dir: Path = BACKUP_DIR, keep: int = 8,
           today: dt.date | None = None) -> dict[str, Any]:  # fmt: skip
    """One backup of every table into `out_dir/<date>/`; its manifest (see the module text)."""
    import psycopg

    started = clock.perf_counter()
    day = (today or dt.datetime.now(dt.UTC).date()).isoformat()
    final, partial = out_dir / day, out_dir / f"{day}.partial"
    if final.exists():
        raise RefusedError(f"a backup for {day} already exists; it is never overwritten")
    if partial.exists():
        shutil.rmtree(partial)  # a run that died before its manifest: not a backup
    partial.mkdir(parents=True)
    saved: list[Saved] = []
    with psycopg.connect(dsn, autocommit=True, connect_timeout=30) as con:
        _session(con)
        with con.transaction():
            con.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            snapshot_at = _value(con, "SELECT now()")
            revision = _value(con, "SELECT version_num FROM public.alembic_version")
            server = _value(con, "SHOW server_version")
            found = tables(con)
            for table in found:
                count = _value(con, sql.SQL("SELECT count(*) FROM {}").format(table.ident()))
                path = partial / f"{table.qualified}.copy.gz"
                with gzip.open(path, "wb", compresslevel=6) as fh:
                    lines, digest = _stream(con, table, fh.write)
                if lines != count:
                    raise BackupError(f"{table.qualified}: {lines} lines sent, {count} rows")
                saved.append(Saved(table.qualified, count, digest, path.stat().st_size,
                                   path.name))  # fmt: skip
    snapshot_seconds = clock.perf_counter() - started
    for item in saved:  # the files as they are on disk, after the connection is closed
        lines, digest = read_back(partial / item.file)
        if (lines, digest) != (item.rows, item.sha256):
            raise BackupError(f"{item.table}: the file read back differs from what was sent")
    manifest = {
        "date": day,
        "snapshot_at": snapshot_at.astimezone(dt.UTC).isoformat(),
        "alembic_revision": revision,
        "server_version": server,
        "session": list(SESSION),
        "tables": {s.table: {k: v for k, v in asdict(s).items() if k != "table"} for s in saved},
        "keys": {t.qualified: list(t.key) for t in found},
        "identities": {t.qualified: list(t.identities) for t in found if t.identities},
    }
    (partial / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    partial.replace(final)
    removed = prune(out_dir, keep)
    return {
        "folder": final.as_posix(),
        "tables": len(saved),
        "rows": sum(s.rows for s in saved),
        "bytes_on_disk": sum(s.bytes for s in saved),
        "snapshot_seconds": round(snapshot_seconds, 1),
        "seconds": round(clock.perf_counter() - started, 1),
        "removed": removed,
    }  # fmt: skip


def complete_backups(out_dir: Path) -> list[Path]:
    """The finished backups (a dated folder with a manifest), oldest first."""
    if not out_dir.is_dir():
        return []
    return sorted(
        p
        for p in out_dir.iterdir()
        if p.is_dir() and FOLDER.match(p.name) and (p / "manifest.json").is_file()
    )


def prune(out_dir: Path, keep: int) -> list[str]:
    """Delete all but the newest `keep` finished backups; the names deleted."""
    if keep < 1:
        raise ValueError("at least one backup is kept")
    old = complete_backups(out_dir)[:-keep]
    for folder in old:
        shutil.rmtree(folder)
    return [p.name for p in old]


# Restore ------------------------------------------------------------------------------------


def _load_order(names: Iterable[str], edges: Sequence[tuple[str, str]]) -> list[str]:
    """Tables ordered so that every table comes after the tables its foreign keys name."""
    pending = sorted(set(names))
    needs = {n: {parent for child, parent in edges if child == n and parent != n}
             for n in pending}  # fmt: skip
    ordered: list[str] = []
    while pending:
        ready = [n for n in pending if needs[n] <= set(ordered)]
        if not ready:
            raise BackupError("the foreign keys form a cycle")
        ordered += ready
        pending = [n for n in pending if n not in ready]
    return ordered


def _check_local(dsn: str) -> None:
    from psycopg import conninfo

    hosts = str(conninfo.conninfo_to_dict(dsn).get("host") or "localhost").split(",")
    outside = [h for h in hosts if h not in LOCAL_HOSTS]
    if outside:
        raise RefusedError("a restore goes only into the laptop's Postgres (localhost), "
                           "never Neon: DATABASE_URL names another host")  # fmt: skip


def restore(folder: Path, base_dsn: str, database: str = RESTORE_PREFIX) -> dict[str, Any]:
    """Load a backup into a fresh local database and check it (see the module text)."""
    import psycopg
    from alembic import command
    from alembic.config import Config
    from psycopg import conninfo

    _check_local(base_dsn)
    if not re.fullmatch(rf"{RESTORE_PREFIX}[a-z0-9_]*", database):
        raise RefusedError(f"a restore database's name starts with {RESTORE_PREFIX}")
    manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
    started = clock.perf_counter()
    admin = conninfo.make_conninfo(base_dsn, dbname="postgres", connect_timeout=10)
    with psycopg.connect(admin, autocommit=True) as con:
        con.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
            sql.Identifier(database)))  # fmt: skip
        con.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    target = conninfo.make_conninfo(base_dsn, dbname=database)
    alembic = Config(str(REPO_ROOT / "alembic.ini"))
    alembic.attributes["url"] = _sqlalchemy_url(target)
    command.upgrade(alembic, manifest["alembic_revision"])
    checked: dict[str, dict[str, Any]] = {}
    with psycopg.connect(target, autocommit=True) as con:
        _session(con)
        edges = con.execute(
            """SELECT conrelid::regclass::text, confrelid::regclass::text FROM pg_constraint
                WHERE contype = 'f' AND connamespace::regnamespace::text = ANY (%s)""",
            (list(SCHEMAS),),
        ).fetchall()
        found = {t.qualified: t for t in tables(con)}
        if set(found) != set(manifest["tables"]):
            raise BackupError("the restored schema's tables differ from the backup's")
        with con.transaction():  # every table emptied first: a migration may seed rows
            con.execute(sql.SQL("TRUNCATE {}").format(
                sql.SQL(", ").join(t.ident() for t in found.values())))  # fmt: skip
            for name in _load_order(found, edges):
                table = found[name]
                with (
                    con.cursor().copy(sql.SQL("COPY {} FROM STDIN").format(table.ident())) as copy,
                    gzip.open(folder / manifest["tables"][name]["file"], "rb") as fh,
                ):
                    while chunk := fh.read(1 << 20):
                        copy.write(chunk)
                for column in table.identities:
                    con.execute(
                        sql.SQL("SELECT setval(pg_get_serial_sequence({}, {}), "
                                "coalesce(max({}), 0) + 1, false) FROM {}").format(
                            sql.Literal(name), sql.Literal(column), sql.Identifier(column),
                            table.ident()))  # fmt: skip
        for name, table in found.items():
            lines, digest = _stream(con, table, lambda _: None)
            want = manifest["tables"][name]
            checked[name] = {"rows": lines, "rows_equal": lines == want["rows"],
                             "digest_equal": digest == want["sha256"]}  # fmt: skip
    equal = all(c["rows_equal"] and c["digest_equal"] for c in checked.values())
    return {"database": database, "tables": len(checked), "equal": equal,
            "rows": sum(c["rows"] for c in checked.values()),
            "different": sorted(n for n, c in checked.items()
                                if not (c["rows_equal"] and c["digest_equal"])),
            "seconds": round(clock.perf_counter() - started, 1)}  # fmt: skip


def _sqlalchemy_url(dsn: str) -> str:
    from urllib.parse import quote

    from psycopg import conninfo

    parts = conninfo.conninfo_to_dict(dsn)
    user = quote(str(parts.get("user", "")), safe="")
    password = quote(str(parts.get("password", "")), safe="")
    host, port = parts.get("host", "localhost"), parts.get("port", 5432)
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{parts['dbname']}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up the live system, or restore it.")
    steps = parser.add_subparsers(dest="step", required=True)
    up = steps.add_parser("backup", help="export every table to data/backups/<date>/")
    up.add_argument("--target", choices=("local", "neon"), default="neon")
    up.add_argument("--out-dir", type=Path, default=BACKUP_DIR)
    down = steps.add_parser("restore", help="load a backup into a fresh local database")
    down.add_argument("--from", dest="folder", type=Path, required=True)
    down.add_argument("--database", default=RESTORE_PREFIX)
    args = parser.parse_args(argv)
    if args.step == "backup":
        keep = load_project_config().live.backup_keep
        result = backup(_dsn(args.target), args.out_dir, keep)
    else:
        result = restore(args.folder, _local_dsn(), args.database)
    print(json.dumps(result, indent=2))
    if args.step == "restore" and not result["equal"]:
        print(f"failed: {len(result['different'])} tables differ from the backup")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
