"""The future-perturbation check (CLAUDE.md Section 8, rule 5).

    uv run python -m trialpulse.features.leakage

`rewrite_after` rewrites the registry after a day L. Every version posted after L gets
another value in every field a feature may read (`REWRITTEN_FIELDS` covers the whole
whitelist, and a test says so), a later post date, and one or two interventions of any type;
about a fifth of those versions are dropped; and every trial gets one more version after its
last. On or before L one thing changes: a version the registry shows as UNKNOWN is shown
with its submitted status instead, because the registry computes that label long after the
version was posted (ADR 0014). If a feature at a landmark on or before L read anything from
after L, it would change.

The tests run this on a synthetic registry for 500 sampled landmarks, each against the
rewrite after its own day. The command runs it on the real warehouse: for each walk-forward
origin T it rewrites everything posted after T, of every trial, recomputes the features of
every landmark row dated on or before T, and compares them with the rows of the last build.
It reads the warehouse read-only and writes nothing. A leak that looks only a short way
ahead would show in the rows dated just before an origin; the synthetic test is the one
that puts every sampled landmark right before its own rewrite.

The rewritten registry is a pair of views over the warehouse, so nothing is copied: the
features are computed from them as they are from the warehouse.
"""

import argparse
import datetime as dt
import time
from collections.abc import Sequence
from pathlib import Path

import duckdb

from trialpulse.cli import RefusedError, run
from trialpulse.cohort.build import COHORT_DIR
from trialpulse.cohort.rules import CohortRules, sql_date, submitted_status_sql
from trialpulse.config import load_project_config
from trialpulse.features.build import (
    FEATURE_TABLE,
    FEATURES_DIR,
    FEATURES_NAME,
    TEXT_KEYS_NAME,
    TEXT_KEYS_TABLE,
    cohort_key_files,
    compute_features,
    connect,
    prepare,
)
from trialpulse.features.registry import (
    ALLOWED_VERSION_FIELDS,
    INTERVENTION_TYPES,
    KEY_COLUMNS,
    TEXT_FIELDS,
    built_columns,
)
from trialpulse.features.states import KEYS_TABLE, expose_versions
from trialpulse.ingest.history import RAW_HISTORY_DIR, config_glob
from trialpulse.warehouse.build import TEMP_DIR, WAREHOUSE_PATH

REWRITTEN_VERSIONS = "rewritten_versions"
REWRITTEN_INTERVENTIONS = "rewritten_intervention_versions"
TRIAL_ENDS = "rewrite_trial_ends"
ADDED_VERSION_OFFSET = 100_000
MAX_SHIFT_DAYS = 300
_STATUSES = (
    "WITHDRAWN",
    "COMPLETED",
    "RECRUITING",
    "TERMINATED",
    "SUSPENDED",
    "NOT_YET_RECRUITING",
    "UNKNOWN",
)
# Fields that say which version a row is. The rewrite keeps them (a later post date apart).
IDENTIFYING_FIELDS: tuple[str, ...] = ("nct_id", "nct_version")


def _pick(values: Sequence[str], seed: str) -> str:
    cases = " ".join(f"WHEN {i} THEN '{v}'" for i, v in enumerate(values))
    return f"(CASE {seed} % {len(values)} {cases} END)"


def replacements(seed: str) -> dict[str, str]:
    """SQL for the value a version posted after the day gets, per field. `seed` is SQL for
    a number that differs from version to version."""
    days = f"CAST({seed} % 2000 AS INTEGER)"
    either = f"(CASE WHEN {seed} % 2 = 0 THEN 'ACTUAL' ELSE 'ESTIMATED' END)"
    precision = f"(CASE WHEN {seed} % 2 = 0 THEN 'day' ELSE 'month' END)"
    text = f"'rewritten-' || CAST({seed} AS VARCHAR)"
    return {
        "overall_status": _pick(_STATUSES, seed),
        "last_known_status": f"(CASE WHEN {seed} % 3 = 0 THEN 'RECRUITING' END)",
        "study_type": f"(CASE WHEN {seed} % 3 = 0 THEN 'OBSERVATIONAL' ELSE 'INTERVENTIONAL' END)",
        "study_first_post_date": f"DATE '1999-01-01' + {days}",
        "status_verified_date": f"DATE '2001-01-01' + {days}",
        "start_date": f"DATE '2003-01-01' + {days}",
        "start_date_precision": precision,
        "start_date_type": either,
        "primary_completion_date": f"DATE '2031-01-01' + {days}",
        "primary_completion_date_precision": precision,
        "primary_completion_date_type": either,
        "completion_date": f"DATE '2033-01-01' + {days}",
        "completion_date_precision": precision,
        "completion_date_type": either,
        "allocation": "'REWRITTEN'",
        "intervention_model": "'REWRITTEN'",
        "primary_purpose": "'REWRITTEN'",
        "masking": "'REWRITTEN'",
        "healthy_volunteers": f"({seed} % 2 = 0)",
        "sex": "'REWRITTEN'",
        "minimum_age_years": f"CAST({seed} % 50 AS DOUBLE)",
        "maximum_age_years": f"CAST(50 + {seed} % 50 AS DOUBLE)",
        "enrollment_count": f"CAST(1 + {seed} % 5000 AS BIGINT)",
        "enrollment_type": either,
        "lead_sponsor_class": "'INDUSTRY'",
        "organization_class": "'INDUSTRY'",
        "sponsor_key": f"'rewritten sponsor ' || CAST({seed} % 40 AS VARCHAR)",
        "sponsor_is_individual": f"({seed} % 11 = 0)",
        "brief_title_hash": text,
        "official_title_hash": text,
        "brief_summary_hash": text,
        "eligibility_criteria_hash": text,
    }


# Every field the rewrite changes after the day: the post date, and the fields above.
REWRITTEN_FIELDS: tuple[str, ...] = ("effective_date", *replacements("0"))


def rewrite_after(
    con: duckdb.DuckDBPyConnection,
    versions: str,
    interventions: str,
    day: dt.date,
    rules: CohortRules,
    salt: int = 0,
) -> None:
    """Create the views `rewritten_versions` and `rewritten_intervention_versions` over the
    tables or views `versions` (canonical version columns) and `interventions` (nct_id,
    nct_version, intervention_types): the same registry up to `day`, another one after it.

    Post dates after the day move later by a number of days that is the same for all
    versions of a trial, so they stay after the day and in the order of the version
    numbers, as the cohort rules require."""
    late = f"v.effective_date > {sql_date(day)}"
    seed = f"(hash(v.nct_id, v.nct_version, {int(salt)}) % 1000003)"
    shift = f"CAST(hash(v.nct_id, {int(salt)}) % {MAX_SHIFT_DAYS} AS INTEGER)"
    available = [row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {versions}").fetchall()]
    changed = {
        name: f"CASE WHEN {late} THEN {sql} ELSE v.{name} END"
        for name, sql in replacements(seed).items()
        if name in available
    }
    changed["effective_date"] = (
        f"CASE WHEN {late} THEN v.effective_date + {shift} ELSE v.effective_date END"
    )
    if "overall_status" in changed and "last_known_status" in changed:
        # On or before the day, the registry's later label gives way to the submitted status.
        status = replacements(seed)
        changed["overall_status"] = (
            f"CASE WHEN {late} THEN {status['overall_status']} "
            f"ELSE {submitted_status_sql(rules)} END"
        )
        changed["last_known_status"] = (
            f"CASE WHEN {late} THEN {status['last_known_status']} "
            f"WHEN v.overall_status = {submitted_status_sql(rules)} THEN v.last_known_status END"
        )
    kept = ", ".join(f"{changed.get(name, 'v.' + name)} AS {name}" for name in available)
    # The added version copies the trial's first version and is posted after its last one.
    added = {
        "nct_version": f"v.nct_version + {ADDED_VERSION_OFFSET}",
        "effective_date": (
            f"greatest(e.last_date, {sql_date(day)}) + {MAX_SHIFT_DAYS} + 1 "
            f"+ CAST(hash(v.nct_id, {int(salt)}) % 400 AS INTEGER)"
        ),
        "overall_status": "'TERMINATED'",
        "sponsor_key": "'rewritten added sponsor'",
        "sponsor_is_individual": "false",
    }
    extra = ", ".join(f"{added.get(name, 'v.' + name)} AS {name}" for name in available)
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE {TRIAL_ENDS} AS
        SELECT nct_id, min(nct_version) AS first_version, max(effective_date) AS last_date
        FROM {versions} GROUP BY nct_id"""
    )
    con.execute(
        f"""CREATE OR REPLACE TEMP VIEW {REWRITTEN_VERSIONS} AS
        SELECT {kept} FROM {versions} v WHERE NOT ({late} AND {seed} % 5 = 0)
        UNION ALL
        SELECT {extra} FROM {versions} v
        JOIN {TRIAL_ENDS} e ON e.nct_id = v.nct_id AND e.first_version = v.nct_version"""
    )
    # One or two interventions of any type, so that every type flag and both counts move.
    first = _pick(INTERVENTION_TYPES, seed)
    second = _pick(INTERVENTION_TYPES, f"({seed} // {len(INTERVENTION_TYPES)})")
    kinds = f"CASE WHEN {seed} % 2 = 0 THEN [{first}] ELSE [{first}, {second}] END"
    con.execute(
        f"""CREATE OR REPLACE TEMP VIEW {REWRITTEN_INTERVENTIONS} AS
        SELECT v.nct_id, v.nct_version,
          CASE WHEN {late} THEN {kinds} ELSE i.intervention_types END AS intervention_types
        FROM {versions} v LEFT JOIN {interventions} i USING (nct_id, nct_version)
        WHERE {late} OR i.nct_id IS NOT NULL"""
    )


def differing_rows(
    con: duckdb.DuckDBPyConnection, expected: str, expected_text: str, day: dt.date
) -> tuple[int, int]:
    """Landmark rows on or before `day`, and how many of them differ between the freshly
    computed tables and the expected ones (in a feature or in a text hash), counting rows
    that exist on one side only."""
    columns = ", ".join((*KEY_COLUMNS, *built_columns()))
    text_columns = ", ".join(("trial_id", "landmark_index", *(f"{f}_hash" for f in TEXT_FIELDS)))
    limit = f"landmark_date <= {sql_date(day)}"
    row = con.execute(
        f"""WITH fresh AS (
          SELECT f.trial_id, f.landmark_index, hash(f) AS features, hash(k) AS texts
          FROM (SELECT {columns} FROM {FEATURE_TABLE} WHERE {limit}) f
          JOIN (SELECT {text_columns} FROM {TEXT_KEYS_TABLE}) k USING (trial_id, landmark_index)
        ),
        wanted AS (
          SELECT f.trial_id, f.landmark_index, hash(f) AS features, hash(k) AS texts
          FROM (SELECT {columns} FROM {expected} WHERE {limit}) f
          JOIN (SELECT {text_columns} FROM {expected_text}) k USING (trial_id, landmark_index)
        )
        SELECT (SELECT count(*) FROM fresh),
          count(*) FILTER (WHERE a.features IS DISTINCT FROM b.features
                             OR a.texts IS DISTINCT FROM b.texts)
        FROM fresh a FULL JOIN wanted b USING (trial_id, landmark_index)"""
    ).fetchone() or (0, 0)
    return int(row[0]), int(row[1])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Future-perturbation check on the real data.")
    parser.add_argument("--warehouse", type=Path, default=WAREHOUSE_PATH)
    parser.add_argument("--cohort-dir", type=Path, default=COHORT_DIR)
    parser.add_argument("--features-dir", type=Path, default=FEATURES_DIR)
    parser.add_argument("--interventions", default=None)
    parser.add_argument("--temp-dir", type=Path, default=TEMP_DIR, help="where DuckDB spills")
    args = parser.parse_args(argv)
    cfg = load_project_config()
    rules = CohortRules.from_config(cfg)
    origins = [origin.date for origin in cfg.walk_forward.origins]
    features = args.features_dir / FEATURES_NAME
    text_keys = args.features_dir / TEXT_KEYS_NAME
    for path in (features, text_keys):
        if not path.is_file():
            raise RefusedError(
                f"{path} is missing. Create it with: uv run python -m trialpulse.features.build"
            )
    interventions = args.interventions or config_glob(
        RAW_HISTORY_DIR, str(cfg.dataset.revision), "interventions"
    )
    failed = False
    with connect(args.temp_dir) as con:
        files = cohort_key_files(args.cohort_dir, origins)
        prepare(con, args.warehouse, interventions, files, rules)
        con.execute(
            f"CREATE TEMP VIEW built AS SELECT * FROM read_parquet('{features.as_posix()}')"
        )
        con.execute(
            f"CREATE TEMP VIEW built_text AS SELECT * FROM read_parquet('{text_keys.as_posix()}')"
        )
        # The registry as the warehouse holds it, without the fields no feature may read.
        con.execute(
            f"""CREATE TEMP VIEW registry_versions AS
            SELECT {", ".join(ALLOWED_VERSION_FIELDS)} FROM wh.versions"""
        )
        con.execute("ALTER TABLE intervention_versions RENAME TO real_intervention_versions")
        con.execute(f"ALTER TABLE {KEYS_TABLE} RENAME TO all_feature_keys")
        total = con.execute("SELECT count(*) FROM wh.versions").fetchone() or (0,)
        for origin in origins:
            started = time.monotonic()
            late = con.execute(
                "SELECT count(*) FROM wh.versions WHERE effective_date > ?", [origin]
            ).fetchone() or (0,)
            con.execute(
                f"""CREATE OR REPLACE TEMP TABLE {KEYS_TABLE} AS
                SELECT * FROM all_feature_keys WHERE landmark_date <= {sql_date(origin)}"""
            )
            rewrite_after(con, "registry_versions", "real_intervention_versions", origin, rules)
            expose_versions(con, REWRITTEN_VERSIONS, rules)
            con.execute(
                f"""CREATE OR REPLACE TEMP VIEW intervention_versions AS
                SELECT * FROM {REWRITTEN_INTERVENTIONS}"""
            )
            compute_features(con, rules, cfg)
            rows, differing = differing_rows(con, "built", "built_text", origin)
            failed |= differing > 0
            print(
                f"{origin}: {late[0]:,} of {total[0]:,} versions posted after it were rewritten; "
                f"{rows:,} landmark rows on or before it, {differing:,} differ from the build "
                f"({time.monotonic() - started:.0f} s)",
                flush=True,
            )
    if failed:
        raise RefusedError("a feature changed when later versions were rewritten: a leak")
    print("No feature at a landmark changed when the versions posted after it were rewritten.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
