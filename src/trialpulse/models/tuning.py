"""Choosing the settings of M2, M3 and M4 (CLAUDE.md Section 9).

    uv run python -m trialpulse.models.tuning --model m4

Optuna with a fixed budget of trials per model (`tuning.n_trials` in config/project.yaml),
on the development origins only, with a fixed seed.

**A trial** is one setting of a model. For each development origin the model is fitted with
it on that origin's training rows and scored on that origin's evaluation rows, as the
walk-forward harness does. To keep the tuning of a model under an hour, a trial is fitted
on a seeded share of the training trials (`tuning.trial_sample_share`: every landmark row
of a drawn trial, none of the others). Every evaluation row is scored. The final fits of
the harness use every training row.

**The objective** is the selection rule of Section 9, applied to settings as it is applied
to models: the mean over the development origins of the IPCW AUC at 12 months, over all
landmark indices, with ties broken by the Brier score at 12 months and then by the
calibration slope closest to 1. The first trial is the default setting, so the chosen
setting is never worse than the default on the objective.

**What it writes.** The chosen settings go to config/tuned/<model>.json, which is
committed: every later run reads them (`models.discrete_time.tuned_settings`), the locked
test of Step 11 included, and nothing is tuned again there. All trials go to
data/results/tuning/<model>.json, and the study is logged to MLflow.

The locked origins are never read: the origins are the ones with the role "dev".
"""

import argparse
import datetime as dt
import json
import sys
import time as clock
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import optuna

from trialpulse import tracking
from trialpulse.cli import RefusedError, run
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.eval.ipcw import FloatArray, IntArray, censoring_groups
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
    EVALUATION,
    FEATURES_DIR,
    TRAINING,
    FeatureLoader,
    attach_features,
    evaluation_rows,
    feature_loader,
    training_rows,
)
from trialpulse.models import discrete_time

RESULTS_DIR = REPO_ROOT / "data" / "results" / "tuning"
DEV_ROLE = "dev"
TUNABLE: tuple[str, ...] = ("m2", "m3", "m4")
OBJECTIVE = (
    "mean over the development origins of the IPCW AUC at {months} months, all landmark "
    "indices; ties by the Brier score, then by the calibration slope closest to 1"
)
# The ranges searched, per kind of model: (low, high, scale).
SPACES: dict[str, dict[str, tuple[float, float, str]]] = {
    "logistic": {"C": (1e-3, 1e2, "log")},
    "boosting": {
        "learning_rate": (0.03, 0.2, "log"),
        "num_leaves": (15, 255, "log-int"),
        "min_data_in_leaf": (20, 500, "log-int"),
        "feature_fraction": (0.4, 1.0, "linear"),
        "bagging_fraction": (0.5, 1.0, "linear"),
        "lambda_l2": (1e-3, 30.0, "log"),
    },
}


def space(model: str) -> dict[str, tuple[float, float, str]]:
    return SPACES["boosting" if model.startswith("m4") else "logistic"]


def defaults(model: str) -> dict[str, Any]:
    chosen = (
        discrete_time.BOOSTING_DEFAULTS
        if model.startswith("m4")
        else discrete_time.LOGISTIC_DEFAULTS
    )
    return dict(chosen)


def suggest(trial: optuna.Trial, model: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, (low, high, scale) in space(model).items():
        if scale == "log-int":
            out[name] = trial.suggest_int(name, int(low), int(high), log=True)
        else:
            out[name] = trial.suggest_float(name, low, high, log=scale == "log")
    return out


def sample_trials(trial_id: npt.NDArray[Any], share: float, seed: int) -> npt.NDArray[np.bool_]:
    """The rows of a seeded share of the trials: every row of a drawn trial, none of the
    others. The draw depends on the set of trial ids and the seed only."""
    ids, inverse = np.unique(np.asarray(trial_id).astype(str), return_inverse=True)
    if share >= 1.0:
        return np.ones(len(inverse), dtype=bool)
    drawn = np.random.default_rng(seed).random(len(ids)) < share
    chosen: npt.NDArray[np.bool_] = drawn[inverse]
    return chosen


@dataclass(frozen=True)
class OriginData:
    """What a trial needs of one origin: the sampled training rows and every evaluation row."""

    origin: dt.date
    train: LandmarkRows
    train_time: FloatArray
    train_event: IntArray
    evaluation: LandmarkRows
    time: FloatArray
    event: IntArray
    groups: npt.NDArray[Any]
    training_trials: int
    sampled_trials: int


def prepare(
    cfg: ProjectConfig,
    final: LandmarkRows,
    load_training: Callable[[dt.date], LandmarkRows],
    load_features: FeatureLoader,
    share: float,
) -> list[OriginData]:
    """The rows of every development origin, with their features, as the harness joins them."""
    out = []
    for origin in (o.date for o in cfg.walk_forward.origins if o.role == DEV_ROLE):
        t = np.datetime64(origin, "D")
        train, time, event = training_rows(load_training(origin), t)
        train = attach_features(train, load_features(origin, TRAINING, None), "training rows")
        keep = sample_trials(train.trial_id, share, cfg.seeds.default)
        ev, ev_time, ev_event = evaluation_rows(final, t, cfg.walk_forward.eval_window_months)
        groups = censoring_groups(ev.features[CENSORING_COLUMN], cfg.evaluation.censoring_min_rows)
        ev = attach_features(ev, load_features(origin, EVALUATION, None), "evaluation rows")
        out.append(
            OriginData(
                origin, train.subset(keep), time[keep], event[keep], ev, ev_time, ev_event, groups,
                len(np.unique(train.trial_id)), len(np.unique(train.trial_id[keep])),
            )
        )  # fmt: skip
    if not out:
        raise RefusedError("config/project.yaml names no development origin")
    return out


def score(
    cfg: ProjectConfig, model: str, settings: Mapping[str, Any], data: Sequence[OriginData]
) -> dict[str, Any]:
    """One trial: the model with these settings, fitted and scored on every development
    origin. Returns the objective and what breaks its ties."""
    months = min(cfg.horizons_months)
    by_origin: dict[str, dict[str, Any]] = {}
    for d in data:
        fitted = discrete_time.build(model, cfg, settings).fit_rows(
            d.train, d.train_time, d.train_event, d.origin
        )
        h = horizon_days(d.evaluation.landmark_date, months)
        predicted = fitted.predict_months(d.evaluation, months)
        slope, _ = calibration_slope_intercept(d.time, d.event, predicted, h, d.groups)
        by_origin[d.origin.isoformat()] = {
            "auc": ipcw_auc(d.time, d.event, predicted, h, d.groups),
            "brier": ipcw_brier(d.time, d.event, predicted, h, d.groups),
            "calibration_slope": slope,
            "rounds": fitted.summary.get("rounds"),
            "fit_seconds": fitted.summary["fit_seconds"],
        }
    values = list(by_origin.values())
    return {
        "auc": float(np.mean([v["auc"] for v in values])),
        "brier": float(np.mean([v["brier"] for v in values])),
        "calibration_slope": float(np.mean([v["calibration_slope"] for v in values])),
        "by_origin": by_origin,
    }


def rank(trial: Mapping[str, Any]) -> tuple[float, float, float]:
    """The selection rule of Section 9 as a sort key: smaller is better."""
    return (-trial["auc"], trial["brier"], abs(trial["calibration_slope"] - 1.0))


def tune(
    cfg: ProjectConfig,
    model: str,
    data: Sequence[OriginData],
    n_trials: int,
    scorer: Callable[..., dict[str, Any]] = score,
) -> dict[str, Any]:
    """Run the study and return every trial and the chosen one."""
    if model not in TUNABLE:
        raise RefusedError(f"{model} has no settings of its own to tune (it runs with M4's)")
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        direction="maximize", sampler=optuna.samplers.TPESampler(seed=cfg.seeds.default)
    )
    study.enqueue_trial(defaults(model))  # the first trial is the default setting
    trials: list[dict[str, Any]] = []

    def objective(trial: optuna.Trial) -> float:
        started = clock.perf_counter()
        settings = suggest(trial, model)
        result = scorer(cfg, model, settings, data)
        trials.append(
            {"number": trial.number, "settings": settings, **result,
             "seconds": round(clock.perf_counter() - started, 1)}
        )  # fmt: skip
        return float(result["auc"])

    study.optimize(objective, n_trials=n_trials)
    best = min(trials, key=rank)
    return {"model": model, "trials": trials, "best": best, "default": trials[0]}


def tuned_record(
    cfg: ProjectConfig, study: Mapping[str, Any], data: Sequence[OriginData], share: float
) -> dict[str, Any]:
    """What is committed for a model: its settings and how they were chosen."""
    best, default = study["best"], study["default"]
    months = min(cfg.horizons_months)
    return {
        "model": study["model"],
        "settings": best["settings"],
        "objective": OBJECTIVE.format(months=months),
        "auc": best["auc"],
        "brier": best["brier"],
        "calibration_slope": best["calibration_slope"],
        "by_origin": {day: values["auc"] for day, values in best["by_origin"].items()},
        "default_settings": default["settings"],
        "default_auc": default["auc"],
        "chosen_trial": best["number"],
        "trials": len(study["trials"]),
        "trial_sample_share": share,
        "sampled_training_trials": {d.origin.isoformat(): d.sampled_trials for d in data},
        "seed": cfg.seeds.default,
        "search_space": {name: list(bounds) for name, bounds in space(study["model"]).items()},
        "dataset_revision": cfg.dataset.revision,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Choose the settings of a model (Section 9).")
    parser.add_argument("--model", required=True, choices=TUNABLE)
    parser.add_argument("--input", type=Path, default=LANDMARKS_PATH)
    parser.add_argument("--training-dir", type=Path, default=TRAINING_DIR)
    parser.add_argument("--features-dir", type=Path, default=FEATURES_DIR)
    parser.add_argument("--tuned-dir", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--trials", type=int, default=None, help="default: tuning.n_trials")
    parser.add_argument("--sample-share", type=float, default=None)
    tracking.add_arguments(parser)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    started = tracking.git_state()
    share = cfg.tuning.trial_sample_share if args.sample_share is None else args.sample_share
    n_trials = cfg.tuning.n_trials if args.trials is None else args.trials
    tuned_dir = discrete_time.TUNED_DIR if args.tuned_dir is None else args.tuned_dir
    out = RESULTS_DIR / f"{args.model}.json" if args.out is None else args.out
    begun = clock.perf_counter()
    try:
        data = prepare(
            cfg,
            load_landmark_rows(args.input),
            lambda origin: load_landmark_rows(training_path(args.training_dir, origin)),
            feature_loader(cfg, args.features_dir, args.input.parent),
            share,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise RefusedError(str(exc)) from exc
    study = tune(cfg, args.model, data, n_trials)
    record = tuned_record(cfg, study, data, share)
    saved: dict[str, Any] = {**record, "all_trials": study["trials"],
                             "seconds": round(clock.perf_counter() - begun, 1)}  # fmt: skip
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    tuned_path = tuned_dir / f"{args.model}.json"
    tuned_path.parent.mkdir(parents=True, exist_ok=True)
    tuned_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        f"{args.model}: {len(study['trials'])} trials in {saved['seconds']:.0f} seconds; mean AUC "
        f"at {min(cfg.horizons_months)} months {record['auc']:.4f} (default settings "
        f"{record['default_auc']:.4f}); settings {record['settings']}"
    )
    print(f"wrote {tuned_path}")
    print(f"wrote {out}")
    if not args.no_track:
        try:
            params = {"model": args.model, "trials": len(study["trials"]),
                      "trial_sample_share": share}  # fmt: skip
            params.update({f"best_{k}": v for k, v in record["settings"].items()})
            metrics = {
                "auc_mean": record["auc"],
                "auc_mean_default_settings": record["default_auc"],
                "brier_mean": record["brier"],
                "calibration_slope_mean": record["calibration_slope"],
                "seconds": saved["seconds"],
            }
            saved["tracking"] = tracking.log_run(
                f"tune-{args.model}",
                params,
                metrics,
                [out, tuned_path],
                cfg,
                tags={"step": "10", "kind": "tuning", "roles": DEV_ROLE},
                local=args.local_tracking,
                state=started,
            )
        except tracking.TrackingError as exc:  # both files are written: say so and fail
            print(f"failed: the run was not logged to MLflow ({exc}); its results are in {out}",
                  file=sys.stderr)  # fmt: skip
            return tracking.FAILED_EXIT_CODE
        out.write_text(json.dumps(saved, indent=2), encoding="utf-8")
        print(f"logged to {saved['tracking']['store_description']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
