"""Metrics at a horizon (CLAUDE.md Section 6): IPCW time-dependent AUC, IPCW Brier score,
calibration (by risk decile against Aalen-Johansen, plus slope and intercept) and lift.

All functions take time (days since the landmark), event (0 censored, 1 early stop,
2 completion), the predicted CIF of an early stop at the horizon, and the horizon (a
scalar or one value per row, on the same time scale).
"""

from typing import Any

import numpy as np
import numpy.typing as npt

from trialpulse.eval.ipcw import FloatArray, IntArray, horizon_labels_and_weights
from trialpulse.models.aalen_johansen import aalen_johansen


def weighted_auc(y: npt.NDArray[np.int8], score: FloatArray, w: FloatArray) -> float:
    """sum of w_i * w_j over case-control pairs with score_i > score_j (ties count half),
    divided by the sum of w_i * w_j over all case-control pairs. O(n log n)."""
    keep = w > 0
    y, score, w = np.asarray(y)[keep], np.asarray(score, dtype=np.float64)[keep], w[keep]
    order = np.argsort(score, kind="stable")
    s, yy, ww = score[order], y[order], w[order]
    uniq, first = np.unique(s, return_index=True)
    if uniq.size == 0:
        return float("nan")
    pos = np.add.reduceat(ww * (yy == 1), first)
    neg = np.add.reduceat(ww * (yy == 0), first)
    denominator = pos.sum() * neg.sum()
    if denominator == 0:
        return float("nan")
    neg_below = np.cumsum(neg) - neg
    return float(np.sum(pos * neg_below + 0.5 * pos * neg) / denominator)


def ipcw_auc(
    time: FloatArray, event: IntArray, score: FloatArray, horizon: FloatArray | float
) -> float:
    """Time-dependent AUC for an early stop by the horizon (cases vs controls, where
    controls include completions), with inverse probability of censoring weights."""
    y, w = horizon_labels_and_weights(time, event, horizon)
    return weighted_auc(y, score, w)


def ipcw_brier(
    time: FloatArray, event: IntArray, score: FloatArray, horizon: FloatArray | float
) -> float:
    """IPCW Brier score: sum of w_i * (y_i - p_i)^2 over all rows, divided by n."""
    y, w = horizon_labels_and_weights(time, event, horizon)
    p = np.asarray(score, dtype=np.float64)
    return float(np.sum(w * (y - p) ** 2) / len(p)) if len(p) else float("nan")


def lift_at(
    time: FloatArray,
    event: IntArray,
    score: FloatArray,
    horizon: FloatArray | float,
    top_fraction: float,
) -> float:
    """IPCW early-stop rate among the top_fraction of rows by predicted risk, divided by
    the IPCW rate among all rows. Ties are broken by row order, so the result is stable."""
    y, w = horizon_labels_and_weights(time, event, horizon)
    n = len(y)
    if n == 0:
        return float("nan")
    k = max(1, int(np.ceil(top_fraction * n)))
    top = np.argsort(-np.asarray(score, dtype=np.float64), kind="stable")[:k]
    overall = np.sum(w * y) / np.sum(w) if np.sum(w) > 0 else float("nan")
    top_rate = np.sum(w[top] * y[top]) / np.sum(w[top]) if np.sum(w[top]) > 0 else float("nan")
    return float(top_rate / overall) if overall > 0 else float("nan")


def _weighted_logistic(
    x: FloatArray, y: FloatArray, w: FloatArray, fit_slope: bool, iterations: int = 100
) -> tuple[float, float]:
    """Weighted logistic regression of y on a + b * x by Newton-Raphson. With
    fit_slope=False, b is fixed at 1 (x is an offset) and only a is estimated."""
    a, b = 0.0, 1.0
    for _ in range(iterations):
        eta = a + b * x
        p = 1.0 / (1.0 + np.exp(-eta))
        r = w * (y - p)
        v = w * p * (1.0 - p)
        if fit_slope:
            grad = np.array([r.sum(), (r * x).sum()])
            hess = np.array([[v.sum(), (v * x).sum()], [(v * x).sum(), (v * x * x).sum()]])
            step = np.linalg.solve(hess, grad)
            a, b = a + step[0], b + step[1]
            done = np.max(np.abs(step)) < 1e-10
        else:
            step_a = r.sum() / v.sum()
            a += step_a
            done = abs(step_a) < 1e-10
        if done:
            break
    return float(a), float(b)


def calibration_slope_intercept(
    time: FloatArray, event: IntArray, score: FloatArray, horizon: FloatArray | float
) -> tuple[float, float]:
    """IPCW logistic recalibration. Slope: b in logit(P(y)) = a + b * logit(p); 1 is
    ideal. Intercept (calibration in the large): a with b fixed at 1; 0 is ideal."""
    y, w = horizon_labels_and_weights(time, event, horizon)
    keep = w > 0
    p = np.clip(np.asarray(score, dtype=np.float64)[keep], 1e-6, 1 - 1e-6)
    x = np.log(p / (1 - p))
    yy, ww = y[keep].astype(np.float64), w[keep]
    _, slope = _weighted_logistic(x, yy, ww, fit_slope=True)
    intercept, _ = _weighted_logistic(x, yy, ww, fit_slope=False)
    return slope, intercept


def calibration_table(
    time: FloatArray,
    event: IntArray,
    score: FloatArray,
    horizon: float,
    bins: int,
) -> list[dict[str, Any]]:
    """Mean predicted CIF versus the Aalen-Johansen observed CIF at the horizon, per bin of
    predicted risk (equal-count bins; deciles when bins is 10)."""
    p = np.asarray(score, dtype=np.float64)
    order = np.argsort(p, kind="stable")
    table: list[dict[str, Any]] = []
    for i, idx in enumerate(np.array_split(order, bins)):
        if idx.size == 0:
            continue
        curve = aalen_johansen(np.asarray(time)[idx], np.asarray(event)[idx])
        table.append(
            {
                "bin": i + 1,
                "n": int(idx.size),
                "mean_predicted": float(p[idx].mean()),
                "observed_cif": float(curve.at(horizon)),
            }
        )
    return table
