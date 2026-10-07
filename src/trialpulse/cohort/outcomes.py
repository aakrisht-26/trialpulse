"""States and trial-level outcomes (CLAUDE.md Section 6, ADR 0014).

Input: a table or view `versions` with the canonical version columns. Output tables, in the
same DuckDB connection:

- `cohort_states`: one row per (trial, post date), the state from that date on: the submitted
  status, study type, sponsor class, the date the next state starts, and `lapse_from`, the
  first day the registry's UNKNOWN rule holds for this state (NULL if it never can).
- `cohort_outcomes`: one row per trial:
  - `event` 1 (early stop: the first version whose status is TERMINATED or WITHDRAWN) or 2
    (completion: the first version whose status is COMPLETED), whichever comes first, with
    `event_date` its post date;
  - otherwise `event` 0 and `event_date` the censoring date: the status verified date of
    the last state when that state is lapsed at the data cutoff (`censor_reason` unknown),
    else the data cutoff (`censor_reason` cutoff);
  - `reversal`: an open status after the first terminal version (such trials are excluded
    from training and evaluation, Section 6). A bare UNKNOWN after a terminal version counts
    too: the registry shows UNKNOWN only over an open status;
  - `lapse_date`: the day the last state lapsed, for the UNKNOWN sensitivity analysis.

Versions posted after the data cutoff are ignored, so the result depends only on the pinned
revision.

States follow post dates and the first terminal version follows version numbers. The two
orders agree only while a trial's post dates never decrease with its version number, so
`check_version_order` refuses a build on input where they do (the pinned dataset has none).
"""

import duckdb

from trialpulse.cli import RefusedError
from trialpulse.cohort.rules import (
    CohortRules,
    completion_ref_sql,
    lapse_from_sql,
    sql_date,
    sql_list,
    submitted_status_sql,
    verified_sql,
)


def check_version_order(con: duckdb.DuckDBPyConnection, rules: CohortRules) -> None:
    """Refuse versions whose post date is earlier than a lower-numbered version's."""
    row = con.execute(
        f"""SELECT count(*), count(DISTINCT nct_id) FROM (
          SELECT nct_id, effective_date < max(effective_date) OVER (
            PARTITION BY nct_id ORDER BY nct_version
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS earlier
          FROM versions WHERE effective_date <= {sql_date(rules.last_post_date)})
          WHERE earlier"""
    ).fetchone()
    if row and row[0]:
        raise RefusedError(
            f"{row[0]} versions of {row[1]} trials are posted earlier than a lower-numbered "
            "version; the cohort rules assume post dates never decrease with the version number"
        )


def build_states(con: duckdb.DuckDBPyConnection, rules: CohortRules) -> None:
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE cohort_states AS
        WITH s AS (
          SELECT v.nct_id, v.nct_version, v.effective_date,
            {submitted_status_sql(rules)} AS status,
            v.study_type, v.lead_sponsor_class, v.status_verified_date,
            {verified_sql()} AS verified,
            {completion_ref_sql()} AS completion_ref
          FROM versions v
          WHERE v.effective_date <= {sql_date(rules.last_post_date)}
          QUALIFY row_number() OVER (
            PARTITION BY v.nct_id, v.effective_date ORDER BY v.nct_version DESC) = 1
        )
        SELECT *,
          lead(effective_date) OVER (PARTITION BY nct_id ORDER BY effective_date)
            AS next_effective,
          {lapse_from_sql(rules, "status", "effective_date", "completion_ref", "verified")}
            AS lapse_from
        FROM s"""
    )


def build_outcomes(con: duckdb.DuckDBPyConnection, rules: CohortRules) -> None:
    """Needs cohort_states (build_states)."""
    cutoff = sql_date(rules.cutoff)
    terminal = sql_list(rules.terminal)
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE cohort_outcomes AS
        WITH v AS (
          SELECT v.nct_id, v.nct_version, v.effective_date, v.overall_status,
            {submitted_status_sql(rules)} AS status, v.study_type, v.study_first_post_date
          FROM versions v WHERE v.effective_date <= {sql_date(rules.last_post_date)}
        ),
        trial AS (
          SELECT nct_id,
            arg_min(study_first_post_date, nct_version) AS t0,
            coalesce(bool_or(study_type = '{rules.study_type}'), false) AS ever_in_population,
            min(nct_version) FILTER (WHERE status IN {terminal}) AS first_terminal_version,
            arg_max(overall_status, nct_version) IN {sql_list(rules.unknown_labels)}
              AS registry_unknown
          FROM v GROUP BY nct_id
        ),
        term AS (
          SELECT t.nct_id, v.status AS terminal_status, v.effective_date AS terminal_date
          FROM trial t JOIN v ON v.nct_id = t.nct_id AND v.nct_version = t.first_terminal_version
        ),
        rev AS (
          SELECT t.nct_id,
            bool_or(v.status IN {sql_list((*rules.open_statuses, *rules.unknown_labels))})
              AS reversal
          FROM trial t JOIN v ON v.nct_id = t.nct_id AND v.nct_version > t.first_terminal_version
          GROUP BY t.nct_id
        ),
        last_state AS (
          SELECT nct_id, lapse_from, coalesce(status_verified_date, effective_date) AS verified
          FROM cohort_states WHERE next_effective IS NULL
        )
        SELECT trial.nct_id AS trial_id, trial.t0, year(trial.t0) AS first_post_year,
          trial.t0 >= {sql_date(rules.min_first_post_date)} AS in_window,
          trial.ever_in_population,
          coalesce(rev.reversal, false) AS reversal,
          term.terminal_status, term.terminal_date,
          CASE WHEN term.terminal_status IS NULL AND ls.lapse_from <= {cutoff}
            THEN ls.lapse_from END AS lapse_date,
          CASE WHEN term.terminal_status IN {sql_list(rules.early_stop)} THEN 1
               WHEN term.terminal_status IN {sql_list(rules.competing)} THEN 2
               ELSE 0 END AS event,
          CASE WHEN term.terminal_status IS NOT NULL THEN term.terminal_date
               WHEN ls.lapse_from <= {cutoff} THEN least(ls.verified, {cutoff})
               ELSE {cutoff} END AS event_date,
          CASE WHEN term.terminal_status IS NOT NULL THEN NULL
               WHEN ls.lapse_from <= {cutoff} THEN 'unknown'
               ELSE 'cutoff' END AS censor_reason,
          trial.registry_unknown
        FROM trial
        LEFT JOIN term USING (nct_id)
        LEFT JOIN rev USING (nct_id)
        LEFT JOIN last_state ls USING (nct_id)
        ORDER BY trial_id"""
    )
