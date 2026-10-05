"""Person-period rows for the discrete-time models (CLAUDE.md Section 6).

Each landmark row expands into up to four 6-month intervals after L: interval j covers
(L + 6(j - 1) months, L + 6j months], with the same calendar arithmetic as the harness's
horizons, so the CIF at 12 months is the sum over intervals 1 and 2, and an event on
exactly L + 12 months counts within 12 months, as the harness's "stopped by the horizon"
does. Per interval the outcome is 0 (continue), 1 (early stop) or 2 (completion):

- an event inside the interval sets the outcome and ends the sequence;
- a trial censored at c keeps the interval if c is on or after the interval's end (it was
  followed through the whole interval, the same convention as Aalen-Johansen's risk set);
  an interval censored before its end is dropped and ends the sequence.

The table feeds the models without reshaping: the features are the landmark's (frozen at
L) plus `interval`; the target is `outcome`. For walk-forward training at origin T,
`training_rows` applies Section 6's administrative censoring at T to these rows with a
filter and a recode, and nothing else.
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
    landmark_date, event, event_date, stratum), ordered by landmark then interval."""
    landmark_date = np.asarray(landmarks["landmark_date"], dtype="datetime64[D]")
    event = np.asarray(landmarks["event"], dtype=np.int64)
    event_date = np.asarray(landmarks["event_date"], dtype="datetime64[D]")
    row = np.arange(len(event))
    parts: list[Columns] = []
    for j in range(1, rules.n_intervals + 1):
        start = add_months(landmark_date, rules.interval_months * (j - 1))
        end = add_months(landmark_date, rules.interval_months * j)
        has_event = event != EVENT_CENSORED
        keep = np.where(has_event, event_date > start, event_date >= end)
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


def training_rows(rows: Mapping[str, npt.NDArray[Any]], origin: np.datetime64) -> Columns:
    """The rows known at origin T, for training: landmarks before T, with every outcome
    administratively censored at T (Section 6), as the harness's training_rows does for
    landmark rows. An event dated T or later becomes unknown; an interval that ends after T
    without a known event was censored before its end and is dropped."""
    origin = np.datetime64(origin, "D")
    landmark_date = np.asarray(rows["landmark_date"], dtype="datetime64[D]")
    outcome = np.asarray(rows["outcome"], dtype=np.int64)
    event_date = np.asarray(rows["event_date"], dtype="datetime64[D]")
    interval_end = np.asarray(rows["interval_end"], dtype="datetime64[D]")
    known_event = (outcome != OUTCOME_CONTINUE) & (event_date < origin)
    keep = (landmark_date < origin) & (known_event | (interval_end <= origin))
    out = {k: np.asarray(v)[keep] for k, v in rows.items()}
    out["outcome"] = np.where(known_event, outcome, OUTCOME_CONTINUE)[keep].astype(np.int64)
    return out
