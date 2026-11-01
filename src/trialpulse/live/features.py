"""Features of the live path (ADR 0037): from the wide rows as of the run's date d, through
the final SELECT the offline build uses (`features.build.feature_select_sql`).

- **The population on d** (ADR 0034, decision 1): every wide row first posted from
  2008-01-01 through d whose state is interventional, open (SUSPENDED included) and not
  lapsed on d, with its reversal flag. `scoring.score.classify` then decides which are
  scored, flagged or not scored, as offline.
- **The open count on d** (ADR 0007): the wide rows whose state is open, interventional and
  not lapsed on d, whatever their first-post date: a trial without a wide row has ended.
- **One row per scored trial**: the latest-record fields, the version-0 values, the version
  count, the versions in the recent window (from the stored post dates), the sponsor's
  record from the sponsor file, the open count, and no text statistics (ADR 0026). The
  inputs relation is cast to the offline types, so the shared SELECT returns the same
  columns and types on both paths.
"""

import datetime as dt
from collections.abc import Mapping, Sequence

import duckdb
import numpy as np
import pandas as pd

from trialpulse.cohort.rules import CohortRules
from trialpulse.config import ProjectConfig
from trialpulse.features.select import feature_select_sql
from trialpulse.live.contract import LIVE_FIELDS
from trialpulse.live.sponsors import SponsorFile
from trialpulse.live.state import FIRST_FIELDS, Wide
from trialpulse.scoring import rows as served
from trialpulse.scoring.predict import keys_for

LIVE_INPUTS = "live_feature_inputs"
TYPES: dict[str, str] = {
    "trial_id": "VARCHAR",
    "landmark_index": "BIGINT",
    "landmark_date": "DATE",
    **{name: kind for name, kind in LIVE_FIELDS.items() if name != "sponsor_id"},
    "first_start_date": "DATE",
    "first_primary_completion_date": "DATE",
    "first_completion_date": "DATE",
    "first_enrollment_count": "BIGINT",
    "first_enrollment_type": "VARCHAR",
    "n_versions": "BIGINT",
    "n_versions_before_window": "BIGINT",
    "ever_suspended": "BOOLEAN",
    "sponsor_has_identity": "BOOLEAN",
    "sponsor_prior_registrations": "BIGINT",
    "sponsor_prior_ended": "BIGINT",
    "sponsor_prior_stopped": "BIGINT",
    "open_interventional_trials": "BIGINT",
    "eligibility_n_inclusion": "BIGINT",
    "eligibility_n_exclusion": "BIGINT",
    "eligibility_length": "BIGINT",
    "eligibility_n_thresholds": "BIGINT",
}


def is_open_on(rules: CohortRules, wide: Wide, day: dt.date) -> bool:
    row = wide.row
    return (
        row["status"] in rules.open_statuses
        and row.get("study_type") == rules.study_type
        and not (wide.lapse_from is not None and wide.lapse_from <= day)
    )


def open_count(rules: CohortRules, wides: Sequence[Wide], day: dt.date) -> int:
    return sum(1 for w in wides if w.effective_date <= day and is_open_on(rules, w, day))


def population(rules: CohortRules, wides: Sequence[Wide], day: dt.date) -> pd.DataFrame:
    """The open trials on `day`, in the columns of `scoring.score.population`."""
    rows = [
        {
            "trial_id": w.nct_id,
            "t0": w.row["study_first_post_date"],
            "reversal": w.reversal,
            "content_hash": w.row["feature_hash"],
            "sponsor_class": w.row.get("lead_sponsor_class") or "MISSING",
        }
        for w in wides
        if w.row.get("study_first_post_date") is not None
        and rules.min_first_post_date <= w.row["study_first_post_date"] <= day
        and w.effective_date <= day
        and is_open_on(rules, w, day)
    ]
    frame = pd.DataFrame(rows, columns=["trial_id", "t0", "reversal", "content_hash",
                                        "sponsor_class"])  # fmt: skip
    frame["t0"] = pd.to_datetime(frame["t0"])
    return frame.sort_values("trial_id").reset_index(drop=True)


def inputs_frame(
    wides: Mapping[str, Wide],
    keys: pd.DataFrame,
    sponsors: SponsorFile,
    open_trials: int,
) -> pd.DataFrame:
    """One row of feature inputs per served key (`scoring.features.keys_for`)."""
    out = []
    for key in keys.itertuples(index=False):
        wide = wides[str(key.trial_id)]
        day = pd.Timestamp(key.landmark_date).date()
        start = pd.Timestamp(key.window_start).date()
        recent = sum(1 for d in wide.recent_post_dates if start < d <= day)
        sponsor_id = wide.row.get("sponsor_id")
        record = sponsors.answer(day, sponsor_id, wide.nct_id) if sponsor_id else None
        row = {
            "trial_id": wide.nct_id,
            "landmark_index": int(key.landmark_index),
            "landmark_date": day,
            **{name: wide.row.get(name) for name in LIVE_FIELDS if name != "sponsor_id"},
            **{name: wide.first.get(name) for name in FIRST_FIELDS},
            "n_versions": wide.n_versions,
            "n_versions_before_window": wide.n_versions - recent,
            "ever_suspended": wide.ever_suspended,
            "sponsor_has_identity": record is not None,
            "sponsor_prior_registrations": record[0] if record else None,
            "sponsor_prior_ended": record[1] if record else None,
            "sponsor_prior_stopped": record[2] if record else None,
            "open_interventional_trials": open_trials,
        }
        out.append(row)
    frame = pd.DataFrame(out, columns=[c for c in TYPES if not c.startswith("eligibility_n")
                                       and c != "eligibility_length"])  # fmt: skip
    for name in ("intervention_types",):
        frame[name] = [list(v) if v is not None else None for v in frame[name]]
    return frame


def compute_live(
    wides: Mapping[str, Wide],
    keys: pd.DataFrame,
    sponsors: SponsorFile,
    open_trials: int,
    rules: CohortRules,
) -> pd.DataFrame:
    """The served features of the keys, as `scoring.features.compute_served` returns them
    offline: sorted by trial and index, the index in `true_index`, the time features held
    at k*."""
    frame = inputs_frame(wides, keys, sponsors, open_trials)
    casts = ", ".join(
        f"CAST({name} AS {kind}) AS {name}" if name in frame.columns
        else f"CAST(NULL AS {kind}) AS {name}"
        for name, kind in TYPES.items()
    )  # fmt: skip
    with duckdb.connect() as con:
        con.execute(f"CREATE TEMP TABLE {LIVE_INPUTS} AS SELECT {casts} FROM frame")
        out: pd.DataFrame = con.execute(
            f"SELECT * FROM ({feature_select_sql(LIVE_INPUTS)}) ORDER BY trial_id, landmark_index"
        ).df()
    out = out.rename(columns={"landmark_index": "true_index"})
    k_star = served.capped(out["true_index"].to_numpy(dtype=np.int64), rules.max_index)
    for name, values in served.time_features(k_star, rules.spacing_months).items():
        out[name] = values
    return out


def served_keys(scored: pd.DataFrame, day: dt.date, rules: CohortRules, cfg: ProjectConfig) -> (
    pd.DataFrame
):  # fmt: skip
    """The served keys of the scored trials on `day` (as `scoring.score.score` makes them)."""
    return keys_for(
        scored["trial_id"], scored["t0"], scored["true_index"],
        np.full(len(scored), np.datetime64(day, "D")), rules, cfg.features.recent_versions_months,
    )  # fmt: skip
