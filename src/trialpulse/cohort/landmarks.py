"""Landmarks and the point-in-time population (CLAUDE.md Section 6, ADRs 0013 and 0014).

For every trial first posted on or after the population's first date, interventional in at
least one version and without a status reversal, the candidate landmarks are L_k = t0 + 6k
months (k = 0 to 6), computed with the same calendar arithmetic as the evaluation harness's
horizons (`trialpulse.dates`). A candidate becomes a landmark row only if, in this order:

1. the trial's follow-up has not ended at L: its event or censoring date is after L;
2. some version is public at L (the state at L exists);
3. the state at L is interventional (the population, ADR 0013);
4. the state at L has an open submitted status;
5. the state at L is not lapsed under the registry's UNKNOWN rule (ADR 0014).

Each rejected candidate keeps the first rule it fails, for the audit's funnel.

The output `cohort_landmarks` is the Step 8 input contract: one row per (trial, landmark)
with trial_id, landmark_index, landmark_date, event (0 censored, 1 early stop, 2
completion) and event_date (the event's date, or the censoring date when event is 0), plus
`stratum`, M0's stratum: the lead sponsor class of the state at L (ADR 0006), "MISSING"
when the state has none. Label columns stay out of it on purpose: every other column the
harness reads is passed to models as a feature.
"""

import duckdb
import numpy as np
import pandas as pd

from trialpulse.cohort.rules import CohortRules, sql_list
from trialpulse.dates import add_months

LANDMARK_COLUMNS: tuple[str, ...] = (
    "trial_id",
    "landmark_index",
    "landmark_date",
    "event",
    "event_date",
    "stratum",
)
MISSING_STRATUM = "MISSING"
EXCLUSION_ORDER: tuple[str, ...] = (
    "ended_or_censored",
    "no_version_yet",
    "not_interventional",
    "not_open",
    "lapsed",
)


def candidate_landmarks(trial_id: np.ndarray, t0: np.ndarray, rules: CohortRules) -> pd.DataFrame:
    """Every trial at k = 0 to max_index: L_k = t0 + spacing * k calendar months."""
    t0_days = np.asarray(t0, dtype="datetime64[D]")
    if len(t0_days) == 0:  # no eligible trial: an empty frame with the right types
        return pd.DataFrame(
            {
                "trial_id": pd.Series(dtype=object),
                "landmark_index": pd.Series(dtype=np.int64),
                "landmark_date": pd.Series(dtype="datetime64[s]"),
            }
        )
    frames = []
    for k in range(rules.max_index + 1):
        frames.append(
            pd.DataFrame(
                {
                    "trial_id": trial_id,
                    "landmark_index": np.full(len(trial_id), k, dtype=np.int64),
                    "landmark_date": add_months(t0_days, rules.spacing_months * k),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def build_landmarks(con: duckdb.DuckDBPyConnection, rules: CohortRules) -> None:
    """Needs cohort_states and cohort_outcomes. Writes cohort_landmark_candidates (every
    candidate with its exclusion reason, NULL when kept) and cohort_landmarks (kept rows)."""
    eligible = con.execute(
        """SELECT trial_id, t0 FROM cohort_outcomes
        WHERE in_window AND ever_in_population AND NOT reversal AND t0 IS NOT NULL
        ORDER BY trial_id"""
    ).fetchnumpy()
    candidates = candidate_landmarks(
        np.asarray(eligible["trial_id"]), np.asarray(eligible["t0"]), rules
    )
    con.register("landmark_candidates_frame", candidates)
    lapsed = "s.lapse_from IS NOT NULL AND s.lapse_from <= c.landmark_date"
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE cohort_landmark_candidates AS
        SELECT c.trial_id, c.landmark_index, CAST(c.landmark_date AS DATE) AS landmark_date,
          o.event, o.event_date, s.status, s.study_type, s.lead_sponsor_class,
          CASE
            WHEN o.event_date <= c.landmark_date THEN 'ended_or_censored'
            WHEN s.nct_id IS NULL THEN 'no_version_yet'
            WHEN s.study_type IS DISTINCT FROM '{rules.study_type}' THEN 'not_interventional'
            WHEN s.status NOT IN {sql_list(rules.open_statuses)} THEN 'not_open'
            WHEN {lapsed} THEN 'lapsed'
          END AS excluded
        FROM landmark_candidates_frame c
        JOIN cohort_outcomes o USING (trial_id)
        ASOF LEFT JOIN cohort_states s
          ON s.nct_id = c.trial_id AND c.landmark_date >= s.effective_date"""
    )
    con.unregister("landmark_candidates_frame")
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE cohort_landmarks AS
        SELECT trial_id, CAST(landmark_index AS BIGINT) AS landmark_index, landmark_date,
          CAST(event AS BIGINT) AS event, event_date,
          coalesce(lead_sponsor_class, '{MISSING_STRATUM}') AS stratum
        FROM cohort_landmark_candidates WHERE excluded IS NULL
        ORDER BY trial_id, landmark_index"""
    )
