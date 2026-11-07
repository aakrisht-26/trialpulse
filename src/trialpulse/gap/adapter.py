"""The newer release's layout mapped to the pinned columns (ADR 0041, decision 4).

The release at `gap_read.commit` (anamnesis-data/clinical_trials_history) changed the layout
of the two configs TrialPulse reads. Every difference from the pinned revision is listed
here, and handled or refused; a difference not listed is refused (`check_schema`), never
passed through. The pinned schema is read from the pinned files themselves.

`core`:

- **Failed review.** A version that failed the registry's review was never public: its
  `last_update_post_date` is empty and `reset_notice_post_date` holds the reset notice's date.
  Such versions are outside the version clock, so they are left out and counted
  (`FAILED_REVIEW`). A version with neither date, or a public version with a reset notice,
  is refused.
- **New columns.** `reset_notice_post_date` (read above, then dropped); `is_obsolete` and
  `obsolete_by` (the card's flag of registrations merged into another; the pinned revision
  kept their rows, so they are kept and the flag dropped); `status_verified_date_precision`
  and `document_no_sap` (no TrialPulse code reads them; dropped).
- **Retyped columns.** Six columns the pinned files typed INTEGER because every value was
  NULL now carry their real types (`RETYPED`). No TrialPulse code reads them, and the
  pinned revision held no value in them; they are set to NULL of the pinned type.
- **Text.** Rich-text fields are Markdown instead of HTML. They pass unchanged:
  `contracts.text.normalize_text` reads Markdown as it reads API v2's (ADR 0012).

`interventions`:

- **Keyed by `ord`.** Entries repeating a (name, type) pair in a version are separate rows,
  and entries without a name are kept. The pinned config held one row per distinct pair and
  no entry without a name, which the features count (ADR 0020), so the adapter gives one row
  per distinct (name, type), the description of its first entry, and leaves out and counts
  the entries without a name. `ord`, `is_obsolete` and `obsolete_by` are dropped.
"""

from collections.abc import Mapping
from dataclasses import dataclass

import duckdb

from trialpulse.cli import RefusedError
from trialpulse.ingest.history import refuse_on_a_runner

FAILED_REVIEW = "failed review: never public, left out of the version clock"
NEW_CORE: Mapping[str, str] = {
    "reset_notice_post_date": "read to tell a version that failed review, then dropped",
    "is_obsolete": "dropped: the pinned revision kept the rows of merged registrations",
    "obsolete_by": "dropped, with is_obsolete",
    "status_verified_date_precision": "dropped: no TrialPulse code reads it",
    "document_no_sap": "dropped: no TrialPulse code reads it",
}
RETYPED: frozenset[str] = frozenset({
    "version_holder", "expanded_access_status_for_nct_id", "unposted_responsible_party",
    "first_mcp_post_date", "first_mcp_post_date_type", "estimated_results_first_submit_date",
})  # fmt: skip
NEW_INTERVENTIONS: Mapping[str, str] = {
    "ord": "dropped: one row per distinct (name, type), as the pinned config held",
    "is_obsolete": "dropped",
    "obsolete_by": "dropped",
}


def schema(con: duckdb.DuckDBPyConnection, glob: str) -> dict[str, str]:
    rows = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{glob}', union_by_name=true)"
    ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows}


def check_schema(
    new: Mapping[str, str],
    pinned: Mapping[str, str],
    known_new: Mapping[str, str],
    retyped: frozenset[str] = frozenset(),
) -> None:
    """Refuse any difference not listed: a pinned column missing, a new column not in
    `known_new`, or a type change outside `retyped` (whose pinned type must be INTEGER, the
    type of a column that held no value)."""
    problems = [f"missing: {c}" for c in pinned if c not in new]
    problems += [f"new and not handled: {c}" for c in new if c not in pinned and c not in known_new]
    for column, kind in pinned.items():
        changed = column in new and new[column] != kind
        if changed and (column not in retyped or kind != "INTEGER"):
            problems.append(f"type changed: {column} {kind} to {new[column]}")
    if problems:
        raise RefusedError("the release's layout differs from the pinned one in a way the "
                           "adapter does not handle: " + "; ".join(problems))  # fmt: skip


@dataclass(frozen=True)
class Adapted:
    """The SELECTs that give the pinned layout, and what they leave out."""

    core_sql: str
    interventions_sql: str


def adapt(
    con: duckdb.DuckDBPyConnection,
    new_core: str,
    new_interventions: str,
    pinned_core: str,
    pinned_interventions: str,
) -> Adapted:
    """Check both configs' layouts and give the SELECTs of the pinned columns (see the module
    text). Refuses a failed-review row that breaks the card's rule."""
    refuse_on_a_runner()  # the release is read on the laptop only (ADR 0040)
    core_new, core_pinned = schema(con, new_core), schema(con, pinned_core)
    check_schema(core_new, core_pinned, NEW_CORE, RETYPED)
    iv_new, iv_pinned = schema(con, new_interventions), schema(con, pinned_interventions)
    check_schema(iv_new, iv_pinned, NEW_INTERVENTIONS)
    broken = con.execute(
        f"""SELECT count(*) FILTER (WHERE last_update_post_date IS NULL
                                    AND reset_notice_post_date IS NULL),
                   count(*) FILTER (WHERE last_update_post_date IS NOT NULL
                                    AND reset_notice_post_date IS NOT NULL)
        FROM read_parquet('{new_core}', union_by_name=true)"""
    ).fetchone()
    if broken and (broken[0] or broken[1]):
        raise RefusedError(
            f"{broken[0]} versions have neither a post date nor a reset notice, and "
            f"{broken[1]} public versions have a reset notice"
        )
    columns = ", ".join(
        f"CAST(NULL AS {kind}) AS {column}" if column in RETYPED and core_new[column] != kind
        else column
        for column, kind in core_pinned.items()
    )  # fmt: skip
    core_sql = (f"SELECT {columns} FROM read_parquet('{new_core}', union_by_name=true) "
                "WHERE last_update_post_date IS NOT NULL")  # fmt: skip
    pinned_iv = ", ".join(iv_pinned)
    interventions_sql = f"""SELECT {pinned_iv} FROM (
          SELECT *, row_number() OVER (PARTITION BY nct_id, nct_version, intervention_name,
                                        intervention_type ORDER BY ord) AS first_entry
          FROM read_parquet('{new_interventions}', union_by_name=true)
          WHERE intervention_name IS NOT NULL)
        WHERE first_entry = 1"""
    return Adapted(core_sql, interventions_sql)


def left_out(
    con: duckdb.DuckDBPyConnection, new_core: str, new_interventions: str
) -> dict[str, int]:
    """What the adapter leaves out, counted."""
    refuse_on_a_runner()
    core = con.execute(
        f"""SELECT count(*) FILTER (WHERE last_update_post_date IS NULL),
                   count(DISTINCT nct_id) FILTER (WHERE last_update_post_date IS NULL),
                   count(*) FILTER (WHERE is_obsolete)
        FROM read_parquet('{new_core}', union_by_name=true)"""
    ).fetchone()
    iv = con.execute(
        f"""SELECT count(*) FILTER (WHERE intervention_name IS NULL),
                   count(*) - count(DISTINCT (nct_id, nct_version, intervention_name,
                                              intervention_type))
        FROM read_parquet('{new_interventions}', union_by_name=true)"""
    ).fetchone()
    if core is None or iv is None:
        raise RefusedError("the counts of what the adapter leaves out could not be read")
    return {
        "failed_review_versions": int(core[0]),
        "failed_review_trials": int(core[1]),
        "obsolete_rows_kept": int(core[2]),
        "interventions_without_a_name": int(iv[0]),
        "interventions_repeating_a_pair": int(iv[1]),
    }
