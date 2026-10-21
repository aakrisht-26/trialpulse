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

**The number reported** is the mean of the difference over the origins compared (the
development origins, or the locked test origins), the quantity the selection rule of
Section 9 reads. A trial can have evaluation rows at two origins; it is one cluster, so the
origins are resampled together.

**The trend over landmark indices** (H2 of `docs/preregistration.md`). For a pair compared
at every landmark index, each resample also gives the seven differences in AUC, the slope
of the least-squares line through them against the landmark index, and the mean
difference at the late landmarks (4 to 6) minus the mean at the early ones (0 and 1). The
weights of each landmark index are those of its own slice, as in the table by index.

**The rows** come from the prediction files the walk-forward harness writes, from a run of
the origins compared (`data/results/walkforward/predictions/<model>_dev/`) or from a run of
one origin (`<model>_2016/`), never both. Two models are compared only on rows that both
files hold with the same follow-up, event, landmark date and censoring group: anything else
means the files are not from the same evaluation rows, and is refused.

**One file per pair** (`data/results/compare/<dev or test>/<model>__<baseline>.json`), so
that each pair can be its own job (`--pairs m4:m1`); `trialpulse.eval.results_dev` reads the
development ones.

**The test lock.** `--origins test` compares on the locked test origins (2018 and 2019) and
needs `--unlock-test` and every condition of ADR 0004; the lock is checked before any
prediction file is read, and the unlock is recorded in each result file.
"""

import argparse
import datetime as dt
import json
import sys
import time as clock
from collections.abc import Sequence
from dataclasses import asdict, dataclass
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
from trialpulse.eval.lock import TestLockError, require_unlock
from trialpulse.eval.metrics import weighted_auc
from trialpulse.eval.walkforward import (
    PREDICTIONS_DIR,
    predictions_path,
    relative,
    select_origins,
    sha256_of,
)

RESULTS_DIR = REPO_ROOT / "data" / "results" / "compare"
PAIR_SEPARATOR = "__"  # m4__m1.json: model names hold hyphens, never two underscores
DEV = "dev"
TEST = "test"
ALL = "all"
TREND = "trend"
# The late-minus-early contrast of the trend (H2 of docs/preregistration.md).
EARLY_INDICES: tuple[int, ...] = (0, 1)
LATE_INDICES: tuple[int, ...] = (4, 5, 6)
# (model, baseline, what the difference shows, also at every landmark index)
PAIRS: tuple[tuple[str, str, str, bool], ...] = (
    ("m2", "m0", "the design family, the landmark index and a regression for a curve", True),
    ("m3", "m2", "the amendment signals", True),
    ("m4", "m3", "the sponsor record and competition, and trees for a linear model", True),
    ("m4", "m0", "the whole ladder", False),
    ("m4", "m1", "the dynamic model against the static one, at registration", False),
    ("m4-amendments", "m4-design", "ablation of M4: the amendment signals", False),
    ("m4", "m4-amendments", "ablation of M4: the sponsor record and competition", False),
    ("m4-text", "m4", "ablation of M4: the text families, which M4 leaves out", False),
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


def prediction_file(folder: Path, model: str, origin: dt.date, spec: str = DEV) -> Path:
    """The scores of a model at an origin: from a run of the origins compared (`spec`, "dev"
    or "test") or from a run of that origin alone. Both at once are two answers to one
    question, and are refused."""
    found = [
        path
        for path in (
            predictions_path(folder, model, spec, origin),
            predictions_path(folder, model, str(origin.year), origin),
        )
        if path.is_file()
    ]
    if len(found) > 1:
        raise RefusedError(
            f"two prediction files of {model} for origin {origin} ({found[0]} and {found[1]}): "
            "keep the one of the run that the result files name"
        )
    if not found:
        raise FileNotFoundError(
            f"no prediction file of {model} for origin {origin}. Create it with: uv run python "
            f"-m trialpulse.eval.walkforward --model {model} --origins {spec} (or --origins "
            f"{origin.year} for that origin alone)"
        )
    return found[0]


def inputs_of(
    folder: Path, models: Sequence[str], origins: Sequence[dt.date], spec: str
) -> list[dict[str, str]]:
    """The scores files a job reads, with their SHA-256: the generator of docs/results.md
    holds them against the files the walk-forward runs wrote (Step 11)."""
    out = []
    for model in models:
        for origin in origins:
            path = prediction_file(folder, model, origin, spec)
            out.append(
                {
                    "model": model,
                    "origin": origin.isoformat(),
                    "path": path.relative_to(folder).as_posix(),
                    "sha256": sha256_of(path),
                }
            )
    return out


def pair_path(folder: Path, model: str, baseline: str) -> Path:
    return folder / f"{model}{PAIR_SEPARATOR}{baseline}.json"


def load_pair(
    folder: Path,
    model: str,
    baseline: str,
    origins: Sequence[dt.date],
    months: Sequence[int],
    spec: str = DEV,
) -> Paired:
    """The rows both models scored, for every origin, in one table."""
    parts: dict[str, list[npt.NDArray[Any]]] = {}
    for position, origin in enumerate(origins):
        a = prediction_file(folder, model, origin, spec)
        b = prediction_file(folder, baseline, origin, spec)
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


def slope(x: Sequence[float], y: Sequence[float]) -> float:
    """The slope of the least-squares line through (x, y); NaN if any y is not finite."""
    xs, ys = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    if len(xs) < 2 or not np.all(np.isfinite(ys)):
        return float("nan")
    dx = xs - xs.mean()
    return float(np.sum(dx * (ys - ys.mean())) / np.sum(dx**2))


def trend(
    rows: Paired, months: int, idx: npt.NDArray[np.int64], indices: Sequence[int]
) -> dict[str, float]:
    """On some rows (a resample of trials): the difference in AUC at each landmark index,
    each with the weights of its own slice; the slope of the least-squares line through
    them against the landmark index; and the late-minus-early contrast."""
    k = rows.landmark_index[idx]
    delta = {i: differences(rows, months, idx[k == i])["delta_auc"] for i in indices}
    late = [delta[i] for i in LATE_INDICES if i in delta]
    early = [delta[i] for i in EARLY_INDICES if i in delta]
    contrast = float(np.mean(late) - np.mean(early)) if late and early else float("nan")
    return {
        "slope": slope(list(indices), [delta[i] for i in indices]),
        "late_minus_early": contrast,
    }


def compare_trend(
    rows: Paired, months: int, indices: Sequence[int], cfg: ProjectConfig, n_resamples: int
) -> dict[str, Any]:
    """The trend of one pair at one horizon, with paired intervals: trials are resampled
    once, and both models are scored at every landmark index of each resample."""
    if not np.all(np.isin(rows.landmark_index, indices)):
        raise ValueError("the trend needs every landmark index of the rows")
    return {
        "landmark_indices": list(indices),
        "late_indices": [i for i in LATE_INDICES if i in indices],
        "early_indices": [i for i in EARLY_INDICES if i in indices],
        **cluster_bootstrap_many(
            rows.trial_id,
            lambda idx: trend(rows, months, idx, indices),
            n_resamples,
            cfg.seeds.default,
            cfg.evaluation.confidence_level,
        ),
    }


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
    spec: str = DEV,
) -> dict[str, Any]:
    months = tuple(cfg.horizons_months)
    rows = load_pair(folder, model, baseline, origins, months, spec)
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
    if by_index and len(indices) > 2:
        out[TREND] = {}
        for m in months:
            try:
                found = compare_trend(rows, m, indices, cfg, n_resamples)
            except BootstrapError as exc:
                where = f"{model} against {baseline}, {m} months, trend"
                raise BootstrapError(f"{where}: {exc}") from exc
            out[TREND][str(m)] = found
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
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="one file for every pair (default: one file per pair in data/results/compare/dev/)",
    )
    parser.add_argument("--resamples", type=int, default=None)
    parser.add_argument(
        "--pairs", default=None, help="for example m4:m1,m3:m2 (default: every pair with files)"
    )
    parser.add_argument(
        "--origins", default=DEV, choices=[DEV, TEST], help="the origins compared (default dev)"
    )
    parser.add_argument("--unlock-test", action="store_true", help="see ADR 0004")
    tracking.add_arguments(parser)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    started = tracking.git_state()
    selected = select_origins(cfg, args.origins)
    try:  # before any prediction file is read
        unlock = require_unlock({o.role for o in selected}, REPO_ROOT, args.unlock_test)
    except TestLockError as exc:
        raise RefusedError(str(exc)) from exc
    origins = [o.date for o in selected]
    spec = args.origins
    wanted = None if args.pairs is None else {tuple(p.split(":")) for p in args.pairs.split(",")}
    n = cfg.evaluation.bootstrap_resamples if args.resamples is None else args.resamples
    result: dict[str, Any] = {
        "origins_spec": spec,
        "origins": [o.isoformat() for o in origins],
        "bootstrap_resamples": n,
        "unlock": None
        if unlock is None
        else {**asdict(unlock), "registered": unlock.registered.isoformat()},
        "predictions_dir": relative(args.predictions_dir),
        "pairs": [],
        "skipped": [],
    }
    begun = clock.perf_counter()
    for model, baseline, what, by_index in PAIRS:
        if wanted is not None and (model, baseline) not in wanted:
            continue
        have = all(
            any(
                predictions_path(args.predictions_dir, name, kind, origin).is_file()
                for kind in (spec, str(origin.year))
            )
            for name in (model, baseline)
            for origin in origins
        )
        if not have:
            result["skipped"].append({"model": model, "baseline": baseline})
            continue
        pair_started = clock.perf_counter()
        try:
            compared = compare_pair(
                cfg, args.predictions_dir, model, baseline, by_index, origins, n, spec
            )
        except BootstrapError as exc:
            print(f"failed: {exc}", file=sys.stderr)
            return 3
        result["pairs"].append(
            {"model": model, "baseline": baseline, "what": what, **compared,
             "inputs": inputs_of(args.predictions_dir, (model, baseline), origins, spec),
             "seconds": round(clock.perf_counter() - pair_started, 1)}
        )  # fmt: skip
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
    # One file for every pair if asked for, else one file per pair, each a result of its own.
    if args.out is not None:
        files = {args.out: result}
    else:
        files = {
            pair_path(RESULTS_DIR / spec, p["model"], p["baseline"]): {
                **{k: v for k, v in result.items() if k not in ("pairs", "skipped", "seconds")},
                "pairs": [p],
                "seconds": p["seconds"],
            }
            for p in result["pairs"]
        }
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content, indent=2), encoding="utf-8")
        print(f"wrote {path}")
    if not args.no_track:
        only = result["pairs"][0] if len(result["pairs"]) == 1 else None
        base = f"compare-{only['model']}-vs-{only['baseline']}" if only else "compare"
        name = base if spec == DEV and only else f"{base}-{spec}"
        lock_tags = {} if unlock is None else {
            "test_lock": "unlocked", "unlock_tag_object": unlock.tag_object,
            "unlock_commit": unlock.commit,
        }  # fmt: skip
        try:
            logged = tracking.log_run(
                name,
                {"pairs": len(result["pairs"]), "bootstrap_resamples": n,
                 "origins": ",".join(result["origins"])},
                run_metrics(result),
                list(files),
                cfg,
                tags={"step": "10" if unlock is None else "11", "kind": "comparison",
                      "roles": spec, **lock_tags},
                local=args.local_tracking,
                state=started,
            )  # fmt: skip
        except tracking.TrackingError as exc:  # the results are on disk: say so and fail
            print(f"failed: the run was not logged to MLflow ({exc}); its results are in "
                  f"{', '.join(map(str, files))}", file=sys.stderr)  # fmt: skip
            return tracking.FAILED_EXIT_CODE
        for path, content in files.items():
            content["tracking"] = logged
            path.write_text(json.dumps(content, indent=2), encoding="utf-8")
        print(f"logged to {logged['store_description']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
