"""Data audit, part 1 (CLAUDE.md Step 3): docs/data_audit.md, generated from the warehouse.

Reports duplicates, version order and post dates, impossible dates, enum drift, status
reversals, schema drift, text normalization, personal-data handling and validation. It gives
counts only: no sponsor names and no free text.
"""

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import duckdb

from trialpulse.cli import RefusedError, run
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.contracts.sponsor import ORGANIZATION_WORD_KEY, PERSONAL_TITLE_KEY
from trialpulse.contracts.text import EMAIL_PATTERN
from trialpulse.contracts.versions import (
    CONTENT_COLUMNS,
    ENUMS,
    FIRST_INVALID_DATE,
    FIRST_VALID_DATE,
    TEXT_FIELDS,
)
from trialpulse.reports import replace_section
from trialpulse.warehouse.build import (
    DERIVED_FROM,
    DRIFT_COLUMNS,
    PERSONAL_COLUMNS,
    RAW_COLUMNS,
    TABLES,
    WAREHOUSE_PATH,
    _sql_enum,
    dataset_glob,
)

AUDIT_PATH = REPO_ROOT / "docs" / "data_audit.md"
AUDIT_FAMILY = "# Data audit, part "  # the prefix of every part heading
PART_1_HEADING = AUDIT_FAMILY + "1"
PARITY_PATH = REPO_ROOT / "data" / "warehouse_parity.json"
RAW_DATES: tuple[str, ...] = (
    "last_update_post_date",
    "last_update_submit_date",
    "study_first_post_date",
    "study_first_submit_date",
    "status_verified_date",
    "start_date",
    "primary_completion_date",
    "completion_date",
)
IMPLAUSIBLY_EARLY = "1990-01-01"
IMPLAUSIBLY_LATE = "2050-01-01"
IMPLAUSIBLE_AGE_YEARS = 120
LARGE_ENROLLMENT = 1_000_000
VERSION_DATE_COLUMNS = ("effective_date", "effective_date_type", "submitted_date")


def _n(value: Any) -> str:
    return f"{int(value):,}" if value is not None else "n/a"


def _pct(part: Any, whole: Any) -> str:
    return f"{100 * part / whole:.2f}%" if whole else "n/a"


def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return lines


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[Any, ...]:
    row = con.execute(sql).fetchone()
    assert row is not None
    return tuple(row)


def _in(values: Iterable[str]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def _source_section(con: duckdb.DuckDBPyConnection, glob: str, info: dict[str, str]) -> list[str]:
    parquet_columns = [
        r[0]
        for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{glob}', union_by_name=true)"
        ).fetchall()
    ]
    loaded = [c for c in RAW_COLUMNS if c in parquet_columns]
    derived = [c for c in DERIVED_FROM if c in parquet_columns]
    not_loaded = [c for c in parquet_columns if c not in RAW_COLUMNS and c not in DERIVED_FROM]
    personal = [c for c in PERSONAL_COLUMNS if c in parquet_columns]
    lines = [
        "## Source",
        "",
        f"- Dataset `{info['dataset.repo_id']}`, config `{info['dataset.config_name']}`, pinned "
        f"revision `{info['dataset.revision']}`, data cutoff {info['dataset.cutoff']}.",
        f"- {_n(info['parquet.files'])} Parquet files, {_n(info['raw.rows'])} rows, "
        f"{len(parquet_columns)} columns.",
        f"- Loaded into `raw_versions` with explicit types: {len(loaded)} columns, each with a "
        "planned use (below). Read to derive stored values, never stored raw: "
        + ", ".join(f"`{c}` ({DERIVED_FROM[c]})" for c in derived)
        + ".",
        f"- Not loaded: {len(not_loaded)} columns, including the {len(personal)} personal-data "
        "columns (" + ", ".join(f"`{c}`" for c in personal) + "), which the build never reads.",
        "",
        *_table(
            ["Column", "Type", "Planned use"],
            [(f"`{c}`", RAW_COLUMNS[c][0], RAW_COLUMNS[c][1]) for c in loaded],
        ),
        "",
    ]
    return lines


def _drift_section(con: duckdb.DuckDBPyConnection, glob: str) -> list[str]:
    files = sorted(Path(glob).parent.glob("*.parquet"))
    rows = []
    for column in DRIFT_COLUMNS:
        kinds: dict[str, int] = {}
        for file in files:
            described = con.execute(
                f"DESCRIBE SELECT {column} FROM read_parquet('{file.as_posix()}')"
            ).fetchall()
            kinds[str(described[0][1])] = kinds.get(str(described[0][1]), 0) + 1
        present = _one(con, f"SELECT count({column}) FROM raw_versions")[0]
        by_file = "; ".join(f"{kind} in {count}" for kind, count in sorted(kinds.items()))
        rows.append((f"`{column}`", by_file, _n(present), RAW_COLUMNS[column][0]))
    return [
        "## Schema drift",
        "",
        "Columns whose Parquet type differs between files are read with an explicit type, so "
        "the warehouse never depends on which file DuckDB reads first. None of them feeds a "
        "feature; they are loaded for this audit.",
        "",
        *_table(["Column", "Type by file", "Values present", "Loaded as"], rows),
        "",
    ]


def _duplicates_section(con: duckdb.DuckDBPyConnection) -> list[str]:
    keys, rows = _one(
        con,
        """SELECT count(*), coalesce(sum(n), 0) FROM (
          SELECT count(*) AS n FROM raw_versions GROUP BY nct_id, nct_version HAVING n > 1)""",
    )
    substantive = ", ".join(c for c in CONTENT_COLUMNS if c not in VERSION_DATE_COLUMNS)
    same, total = _one(
        con,
        f"""SELECT count(*) FILTER (WHERE same), count(*) FROM (
          SELECT hash({substantive}) = lag(hash({substantive}))
            OVER (PARTITION BY nct_id ORDER BY nct_version) AS same FROM versions)""",
    )
    return [
        "## Duplicates",
        "",
        f"- Duplicate (nct_id, nct_version) keys in the source: {_n(keys)} ({_n(rows)} rows).",
        f"- Versions identical to the previous version in every loaded column except their own "
        f"post and submit dates: {_n(same)} of {_n(total)} ({_pct(same, total)}). They changed "
        "only columns the warehouse does not load. They are kept: each is a real registry "
        "update and counts toward amendment signals such as versions so far.",
        "",
    ]


def _order_section(con: duckdb.DuckDBPyConnection, cutoff: str) -> list[str]:
    r = _one(
        con,
        """WITH o AS (
          SELECT nct_id, nct_version, effective_date, submitted_date,
            lag(effective_date) OVER w AS prev_effective,
            lag(submitted_date) OVER w AS prev_submitted,
            lag(nct_version) OVER w AS prev_version
          FROM versions WINDOW w AS (PARTITION BY nct_id ORDER BY nct_version))
        SELECT
          count(*) FILTER (WHERE effective_date < prev_effective),
          count(DISTINCT nct_id) FILTER (WHERE effective_date < prev_effective),
          count(*) FILTER (WHERE effective_date = prev_effective),
          count(DISTINCT nct_id) FILTER (WHERE effective_date = prev_effective),
          count(*) FILTER (WHERE submitted_date < prev_submitted),
          count(*) FILTER (WHERE nct_version <> prev_version + 1),
          count(*) FILTER (WHERE effective_date < submitted_date)
        FROM o""",
    )
    first_not_zero = _one(con, "SELECT count(*) FROM trials WHERE first_version <> 0")[0]
    after_cutoff = _one(
        con, f"SELECT count(*) FROM versions WHERE effective_date > DATE '{cutoff}'"
    )[0]
    return [
        "## Version order and post dates",
        "",
        "The version clock is `last_update_post_date` (CLAUDE.md Section 6): the state at time "
        "t is the latest version posted on or before t.",
        "",
        *_table(
            ["Check", "Versions", "Trials"],
            [
                ("Posted earlier than the previous version (non-monotonic)", _n(r[0]), _n(r[1])),
                ("Posted on the same day as the previous version", _n(r[2]), _n(r[3])),
                ("Submitted earlier than the previous version", _n(r[4]), ""),
                ("Version number not the previous plus one", _n(r[5]), ""),
                ("Posted before it was submitted", _n(r[6]), ""),
                ("Trials whose first version is not 0", "", _n(first_not_zero)),
                (f"Posted after the data cutoff ({cutoff})", _n(after_cutoff), ""),
            ],
        ),
        "",
        "When two versions share a post date, the state on that day is the one with the "
        "higher version number (Step 4 applies this tie-break).",
        "",
    ]


def _dates_section(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = []
    for column in RAW_DATES:
        placeholder, early, late = _one(
            con,
            f"""SELECT
              count(*) FILTER (WHERE {column} < DATE '{FIRST_VALID_DATE}'
                OR {column} >= DATE '{FIRST_INVALID_DATE}'),
              count(*) FILTER (WHERE {column} >= DATE '{FIRST_VALID_DATE}'
                AND {column} < DATE '{IMPLAUSIBLY_EARLY}'),
              count(*) FILTER (WHERE {column} >= DATE '{IMPLAUSIBLY_LATE}'
                AND {column} < DATE '{FIRST_INVALID_DATE}')
            FROM raw_versions""",
        )
        rows.append((f"`{column}`", _n(placeholder), _n(early), _n(late)))
    c = _one(
        con,
        """SELECT
          count(*) FILTER (WHERE primary_completion_date < start_date),
          count(*) FILTER (WHERE completion_date < primary_completion_date),
          count(*) FILTER (WHERE start_date_type = 'ACTUAL' AND start_date > effective_date),
          count(*) FILTER (WHERE primary_completion_date_type = 'ACTUAL'
            AND primary_completion_date > effective_date),
          count(*) FILTER (WHERE study_first_post_date > effective_date),
          count(*) FILTER (WHERE study_first_submit_date > study_first_post_date)
        FROM versions""",
    )
    return [
        "## Impossible and implausible dates",
        "",
        f"Dates before {FIRST_VALID_DATE} or from {FIRST_INVALID_DATE} on are registry "
        "placeholders (for example 1900-01-01 and 2100-12-31): the canonical version stores "
        f"them as missing. Dates before {IMPLAUSIBLY_EARLY} or from {IMPLAUSIBLY_LATE} on are "
        "unusual but possible, and are kept.",
        "",
        *_table(
            [
                "Column",
                "Placeholders set to missing",
                f"Kept, before {IMPLAUSIBLY_EARLY}",
                f"Kept, from {IMPLAUSIBLY_LATE}",
            ],
            rows,
        ),
        "",
        "Inconsistent dates within a version (kept as registered; features in Step 7 must not "
        "assume the order):",
        "",
        *_table(
            ["Check", "Versions"],
            [
                ("Primary completion before start", _n(c[0])),
                ("Completion before primary completion", _n(c[1])),
                ("ACTUAL start date after the version's post date", _n(c[2])),
                ("ACTUAL primary completion after the version's post date", _n(c[3])),
                ("First posted after the version's post date", _n(c[4])),
                ("First submitted after first posted", _n(c[5])),
            ],
        ),
        "",
    ]


def _enum_section(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = []
    for column, allowed in ENUMS.items():
        raw_column = {"effective_date_type": "last_update_post_date_type"}.get(column, column)
        values = con.execute(
            f"""SELECT coalesce({column}, '(missing)') AS v, count(*) AS n,
              min(year(effective_date)), max(year(effective_date))
            FROM (SELECT {column}, effective_date FROM versions
                  UNION ALL SELECT {column}, effective_date FROM versions_quarantine)
            GROUP BY v ORDER BY n DESC, v"""
        ).fetchall()
        known = (*allowed, "(missing)")
        listing = "; ".join(
            f"{v} {_n(n)} ({lo} to {hi})" + ("" if v in known else " **not canonical**")
            for v, n, lo, hi in values
        )
        mapped = _one(
            con,
            f"""SELECT count(*) FROM raw_versions WHERE {raw_column} IS NOT NULL
            AND {raw_column} IS DISTINCT FROM {_sql_enum(raw_column)}""",
        )[0]
        rows.append((f"`{column}`", listing, _n(mapped)))
    return [
        "## Enum drift",
        "",
        "Each value with its count and the first and last year (by post date) it appears in. "
        "Values are the API v2 names; older spellings are mapped to them (for example "
        "ANTICIPATED to ESTIMATED). A value outside the canonical set fails validation.",
        "",
        *_table(["Column", "Values: count (years)", "Raw values remapped"], rows),
        "",
    ]


def _reversal_section(con: duckdb.DuckDBPyConnection, cfg: ProjectConfig) -> list[str]:
    terminal = (*cfg.statuses.early_stop, *cfg.statuses.competing)
    rows = con.execute(
        f"""WITH s AS (
          SELECT nct_id, nct_version, overall_status,
            min(CASE WHEN overall_status IN {_in(terminal)} THEN nct_version END)
              OVER (PARTITION BY nct_id) AS first_terminal_version
          FROM versions),
        f AS (SELECT nct_id, overall_status AS first_terminal FROM s
              WHERE nct_version = first_terminal_version),
        later AS (SELECT s.nct_id, f.first_terminal, s.overall_status FROM s JOIN f USING (nct_id)
                  WHERE s.nct_version > s.first_terminal_version)
        SELECT f.first_terminal, count(DISTINCT f.nct_id),
          count(DISTINCT l.nct_id) FILTER (WHERE l.overall_status IN {_in(cfg.statuses.open)}),
          count(DISTINCT l.nct_id) FILTER (WHERE l.overall_status IN {_in(terminal)}
            AND l.overall_status <> l.first_terminal),
          count(DISTINCT l.nct_id) FILTER (WHERE l.overall_status IN
            {_in(cfg.statuses.unknown)})
        FROM f LEFT JOIN later l USING (nct_id)
        GROUP BY f.first_terminal ORDER BY f.first_terminal"""
    ).fetchall()
    total_open = sum(r[2] for r in rows)
    return [
        "## Status reversals",
        "",
        "For each trial, the first version with a terminal status (TERMINATED, WITHDRAWN or "
        "COMPLETED), and what later versions show. CLAUDE.md Section 6 excludes a trial from "
        "training and evaluation when a terminal status is later replaced by an open one; Step "
        "4 applies that rule. Counts are trials, all study types and years.",
        "",
        *_table(
            [
                "First terminal status",
                "Trials",
                "Later open",
                "Later another terminal status",
                "Later UNKNOWN",
            ],
            [(s, _n(n), _n(o), _n(t), _n(u)) for s, n, o, t, u in rows],
        ),
        "",
        f"Reversals to an open status: {_n(total_open)} trials.",
        "",
    ]


def _text_section(con: duckdb.DuckDBPyConnection, info: dict[str, str]) -> list[str]:
    rows = [
        (
            f"`{f}`",
            _n(info[f"texts.{f}.distinct"]),
            _n(info[f"texts.{f}.html"]),
            _n(info[f"texts.{f}.email_removed"]),
            _n(info[f"texts.{f}.empty"]),
        )
        for f in TEXT_FIELDS
    ]
    emails_left = _one(
        con, f"SELECT count(*) FROM texts WHERE regexp_matches(text, '{EMAIL_PATTERN.pattern}')"
    )[0]
    titled = f"regexp_matches(sponsor_key, '{PERSONAL_TITLE_KEY}')"
    organization = f"regexp_matches(sponsor_key, '{ORGANIZATION_WORD_KEY}')"
    individual = _one(
        con,
        f"""SELECT count(*) FILTER (WHERE sponsor_is_individual AND lead_sponsor_class = 'INDIV'),
          count(*) FILTER (WHERE sponsor_is_individual AND lead_sponsor_class IS DISTINCT FROM
            'INDIV'),
          count(DISTINCT nct_id) FILTER (WHERE sponsor_is_individual),
          count(*) FILTER (WHERE sponsor_is_individual
            AND (lead_sponsor_name IS NOT NULL OR sponsor_key IS NOT NULL)),
          count(DISTINCT sponsor_key) FILTER (WHERE {titled} AND NOT {organization}),
          count(DISTINCT sponsor_key) FILTER (WHERE {titled} AND {organization})
        FROM versions""",
    )
    personal_columns = _one(
        con,
        f"""SELECT count(*) FROM information_schema.columns
        WHERE column_name IN {_in(PERSONAL_COLUMNS)}""",
    )[0]
    return [
        "## Text normalization and personal data",
        "",
        "The dataset stores most long texts as HTML with entities; API v2 returns Markdown. "
        "Both are reduced to the same plain text: one line per paragraph or list item, entities "
        "unescaped, Markdown escapes removed, email addresses replaced by a marker. Each text is "
        "stored once in `texts`, keyed by its SHA-256.",
        "",
        *_table(
            [
                "Field",
                "Distinct raw texts",
                "HTML",
                "Email address removed",
                "Empty after normalization",
            ],
            rows,
        ),
        "",
        f"- Distinct normalized texts stored: {_n(info['texts.rows'])}. Email addresses left in "
        f"stored text: {_n(emails_left)}.",
        f"- Individual sponsors: {_n(individual[0])} versions with class INDIV and "
        f"{_n(individual[1])} with a person's name under another class (a degree title such "
        "as PhD or MD, or a personal title such as Dr or Prof at the start, and no "
        f"organization word; ADRs 0012 and 0022), in {_n(individual[2])} trials. Names or keys "
        f"stored for them: {_n(individual[3])}. Their sponsor track record in Step 7 falls back "
        "to the class-level rate.",
        f"- Stored sponsor keys that open with a personal title and hold no organization word: "
        f"{_n(individual[4])}. Keys that open with one and hold an organization word (a "
        f"hospital, company or foundation named after a person): {_n(individual[5])}; these "
        "are kept as organizations.",
        f"- Personal-data columns in the warehouse: {_n(personal_columns)}.",
        "",
    ]


def _values_section(con: duckdb.DuckDBPyConnection, info: dict[str, str]) -> list[str]:
    unparsed_versions, old_ages, large, largest = _one(
        con,
        f"""SELECT
          (SELECT count(*) FROM raw_versions r JOIN versions v USING (nct_id, nct_version)
           WHERE (r.minimum_age IS NOT NULL AND v.minimum_age_years IS NULL)
              OR (r.maximum_age IS NOT NULL AND v.maximum_age_years IS NULL)),
          (SELECT count(*) FROM versions WHERE minimum_age_years > {IMPLAUSIBLE_AGE_YEARS}
             OR maximum_age_years > {IMPLAUSIBLE_AGE_YEARS}),
          (SELECT count(*) FROM versions WHERE enrollment_count >= {LARGE_ENROLLMENT}),
          (SELECT max(enrollment_count) FROM versions)""",
    )
    return [
        "## Ages and enrollment",
        "",
        f"- Age texts that do not parse (stored as missing): {_n(info['ages.unparsed_texts'])} "
        f"distinct texts in {_n(unparsed_versions)} versions.",
        f"- Versions with an age limit above {IMPLAUSIBLE_AGE_YEARS} years (kept; often a "
        f"stand-in for no limit): {_n(old_ages)}.",
        f"- Versions with an enrollment target of {LARGE_ENROLLMENT:,} or more (kept): "
        f"{_n(large)}; the largest is {_n(largest)}.",
        "",
    ]


def _validation_section(con: duckdb.DuckDBPyConnection) -> list[str]:
    passed = _one(con, "SELECT count(*) FROM versions")[0]
    failed = _one(con, "SELECT count(*) FROM versions_quarantine")[0]
    reasons = con.execute(
        "SELECT reasons, count(*) AS n FROM versions_quarantine GROUP BY reasons ORDER BY n DESC"
    ).fetchall()
    trials_hit, trials_lost = _one(
        con,
        """SELECT count(DISTINCT nct_id),
          count(DISTINCT nct_id) FILTER (WHERE nct_id NOT IN (SELECT nct_id FROM trials))
        FROM versions_quarantine""",
    )
    lines = [
        "## Validation (Pandera)",
        "",
        "Every canonical row is validated against the contract in "
        "`trialpulse.contracts.versions` (the same schema the live API v2 path uses). Rows that "
        "fail go to `versions_quarantine` with their reasons and are not used downstream.",
        "",
        f"- Validated: {_n(passed + failed)}. Passed: {_n(passed)}. Quarantined: {_n(failed)} "
        f"({_pct(failed, passed + failed)}).",
        f"- Trials with at least one quarantined version: {_n(trials_hit)} (the `trials` table "
        f"counts them in `quarantined_versions`); trials with every version quarantined: "
        f"{_n(trials_lost)}.",
    ]
    if reasons:
        lines += ["", *_table(["Reasons", "Versions"], [(r, _n(n)) for r, n in reasons])]
    return [*lines, ""]


def _parity_section(revision: str, parity_path: Path) -> list[str]:
    if not parity_path.is_file():
        return []
    parity = json.loads(parity_path.read_text(encoding="utf-8"))
    if parity.get("dataset_revision") != revision:
        return []
    rows = [
        (f"`{c}`", f"{v['agree']} of {v['total']}")
        for c, v in parity["per_column"].items()
        if v["agree"] != v["total"]
    ]
    unexplained = sum(1 for m in parity["mismatches"] if not m.get("cause"))
    lines = [
        "## Source parity sample (API v2)",
        "",
        f"`uv run python -m trialpulse.warehouse.parity` on {parity['checked_on']}: "
        f"{parity['sampled']} seeded trials, {parity['compared']} of them unchanged since the "
        "dataset (same last update posted date), mapped from API v2 with "
        "`canonical_from_api_v2` and compared column by column with their latest warehouse "
        f"version. Rows identical in every column (same content hash): "
        f"{parity['identical_rows']} of {parity['compared']}. Every other column agrees for "
        f"every compared trial. Differences without a known cause: {unexplained}.",
    ]
    if rows:
        lines += ["", *_table(["Column that differs in some trial", "Agree"], rows)]
    if parity.get("mismatches"):
        lines += ["", "Differences (trial: columns; cause):", ""]
        lines += [
            f"- {m['nct_id']}: {', '.join(m['columns'])}; {m.get('cause') or 'no known cause'}"
            for m in parity["mismatches"]
        ]
    return [*lines, ""]


def render_audit(
    con: duckdb.DuckDBPyConnection, glob: str, cfg: ProjectConfig, parity_path: Path = PARITY_PATH
) -> str:
    info = dict(con.execute("SELECT key, value FROM build_info").fetchall())
    counts = [(f"`{t}`", _n(_one(con, f"SELECT count(*) FROM {t}")[0])) for t in TABLES]
    lines = [
        "# Data audit, part 1: the version-history warehouse",
        "",
        "Generated by `uv run python -m trialpulse.warehouse.build` from the warehouse it builds "
        "(`data/warehouse.duckdb`). Counts only: no sponsor names and no free text. Part 2 (the "
        "cohort funnel and outcomes) is written by `uv run python -m trialpulse.cohort.build`.",
        "",
        *_source_section(con, glob, info),
        *_drift_section(con, glob),
        *_duplicates_section(con),
        *_order_section(con, info["dataset.cutoff"]),
        *_dates_section(con),
        *_enum_section(con),
        *_reversal_section(con, cfg),
        *_text_section(con, info),
        *_values_section(con, info),
        *_validation_section(con),
        *_parity_section(info["dataset.revision"], parity_path),
        "## Tables",
        "",
        *_table(["Table", "Rows"], counts),
        "",
    ]
    return "\n".join(lines)


def write_audit(
    warehouse: Path, glob: str, out_path: Path = AUDIT_PATH, cfg: ProjectConfig | None = None
) -> None:
    with duckdb.connect(str(warehouse), read_only=True) as con:
        text = render_audit(con, glob, cfg or load_project_config())
    # Part 2 belongs to the cohort build and is left as it is.
    replace_section(out_path, PART_1_HEADING, text, AUDIT_FAMILY)


def main(argv: Sequence[str] | None = None) -> int:
    """Regenerate docs/data_audit.md from the existing warehouse (the build also writes it)."""
    del argv
    if not WAREHOUSE_PATH.is_file():
        raise RefusedError("no warehouse; run: uv run python -m trialpulse.warehouse.build")
    cfg = load_project_config()
    write_audit(WAREHOUSE_PATH, dataset_glob(cfg), AUDIT_PATH, cfg)
    print(f"written: {AUDIT_PATH.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
