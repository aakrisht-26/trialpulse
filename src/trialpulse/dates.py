"""Calendar helpers shared by landmarks, horizons and walk-forward origins."""

import numpy as np
import numpy.typing as npt

DateArray = npt.NDArray[np.datetime64]


def add_months(dates: DateArray, months: int) -> DateArray:
    """Add calendar months to datetime64[D] dates, clamping the day to the target
    month's length (2020-01-31 plus 1 month is 2020-02-29)."""
    days = dates.astype("datetime64[D]")
    month_start = days.astype("datetime64[M]")
    day_of_month = (days - month_start.astype("datetime64[D]")).astype(np.int64)
    target = month_start + np.timedelta64(months, "M")
    target_len = (
        (target + np.timedelta64(1, "M")).astype("datetime64[D]") - target.astype("datetime64[D]")
    ).astype(np.int64)
    clamped = np.minimum(day_of_month, target_len - 1)
    result: DateArray = target.astype("datetime64[D]") + clamped.astype("timedelta64[D]")
    return result


def days_between(start: DateArray, end: DateArray) -> npt.NDArray[np.float64]:
    """Whole days from start to end, as floats (the evaluation time scale)."""
    delta = end.astype("datetime64[D]") - start.astype("datetime64[D]")
    days: npt.NDArray[np.float64] = delta.astype(np.int64).astype(np.float64)
    return days
