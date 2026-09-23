"""Inverse probability of censoring weights (IPCW) at a horizon.

Rows are split at horizon H into cases (early stop by H), controls (no early stop by H,
completions included) and rows censored by H, whose outcome is unknown. Censored rows get
weight 0; the rest are reweighted by the inverse of the Kaplan-Meier estimate G of the
censoring distribution, so the known outcomes stand in for the unknown ones:

- case (stop at T <= H): 1 / G(T-)
- control completed at T <= H: 1 / G(T-)
- control still event-free at H (T > H): 1 / G(H)

Without censoring G is 1 everywhere and every weight is 1.
"""

from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

from trialpulse.eval import EVENT_CENSORED, EVENT_COMPLETE, EVENT_STOP

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]


@dataclass(frozen=True)
class StepFunction:
    """A right-continuous survival step function S(t) from a Kaplan-Meier fit."""

    times: FloatArray  # distinct times where S drops, ascending
    values: FloatArray  # S(t) at and just after each time

    def at(self, t: FloatArray | float) -> FloatArray:
        """S(t), including drops at t."""
        return self._lookup(t, "right")

    def before(self, t: FloatArray | float) -> FloatArray:
        """S(t-), the left limit: drops at exactly t are not yet applied."""
        return self._lookup(t, "left")

    def _lookup(self, t: FloatArray | float, side: Literal["left", "right"]) -> FloatArray:
        tt = np.asarray(t, dtype=np.float64)
        if self.times.size == 0:  # no drops: S is 1 everywhere
            return np.ones_like(tt)
        idx = np.searchsorted(self.times, tt, side=side)
        return np.where(idx == 0, 1.0, self.values[np.maximum(idx - 1, 0)])


def kaplan_meier(time: FloatArray, is_event: npt.NDArray[np.bool_]) -> StepFunction:
    """Kaplan-Meier survival for the event flagged by is_event; other rows are censored."""
    time = np.asarray(time, dtype=np.float64)
    order = np.argsort(time, kind="stable")
    t_sorted, e_sorted = time[order], np.asarray(is_event, dtype=bool)[order]
    uniq, first = np.unique(t_sorted, return_index=True)
    events = np.add.reduceat(e_sorted.astype(np.float64), first) if len(uniq) else np.array([])
    at_risk = len(t_sorted) - first
    factors = 1.0 - events / at_risk
    keep = events > 0
    return StepFunction(uniq[keep], np.cumprod(factors)[keep])


def censoring_survival(time: FloatArray, event: IntArray) -> StepFunction:
    """G(t): the Kaplan-Meier estimate of staying uncensored, with censoring as the event."""
    return kaplan_meier(time, np.asarray(event) == EVENT_CENSORED)


def _safe_inverse(g: FloatArray) -> FloatArray:
    return np.divide(1.0, g, out=np.zeros_like(g), where=g > 0)


def horizon_labels_and_weights(
    time: FloatArray, event: IntArray, horizon: FloatArray | float
) -> tuple[npt.NDArray[np.int8], FloatArray]:
    """Return (y, w): y is 1 for cases and 0 otherwise; w is the IPC weight, 0 for rows
    censored at or before the horizon. horizon may be a scalar or one value per row."""
    time = np.asarray(time, dtype=np.float64)
    event = np.asarray(event)
    h = np.broadcast_to(np.asarray(horizon, dtype=np.float64), time.shape)
    g = censoring_survival(time, event)
    within = time <= h
    case = within & (event == EVENT_STOP)
    competed = within & (event == EVENT_COMPLETE)
    survived = ~within
    w = np.zeros_like(time)
    w[case | competed] = _safe_inverse(g.before(time[case | competed]))
    w[survived] = _safe_inverse(g.at(h[survived]))
    return case.astype(np.int8), w
