"""M1: a static classifier at registration (CLAUDE.md Section 9).

One gradient-boosted binary classifier per horizon, trained on landmark 0 only: the
features of a trial on the day it is first posted, and whether it stopped early within the
horizon. It is the published-style comparison, a model that sees a trial once, at
registration, and is never updated. It answers "how far does registration-time information
alone go?", which is the bar the dynamic models of Step 10 have to clear at landmark 0.

**Which rows.** For a horizon of H months the training rows are the registrations whose
horizon had passed by the origin: first posted more than H months before it (an outcome
dated on the origin is not known on the origin, ADR 0015). A later
registration has a known outcome at H only if the trial ended early, so among the latest
registrations the rows with a label are selected on the outcome. Censoring weights repair
that on average, but not for a model that can see how recent a row is (the registration
year, or any feature that grows with calendar time): it would learn that recent means
risky. Leaving those rows out removes the selection instead of weighting it.

**Labels and weights.** At a horizon, a row is a case if the trial stopped early within it
and a control if it did not (it completed, or was still open at the horizon). Among the
rows above, a row can still be censored before the horizon, under the registry's UNKNOWN
rule (ADR 0014). Such a row has no label and gets weight 0, and the labeled rows are
weighted by the inverse probability of staying uncensored, from Kaplan-Meier censoring
curves fitted within each lead sponsor class on these training rows themselves: the same
weights the evaluation uses (ADR 0017), so training and evaluation make one assumption
about censoring. With these weights the classifier's probability estimates the cumulative
incidence of an early stop at the horizon, completions counted as controls.

**The number of trees.** The settings are fixed in Step 9; tuning belongs to Step 10. The
number of boosting rounds is chosen by early stopping on the last training year (Section
9): the latest 12 months of the registrations above. The model that chooses the rounds is
fitted on the registrations before that year. The final model then runs that many rounds on
every labeled training row.

**Categories.** A category becomes an integer code. The levels are those of the origin's
training rows (a fitted encoder in the sense of Section 6); a level first seen later is
missing, like a value that was never given.
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Self

import lightgbm as lgb
import numpy as np
import numpy.typing as npt

from trialpulse.config import ProjectConfig
from trialpulse.dates import add_months
from trialpulse.eval.ipcw import censoring_groups, horizon_labels_and_weights
from trialpulse.eval.rows import FloatArray, IntArray, LandmarkRows, horizon_days
from trialpulse.features.registry import CATEGORY, FEATURES

CENSORING_COLUMN = "stratum"  # the lead sponsor class at the landmark (ADR 0017)
MAX_ROUNDS = 2000
EARLY_STOPPING_ROUNDS = 50
VALIDATION_MONTHS = 12
MIN_VALIDATION_ROWS = 200
# Rounds used when the last training year cannot choose them (too few labeled rows, or one
# class only): small synthetic registries in the tests, never the real data.
FALLBACK_ROUNDS = 200
TOP_FEATURES = 15
PARAMS: dict[str, Any] = {
    "objective": "binary",
    "metric": "binary_logloss",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 100,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "max_cat_to_onehot": 8,
    "deterministic": True,
    "force_col_wise": True,
    "num_threads": 4,
    "verbosity": -1,
}
MODEL_FEATURES: tuple[str, ...] = tuple(f.name for f in FEATURES if f.main)
CATEGORICAL: tuple[str, ...] = tuple(f.name for f in FEATURES if f.main and f.kind == CATEGORY)


def category_levels(features: Mapping[str, npt.NDArray[Any]]) -> dict[str, tuple[str, ...]]:
    """The levels of each categorical feature among these rows, sorted."""
    levels = {}
    for name in CATEGORICAL:
        values = np.asarray(features[name], dtype=object)
        present = {str(v) for v in values if v is not None and v == v}
        levels[name] = tuple(sorted(present))
    return levels


def design_matrix(
    features: Mapping[str, npt.NDArray[Any]], levels: Mapping[str, tuple[str, ...]]
) -> FloatArray:
    """One float column per model feature, in registry order. A missing value, and a
    category outside `levels`, is NaN."""
    missing = [name for name in MODEL_FEATURES if name not in features]
    if missing:
        raise ValueError(f"the rows lack {len(missing)} model features, for example {missing[:3]}")
    n = len(next(iter(features.values()))) if features else 0
    out = np.full((n, len(MODEL_FEATURES)), np.nan, dtype=np.float64)
    for j, name in enumerate(MODEL_FEATURES):
        values = features[name]
        if name in levels:
            code = {level: float(i) for i, level in enumerate(levels[name])}
            out[:, j] = [
                code.get(str(v), np.nan) if v is not None and v == v else np.nan
                for v in np.asarray(values, dtype=object)
            ]
        else:
            out[:, j] = np.asarray(values, dtype=np.float64)
    return out


@dataclass
class StaticClassifier:
    """M1. One LightGBM classifier per horizon, on landmark 0."""

    cfg: ProjectConfig
    name: str = "m1"
    landmark_indices: tuple[int, ...] | None = (0,)
    feature_matrix: bool = True
    boosters: dict[int, lgb.Booster] = field(default_factory=dict)
    levels: dict[str, tuple[str, ...]] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def _dataset(self, x: FloatArray, y: IntArray, w: FloatArray) -> lgb.Dataset:
        return lgb.Dataset(
            x,
            label=y,
            weight=w,
            feature_name=list(MODEL_FEATURES),
            categorical_feature=list(CATEGORICAL),
            free_raw_data=False,
        )

    def fit_rows(
        self, rows: LandmarkRows, time: FloatArray, event: IntArray, origin: dt.date
    ) -> Self:
        if (
            self.landmark_indices is not None
            and not np.isin(rows.landmark_index, self.landmark_indices).all()
        ):
            raise ValueError("M1 is fitted on landmark 0 only")
        cfg = self.cfg
        self.levels = category_levels(rows.features)
        x = design_matrix(rows.features, self.levels)
        classes = np.asarray(rows.features[CENSORING_COLUMN]).astype(str)
        params = {**PARAMS, "seed": cfg.seeds.default}
        day = np.datetime64(origin, "D")
        self.summary = {"settings": {**params, "max_rounds": MAX_ROUNDS}, "horizons": {}}
        for months in cfg.horizons_months:
            # Registrations whose horizon had passed by the origin (see the module text).
            complete = add_months(rows.landmark_date, months) < day
            groups = censoring_groups(classes[complete], cfg.evaluation.censoring_min_rows)
            label, weight = np.zeros(len(rows), dtype=np.int8), np.zeros(len(rows))
            label[complete], weight[complete] = horizon_labels_and_weights(
                time[complete],
                event[complete],
                horizon_days(rows.landmark_date[complete], months),
                groups,
            )
            y = label.astype(np.int64)
            labeled = weight > 0
            # The last training year: the latest 12 months of those registrations. The
            # earlier ones choose the number of trees.
            year_end = add_months(np.array([day]), -months)[0]
            year_start = add_months(np.array([day]), -(months + VALIDATION_MONTHS))[0]
            valid = labeled & (rows.landmark_date >= year_start)
            early = labeled & (rows.landmark_date < year_start)
            rounds, chosen_by = FALLBACK_ROUNDS, "fallback"
            if (
                valid.sum() >= MIN_VALIDATION_ROWS
                and len(np.unique(y[valid])) == 2
                and len(np.unique(y[early])) == 2
            ):
                trial_fit = lgb.train(
                    params,
                    self._dataset(x[early], y[early], weight[early]),
                    num_boost_round=MAX_ROUNDS,
                    valid_sets=[self._dataset(x[valid], y[valid], weight[valid])],
                    callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)],
                )
                rounds, chosen_by = int(trial_fit.best_iteration), "last training year"
            booster = lgb.train(
                params,
                self._dataset(x[labeled], y[labeled], weight[labeled]),
                num_boost_round=max(1, rounds),
            )
            self.boosters[months] = booster
            gain = booster.feature_importance(importance_type="gain")
            order = np.argsort(-gain, kind="stable")[:TOP_FEATURES]
            total = float(gain.sum())
            self.summary["horizons"][str(months)] = {
                "rows_at_landmark_0": len(y),
                "training_rows": int(complete.sum()),
                "labeled_rows": int(labeled.sum()),
                "cases": int((y[labeled] == 1).sum()),
                "weighted_case_share": float(
                    (weight[labeled] * y[labeled]).sum() / weight[labeled].sum()
                ),
                "rounds": max(1, rounds),
                "rounds_chosen_by": chosen_by,
                "validation_rows": int(valid.sum()),
                "validation_from": str(year_start),
                "validation_before": str(year_end),
                "top_features_by_gain": [
                    {"feature": MODEL_FEATURES[i], "share_of_gain": float(gain[i] / total)}
                    for i in order
                    if total > 0 and gain[i] > 0
                ],
            }
        return self

    def predict_months(self, rows: LandmarkRows, months: int) -> FloatArray:
        if months not in self.boosters:
            raise RuntimeError(f"M1 has no classifier for a horizon of {months} months; fit first")
        x = design_matrix(rows.features, self.levels)
        predicted: FloatArray = np.asarray(self.boosters[months].predict(x), dtype=np.float64)
        return predicted
