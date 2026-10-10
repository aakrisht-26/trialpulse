"""Walk-forward evaluation runner (CLAUDE.md Section 6).

    uv run python -m trialpulse.eval.walkforward --model m0 --origins dev

For origin T, training rows are landmarks with L before T, with every outcome
administratively censored at T. They come from the cohort as it would have been built on T
(ADR 0016): a training row's outcome, its censoring, whether a lapse was resolved and
whether its trial is excluded for a reversal use only versions posted before T. The cohort
build writes one such file per origin, and `training_rows` refuses a file that holds
anything dated after its origin. Evaluation rows are landmarks with T <= L < T + 12 months,
scored against outcomes observed through the data cutoff. Metrics are reported per
horizon, pooled over landmark indices and per landmark index, with cluster-bootstrap
intervals that resample trials.

The IPCW metrics weight each row by a censoring curve estimated within its lead sponsor
class at the landmark (ADR 0017), because censoring under the UNKNOWN rule depends on the
sponsor class and so does the outcome. Classes with too few rows among the evaluation rows
of an origin share one pooled curve; the groups are decided once per origin and are the
same at every landmark index. The same metrics with one curve for all rows are reported
beside them as the sensitivity check ("single_censoring_curve"), as point estimates.

Locked origins (2018, 2019 and the 2020 stress test) need the test lock (ADR 0004). The
lock is checked before any data is loaded or any model is fitted.

Input contract: the landmark rows of `trialpulse.eval.rows`. Evaluation rows must carry
"stratum", the lead sponsor class at the landmark: the censoring weights are estimated
within it (ADR 0017).

**Models.** A model is fitted once per origin (`fit_rows`) and asked for the CIF of an
early stop at each horizon (`predict_months`). Two attributes say what it needs:

- `feature_matrix`: the Step 7 features of each row, joined from the feature build for the
  origin (so every fitted transform is the one of that origin, Section 6);
- `landmark_indices`: the landmark indices it is trained and scored on. M1 is a model of
  landmark 0 only. Its censoring groups are still decided on all the evaluation rows of
  the origin, so its rows carry the same weights as M0's rows at that landmark index.

**Tracking.** The command logs each run to MLflow (`trialpulse.tracking`): the commit it
started from, the dataset revision, the configuration, the pooled metrics and the results
file. The results file is written first. If the run cannot be logged, the command says so
and exits with code 5, and the results stay on disk.
"""

import argparse
import datetime as dt
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, Self

import numpy as np
import numpy.typing as npt

from trialpulse import tracking
from trialpulse.config import REPO_ROOT, Origin, ProjectConfig, load_project_config
from trialpulse.dates import add_months, days_between
from trialpulse.eval import EVENT_CENSORED
from trialpulse.eval.bootstrap import BootstrapError, cluster_bootstrap
from trialpulse.eval.ipcw import FloatArray, IntArray, censoring_groups
from trialpulse.eval.lock import TestLockError, require_unlock
from trialpulse.eval.metrics import (
    calibration_by_group,
    calibration_slope_intercept,
    calibration_table,
    ipcw_auc,
    ipcw_brier,
    lift_at,
)
from trialpulse.eval.rows import (
    LANDMARKS_PATH,
    TRAINING_DIR,
    LandmarkRows,
    horizon_days,
    load_landmark_rows,
    training_path,
)
from trialpulse.models.aalen_johansen import AalenJohansenModel
from trialpulse.models.static_clf import StaticClassifier

RESULTS_DIR = REPO_ROOT / "data" / "results" / "walkforward"
FEATURES_DIR = REPO_ROOT / "data" / "features"
CENSORING_COLUMN = "stratum"  # the lead sponsor class at the landmark (ADR 0017)
TRAINING, EVALUATION = "training", "evaluation"


class WalkForwardModel(Protocol):
    """What the harness asks of a model (see the module text)."""

    name: str
    landmark_indices: tuple[int, ...] | None
    feature_matrix: bool

    def fit_rows(
        self, rows: LandmarkRows, time: FloatArray, event: IntArray, origin: dt.date
    ) -> Self: ...

    def predict_months(self, rows: LandmarkRows, months: int) -> FloatArray: ...


@dataclass(frozen=True)
class FeatureRows:
    """The feature matrix of some landmark rows, with the keys it belongs to."""

    trial_id: npt.NDArray[Any]
    landmark_index: IntArray
    features: Mapping[str, npt.NDArray[Any]]


# Given an origin, "training" or "evaluation", and the landmark indices wanted (None: all).
FeatureLoader = Callable[[dt.date, str, tuple[int, ...] | None], FeatureRows]
MODELS: dict[str, Callable[[ProjectConfig], WalkForwardModel]] = {
    "m0": lambda cfg: AalenJohansenModel(),
    "m1": StaticClassifier,
}


def attach_features(rows: LandmarkRows, loaded: FeatureRows, what: str) -> LandmarkRows:
    """The rows with their feature columns. The features must be those of exactly these
    rows, in the same order: anything else means the cohort and the feature build are not
    from the same run, and is refused."""
    same = (
        len(loaded.trial_id) == len(rows)
        and bool((np.asarray(loaded.trial_id).astype(str) == rows.trial_id.astype(str)).all())
        and bool((np.asarray(loaded.landmark_index) == rows.landmark_index).all())
    )
    if not same:
        raise ValueError(
            f"the feature matrix does not hold the {what} ({len(loaded.trial_id):,} feature "
            f"rows for {len(rows):,} landmark rows, or other keys): rebuild the cohort and "
            "the features (uv run python -m trialpulse.cohort.build, then "
            "uv run python -m trialpulse.features.build)"
        )
    return rows.with_features(loaded.features)


def select_origins(cfg: ProjectConfig, spec: str) -> list[Origin]:
    """ "dev", "test", "stress", "all", or a comma-separated list of origin years."""
    origins = list(cfg.walk_forward.origins)
    if spec == "all":
        return origins
    if spec in {"dev", "test", "stress"}:
        return [o for o in origins if o.role == spec]
    years = {int(y) for y in spec.split(",") if y.strip()}
    chosen = [o for o in origins if o.date.year in years]
    if len(chosen) != len(years):
        raise ValueError(f"unknown origin year in {spec!r}")
    return chosen


def training_rows(
    rows: LandmarkRows, origin: np.datetime64
) -> tuple[LandmarkRows, FloatArray, IntArray]:
    """The training rows of an origin, with their follow-up time and event. `rows` must be
    the landmark rows of the cohort built as of the origin (ADR 0016): every landmark is
    before T, no event is dated T or later, and nothing is censored after T. Rows that
    break this were not built as of T, and are refused instead of being truncated."""
    after = (
        (rows.landmark_date >= origin)
        | (rows.event_date > origin)
        | ((rows.event != EVENT_CENSORED) & (rows.event_date >= origin))
    )
    if after.any():
        raise ValueError(
            f"{int(after.sum())} training rows for origin {origin} hold a landmark, an event "
            "or a censoring dated after it; training rows must come from the cohort built as "
            "of the origin (ADR 0016): run the cohort build"
        )
    return rows, days_between(rows.landmark_date, rows.event_date), rows.event.astype(np.int64)


def evaluation_rows(
    rows: LandmarkRows, origin: np.datetime64, window_months: int
) -> tuple[LandmarkRows, FloatArray, IntArray]:
    """Landmarks with T <= L < T + window, with outcomes through the data cutoff."""
    window_end = add_months(np.array([origin], dtype="datetime64[D]"), window_months)[0]
    ev = rows.subset((rows.landmark_date >= origin) & (rows.landmark_date < window_end))
    return ev, days_between(ev.landmark_date, ev.event_date), ev.event


def evaluate_predictions(
    time: FloatArray,
    event: IntArray,
    score: FloatArray,
    horizon: FloatArray,
    clusters: npt.NDArray[Any],
    groups: npt.NDArray[Any],
    cfg: ProjectConfig,
    n_resamples: int,
    context: str = "",
) -> dict[str, Any]:
    """The metrics of one slice. The primary values weight each row by the censoring curve
    of its group (ADR 0017). `groups` holds the group of each row, decided beforehand on all
    the evaluation rows of the origin (`censoring_groups`); the curves are fitted on the rows
    of the slice, and each bootstrap resample fits its own. "single_censoring_curve" holds
    the same metrics with one curve for all rows, the sensitivity check, without intervals."""
    ev = cfg.evaluation
    seed = cfg.seeds.default
    groups = np.asarray(groups).astype(str)

    def lift(t: FloatArray, e: IntArray, s: FloatArray, h: FloatArray, g: Any) -> float:
        return lift_at(t, e, s, h, ev.lift_top_fraction, g)

    def boot(name: str, metric: Callable[..., float]) -> dict[str, float | int]:
        try:
            return cluster_bootstrap(
                clusters,
                lambda idx: metric(time[idx], event[idx], score[idx], horizon[idx], groups[idx]),
                n_resamples,
                seed,
                ev.confidence_level,
            )
        except BootstrapError as exc:  # name the slice and metric that failed
            raise BootstrapError(f"{context}, metric {name}: {exc}") from exc

    slope, intercept = calibration_slope_intercept(time, event, score, horizon, groups)
    single_slope, single_intercept = calibration_slope_intercept(time, event, score, horizon)
    names, counts = np.unique(groups, return_counts=True)
    return {
        "n_rows": len(time),
        "n_trials": len(np.unique(clusters)),
        "censoring_groups": {str(n): int(c) for n, c in zip(names, counts, strict=True)},
        "auc": boot("auc", ipcw_auc),
        "brier": boot("brier", ipcw_brier),
        "lift": boot("lift", lift),
        "calibration_slope": slope,
        "calibration_intercept": intercept,
        "calibration_table": calibration_table(
            time, event, score, float(np.median(horizon)), ev.calibration_bins
        ),
        "calibration_by_group": calibration_by_group(time, event, score, horizon, groups),
        "single_censoring_curve": {
            "auc": ipcw_auc(time, event, score, horizon),
            "brier": ipcw_brier(time, event, score, horizon),
            "lift": lift(time, event, score, horizon, None),
            "calibration_slope": single_slope,
            "calibration_intercept": single_intercept,
        },
    }


def run(
    cfg: ProjectConfig,
    model_name: str,
    origins_spec: str,
    load_rows: Callable[[], LandmarkRows],
    load_training: Callable[[dt.date], LandmarkRows],
    unlock_flag: bool = False,
    repo: Path = REPO_ROOT,
    n_resamples: int | None = None,
    load_features: FeatureLoader | None = None,
) -> dict[str, Any]:
    origins = select_origins(cfg, origins_spec)
    unlock = require_unlock({o.role for o in origins}, repo, unlock_flag)  # before any data
    rows = load_rows()
    resamples = cfg.evaluation.bootstrap_resamples if n_resamples is None else n_resamples
    results: dict[str, Any] = {
        "model": model_name,
        "origins_spec": origins_spec,
        "bootstrap_resamples": resamples,
        "dataset_revision": cfg.dataset.revision,
        "training_labels": "as of each origin (ADR 0016)",
        "censoring_weights": (
            "by lead sponsor class at the landmark; classes under "
            f"{cfg.evaluation.censoring_min_rows} rows among the evaluation rows of an origin "
            "share one pooled curve, at every landmark index (ADR 0017)"
        ),
        "unlock": {**asdict(unlock), "registered": unlock.registered.isoformat()}
        if unlock
        else None,
        "origins": [],
    }
    for origin in origins:
        t = np.datetime64(origin.date, "D")
        train, train_time, train_event = training_rows(load_training(origin.date), t)
        ev, ev_time, ev_event = evaluation_rows(rows, t, cfg.walk_forward.eval_window_months)
        if CENSORING_COLUMN not in ev.features:
            raise ValueError(
                f'the evaluation rows have no "{CENSORING_COLUMN}" column; the censoring weights '
                "are estimated within the lead sponsor class at the landmark (ADR 0017)"
            )
        groups = censoring_groups(
            ev.features[CENSORING_COLUMN], cfg.evaluation.censoring_min_rows
        )  # once per origin, on all its evaluation rows, the same at every landmark index
        model = MODELS[model_name](cfg)
        if model.landmark_indices is not None:
            keep = np.isin(train.landmark_index, model.landmark_indices)
            train, train_time, train_event = train.subset(keep), train_time[keep], train_event[keep]
            keep = np.isin(ev.landmark_index, model.landmark_indices)
            ev, ev_time, ev_event, groups = (
                ev.subset(keep),
                ev_time[keep],
                ev_event[keep],
                groups[keep],
            )
        if model.feature_matrix:
            if load_features is None:
                raise ValueError(f"model {model_name} needs the feature matrix of Step 7")
            only = model.landmark_indices
            train = attach_features(
                train, load_features(origin.date, TRAINING, only), "training rows"
            )
            ev = attach_features(
                ev, load_features(origin.date, EVALUATION, only), "evaluation rows"
            )
        model.fit_rows(train, train_time, train_event, origin.date)
        horizons: dict[str, Any] = {}
        for months in cfg.horizons_months:
            h = horizon_days(ev.landmark_date, months)
            score = model.predict_months(ev, months)
            where = f"origin {origin.date.isoformat()}, horizon {months} months"
            pooled = evaluate_predictions(
                ev_time, ev_event, score, h, ev.trial_id, groups, cfg, resamples,
                f"{where}, pooled",
            )  # fmt: skip
            by_index = {}
            for k in np.unique(ev.landmark_index):
                m = ev.landmark_index == k
                by_index[str(int(k))] = evaluate_predictions(
                    ev_time[m], ev_event[m], score[m], h[m], ev.trial_id[m], groups[m],
                    cfg, resamples, f"{where}, landmark index {int(k)}",
                )  # fmt: skip
            horizons[str(months)] = {"pooled": pooled, "by_landmark_index": by_index}
        results["origins"].append(
            {
                "origin": origin.date.isoformat(),
                "role": origin.role,
                "n_train_rows": len(train),
                "n_eval_rows": len(ev),
                "landmark_indices": "all"
                if model.landmark_indices is None
                else list(model.landmark_indices),
                "model_summary": getattr(model, "summary", None),
                "censoring_groups": {
                    str(name): int(count)
                    for name, count in zip(*np.unique(groups, return_counts=True), strict=True)
                },
                "horizons": horizons,
            }
        )
    return results


def feature_loader(cfg: ProjectConfig, features_dir: Path, cohort_dir: Path) -> FeatureLoader:
    """Read the feature matrix of an origin's rows from the Step 7 build. The feature
    package is imported here, when a model first asks for features, so the harness itself
    does not depend on it."""

    def load(origin: dt.date, role: str, landmark_indices: tuple[int, ...] | None) -> FeatureRows:
        from trialpulse.cli import RefusedError
        from trialpulse.features import frame

        if role not in (TRAINING, EVALUATION):
            raise ValueError(f"unknown role {role!r}")
        try:
            matrix = frame.load(
                cfg,
                origin,
                "training" if role == TRAINING else "evaluation",
                features_dir,
                cohort_dir,
                landmark_indices,
            )
        except RefusedError as exc:  # a missing or stale build: one line, like the lock
            raise ValueError(str(exc)) from exc
        return FeatureRows(
            matrix["trial_id"].to_numpy(dtype=object),
            matrix["landmark_index"].to_numpy(dtype=np.int64),
            frame.as_arrays(matrix),
        )

    return load


def track(
    results: dict[str, Any],
    cfg: ProjectConfig,
    out: Path,
    local: bool = False,
    state: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Log one walk-forward result to MLflow and return where it went."""
    unlock = results.get("unlock") or {}
    first = results["origins"][0] if results["origins"] else {}
    return tracking.log_run(
        f"{results['model']}-{results['origins_spec']}",
        {
            "model": results["model"],
            "origins": results["origins_spec"],
            "origin_dates": ",".join(o["origin"] for o in results["origins"]),
            "bootstrap_resamples": results["bootstrap_resamples"],
            "horizons_months": ",".join(str(m) for m in cfg.horizons_months),
            "landmark_indices": first.get("landmark_indices", "all"),
            "training_labels": results["training_labels"],
            "censoring_weights": "by lead sponsor class (ADR 0017)",
        },
        tracking.walkforward_metrics(results),
        [out],
        cfg,
        tags={
            "kind": "walk-forward",
            "roles": ",".join(sorted({o["role"] for o in results["origins"]})),
            "test_lock": "unlocked" if unlock else "locked",
            **{f"unlock_{key}": str(value) for key, value in unlock.items()},
        },
        local=local,
        state=state,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Walk-forward evaluation")
    parser.add_argument("--model", required=True, choices=sorted(MODELS))
    parser.add_argument("--origins", default="dev", help="dev, test, stress, all, or years")
    parser.add_argument("--unlock-test", action="store_true", help="see ADR 0004")
    parser.add_argument("--input", type=Path, default=LANDMARKS_PATH)
    parser.add_argument("--training-dir", type=Path, default=TRAINING_DIR)
    parser.add_argument("--features-dir", type=Path, default=FEATURES_DIR)
    parser.add_argument("--resamples", type=int, default=None)
    tracking.add_arguments(parser)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    started = tracking.git_state()
    try:
        results = run(
            cfg,
            args.model,
            args.origins,
            lambda: load_landmark_rows(args.input),
            lambda origin: load_landmark_rows(training_path(args.training_dir, origin)),
            unlock_flag=args.unlock_test,
            n_resamples=args.resamples,
            load_features=feature_loader(cfg, args.features_dir, args.input.parent),
        )
    except (TestLockError, FileNotFoundError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except BootstrapError as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 3
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{args.model}_{args.origins.replace(',', '-')}.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    if not args.no_track:
        try:
            results["tracking"] = track(results, cfg, out, args.local_tracking, started)
        except tracking.TrackingError as exc:  # the results are on disk: say so and fail
            print(f"failed: the run was not logged to MLflow ({exc}); its results are in {out}",
                  file=sys.stderr)  # fmt: skip
            return tracking.FAILED_EXIT_CODE
        out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"logged to {results['tracking']['store_description']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
