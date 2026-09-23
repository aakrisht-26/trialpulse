"""Aalen-Johansen estimator of the cumulative incidence function (CIF), and M0.

With competing events, 1 - Kaplan-Meier overstates the probability of an early stop,
because it treats completed trials as if they could still stop. Aalen-Johansen weights
each early stop by the probability of still being event-free just before it:

    CIF_stop(t) = sum over event times t_j <= t of S(t_j-) * d_stop(t_j) / n(t_j)

where S is the all-cause Kaplan-Meier survival and n the number at risk.

M0 (CLAUDE.md Section 9) is this estimator fitted per stratum (phase group, or sponsor
class if phase is not allowed): it asks whether there is any signal beyond base rates.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from trialpulse.eval import EVENT_CENSORED, EVENT_STOP

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class CIFCurve:
    """A right-continuous step function CIF(t) for one cause."""

    times: FloatArray
    values: FloatArray

    def at(self, t: FloatArray | float) -> FloatArray:
        tt = np.asarray(t, dtype=np.float64)
        if self.times.size == 0:  # no events: the CIF is 0 everywhere
            return np.zeros_like(tt)
        idx = np.searchsorted(self.times, tt, side="right")
        return np.where(idx == 0, 0.0, self.values[np.maximum(idx - 1, 0)])


def aalen_johansen(
    time: FloatArray, event: npt.NDArray[np.int64], cause: int = EVENT_STOP
) -> CIFCurve:
    """Fit the CIF of one cause. Event 0 is censoring; every other code is an event."""
    time = np.asarray(time, dtype=np.float64)
    event = np.asarray(event)
    if time.size == 0:
        return CIFCurve(np.array([]), np.array([]))
    order = np.argsort(time, kind="stable")
    t, e = time[order], event[order]
    uniq, first = np.unique(t, return_index=True)
    d_any = np.add.reduceat((e != EVENT_CENSORED).astype(np.float64), first)
    d_cause = np.add.reduceat((e == cause).astype(np.float64), first)
    at_risk = (len(t) - first).astype(np.float64)
    surv_after = np.cumprod(1.0 - d_any / at_risk)
    surv_before = np.concatenate(([1.0], surv_after[:-1]))
    cif = np.cumsum(surv_before * d_cause / at_risk)
    return CIFCurve(uniq, cif)


@dataclass
class AalenJohansenModel:
    """M0: one Aalen-Johansen CIF per stratum; unseen strata use the pooled curve."""

    stratum_feature: str = "stratum"
    name: str = "m0"
    curves: dict[str, CIFCurve] = field(default_factory=dict)
    pooled: CIFCurve | None = None

    def fit(
        self, time: FloatArray, event: npt.NDArray[np.int64], features: Mapping[str, np.ndarray]
    ) -> "AalenJohansenModel":
        strata = np.asarray(features[self.stratum_feature]).astype(str)
        self.pooled = aalen_johansen(time, event)
        self.curves = {
            s: aalen_johansen(time[strata == s], event[strata == s]) for s in np.unique(strata)
        }
        return self

    def predict_cif(
        self, horizon: FloatArray | float, features: Mapping[str, np.ndarray]
    ) -> FloatArray:
        if self.pooled is None:
            raise RuntimeError("fit the model before predicting")
        strata = np.asarray(features[self.stratum_feature]).astype(str)
        h = np.broadcast_to(np.asarray(horizon, dtype=np.float64), strata.shape)
        out = self.pooled.at(h).astype(np.float64)
        for s, curve in self.curves.items():
            mask = strata == s
            out[mask] = curve.at(h[mask])
        return out
