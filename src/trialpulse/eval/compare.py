"""Is one model better than another? Paired differences on the same evaluation rows.

    uv run python -m trialpulse.eval.compare

The interval of a model's AUC says how well that one number is known. It does not say
whether a second model is better, because both are scored on the same trials: most of what
moves one AUC from resample to resample moves the other too. So the difference is
bootstrapped itself. Trials are resampled once, both models are scored on each resample,
and the interval is that of the difference (a paired cluster bootstrap). A difference
whose interval excludes 0 is one the evaluation trials can tell from no difference.

**What is compared.** The rungs of the ladder with the rung below (ADR 0024), M4 with M0,
M4 with M1 at registration (landmark 0, where M1 lives), and the ablation rungs of M4 with
each other. For each pair and horizon: the difference in IPCW AUC and in IPCW Brier score,
on all landmark indices both models score, at landmark 0, and, for the rungs of the ladder,
at each landmark index.

**The number reported** is the mean of the difference over the development origins, the
quantity the selection rule of Section 9 reads. A trial can have evaluation rows at two
origins; it is one cluster, so the origins are resampled together.

**The rows** come from the prediction files the walk-forward harness writes
(`data/results/walkforward/predictions/<model>_dev/`). Two models are compared only on
rows that both files hold with the same follow-up, event and censoring group: anything
else means the files are not from the same evaluation rows, and is refused.

Development origins only. A prediction file of a locked origin is never read here.
"""

import argparse
import datetime as dt
import json
import sys
import time as clock
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import numpy.typing as npt

from trialpulse import tracking
from trialpulse.cli import RefusedError, run
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.dates import add_months, days_between
from trialpulse.eval.bootstrap import BootstrapError, cluster_bootstrap_many
from trialpulse.eval.ipcw import FloatArray, IntArray, horizon_labels_and_weights
from trialpulse.eval.metrics import weighted_auc
from trialpulse.eval.walkforward import PREDICTIONS_DIR, predictions_path

RESULTS_PATH = REPO_ROOT / "data" / "results" / "compare" / "dev.json"
DEV = "dev"
ALL = "all"
# (model, baseline, what the difference shows, also at every landmark index)
PAIRS: tuple[tuple[str, str, str, bool], ...] = (
    ("m2", "m0", "the design family, the landmark index and a regression for a curve", True),
    ("m3", "m2", "the amendment signals", True),
    ("m4", "m3", "the other families, and trees for a linear model", True),
    ("m4", "m0", "the whole ladder", False),
    ("m4", "m1", "the dynamic model against the static one, at registration", False),
    ("m4-amendments", "m4-design", "ablation of M4: the amendment signals", False),
    ("m4-sponsor", "m4-amendments", "ablation of M4: the sponsor record and competition", False),
    ("m4", "m4-sponsor", "ablation of M4: the text families", False),
)


@dataclass(frozen=True)
class Paired:
    """The evaluation rows two models share, over the origins compared."""

    origin: IntArray  # the position of each row's origin among the origins
    trial_id: npt.NDArray[Any]
    landmark_index: IntArray
    time: FloatArray
    event: IntArray
    groups: npt.NDArray[Any]
    horizon: dict[int, FloatArray]
    model: dict[int, FloatArray]
    baseline: dict[int, FloatArray]

    def __len__(self) -> int:
        return len(self.trial_id)

    def subset(self, mask: npt.NDArray[np.bool_]) -> "Paired":
        return Paired(
            self.origin[mask], self.trial_id[mask], self.landmark_index[mask], self.time[mask],
            self.event[mask], self.groups[mask],
            {m: h[mask] for m, h in self.horizon.items()},
            {m: s[mask] for m, s in self.model.items()},
            {m: s[mask] for m, s in self.baseline.items()},
        )  # fmt: skip


def load_pair(
    folder: Path, model: str, baseline: str, origins: Sequence[dt.date], months: Sequence[int]
) -> Paired:
    """The rows both models scored, for every origin, in one table."""
    parts: dict[str, list[npt.NDArray[Any]]] = {}
    for position, origin in enumerate(origins):
        a = predictions_path(folder, model, DEV, origin)
        b = predictions_path(folder, baseline, DEV, origin)
        for path, name in ((a, model), (b, baseline)):
            if not path.is_file():
                raise FileNotFoundError(
                    f"{path} is missing. Create it with: uv run python -m "
                    f"trialpulse.eval.walkforward --model {name} --origins dev"
                )
        scores = ", ".join(f"a.score_{m} AS model_{m}, b.score_{m} AS baseline_{m}" for m in months)
        with duckdb.connect() as con:
            shared = f"""landmark_index IN (
                SELECT landmark_index FROM read_parquet('{a.as_posix()}')
                INTERSECT SELECT landmark_index FROM read_parquet('{b.as_posix()}'))"""
            n_a, n_b = (
                int(
                    con.execute(  # type: ignore[index]
                        f"SELECT count(*) FROM read_parquet('{p.as_posix()}') WHERE {shared}"
                    ).fetchone()[0]
                )
                for p in (a, b)
            )
            data = con.execute(
                f"""SELECT a.trial_id, a.landmark_index, a.landmark_date, a.time, a.event,
                  a.censoring_group, {scores},
                  (a.time = b.time AND a.event = b.event
                   AND a.censoring_group = b.censoring_group
                   AND a.landmark_date = b.landmark_date) AS same
                FROM read_parquet('{a.as_posix()}') a
                JOIN read_parquet('{b.as_posix()}') b USING (trial_id, landmark_index)
                ORDER BY a.trial_id, a.landmark_index"""
            ).fetchnumpy()
        n = len(data["trial_id"])
        # At the landmark indices both models score, both files must hold the same rows.
        if not n_a == n_b == n or n == 0 or not bool(np.all(data["same"])):
            raise RefusedError(
                f"{model} and {baseline} were not scored on the same evaluation rows of origin "
                f"{origin} ({n_a:,} and {n_b:,} rows at the landmark indices they share, {n:,} "
                f"in both, {int(np.sum(~np.asarray(data['same'], dtype=bool))):,} with another "
                "follow-up, event or censoring group): run both models again"
            )
        landmark = np.asarray(data["landmark_date"]).astype("datetime64[D]")
        parts.setdefault("origin", []).append(np.full(n, position, dtype=np.int64))
        parts.setdefault("landmark_date", []).append(landmark)
        for key in ("trial_id", "landmark_index", "time", "event", "censoring_group"):
            parts.setdefault(key, []).append(np.asarray(data[key]))
        for m in months:
            parts.setdefault(f"model_{m}", []).append(np.asarray(data[f"model_{m}"]))
            parts.setdefault(f"baseline_{m}", []).append(np.asarray(data[f"baseline_{m}"]))
    merged = {key: np.concatenate(values) for key, values in parts.items()}
    landmark = merged["landmark_date"]
    return Paired(
        merged["origin"],
        merged["trial_id"].astype(str),
        merged["landmark_index"].astype(np.int64),
        merged["time"].astype(np.float64),
        merged["event"].astype(np.int64),
        merged["censoring_group"].astype(str),
        {m: days_between(landmark, add_months(landmark, m)) for m in months},
        {m: merged[f"model_{m}"].astype(np.float64) for m in months},
        {m: merged[f"baseline_{m}"].astype(np.float64) for m in months},
    )


def _metrics(rows: Paired, months: int, idx: npt.NDArray[np.int64]) -> dict[str, float]:
    """Both models on some rows of one origin: the labels and weights are computed once."""
    y, w = horizon_labels_and_weights(
        rows.time[idx], rows.event[idx], rows.horizon[months][idx], rows.groups[idx]
    )
    a, b = rows.model[months][idx], rows.baseline[months][idx]
    n = len(idx)
    return {
        "auc_model": weighted_auc(y, a, w),
        "auc_baseline": weighted_auc(y, b, w),
        "brier_model": float(np.sum(w * (y - a) ** 2) / n) if n else float("nan"),
        "brier_baseline": float(np.sum(w * (y - b) ** 2) / n) if n else float("nan"),
    }


def differences(rows: Paired, months: int, idx: npt.NDArray[np.int64]) -> dict[str, float]:
    """The difference model minus baseline in AUC and in Brier score, as the mean over the
    origins of the difference within each origin."""
    origin = rows.origin[idx]
    auc, brier = [], []
    for position in np.unique(rows.origin):
        m = _metrics(rows, months, idx[origin == position])
        auc.append(m["auc_model"] - m["auc_baseline"])
        brier.append(m["brier_model"] - m["brier_baseline"])
    return {"delta_auc": float(np.mean(auc)), "delta_brier": float(np.mean(brier))}


def compare_slice(
    rows: Paired, months: int, origins: Sequence[dt.date], cfg: ProjectConfig, n_resamples: int
) -> dict[str, Any]:
    """One slice of one pair at one horizon: both models at each origin, and the mean
    difference over the origins with its paired interval."""
    by_origin = {}
    for position, origin in enumerate(origins):
        idx = np.flatnonzero(rows.origin == position)
        m = _metrics(rows, months, idx)
        by_origin[origin.isoformat()] = {
            "n_rows": len(idx),
            **m,
            "delta_auc": m["auc_model"] - m["auc_baseline"],
            "delta_brier": m["brier_model"] - m["brier_baseline"],
        }
    intervals = cluster_bootstrap_many(
        rows.trial_id,
        lambda idx: differences(rows, months, idx),
        n_resamples,
        cfg.seeds.default,
        cfg.evaluation.confidence_level,
    )
    return {
        "n_rows": len(rows),
        "n_trials": len(np.unique(rows.trial_id)),
        "by_origin": by_origin,
        **intervals,
    }


def compare_pair(
    cfg: ProjectConfig,
    folder: Path,
    model: str,
    baseline: str,
    by_index: bool,
    origins: Sequence[dt.date],
    n_resamples: int,
) -> dict[str, Any]:
    months = tuple(cfg.horizons_months)
    rows = load_pair(folder, model, baseline, origins, months)
    indices = [int(k) for k in np.unique(rows.landmark_index)]
    slices: dict[str, Paired] = {}
    if len(indices) > 1:
        slices[ALL] = rows
    if 0 in indices:
        slices["0"] = rows.subset(rows.landmark_index == 0)
    if by_index:
        for k in indices:
            slices.setdefault(str(k), rows.subset(rows.landmark_index == k))
    out: dict[str, Any] = {"landmark_indices": indices, "horizons": {}}
    for m in months:
        per_slice = {}
        for name, part in slices.items():
            try:
                per_slice[name] = compare_slice(part, m, origins, cfg, n_resamples)
            except BootstrapError as exc:
                raise BootstrapError(
                    f"{model} against {baseline}, {m} months, slice {name}: {exc}"
                ) from exc
        out["horizons"][str(m)] = per_slice
    return out


def run_metrics(result: dict[str, Any]) -> dict[str, float]:
    """The mean differences as flat metric names, for the logged run."""
    out: dict[str, float] = {}
    for pair in result["pairs"]:
        name = f"{pair['model']}_minus_{pair['baseline']}".replace("-", "_")
        for months, slices in pair["horizons"].items():
            for part, values in slices.items():
                if part not in (ALL, "0"):
                    continue
                where = "all" if part == ALL else "l0"
                for metric in ("delta_auc", "delta_brier"):
                    out[f"{metric}_{months}m_{where}_{name}"] = float(values[metric]["estimate"])
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paired differences between models (dev).")
    parser.add_argument("--predictions-dir", type=Path, default=PREDICTIONS_DIR)
    parser.add_argument("--out", type=Path, default=RESULTS_PATH)
    parser.add_argument("--resamples", type=int, default=None)
    parser.add_argument(
        "--pairs", default=None, help="for example m4:m1,m3:m2 (default: every pair with files)"
    )
    tracking.add_arguments(parser)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    started = tracking.git_state()
    origins = [o.date for o in cfg.walk_forward.origins if o.role == DEV]
    if not origins:
        raise RefusedError("config/project.yaml names no development origin")
    wanted = None if args.pairs is None else {tuple(p.split(":")) for p in args.pairs.split(",")}
    n = cfg.evaluation.bootstrap_resamples if args.resamples is None else args.resamples
    result: dict[str, Any] = {
        "origins_spec": DEV,
        "origins": [o.isoformat() for o in origins],
        "bootstrap_resamples": n,
        "pairs": [],
        "skipped": [],
    }
    begun = clock.perf_counter()
    for model, baseline, what, by_index in PAIRS:
        if wanted is not None and (model, baseline) not in wanted:
            continue
        have = all(
            predictions_path(args.predictions_dir, name, DEV, origin).is_file()
            for name in (model, baseline)
            for origin in origins
        )
        if not have:
            result["skipped"].append({"model": model, "baseline": baseline})
            continue
        try:
            compared = compare_pair(
                cfg, args.predictions_dir, model, baseline, by_index, origins, n
            )
        except BootstrapError as exc:
            print(f"failed: {exc}", file=sys.stderr)
            return 3
        result["pairs"].append({"model": model, "baseline": baseline, "what": what, **compared})
        primary = compared["horizons"][str(min(cfg.horizons_months))]
        part = primary.get(ALL, primary.get("0"))
        d = part["delta_auc"]
        print(
            f"{model} minus {baseline}: AUC {d['estimate']:+.4f} "
            f"({d['ci_low']:+.4f} to {d['ci_high']:+.4f}) at {min(cfg.horizons_months)} months"
        )
    if not result["pairs"]:
        raise RefusedError(
            f"no pair of models has prediction files under {args.predictions_dir}; run the "
            "models first, for example: uv run python -m trialpulse.eval.walkforward --model "
            "m4 --origins dev"
        )
    result["seconds"] = round(clock.perf_counter() - begun, 1)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    if not args.no_track:
        try:
            result["tracking"] = tracking.log_run(
                "compare-dev",
                {"pairs": len(result["pairs"]), "bootstrap_resamples": n,
                 "origins": ",".join(result["origins"])},
                run_metrics(result),
                [args.out],
                cfg,
                tags={"step": "10", "kind": "comparison", "roles": DEV},
                local=args.local_tracking,
                state=started,
            )  # fmt: skip
        except tracking.TrackingError as exc:  # the results are on disk: say so and fail
            print(f"failed: the run was not logged to MLflow ({exc}); its results are in "
                  f"{args.out}", file=sys.stderr)  # fmt: skip
            return tracking.FAILED_EXIT_CODE
        args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"logged to {result['tracking']['store_description']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
