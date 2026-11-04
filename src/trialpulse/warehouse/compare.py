"""Compare two warehouse files table by table and column by column: the old-against-new guard
of a warehouse rebuild (ADRs 0066 and 0067).

    uv run python -m trialpulse.warehouse.compare --old data/warehouse_schema3.duckdb

For every table in either file, the rows of the two files are matched by the table's key
(`KEYS`), and the command counts the rows found on one side only and, for every column, the
matched rows whose values differ (exactly, NULL equal to NULL). A column or table found on
one side only, or whose type changed, is reported too. A table without a usable key (none
listed, or a key that repeats or is missing in either file) is compared as a multiset of
whole rows instead.

**The allowlist** names what a rebuild is expected to change, as `table.column`, or `table`
for a whole table, whose differences are still counted and printed. By default it holds what
ADRs 0066 and 0067 change: `content_hash` and `intervention_types` of the version tables,
and the build's own record (`build_info`: the schema version and the build's statistics).
The command exits with code 1 when any difference falls outside the allowlist, and 0 when
none does.

**Never a value.** The output holds table and column names and counts. For `build_info`,
whose keys are the names of the build's statistics, it also names the keys that differ; no
value of any row is printed, and no key of any other table.
"""

import argparse
import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from trialpulse.cli import RefusedError, run
from trialpulse.warehouse.build import TEMP_DIR, WAREHOUSE_PATH

KEYS: dict[str, tuple[str, ...]] = {
    "raw_versions": ("source_file", "source_row"),
    "texts": ("text_hash",),
    "versions": ("nct_id", "nct_version"),
    "versions_quarantine": ("nct_id", "nct_version"),
    "trials": ("nct_id",),
    "build_info": ("key",),
    "stop_reasons": ("nct_id",),
}
# Tables whose keys name the build's statistics rather than data: their differing keys are
# named in the output.
NAMED_KEY_TABLES: frozenset[str] = frozenset({"build_info"})
DEFAULT_ALLOWED: tuple[str, ...] = (
    "versions.content_hash",
    "versions.intervention_types",
    "versions_quarantine.content_hash",
    "versions_quarantine.intervention_types",
    "build_info",
)
COLUMNS_PER_QUERY = 8


@dataclass
class ColumnResult:
    name: str
    status: str  # "compared", "only in old", "only in new", "type changed"
    differing: int = 0
    allowed: bool = False


@dataclass
class TableResult:
    name: str
    status: str  # "compared", "compared as rows", "only in old", "only in new"
    rows_old: int = 0
    rows_new: int = 0
    key: tuple[str, ...] = ()
    matched: int = 0
    only_old: int = 0
    only_new: int = 0
    allowed: bool = False
    columns: list[ColumnResult] = field(default_factory=list)
    named_keys: dict[str, list[str]] = field(default_factory=dict)

    def problems(self) -> list[str]:
        """The differences outside the allowlist, as short descriptions."""
        if self.allowed:
            return []
        out = []
        if self.status in ("only in old", "only in new"):
            out.append(f"{self.name}: table {self.status}")
        if self.only_old or self.only_new:
            out.append(f"{self.name}: rows on one side only")
        for c in self.columns:
            if c.allowed or (c.status == "compared" and not c.differing):
                continue
            label = f"{self.name}.{c.name}"
            out.append(label if c.status == "compared" else f"{label}: {c.status}")
        return out


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = con.execute(sql).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _tables(con: duckdb.DuckDBPyConnection, database: str) -> list[str]:
    rows = con.execute(
        "SELECT table_name FROM duckdb_tables() WHERE database_name = ? AND schema_name = 'main' "
        "ORDER BY table_name",
        [database],
    ).fetchall()
    return [r[0] for r in rows]


def _columns(con: duckdb.DuckDBPyConnection, database: str, table: str) -> dict[str, str]:
    rows = con.execute(
        "SELECT column_name, data_type FROM duckdb_columns() WHERE database_name = ? "
        "AND schema_name = 'main' AND table_name = ? ORDER BY column_index",
        [database, table],
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _usable_key(con: duckdb.DuckDBPyConnection, table: str, key: Sequence[str]) -> bool:
    """Whether the key is present, never missing and never repeated, in both files."""
    columns = ", ".join(_quote(k) for k in key)
    missing = " OR ".join(f"{_quote(k)} IS NULL" for k in key)
    for database in ("o", "n"):
        row = con.execute(
            f"SELECT count(*), count(*) FILTER (WHERE {missing}), "
            f"count(DISTINCT ({columns})) FROM {database}.{_quote(table)}"
        ).fetchone()
        assert row is not None
        if row[1] or row[2] != row[0]:
            return False
    return True


def _allowed(allowlist: set[str], table: str, column: str | None = None) -> bool:
    return table in allowlist or (column is not None and f"{table}.{column}" in allowlist)


def compare_table(
    con: duckdb.DuckDBPyConnection, table: str, allowlist: set[str], key: Sequence[str] | None
) -> TableResult:
    """One table of the two files attached as `o` (old) and `n` (new)."""
    t = _quote(table)
    old, new = _columns(con, "o", table), _columns(con, "n", table)
    result = TableResult(table, "compared", allowed=_allowed(allowlist, table))
    result.rows_old = _scalar(con, f"SELECT count(*) FROM o.{t}")
    result.rows_new = _scalar(con, f"SELECT count(*) FROM n.{t}")
    for name in old:
        if name not in new:
            result.columns.append(
                ColumnResult(name, "only in old", allowed=_allowed(allowlist, table, name))
            )
    for name in new:
        if name not in old:
            result.columns.append(
                ColumnResult(name, "only in new", allowed=_allowed(allowlist, table, name))
            )
    common = [name for name in old if name in new]
    usable = key is not None and set(key) <= set(common) and _usable_key(con, table, key)
    if not usable:
        # A multiset of whole rows, over the common columns outside the allowlist.
        result.status = "compared as rows"
        kept = [c for c in common if not _allowed(allowlist, table, c)]
        listed = ", ".join(_quote(c) for c in kept) or "1"
        result.only_old = _scalar(
            con, f"SELECT count(*) FROM (SELECT {listed} FROM o.{t} EXCEPT ALL "
            f"SELECT {listed} FROM n.{t})"
        )  # fmt: skip
        result.only_new = _scalar(
            con, f"SELECT count(*) FROM (SELECT {listed} FROM n.{t} EXCEPT ALL "
            f"SELECT {listed} FROM o.{t})"
        )  # fmt: skip
        result.matched = result.rows_old - result.only_old
        return result
    assert key is not None
    result.key = tuple(key)
    on = " AND ".join(f"a.{_quote(k)} = b.{_quote(k)}" for k in key)
    keys = ", ".join(_quote(k) for k in key)
    result.matched = _scalar(con, f"SELECT count(*) FROM o.{t} a JOIN n.{t} b ON {on}")
    result.only_old = _scalar(con, f"SELECT count(*) FROM o.{t} a ANTI JOIN n.{t} b ON {on}")
    result.only_new = _scalar(con, f"SELECT count(*) FROM n.{t} b ANTI JOIN o.{t} a ON {on}")
    if table in NAMED_KEY_TABLES:
        result.named_keys = _named_keys(con, table, key, [c for c in common if c not in key])
    values = [c for c in common if c not in key]
    for start in range(0, len(values), COLUMNS_PER_QUERY):
        batch = values[start : start + COLUMNS_PER_QUERY]
        tests = []
        for name in batch:
            q = _quote(name)
            if old[name] == new[name]:
                tests.append(f"count(*) FILTER (WHERE a.{q} IS DISTINCT FROM b.{q})")
            else:  # compare what the values say, and report the type change
                tests.append(
                    f"count(*) FILTER (WHERE CAST(a.{q} AS VARCHAR) "
                    f"IS DISTINCT FROM CAST(b.{q} AS VARCHAR))"
                )
        selected_a = ", ".join([keys, *(_quote(c) for c in batch)])
        row = con.execute(
            f"SELECT {', '.join(tests)} FROM (SELECT {selected_a} FROM o.{t}) a "
            f"JOIN (SELECT {selected_a} FROM n.{t}) b ON {on}"
        ).fetchone()
        assert row is not None
        for name, differing in zip(batch, row, strict=True):
            status = "compared" if old[name] == new[name] else "type changed"
            result.columns.append(
                ColumnResult(name, status, int(differing), _allowed(allowlist, table, name))
            )
    order = {name: i for i, name in enumerate([*old, *(n for n in new if n not in old)])}
    result.columns.sort(key=lambda c: order[c.name])
    return result


def _named_keys(
    con: duckdb.DuckDBPyConnection, table: str, key: Sequence[str], values: Sequence[str]
) -> dict[str, list[str]]:
    """The keys of a statistics table that differ, are new or are gone (names, not values)."""
    t = _quote(table)
    on = " AND ".join(f"a.{_quote(k)} = b.{_quote(k)}" for k in key)
    differ = " OR ".join(f"a.{_quote(c)} IS DISTINCT FROM b.{_quote(c)}" for c in values) or "false"

    def names(alias: str, sql: str) -> list[str]:
        label = " || '/' || ".join(f"CAST({alias}.{_quote(k)} AS VARCHAR)" for k in key)
        return sorted(str(r[0]) for r in con.execute(f"SELECT {label} {sql}").fetchall())

    return {
        "differing": names("a", f"FROM o.{t} a JOIN n.{t} b ON {on} WHERE {differ}"),
        "only in old": names("a", f"FROM o.{t} a ANTI JOIN n.{t} b ON {on}"),
        "only in new": names("b", f"FROM n.{t} b ANTI JOIN o.{t} a ON {on}"),
    }


def compare_files(
    old: Path,
    new: Path,
    allowed: Iterable[str] = DEFAULT_ALLOWED,
    keys: dict[str, tuple[str, ...]] | None = None,
    memory_limit: str = "2GB",
    temp_dir: Path = TEMP_DIR,
) -> list[TableResult]:
    """Every table of two warehouse files, compared (see the module text)."""
    keys = KEYS if keys is None else keys
    allowlist = set(allowed)
    temp_dir.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as con:
        con.execute(f"SET memory_limit = '{memory_limit}'")
        con.execute(f"SET temp_directory = '{temp_dir.as_posix()}'")
        con.execute(f"ATTACH '{old.as_posix()}' AS o (READ_ONLY)")
        con.execute(f"ATTACH '{new.as_posix()}' AS n (READ_ONLY)")
        old_tables, new_tables = _tables(con, "o"), _tables(con, "n")
        results = []
        for table in sorted({*old_tables, *new_tables}):
            if table not in new_tables or table not in old_tables:
                side = "o" if table in old_tables else "n"
                rows = _scalar(con, f"SELECT count(*) FROM {side}.{_quote(table)}")
                results.append(
                    TableResult(
                        table,
                        "only in old" if side == "o" else "only in new",
                        rows_old=rows if side == "o" else 0,
                        rows_new=rows if side == "n" else 0,
                        allowed=_allowed(allowlist, table),
                    )
                )
                continue
            results.append(compare_table(con, table, allowlist, keys.get(table)))
    return results


def report_lines(results: Sequence[TableResult]) -> list[str]:
    """The printed report: names and counts only."""
    lines = []
    for r in results:
        allowed = " (allowed)" if r.allowed else ""
        if r.status in ("only in old", "only in new"):
            rows = r.rows_old or r.rows_new
            lines.append(f"{r.name}: table {r.status}, {rows:,} rows{allowed}")
            continue
        how = f"matched by ({', '.join(r.key)})" if r.key else "compared as whole rows"
        lines.append(
            f"{r.name}: {r.rows_old:,} rows old, {r.rows_new:,} new; {how}: {r.matched:,} "
            f"matched, {r.only_old:,} only in old, {r.only_new:,} only in new{allowed}"
        )
        same = [c for c in r.columns if c.status == "compared" and not c.differing]
        for c in r.columns:
            if c.status == "compared" and not c.differing:
                continue
            note = " (allowed)" if c.allowed else ""
            if c.status in ("only in old", "only in new"):
                lines.append(f"  {c.name}: column {c.status}{note}")
            else:
                changed = ", type changed" if c.status == "type changed" else ""
                lines.append(
                    f"  {c.name}: {c.differing:,} of {r.matched:,} matched rows differ"
                    f"{changed}{note}"
                )
        if r.key:
            lines.append(f"  identical in every matched row: {len(same)} columns")
        for kind, names in r.named_keys.items():
            if names:
                lines.append(f"  keys {kind}: {', '.join(names)}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two warehouse files.")
    parser.add_argument("--old", type=Path, required=True, help="the warehouse before")
    parser.add_argument("--new", type=Path, default=WAREHOUSE_PATH, help="the warehouse after")
    parser.add_argument(
        "--allow",
        action="append",
        default=None,
        metavar="TABLE[.COLUMN]",
        help="expected to differ (repeatable); replaces the default allowlist",
    )
    parser.add_argument("--memory-limit", default="2GB", help="DuckDB's memory limit")
    parser.add_argument("--temp-dir", type=Path, default=TEMP_DIR, help="where DuckDB spills")
    parser.add_argument("--out", type=Path, default=None, help="also write the counts as JSON")
    args = parser.parse_args(argv)
    for path in (args.old, args.new):
        if not path.is_file():
            raise RefusedError(f"no warehouse at {path}")
    if args.old.resolve() == args.new.resolve():
        raise RefusedError("the old and the new warehouse are the same file")
    allowed = tuple(args.allow) if args.allow else DEFAULT_ALLOWED
    results = compare_files(
        args.old, args.new, allowed, memory_limit=args.memory_limit, temp_dir=args.temp_dir
    )
    print(f"Old: {args.old.as_posix()}")
    print(f"New: {args.new.as_posix()}")
    print(f"Allowed to differ: {', '.join(allowed)}")
    print("\n".join(report_lines(results)))
    problems = [p for r in results for p in r.problems()]
    if args.out is not None:
        record: dict[str, Any] = {
            "old": args.old.as_posix(),
            "new": args.new.as_posix(),
            "allowed": list(allowed),
            "tables": [asdict(r) for r in results],
            "outside_the_allowlist": problems,
        }
        args.out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"Differences outside the allowlist: {'; '.join(problems) if problems else 'none'}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(run(main))
