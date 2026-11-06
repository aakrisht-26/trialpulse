"""Time a 10 MB COPY into a throwaway table on Neon, drop it, and project the bootstrap's write.

    uv run python scripts/neon_copy_probe.py

The bootstrap writes its 167.6 MB of COPY text in one transaction (W1). This measures the
uplink to Neon first. The rows are synthetic random hex (ADR 0040: no dataset row reaches
Neon outside the bootstrap), shaped like `live.trial_state`'s COPY text: about 423 bytes a
row, a bigint primary key, so the server indexes and WAL-logs as it would. Two copies are
timed (the table emptied between them), then the table is dropped and its absence checked.
Nothing else on Neon is touched. It reads NEON_DATABASE_URL, refuses any host but Neon's
direct one, and prints timings only, never the URL.
"""

import secrets
import sys
import time as clock
from urllib.parse import urlsplit

import psycopg

from trialpulse.config import Secrets

TABLE = "public.tp_copy_probe_20261106"
TARGET_BYTES = 10_000_000
FULL_BYTES = 167_596_803  # the bootstrap's COPY bytes, measured on 2026-11-05
ROW_TEXT = 410  # payload characters; with the id and separators about 423 bytes a row
CHUNK = 1 << 20


def payload() -> tuple[bytes, int]:
    lines, size, rows = [], 0, 0
    while size < TARGET_BYTES:
        line = f"{rows}\t{secrets.token_hex(ROW_TEXT // 2)}\n".encode()
        lines.append(line)
        size += len(line)
        rows += 1
    return b"".join(lines), rows


def one(con: psycopg.Connection[tuple[object, ...]], sql: str) -> object:
    row = con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"no row from: {sql}")
    return row[0]


def main() -> int:
    url = Secrets().neon_database_url
    if url is None:
        print("NEON_DATABASE_URL is not set")
        return 2
    dsn = url.get_secret_value().replace("postgresql+psycopg://", "postgresql://")
    host = urlsplit(dsn).hostname or ""
    if not host.endswith(".neon.tech") or "-pooler" in host:
        print("NEON_DATABASE_URL is not Neon's direct URL; nothing done")
        return 2
    data, rows = payload()
    print(f"payload: {len(data):,} bytes in {rows:,} rows")
    with psycopg.connect(dsn, connect_timeout=30, autocommit=True) as con:
        tick = clock.perf_counter()
        con.execute("SELECT 1")
        print(f"round trip: {1000 * (clock.perf_counter() - tick):.0f} ms")
        if one(con, f"SELECT to_regclass('{TABLE}') IS NOT NULL"):
            print("the probe table already exists; nothing done")
            return 2
        con.execute(f"CREATE TABLE {TABLE} (id bigint PRIMARY KEY, payload text NOT NULL)")
        try:
            for attempt in (1, 2):
                if attempt == 2:
                    con.execute(f"TRUNCATE {TABLE}")
                tick = clock.perf_counter()
                with (
                    con.transaction(),
                    con.cursor() as cur,
                    cur.copy(f"COPY {TABLE} (id, payload) FROM STDIN") as copy,
                ):
                    for start in range(0, len(data), CHUNK):
                        copy.write(data[start : start + CHUNK])
                seconds = clock.perf_counter() - tick
                if one(con, f"SELECT count(*) FROM {TABLE}") != rows:
                    raise RuntimeError("the probe table does not hold every row copied")
                mbps = len(data) * 8 / seconds / 1e6
                projected = seconds * FULL_BYTES / len(data)
                print(
                    f"copy {attempt}: {seconds:.1f} s ({mbps:.1f} Mbit/s effective); "
                    f"projected 167.6 MB: {projected:.0f} s ({projected / 60:.1f} min)"
                )
        finally:
            con.execute(f"DROP TABLE IF EXISTS {TABLE}")
            print(f"probe table dropped: {one(con, f"SELECT to_regclass('{TABLE}') IS NULL")}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
