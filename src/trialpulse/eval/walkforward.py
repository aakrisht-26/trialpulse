"""Walk-forward evaluation runner (CLAUDE.md Section 6).

    uv run python -m trialpulse.eval.walkforward --model m0 --origins dev

For origin T, training rows are landmarks with L before T, with every outcome
administratively censored at T. Evaluation rows are landmarks with T <= L < T + 12 months,
scored against outcomes observed through the data cutoff. Metrics are reported per
horizon, pooled over landmark indices and per landmark index, with cluster-bootstrap
intervals that resample trials.

Locked origins (2018, 2019 and the 2020 stress test) need the test lock (ADR 0004). The
lock is checked before any data is loaded or any model is fitted.

Input contract (written by the Step 4 cohort build; approved 2026-09-23): one row per
(trial, landmark) with trial_id, landmark_index, landmark_date, event (0 censored, 1 early
stop, 2 completion) and event_date, plus any feature columns the model needs (M0 uses
"stratum"). event_date holds the date of the event when event is 1 or 2, and the censoring
date when event is 0 (the data cutoff, or the UNKNOWN censoring date of Section 6).
"""

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, Self

import duckdb
import numpy as np
import numpy.typing as npt

from trialpulse.config import REPO_ROOT, Origin, ProjectConfig, load_project_config
from trialpulse.dates import DateArray, add_months, days_between
from trialpulse.eval import EVENT_CENSORED
from trialpulse.eval.bootstrap import BootstrapError, cluster_bootstrap
from trialpulse.eval.ipcw import FloatArray, IntArray
from trialpulse.eval.lock import TestLockError, require_unlock
from trialpulse.eval.metrics import (
    calibration_slope_intercept,
    calibration_table,
    ipcw_auc,
    ipcw_brier,
    lift_at,
)
from trialpulse.models.aalen_johansen import AalenJohansenModel

LANDMARKS_PATH = REPO_ROOT / "data" / "cohort" / "landmarks.parquet"
RESULTS_DIR = REPO_ROOT / "data" / "results" / "walkforward"
REQUIRED_COLUMNS = ("trial_id", "landmark_index", "landmark_date", "event", "event_date")


@dataclass(frozen=True)
class LandmarkRows:
    trial_id: npt.NDArray[Any]
    landmark_index: IntArray
    landmark_date: DateArray
    event: IntArray
    event_date: DateArray
    features: Mapping[str, npt.NDArray[Any]]

    def __len__(self) -> int:
        return len(self.trial_id)

    def subset(self, mask: npt.NDArray[np.bool_]) -> "LandmarkRows":
        return LandmarkRows(
            self.trial_id[mask],
            self.landmark_index[mask],
            self.landmark_date[mask],
            self.event[mask],
            self.event_date[mask],
            {k: v[mask] for k, v in self.features.items()},
        )


class CIFModel(Protocol):
    name: str

    def fit(
        self, time: FloatArray, event: IntArray, features: Mapping[str, npt.NDArray[Any]]
    ) -> Self: ...

    def predict_cif(
        self, horizon: FloatArray | float, features: Mapping[str, npt.NDArray[Any]]
    ) -> FloatArray: ...


MODELS: dict[str, Callable[[], CIFModel]] = {"m0": AalenJohansenModel}


def load_landmark_rows(path: Path) -> LandmarkRows:
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found: run the Step 4 cohort build first")
    with duckdb.connect() as con:
        data = con.execute(f"SELECT * FROM read_parquet('{path.as_posix()}')").fetchnumpy()
    missing = [c for c in REQUIRED_COLUMNS if c not in data]
    if missing:
        raise ValueError(f"{path} lacks required columns: {missing}")
    return LandmarkRows(
        np.asarray(data["trial_id"]),
        np.asarray(data["landmark_index"], dtype=np.int64),
        np.asarray(data["landmark_date"]).astype("datetime64[D]"),
        np.asarray(data["event"], dtype=np.int64),
        np.asarray(data["event_date"]).astype("datetime64[D]"),
        {k: np.asarray(v) for k, v in data.items() if k not in REQUIRED_COLUMNS},
    )


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
    """Landmarks before the origin, with outcomes administratively censored at the origin:
    anything dated on or after T was not known at T."""
    train = rows.subset(rows.landmark_date < origin)
    late = train.event_date >= origin
    event = np.where(late, EVENT_CENSORED, train.event).astype(np.int64)
    end = np.where(late, origin, train.event_date).astype("datetime64[D]")
    return train, days_between(train.landmark_date, end), event


def evaluation_rows(
    rows: LandmarkRows, origin: np.datetime64, window_months: int
) -> tuple[LandmarkRows, FloatArray, IntArray]:
    """Landmarks with T <= L < T + window, with outcomes through the data cutoff."""
    window_end = add_months(np.array([origin], dtype="datetime64[D]"), window_months)[0]
    ev = rows.subset((rows.landmark_date >= origin) & (rows.landmark_date < window_end))
    return ev, days_between(ev.landmark_date, ev.event_date), ev.event


def horizon_days(landmark_dates: DateArray, months: int) -> FloatArray:
    """Each row's horizon in days: from L to L plus the horizon in calendar months."""
    return days_between(landmark_dates, add_months(landmark_dates, months))


def evaluate_predictions(
    time: FloatArray,
    event: IntArray,
    score: FloatArray,
    horizon: FloatArray,
    clusters: npt.NDArray[Any],
    cfg: ProjectConfig,
    n_resamples: int,
    context: str = "",
) -> dict[str, Any]:
    ev = cfg.evaluation
    seed = cfg.seeds.default

    def boot(name: str, metric: Callable[..., float]) -> dict[str, float | int]:
        try:
            return cluster_bootstrap(
                clusters,
                lambda idx: metric(time[idx], event[idx], score[idx], horizon[idx]),
                n_resamples,
                seed,
                ev.confidence_level,
            )
        except BootstrapError as exc:  # name the slice and metric that failed
            raise BootstrapError(f"{context}, metric {name}: {exc}") from exc

    slope, intercept = calibration_slope_intercept(time, event, score, horizon)
    return {
        "n_rows": len(time),
        "n_trials": len(np.unique(clusters)),
        "auc": boot("auc", ipcw_auc),
        "brier": boot("brier", ipcw_brier),
        "lift": boot("lift", lambda t, e, s, h: lift_at(t, e, s, h, ev.lift_top_fraction)),
        "calibration_slope": slope,
        "calibration_intercept": intercept,
        "calibration_table": calibration_table(
            time, event, score, float(np.median(horizon)), ev.calibration_bins
        ),
    }


def run(
    cfg: ProjectConfig,
    model_name: str,
    origins_spec: str,
    load_rows: Callable[[], LandmarkRows],
    unlock_flag: bool = False,
    repo: Path = REPO_ROOT,
    n_resamples: int | None = None,
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
        "unlock": {**asdict(unlock), "registered": unlock.registered.isoformat()}
        if unlock
        else None,
        "origins": [],
    }
    for origin in origins:
        t = np.datetime64(origin.date, "D")
        train, train_time, train_event = training_rows(rows, t)
        ev, ev_time, ev_event = evaluation_rows(rows, t, cfg.walk_forward.eval_window_months)
        model = MODELS[model_name]().fit(train_time, train_event, train.features)
        horizons: dict[str, Any] = {}
        for months in cfg.horizons_months:
            h = horizon_days(ev.landmark_date, months)
            score = model.predict_cif(h, ev.features)
            where = f"origin {origin.date.isoformat()}, horizon {months} months"
            pooled = evaluate_predictions(
                ev_time, ev_event, score, h, ev.trial_id, cfg, resamples, f"{where}, pooled"
            )
            by_index = {}
            for k in np.unique(ev.landmark_index):
                m = ev.landmark_index == k
                by_index[str(int(k))] = evaluate_predictions(
                    ev_time[m], ev_event[m], score[m], h[m], ev.trial_id[m], cfg, resamples,
                    f"{where}, landmark index {int(k)}",
                )  # fmt: skip
            horizons[str(months)] = {"pooled": pooled, "by_landmark_index": by_index}
        results["origins"].append(
            {
                "origin": origin.date.isoformat(),
                "role": origin.role,
                "n_train_rows": len(train),
                "n_eval_rows": len(ev),
                "horizons": horizons,
            }
        )
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Walk-forward evaluation")
    parser.add_argument("--model", required=True, choices=sorted(MODELS))
    parser.add_argument("--origins", default="dev", help="dev, test, stress, all, or years")
    parser.add_argument("--unlock-test", action="store_true", help="see ADR 0004")
    parser.add_argument("--input", type=Path, default=LANDMARKS_PATH)
    parser.add_argument("--resamples", type=int, default=None)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    try:
        results = run(
            cfg,
            args.model,
            args.origins,
            lambda: load_landmark_rows(args.input),
            unlock_flag=args.unlock_test,
            n_resamples=args.resamples,
        )
    except (TestLockError, FileNotFoundError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except BootstrapError as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 3
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{args.model}_{args.origins.replace(',', '-')}.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
