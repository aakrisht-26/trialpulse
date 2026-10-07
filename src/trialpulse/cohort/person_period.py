"""Person-period rows for the discrete-time models (CLAUDE.md Section 6).

Each landmark row expands into up to four 6-month intervals after L: interval j covers
(L + 6(j - 1) months, L + 6j months], with the same calendar arithmetic as the harness's
horizons, so the CIF at 12 months is the sum over intervals 1 and 2, and an event on
exactly L + 12 months counts within 12 months, as the harness's "stopped by the horizon"
does. Per interval the outcome is 0 (continue), 1 (early stop) or 2 (completion):

- an event inside the interval sets the outcome and ends the sequence;
- a trial censored at c keeps the interval if c is on or after the interval's end (it was
  followed through the whole interval, the same convention as Aalen-Johansen's risk set);
  an interval censored before its end is dropped and ends the sequence;
- **an interval that ends after the end of observation is dropped for every trial, whatever
  happened in it.** Observation ends for all trials on the data cutoff (and, for training
  at a walk-forward origin T, on T). Keeping such an interval only when it holds an event
  would leave it with events and no continuations, and bias every hazard upward: on the
  2016 development origin that inflated the no-covariate CIF at 12 months from 0.0319 to
  0.0332, against Aalen-Johansen's 0.0318.

The table feeds the models without reshaping: the features are the landmark's (frozen at
L) plus `interval`; the target is `outcome`.

For walk-forward training at origin T, the cohort build expands the landmark rows of the
cohort built as of T (ADR 0016), with T as the end of observation. Those rows hold only
what was known before T: there is no step that truncates the final table at T, because the
final table knows things the origin did not (a lapse resolved later, a reversal posted
later, an UNKNOWN censoring that had not happened yet).
"""

from collections.abc import Mapping
from typing import Any

import numpy as np
import numpy.typing as npt

from trialpulse.cohort.rules import CohortRules
from trialpulse.dates import add_months
from trialpulse.eval import EVENT_CENSORED

OUTCOME_CONTINUE = 0
PERSON_PERIOD_COLUMNS: tuple[str, ...] = (
    "trial_id",
    "landmark_index",
    "landmark_date",
    "interval",
    "interval_start",
    "interval_end",
    "outcome",
    "event_date",
    "stratum",
)
LABEL_COLUMNS: tuple[str, ...] = ("outcome", "event_date", "interval_start", "interval_end")

Columns = dict[str, npt.NDArray[Any]]


def expand(landmarks: Mapping[str, npt.NDArray[Any]], rules: CohortRules) -> Columns:
    """Person-period columns from landmark columns (trial_id, landmark_index,
    landmark_date, event, event_date, stratum), ordered by landmark then interval. Only
    intervals that end on or before the end of observation (`rules.cutoff`: the data cutoff,
    or the origin for a cohort built as of an origin) exist."""
    landmark_date = np.asarray(landmarks["landmark_date"], dtype="datetime64[D]")
    event = np.asarray(landmarks["event"], dtype=np.int64)
    event_date = np.asarray(landmarks["event_date"], dtype="datetime64[D]")
    cutoff = np.datetime64(rules.cutoff, "D")
    row = np.arange(len(event))
    parts: list[Columns] = []
    for j in range(1, rules.n_intervals + 1):
        start = add_months(landmark_date, rules.interval_months * (j - 1))
        end = add_months(landmark_date, rules.interval_months * j)
        has_event = event != EVENT_CENSORED
        followed = np.where(has_event, event_date > start, event_date >= end)
        keep = followed & (end <= cutoff)
        outcome = np.where(has_event & (event_date <= end), event, OUTCOME_CONTINUE)
        parts.append(
            {
                "_row": row[keep],
                "interval": np.full(int(keep.sum()), j, dtype=np.int64),
                "interval_start": start[keep],
                "interval_end": end[keep],
                "outcome": outcome[keep].astype(np.int64),
            }
        )
    merged = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    order = np.lexsort((merged["interval"], merged["_row"]))
    source = merged["_row"][order]
    out: Columns = {
        "trial_id": np.asarray(landmarks["trial_id"])[source],
        "landmark_index": np.asarray(landmarks["landmark_index"], dtype=np.int64)[source],
        "landmark_date": landmark_date[source],
    }
    out.update(
        {k: merged[k][order] for k in ("interval", "interval_start", "interval_end", "outcome")}
    )
    out["event_date"] = event_date[source]
    out["stratum"] = np.asarray(landmarks["stratum"])[source]
    return out
