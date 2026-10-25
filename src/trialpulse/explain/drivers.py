"""The drivers of M4's predictions: SHAP values by TreeSHAP (Step 12, ADR 0033).

M4 is a multiclass LightGBM model of each 6-month interval after a landmark: continue, stop
early or complete (`trialpulse.models.discrete_time`). For a class c, the raw score f_c(x, j)
of a row x in interval j is a sum of trees, and LightGBM's `pred_contrib` splits it exactly
into one SHAP value per column plus the expected value (the TreeSHAP algorithm of Lundberg
and others, the one the shap package's TreeExplainer runs on these trees).

**What is explained.** The log-odds of an early stop against continuing in interval j is
g(x, j) = f_stop(x, j) - f_continue(x, j), and the target of a horizon of J intervals is the
mean of g over j = 1 to J. SHAP values add up over a sum of outputs, so the target's values
are the mean over the intervals of the stop class's values less the continue class's, and
they add up exactly to the target. A positive value raises the model's odds of an early stop
against continuing; a negative one lowers them.

The target is not the cumulative incidence the model serves: that also depends on the
completion hazard, which no additive split of the trees can carry. `agreement` measures how
closely the two rank the same rows; the report states it beside every importance.

**The interval column** is the interval number the model is asked about, not a property of
the trial. Its value is set by the horizon, so it is kept apart (`Explanation.interval`) and
never listed as a driver.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from trialpulse.eval import EVENT_STOP
from trialpulse.eval.rows import FloatArray, LandmarkRows
from trialpulse.features.registry import BY_NAME, CATEGORY, FLAG, NUMBER
from trialpulse.models.discrete_time import CLASSES, OUTCOME_CONTINUE, DiscreteTimeBoosting

TOP = 5  # drivers stored with each prediction (CLAUDE.md Section 13)


@dataclass(frozen=True)
class Explanation:
    """The SHAP values of some landmark rows at one horizon: one column per feature of the
    model (in its order), the interval column and the expected value apart. Each row adds up
    to its target: base + interval + values.sum(axis=1)."""

    features: tuple[str, ...]
    values: FloatArray
    interval: FloatArray
    base: FloatArray
    months: int

    @property
    def target(self) -> FloatArray:
        out: FloatArray = self.base + self.interval + self.values.sum(axis=1)
        return out


@dataclass(frozen=True)
class Driver:
    feature: str
    value: str  # the feature's value at the landmark, as text
    contribution: float  # its SHAP value, in log-odds

    @property
    def direction(self) -> str:
        return "raises" if self.contribution > 0 else "lowers"


def _intervals(model: DiscreteTimeBoosting, months: int) -> int:
    count, rest = divmod(months, int(model.cfg.discrete_time.interval_months))
    if rest or not 1 <= count <= model.n_intervals:
        raise ValueError(
            f"a horizon of {months} months is not a whole number of intervals within "
            f"{model.n_intervals}"
        )
    return count


def explain(model: DiscreteTimeBoosting, rows: LandmarkRows, months: int) -> Explanation:
    """The SHAP values of the target at a horizon for each row (see the module text)."""
    if model.booster is None:
        raise RuntimeError(f"{model.name} is not fitted")
    count = _intervals(model, months)
    landmark = model.tree_landmark_matrix(rows.features)
    n, width = landmark.shape
    total = np.zeros((n, width + 2))  # the features, the interval column, the expected value
    for j in range(1, count + 1):
        x = np.column_stack([landmark, np.full(n, float(j), dtype=np.float32)])
        raw = np.asarray(model.booster.predict(x, pred_contrib=True), dtype=np.float64)
        per_class = raw.reshape(n, len(CLASSES), width + 2)
        total += per_class[:, EVENT_STOP] - per_class[:, OUTCOME_CONTINUE]
    total /= count
    return Explanation(
        model.features, total[:, :width], total[:, width], total[:, width + 1], months
    )


def log_odds(model: DiscreteTimeBoosting, rows: LandmarkRows, months: int) -> FloatArray:
    """The target computed from the raw scores directly: what the SHAP values add up to."""
    if model.booster is None:
        raise RuntimeError(f"{model.name} is not fitted")
    count = _intervals(model, months)
    landmark = model.tree_landmark_matrix(rows.features)
    out = np.zeros(len(landmark))
    for j in range(1, count + 1):
        x = np.column_stack([landmark, np.full(len(landmark), float(j), dtype=np.float32)])
        raw = np.asarray(model.booster.predict(x, raw_score=True), dtype=np.float64)
        out += raw[:, EVENT_STOP] - raw[:, OUTCOME_CONTINUE]
    result: FloatArray = out / count
    return result


def _rank(values: FloatArray) -> FloatArray:
    order = np.argsort(values, kind="stable")
    ranks = np.empty(len(values))
    ranks[order] = np.arange(len(values), dtype=np.float64)
    # Ties share the mean of their ranks.
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    sums = np.bincount(inverse, weights=ranks)
    out: FloatArray = sums[inverse] / counts[inverse]
    return out


def agreement(target: FloatArray, risk: FloatArray) -> float:
    """Spearman's rank correlation between the explained target and the served risk."""
    a, b = _rank(np.asarray(target, dtype=np.float64)), _rank(np.asarray(risk, dtype=np.float64))
    a, b = a - a.mean(), b - b.mean()
    denominator = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / denominator) if denominator > 0 else float("nan")


def importance(explanation: Explanation) -> dict[str, float]:
    """The mean absolute SHAP value of each feature over the rows, largest first (ties in
    model order)."""
    mean = np.abs(explanation.values).mean(axis=0)
    order = np.argsort(-mean, kind="stable")
    return {explanation.features[i]: float(mean[i]) for i in order}


def direction_of(
    explanation: Explanation, values: Mapping[str, npt.NDArray[Any]]
) -> dict[str, str]:
    """For each number feature, whether larger values go with larger SHAP values: the sign of
    Spearman's correlation between the feature and its SHAP value over the rows with a value.
    Categories and flags get no direction here."""
    out: dict[str, str] = {}
    for j, name in enumerate(explanation.features):
        if BY_NAME[name].kind != NUMBER:
            continue
        raw = np.asarray(values[name], dtype=np.float64)
        given = ~np.isnan(raw)
        if given.sum() < 3 or np.unique(raw[given]).size < 2:
            continue
        rho = agreement(raw[given], explanation.values[given, j])
        if np.isfinite(rho) and abs(rho) >= 0.1:
            out[name] = "higher raises" if rho > 0 else "higher lowers"
        else:
            out[name] = "mixed"
    return out


def shown(name: str, value: Any) -> str:
    """A feature's value as text: a level as it is, a flag as yes or no, a number with up to
    three significant digits; "not given" when missing."""
    kind = BY_NAME[name].kind
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "not given"
    if kind == CATEGORY:
        return str(value)
    number = float(value)
    if np.isnan(number):
        return "not given"
    if kind == FLAG:
        return "yes" if number >= 0.5 else "no"
    return f"{number:.3g}"


def top_drivers(
    explanation: Explanation,
    values: Mapping[str, npt.NDArray[Any]],
    row: int,
    k: int = TOP,
) -> list[Driver]:
    """The k features with the largest absolute SHAP values in one row, with their values;
    ties keep the model's order. Features with a value of exactly 0 are not drivers."""
    contributions = explanation.values[row]
    order = np.argsort(-np.abs(contributions), kind="stable")
    out = []
    for j in order[:k]:
        if contributions[j] == 0:
            break
        name = explanation.features[j]
        out.append(Driver(name, shown(name, values[name][row]), float(contributions[j])))
    return out


def drivers_text(drivers: Sequence[Driver]) -> str:
    """Drivers on one line, for a table cell: "feature (value) raises, ..."."""
    return "; ".join(f"{d.feature} ({d.value}) {d.direction}" for d in drivers)
