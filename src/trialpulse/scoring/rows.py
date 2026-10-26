"""The served row of a trial on a date (ADR 0034, decision 1).

The model was trained on landmark rows: L_k = t0 + 6k months, k = 0 to 6, computed with
the project's calendar arithmetic, which clamps the day to the month's length (31 August
plus 6 months is 28 February). A served row on a date d is described by:

- **the true landmark index**, the largest k with add_months(t0, 6k) on or before d, always
  computed from t0 (never by chaining landmarks, by the day of the month or by a difference
  of months: t0 = 20 January and d = 5 July give k = 0 although the months differ by 6);
- **k\\***, that index capped at the last landmark of the model (6);
- **the time features** the model reads: `landmark_index` = k\\* and `months_since_t0` =
  6k\\*, a pair the trees saw in training;
- **the start of the recent-versions window**, add_months(t0, 6k\\* - 6) plus the days from
  add_months(t0, 6k\\*) to d: the trial's last landmark gap, slid to end on d. On a landmark
  day it is the window of ADR 0021 item 8, clamped days included.
"""

import datetime as dt

import numpy as np
import numpy.typing as npt

from trialpulse.dates import DateArray, add_months

IntArray = npt.NDArray[np.int64]


def _days(values: DateArray | dt.date) -> DateArray:
    out: DateArray = np.asarray(values, dtype="datetime64[D]")
    return out


def _day_numbers(values: DateArray) -> IntArray:
    out: IntArray = values.astype("datetime64[D]").astype(np.int64)
    return out


def true_index(t0: DateArray, day: DateArray | dt.date, spacing_months: int) -> IntArray:
    """The largest k with add_months(t0, spacing * k) on or before the day, per trial; -1
    for a trial first posted after the day."""
    t0 = _days(t0)
    d = np.broadcast_to(_days(day), t0.shape)
    months = d.astype("datetime64[M]").astype(np.int64) - t0.astype("datetime64[M]").astype(
        np.int64
    )
    k = np.floor_divide(months, spacing_months)
    # The month difference can overshoot by one landmark when the day of the month is not
    # reached yet (or the landmark's day is clamped later than d): step back where it does.
    over = _day_numbers(add_months(t0, spacing_months * k)) > _day_numbers(d)
    k = np.where(over, k - 1, k)
    out: IntArray = np.where(_day_numbers(d) < _day_numbers(t0), -1, k).astype(np.int64)
    return out


def capped(index: IntArray, max_index: int) -> IntArray:
    out: IntArray = np.minimum(np.asarray(index, dtype=np.int64), max_index)
    return out


def landmark_date(t0: DateArray, index: IntArray, spacing_months: int) -> DateArray:
    return add_months(_days(t0), spacing_months * np.asarray(index, dtype=np.int64))


def window_start(
    t0: DateArray,
    k_star: IntArray,
    day: DateArray | dt.date,
    spacing_months: int,
    recent_months: int,
) -> DateArray:
    """The start of each served row's recent-versions window (see the module text)."""
    t0 = _days(t0)
    d = np.broadcast_to(_days(day), t0.shape)
    k_star = np.asarray(k_star, dtype=np.int64)
    gap_start = _day_numbers(add_months(t0, spacing_months * k_star - recent_months))
    elapsed = _day_numbers(d) - _day_numbers(add_months(t0, spacing_months * k_star))
    out: DateArray = (gap_start + elapsed).astype("datetime64[D]")
    return out


def time_features(k_star: IntArray, spacing_months: int) -> dict[str, npt.NDArray[np.float64]]:
    """The two time features the model reads for rows held at landmark k*."""
    k = np.asarray(k_star, dtype=np.float64)
    return {"landmark_index": k, "months_since_t0": spacing_months * k}
