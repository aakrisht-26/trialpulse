"""M2, M3 and M4: discrete-time models of early stop and completion (CLAUDE.md Sections 6
and 9).

Follow-up after a landmark is cut into four 6-month intervals. For an interval that a trial
enters, a model predicts one of three outcomes: the trial stops early in it, completes in
it, or continues. The training rows are person-period rows: one per landmark row and
interval, as long as the interval ended within the observation window (ADR 0015). The
features are the landmark's, frozen at L, and the interval number is a feature.

With the hazards h_stop(j) and h_complete(j) of each interval, the probability of entering
interval j is S(j - 1) = product over i < j of (1 - h_stop(i) - h_complete(i)), and the
cumulative incidence of an early stop by the end of interval J is the sum over j <= J of
S(j - 1) * h_stop(j) (`cumulative_incidence`). A horizon of 12 months is two intervals, 24
months four. Completion is a competing event, not a censoring: a trial that completed
cannot stop early any more, and the formula says so.

**The ladder (ADR 0024).** Each rung holds the one below it:

- M0 knows the lead sponsor class (one curve per class, `models.aalen_johansen`);
- M2 is a multinomial logistic regression on the sponsor class and the design family;
- M3 is M2 plus the amendment signals;
- M4 is gradient-boosted trees (LightGBM) on every feature family.

Every rung also has the interval and the time family (the landmark index), which say where
in a trial's life a row sits. The ablation of M4 (`m4-design`, `m4-amendments`,
`m4-sponsor`, then M4 itself) adds the same families one at a time to M4's own learner, so
that the gain of a family is not mixed with the change of learner.

**Censoring.** A person-period row exists only for an interval a trial was followed
through, so the rows need no censoring weights: a trial censored under the lapse rule (ADR
0014) contributes the intervals before its censoring and none after.

**What is fitted on an origin's training rows only** (Section 6): the levels of every
category, the centers, scales and fill values of the linear models, the coefficients and
the trees. A level first seen later is treated as missing.

**The number of trees of M4** is chosen by early stopping on the last training year: the
intervals that ended in the 12 months before the origin. A model fitted on the intervals
that ended earlier chooses the number of boosting rounds; the final model runs that many
rounds on every row.

**Settings.** `tuned_settings` reads the settings chosen by `trialpulse.models.tuning`
from config/tuned/<model>.json. Without that file a model runs on the defaults below.
"""

import datetime as dt
import json
import pickle
import time as clock
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

import lightgbm as lgb
import numpy as np
import numpy.typing as npt
from sklearn.linear_model import LogisticRegression

from trialpulse.cohort.person_period import expand
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import REPO_ROOT, ProjectConfig
from trialpulse.dates import DateArray, add_months
from trialpulse.eval import EVENT_COMPLETE, EVENT_STOP
from trialpulse.eval.rows import FloatArray, IntArray, LandmarkRows
from trialpulse.features.registry import (
    AMENDMENTS,
    BY_NAME,
    CATEGORY,
    COMPETITION,
    DESIGN,
    FEATURES,
    FLAG,
    SPONSOR,
    TEXT,
    TIME,
)

TUNED_DIR = REPO_ROOT / "config" / "tuned"
INTERVAL = "interval"  # the name of the interval number among a model's columns
OUTCOME_CONTINUE = 0
CLASSES = (OUTCOME_CONTINUE, EVENT_STOP, EVENT_COMPLETE)
SPONSOR_CLASS = "sponsor_class"  # M0's information, in every rung (ADR 0024)
LANDMARK_INDEX = "landmark_index"
MONTHS_SINCE_T0 = "months_since_t0"  # six times the landmark index: left out of linear models
VALIDATION_MONTHS = 12
MAX_ROUNDS = 2000
EARLY_STOPPING_ROUNDS = 50
MIN_VALIDATION_ROWS = 500
FALLBACK_ROUNDS = 200  # when the last training year cannot choose (tiny synthetic data)
LOGISTIC_MAX_ITER = 300
# The feature families of each model, beside the sponsor class and the time family.
FAMILY_SETS: dict[str, tuple[str, ...]] = {
    "m2": (DESIGN,),
    "m3": (DESIGN, AMENDMENTS),
    "m4": (DESIGN, AMENDMENTS, SPONSOR, COMPETITION, TEXT),
    "m4-design": (DESIGN,),
    "m4-amendments": (DESIGN, AMENDMENTS),
    "m4-sponsor": (DESIGN, AMENDMENTS, SPONSOR, COMPETITION),
}
# The model whose tuned settings a model runs with: the ablation rungs use M4's.
SETTINGS_OF: dict[str, str] = {
    name: "m4" if name.startswith("m4") else name for name in FAMILY_SETS
}
LOGISTIC_DEFAULTS: dict[str, Any] = {"C": 1.0}
BOOSTING_DEFAULTS: dict[str, Any] = {
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 100,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "lambda_l2": 1.0,
}
BOOSTING_FIXED: dict[str, Any] = {
    "objective": "multiclass",
    "num_class": len(CLASSES),
    "metric": "multi_logloss",
    "bagging_freq": 1,
    "max_cat_to_onehot": 8,
    "deterministic": True,
    "force_col_wise": True,
    "num_threads": 4,
    "verbosity": -1,
}


def feature_names(families: Sequence[str]) -> tuple[str, ...]:
    """The main features of the families, with the sponsor class and the time family, in
    registry order."""
    return tuple(
        f.name
        for f in FEATURES
        if f.main and (f.family in families or f.family == TIME or f.name == SPONSOR_CLASS)
    )


def tuned_settings(model: str, folder: Path | None = None) -> dict[str, Any]:
    """The settings the tuning chose for a model, or nothing if it has not run."""
    path = (folder or TUNED_DIR) / f"{SETTINGS_OF.get(model, model)}.json"
    if not path.is_file():
        return {}
    chosen: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))["settings"]
    return chosen


@dataclass(frozen=True)
class PersonPeriod:
    """The intervals of some landmark rows: `row` is the position of the landmark row."""

    row: IntArray
    interval: IntArray
    outcome: IntArray
    interval_end: DateArray

    def __len__(self) -> int:
        return len(self.row)


def person_period(
    rows: LandmarkRows, time: FloatArray, event: IntArray, origin: dt.date, cfg: ProjectConfig
) -> PersonPeriod:
    """The person-period rows of an origin's training rows: the expansion the cohort build
    uses (`cohort.person_period.expand`), with the origin as the end of observation."""
    rules = CohortRules.from_config(cfg, cutoff=origin)
    event_date = rows.landmark_date + np.asarray(time, dtype=np.int64).astype("timedelta64[D]")
    columns = expand(
        {
            # The position of each row stands in for its trial id: it comes back per interval.
            "trial_id": np.arange(len(rows), dtype=np.int64),
            "landmark_index": rows.landmark_index,
            "landmark_date": rows.landmark_date,
            "event": np.asarray(event, dtype=np.int64),
            "event_date": event_date,
            "stratum": np.zeros(len(rows), dtype=np.int8),
        },
        rules,
    )
    return PersonPeriod(
        np.asarray(columns["trial_id"], dtype=np.int64),
        np.asarray(columns["interval"], dtype=np.int64),
        np.asarray(columns["outcome"], dtype=np.int64),
        np.asarray(columns["interval_end"], dtype="datetime64[D]"),
    )


def cumulative_incidence(stop: FloatArray, complete: FloatArray) -> FloatArray:
    """The cumulative incidence of an early stop by the end of each interval, from the
    hazards of stopping and of completing in each interval (one row per landmark row, one
    column per interval)."""
    stop = np.asarray(stop, dtype=np.float64)
    complete = np.asarray(complete, dtype=np.float64)
    survival = np.cumprod(1.0 - stop - complete, axis=1)
    entering = np.concatenate([np.ones((len(stop), 1)), survival[:, :-1]], axis=1)
    cif: FloatArray = np.cumsum(entering * stop, axis=1)
    return cif


def _missing(values: npt.NDArray[Any]) -> npt.NDArray[np.bool_]:
    return np.array([v is None or v != v for v in np.asarray(values, dtype=object)], dtype=bool)


def _signed_log(values: FloatArray) -> FloatArray:
    out: FloatArray = np.sign(values) * np.log1p(np.abs(values))
    return out


@dataclass
class LinearDesign:
    """The columns of a linear model, fitted on an origin's training rows.

    A number is put on a signed log scale (sign(x) * log(1 + |x|), which tames the long
    tails of counts and month differences and keeps negative slips), centered and scaled;
    a missing number takes the median and sets a "not given" column. A category becomes one
    column per level of the training rows, with one more for a missing value; a level first
    seen later sets none of them. A flag is 0 or 1, with a "not given" column if it is ever
    missing. The landmark index is a category here, so the hazard is free to differ by
    landmark; the months since registration repeat it and are left out."""

    names: tuple[str, ...]
    numbers: dict[str, tuple[float, float, float, bool]] = field(default_factory=dict)
    flags: dict[str, bool] = field(default_factory=dict)
    categories: dict[str, tuple[str, ...]] = field(default_factory=dict)
    plain: tuple[str, ...] = ("registration_year",)  # already on an even scale

    @staticmethod
    def _kind(name: str) -> str:
        return CATEGORY if name == LANDMARK_INDEX else BY_NAME[name].kind

    def _number(self, name: str, values: npt.NDArray[Any]) -> FloatArray:
        raw = np.asarray(values, dtype=np.float64)
        return raw if name in self.plain else _signed_log(raw)

    @classmethod
    def fit(cls, features: Mapping[str, npt.NDArray[Any]], names: Sequence[str]) -> Self:
        design = cls(tuple(n for n in names if n != MONTHS_SINCE_T0))
        for name in design.names:
            values = features[name]
            kind = cls._kind(name)
            if kind == CATEGORY:
                absent = _missing(values)
                levels = sorted({str(v) for v in np.asarray(values, dtype=object)[~absent]})
                design.categories[name] = tuple(levels)
                design.flags[name] = bool(absent.any())
            elif kind == FLAG:
                design.flags[name] = bool(np.isnan(np.asarray(values, dtype=np.float64)).any())
            else:
                v = design._number(name, values)
                given = v[~np.isnan(v)]
                fill = float(np.median(given)) if len(given) else 0.0
                filled = np.where(np.isnan(v), fill, v)
                scale = float(filled.std())
                design.numbers[name] = (
                    float(filled.mean()),
                    scale if scale > 0 else 1.0,
                    fill,
                    bool(np.isnan(v).any()),
                )
        return design

    @property
    def columns(self) -> list[str]:
        out: list[str] = []
        for name in self.names:
            kind = self._kind(name)
            if kind == CATEGORY:
                out += [f"{name}={level}" for level in self.categories[name]]
            else:
                out.append(name)
            if self.flags.get(name) or (name in self.numbers and self.numbers[name][3]):
                out.append(f"{name} not given")
        return out

    def matrix(self, features: Mapping[str, npt.NDArray[Any]]) -> npt.NDArray[np.float32]:
        """One row per landmark row, in the order of `columns`."""
        missing = [name for name in self.names if name not in features]
        if missing:
            raise ValueError(
                f"the rows lack {len(missing)} model features, for example {missing[:3]}"
            )
        n = len(next(iter(features.values()))) if features else 0
        out = np.zeros((n, len(self.columns)), dtype=np.float32)
        j = 0
        for name in self.names:
            values = features[name]
            kind = self._kind(name)
            if kind == CATEGORY:
                absent = _missing(values)
                text = np.array([str(v) for v in np.asarray(values, dtype=object)], dtype=object)
                for level in self.categories[name]:
                    out[:, j] = (text == level) & ~absent
                    j += 1
                if self.flags[name]:
                    out[:, j] = absent
                    j += 1
            elif kind == FLAG:
                raw = np.asarray(values, dtype=np.float64)
                out[:, j] = np.where(np.isnan(raw), 0.0, raw)
                j += 1
                if self.flags[name]:
                    out[:, j] = np.isnan(raw)
                    j += 1
            else:
                center, scale, fill, flagged = self.numbers[name]
                v = self._number(name, values)
                out[:, j] = (np.where(np.isnan(v), fill, v) - center) / scale
                j += 1
                if flagged:
                    out[:, j] = np.isnan(v)
                    j += 1
        return out


def tree_levels(
    features: Mapping[str, npt.NDArray[Any]], names: Sequence[str]
) -> dict[str, tuple[str, ...]]:
    """The levels of each categorical feature among these rows, sorted."""
    levels = {}
    for name in names:
        if BY_NAME[name].kind == CATEGORY:
            values = np.asarray(features[name], dtype=object)
            levels[name] = tuple(sorted({str(v) for v in values[~_missing(values)]}))
    return levels


def tree_matrix(
    features: Mapping[str, npt.NDArray[Any]],
    names: Sequence[str],
    levels: Mapping[str, tuple[str, ...]],
) -> npt.NDArray[np.float32]:
    """One column per feature for the trees: a category as the code of its level, a missing
    value and a level outside `levels` as NaN."""
    missing = [name for name in names if name not in features]
    if missing:
        raise ValueError(f"the rows lack {len(missing)} model features, for example {missing[:3]}")
    n = len(next(iter(features.values()))) if features else 0
    out = np.full((n, len(names)), np.nan, dtype=np.float32)
    for j, name in enumerate(names):
        values = features[name]
        if name in levels:
            code = {level: float(i) for i, level in enumerate(levels[name])}
            absent = _missing(values)
            out[:, j] = [
                np.nan if gone else code.get(str(v), np.nan)
                for v, gone in zip(np.asarray(values, dtype=object), absent, strict=True)
            ]
        else:
            out[:, j] = np.asarray(values, dtype=np.float64)
    return out


@dataclass
class DiscreteTimeModel:
    """What M2, M3 and M4 share: the person-period expansion, the hazards of each interval
    and the cumulative incidence at a horizon."""

    cfg: ProjectConfig
    name: str = "m4"
    landmark_indices: tuple[int, ...] | None = None  # every landmark index
    feature_matrix: bool = True
    settings: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    _scored: tuple[Any, FloatArray, FloatArray] | None = field(default=None, repr=False)

    @property
    def features(self) -> tuple[str, ...]:
        return feature_names(FAMILY_SETS[self.name])

    @property
    def n_intervals(self) -> int:
        return int(self.cfg.discrete_time.n_intervals)

    def fit_rows(
        self, rows: LandmarkRows, time: FloatArray, event: IntArray, origin: dt.date
    ) -> Self:
        started = clock.perf_counter()
        periods = person_period(rows, time, event, origin, self.cfg)
        if len(periods) == 0:
            raise ValueError("no interval of the training rows ended before the origin")
        self._scored = None
        details = self._fit(rows.features, periods, origin)
        counts = np.bincount(periods.outcome, minlength=len(CLASSES))
        self.summary = {
            "features": len(self.features),
            "families": list(FAMILY_SETS[self.name]),
            "landmark_rows": len(rows),
            "person_period_rows": len(periods),
            "outcomes": {
                "continue": int(counts[OUTCOME_CONTINUE]),
                "early stop": int(counts[EVENT_STOP]),
                "completion": int(counts[EVENT_COMPLETE]),
            },
            "settings": dict(self.settings),
            **details,
            "fit_seconds": round(clock.perf_counter() - started, 1),
            "model_bytes": self._size(),
        }
        return self

    def _fit(
        self, features: Mapping[str, npt.NDArray[Any]], periods: PersonPeriod, origin: dt.date
    ) -> dict[str, Any]:
        raise NotImplementedError

    def _size(self) -> int:
        raise NotImplementedError

    def _probabilities(self, features: Mapping[str, npt.NDArray[Any]], interval: int) -> FloatArray:
        """The probability of each of `CLASSES` in one interval, per landmark row."""
        raise NotImplementedError

    def hazards(self, rows: LandmarkRows) -> tuple[FloatArray, FloatArray]:
        """The hazards of an early stop and of a completion in each interval, per row."""
        if self._scored is not None and self._scored[0] is rows:
            return self._scored[1], self._scored[2]
        started = clock.perf_counter()
        stop = np.empty((len(rows), self.n_intervals))
        complete = np.empty((len(rows), self.n_intervals))
        for j in range(1, self.n_intervals + 1):
            p = self._probabilities(rows.features, j)
            stop[:, j - 1], complete[:, j - 1] = p[:, EVENT_STOP], p[:, EVENT_COMPLETE]
        self._scored = (rows, stop, complete)
        self.summary["predict_seconds"] = round(clock.perf_counter() - started, 1)
        self.summary["predicted_rows"] = len(rows)
        return stop, complete

    def predict_months(self, rows: LandmarkRows, months: int) -> FloatArray:
        intervals, rest = divmod(months, int(self.cfg.discrete_time.interval_months))
        if rest or not 1 <= intervals <= self.n_intervals:
            raise ValueError(
                f"a horizon of {months} months is not a whole number of intervals within "
                f"{self.n_intervals}"
            )
        stop, complete = self.hazards(rows)
        cif: FloatArray = cumulative_incidence(stop, complete)[:, intervals - 1]
        return cif


@dataclass
class DiscreteTimeLogistic(DiscreteTimeModel):
    """M2 and M3: a multinomial logistic regression on person-period rows."""

    design: LinearDesign | None = None
    model: LogisticRegression | None = None

    def __post_init__(self) -> None:
        self.settings = {**LOGISTIC_DEFAULTS, **tuned_settings(self.name), **self.settings}

    def _columns(
        self, landmark: npt.NDArray[np.float32], interval: IntArray
    ) -> npt.NDArray[np.float32]:
        one_hot = np.zeros((len(landmark), self.n_intervals), dtype=np.float32)
        one_hot[np.arange(len(landmark)), interval - 1] = 1.0
        return np.hstack([landmark, one_hot])

    def _fit(
        self, features: Mapping[str, npt.NDArray[Any]], periods: PersonPeriod, origin: dt.date
    ) -> dict[str, Any]:
        self.design = LinearDesign.fit(features, self.features)
        x = self._columns(self.design.matrix(features)[periods.row], periods.interval)
        self.model = LogisticRegression(
            C=float(self.settings["C"]), max_iter=LOGISTIC_MAX_ITER, tol=1e-4
        )
        self.model.fit(x, periods.outcome)
        iterations = int(np.max(self.model.n_iter_))
        return {
            "columns": x.shape[1],
            "iterations": iterations,
            "converged": iterations < LOGISTIC_MAX_ITER,
        }

    def _size(self) -> int:
        return len(pickle.dumps((self.design, self.model)))

    def _probabilities(self, features: Mapping[str, npt.NDArray[Any]], interval: int) -> FloatArray:
        if self.design is None or self.model is None:
            raise RuntimeError(f"{self.name} is not fitted")
        landmark = self.design.matrix(features)
        x = self._columns(landmark, np.full(len(landmark), interval, dtype=np.int64))
        raw = self.model.predict_proba(x)
        out = np.zeros((len(landmark), len(CLASSES)))
        for column, label in enumerate(self.model.classes_):  # a class absent in training stays 0
            out[:, int(label)] = raw[:, column]
        return out


@dataclass
class DiscreteTimeBoosting(DiscreteTimeModel):
    """M4 and its ablation rungs: gradient-boosted trees on person-period rows."""

    levels: dict[str, tuple[str, ...]] = field(default_factory=dict)
    booster: lgb.Booster | None = None

    def __post_init__(self) -> None:
        self.settings = {**BOOSTING_DEFAULTS, **tuned_settings(self.name), **self.settings}

    @property
    def columns(self) -> list[str]:
        return [*self.features, INTERVAL]

    def _dataset(self, x: npt.NDArray[np.float32], y: IntArray) -> lgb.Dataset:
        return lgb.Dataset(
            x,
            label=y,
            feature_name=self.columns,
            categorical_feature=list(self.levels),
            free_raw_data=False,
        )

    def _fit(
        self, features: Mapping[str, npt.NDArray[Any]], periods: PersonPeriod, origin: dt.date
    ) -> dict[str, Any]:
        names = self.features
        self.levels = tree_levels(features, names)
        landmark = tree_matrix(features, names, self.levels)
        x = np.column_stack([landmark[periods.row], periods.interval.astype(np.float32)])
        del landmark
        y = periods.outcome
        params = {**BOOSTING_FIXED, **self.settings, "seed": self.cfg.seeds.default}
        day = np.datetime64(origin, "D")
        year_start = add_months(np.array([day]), -VALIDATION_MONTHS)[0]
        # The last training year: the intervals that ended in the 12 months before the
        # origin. The earlier ones choose the number of trees.
        valid = periods.interval_end > year_start
        rounds, chosen_by = FALLBACK_ROUNDS, "fallback"
        if (
            valid.sum() >= MIN_VALIDATION_ROWS
            and len(np.unique(y[valid])) == len(CLASSES)
            and len(np.unique(y[~valid])) == len(CLASSES)
        ):
            choosing = lgb.train(
                params,
                self._dataset(x[~valid], y[~valid]),
                num_boost_round=MAX_ROUNDS,
                valid_sets=[self._dataset(x[valid], y[valid])],
                callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)],
            )
            rounds, chosen_by = int(choosing.best_iteration), "last training year"
            del choosing
        self.booster = lgb.train(params, self._dataset(x, y), num_boost_round=max(1, rounds))
        gain = self.booster.feature_importance(importance_type="gain")
        total = float(gain.sum())
        order = np.argsort(-gain, kind="stable")[:15]
        return {
            "rounds": max(1, rounds),
            "rounds_chosen_by": chosen_by,
            "validation_rows": int(valid.sum()),
            "validation_after": str(year_start),
            "top_features_by_gain": [
                {"feature": self.columns[i], "share_of_gain": float(gain[i] / total)}
                for i in order
                if total > 0 and gain[i] > 0
            ],
        }

    def _size(self) -> int:
        return 0 if self.booster is None else len(self.booster.model_to_string())

    def _probabilities(self, features: Mapping[str, npt.NDArray[Any]], interval: int) -> FloatArray:
        if self.booster is None:
            raise RuntimeError(f"{self.name} is not fitted")
        landmark = tree_matrix(features, self.features, self.levels)
        x = np.column_stack([landmark, np.full(len(landmark), float(interval), dtype=np.float32)])
        out: FloatArray = np.asarray(self.booster.predict(x), dtype=np.float64)
        return out


def build(
    name: str, cfg: ProjectConfig, settings: Mapping[str, Any] | None = None
) -> DiscreteTimeModel:
    """The model of a name, with settings that replace the tuned or default ones."""
    if name not in FAMILY_SETS:
        raise ValueError(f"unknown discrete-time model {name!r}")
    kind = DiscreteTimeBoosting if name.startswith("m4") else DiscreteTimeLogistic
    return kind(cfg, name=name, settings=dict(settings or {}))
