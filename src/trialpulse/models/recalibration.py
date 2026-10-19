"""The recalibration of M4 that ADR 0027 states, and the measurement of its rule.

    uv run python -m trialpulse.models.recalibration --origins dev

**The candidate.** For each of the two hazards of an interval (early stop, completion), its
log odds against continuing get a slope, and an intercept per landmark index:

    log odds of an early stop  = a_stop[k] + b_stop * (the model's log odds of an early stop)
    log odds of a completion   = a_complete[k] + b_complete * (the model's log odds of a completion)

It is fitted by maximum likelihood of the three outcomes of the person-period rows of the
last training year (the intervals that ended in the 12 months before the origin, which M4's
early stopping holds out), with the predictions of the model that did not see that year.
It is applied to the hazards of the final model before the cumulative incidence is formed.

**The rule** (ADR 0027, committed before this measurement): the candidate is adopted only if
at both development origins the pooled 24-month calibration slope is closer to 1, the
24-month calibration intercepts at landmark indices 0 and 6 are each closer to 0, the pooled
12-month Brier score is not larger, and the pooled 12-month AUC is not lower by more than
0.001. This command measures and reports; it changes no model.

Development origins only: the origins pass the test lock with no unlock.
"""

import argparse
import datetime as dt
import json
import sys
import time as clock
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from trialpulse import tracking
from trialpulse.cli import RefusedError, run
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.dates import add_months
from trialpulse.eval.ipcw import FloatArray, IntArray, censoring_groups
from trialpulse.eval.lock import require_unlock
from trialpulse.eval.metrics import calibration_slope_intercept, ipcw_auc, ipcw_brier
from trialpulse.eval.rows import (
    LANDMARKS_PATH,
    TRAINING_DIR,
    LandmarkRows,
    horizon_days,
    load_landmark_rows,
    training_path,
)
from trialpulse.eval.walkforward import (
    CENSORING_COLUMN,
    DEV_ROLE,
    EVALUATION,
    FEATURES_DIR,
    TRAINING,
    FeatureLoader,
    attach_features,
    evaluation_rows,
    feature_loader,
    select_origins,
    training_rows,
)
from trialpulse.models import discrete_time
from trialpulse.models.discrete_time import (
    VALIDATION_MONTHS,
    DiscreteTimeBoosting,
    cumulative_incidence,
    person_period,
)

RESULTS_PATH = REPO_ROOT / "data" / "results" / "recalibration" / "dev.json"
CONTINUE, STOP, COMPLETE = 0, 1, 2
FLOOR = 1e-12  # keeps a log of a probability finite
AUC_ALLOWANCE = 0.001  # condition 4 of the rule
RULE_MONTHS = {"slope": "24", "intercept": "24", "brier": "12", "auc": "12"}
RULE_INDICES = ("0", "6")


@dataclass(frozen=True)
class Recalibration:
    """The fitted candidate: an intercept per landmark index and one slope, per hazard."""

    stop_intercepts: tuple[float, ...]
    stop_slope: float
    complete_intercepts: tuple[float, ...]
    complete_slope: float

    @classmethod
    def identity(cls, n_indices: int) -> "Recalibration":
        zeros = (0.0,) * n_indices
        return cls(zeros, 1.0, zeros, 1.0)


def log_odds(probabilities: npt.NDArray[np.float64]) -> tuple[FloatArray, FloatArray]:
    """The log odds of an early stop and of a completion against continuing."""
    p = np.clip(np.asarray(probabilities, dtype=np.float64), FLOOR, 1.0)
    return np.log(p[:, STOP]) - np.log(p[:, CONTINUE]), np.log(p[:, COMPLETE]) - np.log(
        p[:, CONTINUE]
    )


def _logits(
    theta: FloatArray, stop: FloatArray, complete: FloatArray, index: IntArray, n: int
) -> tuple[FloatArray, FloatArray]:
    return theta[index] + theta[n] * stop, theta[n + 1 + index] + theta[2 * n + 1] * complete


def apply(
    fitted: Recalibration, probabilities: npt.NDArray[np.float64], landmark_index: IntArray
) -> npt.NDArray[np.float64]:
    """The recalibrated probabilities of continuing, stopping early and completing."""
    n = len(fitted.stop_intercepts)
    theta = np.array(
        [*fitted.stop_intercepts, fitted.stop_slope, *fitted.complete_intercepts,
         fitted.complete_slope]
    )  # fmt: skip
    stop, complete = log_odds(probabilities)
    z_stop, z_complete = _logits(theta, stop, complete, np.asarray(landmark_index), n)
    top = np.maximum(0.0, np.maximum(z_stop, z_complete))
    weights = np.column_stack([np.exp(-top), np.exp(z_stop - top), np.exp(z_complete - top)])
    out: npt.NDArray[np.float64] = weights / weights.sum(axis=1, keepdims=True)
    return out


def fit(
    probabilities: npt.NDArray[np.float64],
    landmark_index: IntArray,
    outcome: IntArray,
    n_indices: int,
    iterations: int = 50,
) -> Recalibration:
    """Maximum likelihood of the three outcomes by Newton's method, starting from the
    identity (the loss is convex in the 16 parameters). A landmark index without rows keeps
    the intercept 0."""
    stop, complete = log_odds(probabilities)
    index = np.asarray(landmark_index, dtype=np.int64)
    y = np.asarray(outcome, dtype=np.int64)
    n, rows = n_indices, len(y)
    size = 2 * n + 2
    # How each logit moves with the parameters: one intercept column per landmark index and
    # the model's log odds for the slope.
    d_stop = np.zeros((rows, size))
    d_stop[np.arange(rows), index] = 1.0
    d_stop[:, n] = stop
    d_complete = np.zeros((rows, size))
    d_complete[np.arange(rows), n + 1 + index] = 1.0
    d_complete[:, 2 * n + 1] = complete
    present = np.r_[
        np.bincount(index, minlength=n) > 0, True, np.bincount(index, minlength=n) > 0, True
    ]

    def loss(theta: FloatArray) -> tuple[float, FloatArray, FloatArray]:
        z_stop, z_complete = d_stop @ theta, d_complete @ theta
        top = np.maximum(0.0, np.maximum(z_stop, z_complete))
        e0, e1, e2 = np.exp(-top), np.exp(z_stop - top), np.exp(z_complete - top)
        total = e0 + e1 + e2
        p1, p2 = e1 / total, e2 / total
        chosen = np.where(y == STOP, z_stop, np.where(y == COMPLETE, z_complete, 0.0))
        value = float(np.mean(top + np.log(total) - chosen))
        return value, p1, p2

    theta = np.zeros(size)
    theta[n] = theta[2 * n + 1] = 1.0
    value, p1, p2 = loss(theta)
    for _ in range(iterations):
        g1, g2 = p1 - (y == STOP), p2 - (y == COMPLETE)
        grad = (d_stop.T @ g1 + d_complete.T @ g2) / rows
        w11, w22, w12 = p1 * (1 - p1), p2 * (1 - p2), -p1 * p2
        hess = (
            d_stop.T @ (d_stop * w11[:, None])
            + d_complete.T @ (d_complete * w22[:, None])
            + d_stop.T @ (d_complete * w12[:, None])
            + d_complete.T @ (d_stop * w12[:, None])
        ) / rows
        hess[np.ix_(~present, ~present)] += np.eye(int((~present).sum()))  # empty indices stay
        step = np.linalg.solve(hess + 1e-10 * np.eye(size), grad)
        shrink = 1.0
        while True:  # halve the step until the loss does not grow
            trial = theta - shrink * step
            new_value, q1, q2 = loss(trial)
            if new_value <= value + 1e-15 or shrink < 1e-6:
                break
            shrink /= 2
        converged = value - new_value < 1e-12
        theta, value, p1, p2 = trial, new_value, q1, q2
        if converged:
            break
    theta = np.where(present, theta, np.r_[np.zeros(n), 1.0, np.zeros(n), 1.0])
    return Recalibration(
        tuple(float(v) for v in theta[:n]),
        float(theta[n]),
        tuple(float(v) for v in theta[n + 1 : 2 * n + 1]),
        float(theta[2 * n + 1]),
    )


def _interval_probabilities(
    booster: Any, landmark: npt.NDArray[np.float32], interval: IntArray
) -> npt.NDArray[np.float64]:
    x = np.column_stack([landmark, np.asarray(interval, dtype=np.float32)])
    out: npt.NDArray[np.float64] = np.asarray(booster.predict(x), dtype=np.float64)
    return out


def fit_on_last_training_year(
    model: DiscreteTimeBoosting,
    rows: LandmarkRows,
    time: FloatArray,
    event: IntArray,
    origin: dt.date,
    cfg: ProjectConfig,
) -> tuple[Recalibration, int]:
    """The candidate of an origin: fitted on the intervals that ended in the 12 months before
    the origin, with the predictions of the model the early stopping fitted without them."""
    if model.choosing is None:
        raise RefusedError(
            "the model kept no model of the earlier intervals (too few rows for a last "
            "training year): nothing to fit the recalibration with"
        )
    periods = person_period(rows, time, event, origin, cfg)
    year_start = add_months(np.array([np.datetime64(origin, "D")]), -VALIDATION_MONTHS)[0]
    valid = periods.interval_end > year_start
    landmark = model.tree_landmark_matrix(rows.features)[periods.row[valid]]
    p = _interval_probabilities(model.choosing, landmark, periods.interval[valid])
    index = rows.landmark_index[periods.row[valid]]
    n = int(cfg.landmarks.max_index) + 1
    return fit(p, index, periods.outcome[valid], n), int(valid.sum())


def recalibrated_cif(
    model: DiscreteTimeBoosting,
    fitted: Recalibration | None,
    rows: LandmarkRows,
    months: int,
    cfg: ProjectConfig,
) -> FloatArray:
    """The cumulative incidence of an early stop at a horizon, with or without the candidate."""
    intervals = months // int(cfg.discrete_time.interval_months)
    landmark = model.tree_landmark_matrix(rows.features)
    stop = np.empty((len(rows), intervals))
    complete = np.empty((len(rows), intervals))
    for j in range(1, intervals + 1):
        assert model.booster is not None
        p = _interval_probabilities(model.booster, landmark, np.full(len(rows), j))
        if fitted is not None:
            p = apply(fitted, p, rows.landmark_index)
        stop[:, j - 1], complete[:, j - 1] = p[:, STOP], p[:, COMPLETE]
    cif: FloatArray = cumulative_incidence(stop, complete)[:, intervals - 1]
    return cif


def metrics(
    time: FloatArray,
    event: IntArray,
    scores: Mapping[str, FloatArray],
    rows: LandmarkRows,
    groups: npt.NDArray[Any],
) -> dict[str, Any]:
    """The numbers the rule reads: pooled slope, AUC and Brier score by horizon, and the
    calibration intercept by landmark index."""
    out: dict[str, Any] = {}
    for months, score in scores.items():
        h = horizon_days(rows.landmark_date, int(months))
        slope, intercept = calibration_slope_intercept(time, event, score, h, groups)
        by_index = {}
        for k in np.unique(rows.landmark_index):
            m = rows.landmark_index == k
            by_index[str(int(k))] = calibration_slope_intercept(
                time[m], event[m], score[m], h[m], groups[m]
            )[1]
        out[months] = {
            "auc": ipcw_auc(time, event, score, h, groups),
            "brier": ipcw_brier(time, event, score, h, groups),
            "calibration_slope": slope,
            "calibration_intercept": intercept,
            "intercept_by_landmark_index": by_index,
        }
    return out


def judge(raw: Mapping[str, Any], recalibrated: Mapping[str, Any]) -> dict[str, bool]:
    """The four conditions of ADR 0027 at one origin."""
    s = RULE_MONTHS
    return {
        "slope_closer_to_1": abs(recalibrated[s["slope"]]["calibration_slope"] - 1.0)
        < abs(raw[s["slope"]]["calibration_slope"] - 1.0),
        "intercepts_closer_to_0": all(
            abs(recalibrated[s["intercept"]]["intercept_by_landmark_index"][k])
            < abs(raw[s["intercept"]]["intercept_by_landmark_index"][k])
            for k in RULE_INDICES
        ),
        "brier_not_larger": recalibrated[s["brier"]]["brier"] <= raw[s["brier"]]["brier"],
        "auc_not_lower": recalibrated[s["auc"]]["auc"] >= raw[s["auc"]]["auc"] - AUC_ALLOWANCE,
    }


def measure(
    cfg: ProjectConfig,
    load_final: Callable[[], LandmarkRows],
    load_training: Callable[[dt.date], LandmarkRows],
    load_features: FeatureLoader,
) -> dict[str, Any]:
    origins = select_origins(cfg, DEV_ROLE)
    require_unlock({o.role for o in origins}, REPO_ROOT, unlock_flag=False)
    final = load_final()
    result: dict[str, Any] = {"rule": "ADR 0027", "model": "m4", "origins": []}
    for origin in (o.date for o in origins):
        started = clock.perf_counter()
        day = np.datetime64(origin, "D")
        train, time, event = training_rows(load_training(origin), day)
        train = attach_features(train, load_features(origin, TRAINING, None), "training rows")
        ev, ev_time, ev_event = evaluation_rows(final, day, cfg.walk_forward.eval_window_months)
        groups = censoring_groups(ev.features[CENSORING_COLUMN], cfg.evaluation.censoring_min_rows)
        ev = attach_features(ev, load_features(origin, EVALUATION, None), "evaluation rows")
        model = discrete_time.build("m4", cfg)
        assert isinstance(model, DiscreteTimeBoosting)
        model.keep_choosing = True
        model.fit_rows(train, time, event, origin)
        fitted, n_rows = fit_on_last_training_year(model, train, time, event, origin, cfg)
        horizons = [str(m) for m in cfg.horizons_months]
        raw = metrics(
            ev_time, ev_event,
            {m: recalibrated_cif(model, None, ev, int(m), cfg) for m in horizons}, ev, groups,
        )  # fmt: skip
        recal = metrics(
            ev_time, ev_event,
            {m: recalibrated_cif(model, fitted, ev, int(m), cfg) for m in horizons}, ev, groups,
        )  # fmt: skip
        conditions = judge(raw, recal)
        result["origins"].append(
            {
                "origin": origin.isoformat(),
                "validation_rows": n_rows,
                "recalibration": asdict(fitted),
                "without": raw,
                "with": recal,
                "conditions": conditions,
                "passes": all(conditions.values()),
                "seconds": round(clock.perf_counter() - started, 1),
            }
        )
    result["adopted"] = bool(result["origins"]) and all(o["passes"] for o in result["origins"])
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the recalibration of ADR 0027.")
    parser.add_argument("--origins", default=DEV_ROLE, choices=[DEV_ROLE])
    parser.add_argument("--input", type=Path, default=LANDMARKS_PATH)
    parser.add_argument("--training-dir", type=Path, default=TRAINING_DIR)
    parser.add_argument("--features-dir", type=Path, default=FEATURES_DIR)
    parser.add_argument("--out", type=Path, default=RESULTS_PATH)
    tracking.add_arguments(parser)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    started = tracking.git_state()
    try:
        result = measure(
            cfg,
            lambda: load_landmark_rows(args.input),
            lambda origin: load_landmark_rows(training_path(args.training_dir, origin)),
            feature_loader(cfg, args.features_dir, args.input.parent),
        )
    except (FileNotFoundError, ValueError) as exc:
        raise RefusedError(str(exc)) from exc
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    for o in result["origins"]:
        failed = [name for name, ok in o["conditions"].items() if not ok]
        print(f"{o['origin']}: {'passes' if o['passes'] else 'fails ' + ', '.join(failed)}")
    print(f"adopted under the rule of ADR 0027: {'yes' if result['adopted'] else 'no'}")
    print(f"wrote {args.out}")
    if not args.no_track:
        flat: dict[str, float] = {"adopted": float(result["adopted"])}
        for o in result["origins"]:
            year = o["origin"][:4]
            for side in ("without", "with"):
                for months, values in o[side].items():
                    for key in ("auc", "brier", "calibration_slope"):
                        flat[f"{key}_{months}m_{year}_{side}"] = float(values[key])
        try:
            result["tracking"] = tracking.log_run(
                "recalibration-dev", {"rule": "ADR 0027", "model": "m4"}, flat, [args.out], cfg,
                tags={"step": "10", "kind": "recalibration", "roles": DEV_ROLE},
                local=args.local_tracking, state=started,
            )  # fmt: skip
        except tracking.TrackingError as exc:
            print(f"failed: the run was not logged to MLflow ({exc}); its results are in "
                  f"{args.out}", file=sys.stderr)  # fmt: skip
            return tracking.FAILED_EXIT_CODE
        args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"logged to {result['tracking']['store_description']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
