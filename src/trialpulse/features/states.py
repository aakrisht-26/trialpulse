"""What the registry showed at a landmark (CLAUDE.md Section 8, rule 1).

Everything here follows one rule: **a feature at landmark L reads only versions posted on
or before L.** The state at L is the latest such version (the higher version number when
two share a post date, Section 6). Values that describe the history up to a state (versions
so far, ever suspended, the first version's dates and target) are running values over the
versions in posting order, so they never look past the state they belong to.

**The whitelist.** `expose_versions` creates the `versions` view every feature query
reads. It holds the columns of `registry.ALLOWED_VERSION_FIELDS` and no other, so label-only
fields and names cannot be read by accident.

**The submitted status.** The registry labels a record UNKNOWN when it has gone unverified
for two years, and the dataset shows that label on the record's last version: a label
computed when the record was fetched, on a version posted long before. Read at a landmark,
it would say that no later version exists. The view therefore shows each version's submitted
status in `overall_status` (ADR 0014) and nothing in `last_known_status`, so no feature can
read the label.

**Calendar months.** The registry gave dates to the month through 2016 and mostly to the
day from 2017. Every feature that compares dates therefore compares calendar months
(`month_index`), so that the change of precision cannot become a signal. Counts over the
version clock (post dates, always days) use dates as they are.
"""

import duckdb

from trialpulse.cohort.rules import CohortRules, submitted_status_sql
from trialpulse.features.registry import ALLOWED_VERSION_FIELDS

KEYS_TABLE = "feature_keys"
STATES_TABLE = "feature_states"
AT_LANDMARK_TABLE = "feature_at_landmark"

# Columns of the first version that the amendment signals compare with.
FIRST_VERSION_FIELDS: tuple[str, ...] = (
    "primary_completion_date",
    "completion_date",
    "start_date",
    "enrollment_count",
    "enrollment_type",
)


def month_index(date: str) -> str:
    """SQL for a date's calendar month as a number: months differ by their difference."""
    return f"(year({date}) * 12 + month({date}))"


def expose_versions(con: duckdb.DuckDBPyConnection, source: str, rules: CohortRules) -> None:
    """Create the `versions` view the feature queries read: the whitelisted columns of
    `source` (a table or view of canonical versions) and nothing else, with the submitted
    status in the place of the registry's status."""
    shown = {
        "overall_status": f"{submitted_status_sql(rules)} AS overall_status",
        "last_known_status": "CAST(NULL AS VARCHAR) AS last_known_status",
    }
    columns = ", ".join(shown.get(name, f"v.{name}") for name in ALLOWED_VERSION_FIELDS)
    con.execute(f"CREATE OR REPLACE TEMP VIEW versions AS SELECT {columns} FROM {source} v")


def build_states(con: duckdb.DuckDBPyConnection, rules: CohortRules) -> None:
    """One row per trial and post date, for the trials that have a landmark key: the version
    in effect from that date, with the running values of its history."""
    first = ", ".join(
        f"first_value({name}) OVER history AS first_{name}" for name in FIRST_VERSION_FIELDS
    )
    status = submitted_status_sql(rules)
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE {STATES_TABLE} AS
        WITH v AS (
          SELECT v.*, {status} AS status,
            row_number() OVER history AS n_versions,
            coalesce(bool_or({status} = 'SUSPENDED') OVER history, false) AS ever_suspended,
            {first}
          FROM versions v
          WHERE v.nct_id IN (SELECT DISTINCT trial_id FROM {KEYS_TABLE})
          WINDOW history AS (PARTITION BY v.nct_id ORDER BY v.effective_date, v.nct_version
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        )
        SELECT * FROM v
        QUALIFY row_number() OVER (
          PARTITION BY nct_id, effective_date ORDER BY nct_version DESC) = 1"""
    )


def build_at_landmark(
    con: duckdb.DuckDBPyConnection, recent_months: int, spacing_months: int
) -> None:
    """One row per landmark key: the state at the landmark (`s` columns), and the number of
    versions posted up to the start of the recent window.

    The window is the `recent_months` up to the landmark. Landmarks are calendar months
    after the first post with the day clamped to the month (31 August plus 6 months is 28
    February), so "the landmark minus 6 months" is not always the previous landmark. The
    window starts at the first post plus the landmark's months minus `recent_months`, by the
    same calendar arithmetic as the landmarks: with the two equal, at the previous landmark.
    Before the first post there is no such day and the window starts `recent_months` before
    the landmark."""
    months = f"k.landmark_index * {int(spacing_months)} - {int(recent_months)}"
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE {AT_LANDMARK_TABLE} AS
        WITH now AS (
          SELECT k.trial_id, k.landmark_index, k.landmark_date, s.*
          FROM {KEYS_TABLE} k
          ASOF LEFT JOIN {STATES_TABLE} s
            ON s.nct_id = k.trial_id AND k.landmark_date >= s.effective_date
        ),
        first_post AS (
          SELECT nct_id, arg_min(study_first_post_date, nct_version) AS t0
          FROM versions WHERE nct_id IN (SELECT DISTINCT trial_id FROM {KEYS_TABLE})
          GROUP BY nct_id
        ),
        earlier AS (
          SELECT k.trial_id, k.landmark_index, r.n_versions AS n_versions_before_window
          FROM (SELECT k.trial_id, k.landmark_index,
                  CAST(CASE WHEN {months} >= 0 AND t.t0 IS NOT NULL
                    THEN t.t0 + to_months(CAST({months} AS INTEGER))
                    ELSE k.landmark_date - INTERVAL {int(recent_months)} MONTH END AS DATE)
                    AS window_start
                FROM {KEYS_TABLE} k LEFT JOIN first_post t ON t.nct_id = k.trial_id) k
          ASOF LEFT JOIN {STATES_TABLE} r
            ON r.nct_id = k.trial_id AND k.window_start >= r.effective_date
        )
        SELECT now.*, coalesce(earlier.n_versions_before_window, 0) AS n_versions_before_window
        FROM now JOIN earlier USING (trial_id, landmark_index)"""
    )
