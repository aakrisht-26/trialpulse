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

**Predictions.** With a predictions folder, the scores of every evaluation row are saved
per origin, with the row's follow-up, event and censoring group. `trialpulse.eval.compare`
reads them to bootstrap the difference between two models on the same resampled trials.
The files hold registry ids and scores only, and are not committed.

**Tuning.** `--tune` first chooses a model's settings on the development origins
(`trialpulse.models.tuning`), then runs the model with them.

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
from functools import partial
from pathlib import Path
from typing import Any, Protocol, Self

import duckdb
import numpy as np
import numpy.typing as npt
import pandas as pd

from trialpulse import tracking
from trialpulse.cli import RefusedError
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
    VARIANT,
    VARIANT_COHORT_DIR,
    VARIANT_FEATURES_DIR,
    LandmarkRows,
    horizon_days,
    load_landmark_rows,
    training_path,
)
from trialpulse.models import discrete_time
from trialpulse.models.aalen_johansen import AalenJohansenModel
from trialpulse.models.static_clf import StaticClassifier

RESULTS_DIR = REPO_ROOT / "data" / "results" / "walkforward"
PREDICTIONS_DIR = RESULTS_DIR / "predictions"
FEATURES_DIR = REPO_ROOT / "data" / "features"
CENSORING_COLUMN = "stratum"  # the lead sponsor class at the landmark (ADR 0017)
DEV_ROLE = "dev"  # the origins a model is developed and tuned on (Section 6)
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
    # M2, M3, M4 and the ablation rungs of M4 (ADR 0024).
    **{name: partial(discrete_time.build, name) for name in discrete_time.FAMILY_SETS},
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
        chosen = origins
    elif spec in {"dev", "test", "stress"}:
        chosen = [o for o in origins if o.role == spec]
    else:
        years = {int(y) for y in spec.split(",") if y.strip()}
        chosen = [o for o in origins if o.date.year in years]
        if len(chosen) != len(years):
            raise ValueError(f"unknown origin year in {spec!r}")
    if not chosen:
        raise ValueError(f"no origin is selected by {spec!r}")
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
    years: npt.NDArray[Any] | None = None,
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
        "calibration_by_registration_year": []
        if years is None
        else calibration_by_group(time, event, score, horizon, groups, by=years),
        "single_censoring_curve": {
            "auc": ipcw_auc(time, event, score, horizon),
            "brier": ipcw_brier(time, event, score, horizon),
            "lift": lift(time, event, score, horizon, None),
            "calibration_slope": single_slope,
            "calibration_intercept": single_intercept,
        },
    }


def registration_years(rows: LandmarkRows, spacing_months: int) -> IntArray:
    """The calendar year each row's trial was first posted: its landmark date less the
    landmark index times the spacing of the landmarks."""
    first_post = rows.landmark_date.copy()
    for k in np.unique(rows.landmark_index):
        member = rows.landmark_index == k
        first_post[member] = add_months(rows.landmark_date[member], -spacing_months * int(k))
    years: IntArray = first_post.astype("datetime64[Y]").astype(np.int64) + 1970
    return years


def save_predictions(
    path: Path,
    rows: LandmarkRows,
    time: FloatArray,
    event: IntArray,
    groups: npt.NDArray[Any],
    scores: Mapping[int, FloatArray],
) -> None:
    """The scores of an origin's evaluation rows, one row per landmark row, sorted by key."""
    frame = pd.DataFrame(  # noqa: F841 (read by DuckDB below)
        {
            "trial_id": rows.trial_id.astype(str),
            "landmark_index": rows.landmark_index,
            "landmark_date": rows.landmark_date,
            "time": time,
            "event": event,
            "censoring_group": np.asarray(groups).astype(str),
            **{f"score_{months}": score for months, score in scores.items()},
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as con:
        con.execute(
            f"""COPY (SELECT * REPLACE (CAST(landmark_date AS DATE) AS landmark_date) FROM frame
            ORDER BY trial_id, landmark_index) TO '{path.as_posix()}' (FORMAT parquet)"""
        )


def predictions_path(folder: Path, model: str, origins_spec: str, origin: dt.date) -> Path:
    spec = origins_spec.replace(",", "-")
    return folder / f"{model}_{spec}" / f"origin_{origin.isoformat()}.parquet"


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
    predictions_dir: Path | None = None,
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
        years = registration_years(ev, cfg.landmarks.spacing_months)
        horizons: dict[str, Any] = {}
        scores: dict[int, FloatArray] = {}
        for months in cfg.horizons_months:
            h = horizon_days(ev.landmark_date, months)
            score = scores[months] = model.predict_months(ev, months)
            where = f"origin {origin.date.isoformat()}, horizon {months} months"
            pooled = evaluate_predictions(
                ev_time, ev_event, score, h, ev.trial_id, groups, cfg, resamples,
                f"{where}, pooled", years,
            )  # fmt: skip
            by_index = {}
            for k in np.unique(ev.landmark_index):
                m = ev.landmark_index == k
                by_index[str(int(k))] = evaluate_predictions(
                    ev_time[m], ev_event[m], score[m], h[m], ev.trial_id[m], groups[m],
                    cfg, resamples, f"{where}, landmark index {int(k)}", years[m],
                )  # fmt: skip
            horizons[str(months)] = {"pooled": pooled, "by_landmark_index": by_index}
        if predictions_dir is not None:
            save_predictions(
                predictions_path(predictions_dir, model_name, origins_spec, origin.date),
                ev, ev_time, ev_event, groups, scores,
            )  # fmt: skip
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
    variant = results.get("variant")
    return tracking.log_run(
        f"{results['model']}-{results['origins_spec']}" + (f"-{variant}" if variant else ""),
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
            **({"variant": variant} if variant else {}),
        },
        local=local,
        state=state,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Walk-forward evaluation")
    parser.add_argument("--model", required=True, choices=sorted(MODELS))
    parser.add_argument("--origins", default="dev", help="dev, test, stress, all, or years")
    parser.add_argument("--unlock-test", action="store_true", help="see ADR 0004")
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--training-dir", type=Path, default=None)
    parser.add_argument("--features-dir", type=Path, default=None)
    parser.add_argument("--resamples", type=int, default=None)
    parser.add_argument("--predictions-dir", type=Path, default=None)
    parser.add_argument(
        "--variant",
        choices=[VARIANT],
        default=None,
        help="evaluate on the UNKNOWN sensitivity cohort (Step 11): the model is trained as in "
        "the main run and scores the variant's evaluation rows",
    )
    parser.add_argument(
        "--tune",
        action="store_true",
        help="choose the model's settings first (development origins, Section 9)",
    )
    tracking.add_arguments(parser)
    args = parser.parse_args(argv)
    variant: str | None = args.variant
    args.input = args.input or (
        VARIANT_COHORT_DIR / LANDMARKS_PATH.name if variant else LANDMARKS_PATH
    )
    args.training_dir = args.training_dir or (
        VARIANT_COHORT_DIR / TRAINING_DIR.name if variant else TRAINING_DIR
    )
    args.features_dir = args.features_dir or (VARIANT_FEATURES_DIR if variant else FEATURES_DIR)
    args.predictions_dir = args.predictions_dir or (
        PREDICTIONS_DIR / variant if variant else PREDICTIONS_DIR
    )
    if variant and args.tune:
        print("refused: --tune is for the main cohort; the variant uses the tuned settings",
              file=sys.stderr)  # fmt: skip
        return 2
    cfg = load_project_config()
    started = tracking.git_state()
    if args.tune:
        from trialpulse.models.tuning import TUNABLE
        from trialpulse.models.tuning import main as tune

        if args.model not in TUNABLE:
            print(
                f"refused: --tune is for {', '.join(TUNABLE)}; {args.model} has no settings of "
                "its own to tune (an ablation rung of M4 runs with M4's)",
                file=sys.stderr,
            )
            return 2
        # The origins first, as run() checks the lock before any data: not after an hour of
        # tuning. A locked origin is refused with or without the unlock: settings are tuned
        # on the development origins, committed, and only then used on a locked origin.
        try:
            roles = {o.role for o in select_origins(cfg, args.origins)}
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        if roles != {DEV_ROLE}:
            print(
                "refused: --tune runs with development origins only. Tune on them first, commit "
                f"config/tuned/{args.model}.json, then evaluate the locked origins without "
                "--tune (ADR 0025)",
                file=sys.stderr,
            )
            return 2
        try:
            code: int = tune(
                ["--model", args.model, "--input", str(args.input),
                 "--training-dir", str(args.training_dir),
                 "--features-dir", str(args.features_dir),
                 *(["--no-track"] if args.no_track else []),
                 *(["--local-tracking"] if args.local_tracking else [])]
            )  # fmt: skip
        except RefusedError as exc:  # a refusal or an early stop, reported as the tuning does
            print(f"{exc.prefix}: {' '.join(str(exc).split())}", file=sys.stderr)
            return exc.exit_code
        if code != 0:
            return code
        # The tuning may have rewritten config/tuned/<model>.json: the model run is credited
        # to the tree it reads its settings from, with its uncommitted changes if any. A
        # settings file that is new is not a tracked file yet, so it is asked about by name.
        started = tracking.git_state()
        if tracking.uncommitted(discrete_time.TUNED_DIR / f"{args.model}.json"):
            started["git_dirty"] = "yes"
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
            predictions_dir=args.predictions_dir,
        )
    except (TestLockError, FileNotFoundError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except BootstrapError as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 3
    if variant:
        results["variant"] = variant
    folder = RESULTS_DIR / variant if variant else RESULTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{args.model}_{args.origins.replace(',', '-')}.json"
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
