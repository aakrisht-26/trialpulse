"""Writing build outputs as Parquet with a content checksum.

The cohort and feature builds are deterministic: the same inputs give the same rows. Each
output is written sorted, and its checksum is computed from the rows read back, not from the
file's bytes, so two builds can be compared whatever the Parquet writer does.
"""

from collections.abc import Sequence
from pathlib import Path

import duckdb


def checksum(con: duckdb.DuckDBPyConnection, path: Path) -> tuple[int, str]:
    """Row count and order-independent checksum of a Parquet file's rows."""
    rows, total = con.execute(
        "SELECT count(*), coalesce(sum(hash(t)::HUGEINT), 0) "
        f"FROM read_parquet('{path.as_posix()}') t"
    ).fetchone() or (0, 0)
    return int(rows), str(total)


def write_table(
    con: duckdb.DuckDBPyConnection, table: str, path: Path, columns: Sequence[str], order: str
) -> tuple[int, str]:
    """Write one table to Parquet, sorted, and return its row count and checksum."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"""COPY (SELECT {", ".join(columns)} FROM {table} ORDER BY {order})
        TO '{path.as_posix()}' (FORMAT parquet)"""
    )
    return checksum(con, path)
