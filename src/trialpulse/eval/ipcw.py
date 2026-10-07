"""Inverse probability of censoring weights (IPCW) at a horizon.

Rows are split at horizon H into cases (early stop by H), controls (no early stop by H,
completions included) and rows censored by H, whose outcome is unknown. Censored rows get
weight 0; the rest are reweighted by the inverse of the Kaplan-Meier estimate G of the
censoring distribution, so the known outcomes stand in for the unknown ones:

- case (stop at T <= H): 1 / G(T-)
- control completed at T <= H: 1 / G(T-)
- control still event-free at H (T > H): 1 / G(H)

Without censoring G is 1 everywhere and every weight is 1.

**Ties: events first.** Times are whole days, so a stop or a completion often shares its
day with another row's censoring. A row that ended on day t was observed through day t, so
it is not at risk of being censored on day t: the risk set of a censoring on day t leaves
out the rows that ended that day. With this convention the weights of the rows scored at
one horizon add up to the number of rows.

**One censoring curve per sponsor class (ADR 0017).** The weights are right only if
censoring is unrelated to the outcome among the rows that share a curve. Censoring under
the UNKNOWN rule is several times as common for some sponsor classes as for others, and
sponsor class also predicts the outcome, so one curve for all rows gives the rows of a
heavily censored class too little weight. With `groups`, G is estimated separately within
each group and every row is weighted by its own group's curve. The primary metrics use the
lead sponsor class at the landmark as the group (`censoring_groups`, decided once on all
the evaluation rows of an origin); one curve for all rows is kept as the sensitivity check.
"""

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import numpy.typing as npt

from trialpulse.eval import EVENT_CENSORED, EVENT_COMPLETE, EVENT_STOP

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
POOLED_GROUP = "POOLED"  # the sponsor classes too small to carry a censoring curve of their own


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


def kaplan_meier(
    time: FloatArray, is_event: npt.NDArray[np.bool_], others_first: bool = False
) -> StepFunction:
    """Kaplan-Meier survival for the event flagged by is_event; other rows are censored.

    At a tied time the flagged rows come first by default: a row that leaves unflagged at t
    is still at risk of the event at t. With `others_first` the unflagged rows of time t
    leave before the flagged ones, so they are not at risk at t."""
    time = np.asarray(time, dtype=np.float64)
    order = np.argsort(time, kind="stable")
    t_sorted, e_sorted = time[order], np.asarray(is_event, dtype=bool)[order]
    uniq, first = np.unique(t_sorted, return_index=True)
    if not len(uniq):
        return StepFunction(uniq, uniq)
    events = np.add.reduceat(e_sorted.astype(np.float64), first)
    at_risk = (len(t_sorted) - first).astype(np.float64)
    if others_first:
        at_risk -= np.add.reduceat((~e_sorted).astype(np.float64), first)
    keep = events > 0
    factors = 1.0 - np.divide(events, at_risk, out=np.zeros_like(events), where=keep)
    return StepFunction(uniq[keep], np.cumprod(factors)[keep])


def censoring_survival(time: FloatArray, event: IntArray) -> StepFunction:
    """G(t): the Kaplan-Meier estimate of staying uncensored, with censoring as the event.
    A row that stopped or completed on day t is not at risk of a censoring on day t."""
    return kaplan_meier(time, np.asarray(event) == EVENT_CENSORED, others_first=True)


def _safe_inverse(g: FloatArray) -> FloatArray:
    return np.divide(1.0, g, out=np.zeros_like(g), where=g > 0)


def censoring_groups(classes: npt.NDArray[Any], min_rows: int) -> npt.NDArray[Any]:
    """The group whose censoring curve each row uses: its sponsor class when the class has
    at least `min_rows` rows here, and one pooled group for the smaller classes together.
    The harness calls this once per origin, on all its evaluation rows, and uses the same
    groups at every landmark index, so a class never changes scheme between slices."""
    classes = np.asarray(classes).astype(str)
    names, counts = np.unique(classes, return_counts=True)
    small = names[counts < min_rows]
    return np.where(np.isin(classes, small), POOLED_GROUP, classes)


def horizon_labels_and_weights(
    time: FloatArray,
    event: IntArray,
    horizon: FloatArray | float,
    groups: npt.NDArray[Any] | None = None,
) -> tuple[npt.NDArray[np.int8], FloatArray]:
    """Return (y, w): y is 1 for cases and 0 otherwise; w is the IPC weight, 0 for rows
    censored at or before the horizon. horizon may be a scalar or one value per row. With
    `groups` (one label per row), the censoring distribution is estimated within each group
    and a row is weighted by its own group's curve; without, one curve serves all rows."""
    time = np.asarray(time, dtype=np.float64)
    event = np.asarray(event)
    h = np.broadcast_to(np.asarray(horizon, dtype=np.float64), time.shape)
    within = time <= h
    case = within & (event == EVENT_STOP)
    competed = within & (event == EVENT_COMPLETE)
    ended = case | competed
    w = np.zeros_like(time)
    if groups is None:
        labels = np.zeros(len(time), dtype="U1")
    else:
        labels = np.asarray(groups).astype(str)  # as text, so a missing label is a group too
        if labels.shape != time.shape:
            raise ValueError(f"{labels.size} group labels for {time.size} rows")
    for label in np.unique(labels):
        member = labels == label
        g = censoring_survival(time[member], event[member])
        w[member & ended] = _safe_inverse(g.before(time[member & ended]))
        w[member & ~within] = _safe_inverse(g.at(h[member & ~within]))
    return case.astype(np.int8), w
