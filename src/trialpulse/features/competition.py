"""Competition as of the landmark (CLAUDE.md Section 8, ADR 0007).

The number of interventional trials in the registry that are open on the landmark day, from
every trial's status history. A record counts as open from the post date of a version with
an open submitted status and the interventional study type, until the next version's post
date, or until the day the registry's UNKNOWN rule applies to it (ADR 0014), whichever comes
first. The count includes the row's own trial.

Whether a record is open on day L depends on the version in effect on L and on that
version's own dates. Later versions only decide when the interval ends, and it ends after L
either way, so the count on L does not depend on anything posted after L.
"""

import duckdb

from trialpulse.cohort.outcomes import build_states as build_cohort_states
from trialpulse.cohort.rules import CohortRules, sql_list
from trialpulse.features.states import KEYS_TABLE

COMPETITION_TABLE = "feature_competition"


def build_competition(con: duckdb.DuckDBPyConnection, rules: CohortRules) -> None:
    """Writes `feature_competition`: the open interventional trials on each landmark date.
    Reads the `versions` view through the cohort's state table (every trial)."""
    build_cohort_states(con, rules)
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE {COMPETITION_TABLE} AS
        WITH open_intervals AS (
          SELECT effective_date AS opened,
            least(coalesce(next_effective, DATE '9999-12-31'),
                  coalesce(lapse_from, DATE '9999-12-31')) AS closed
          FROM cohort_states
          WHERE status IN {sql_list(rules.open_statuses)} AND study_type = '{rules.study_type}'
        ),
        changes AS (
          SELECT day, sum(change) AS change FROM (
            SELECT opened AS day, 1 AS change FROM open_intervals WHERE closed > opened
            UNION ALL
            SELECT closed AS day, -1 AS change FROM open_intervals
            WHERE closed > opened AND closed < DATE '9999-12-31'
          ) GROUP BY day
        ),
        running AS (
          SELECT day, sum(change) OVER (ORDER BY day) AS open_trials FROM changes
        )
        SELECT d.landmark_date,
          CAST(coalesce(r.open_trials, 0) AS BIGINT) AS open_interventional_trials
        FROM (SELECT DISTINCT landmark_date FROM {KEYS_TABLE}) d
        ASOF LEFT JOIN running r ON d.landmark_date >= r.day"""
    )
