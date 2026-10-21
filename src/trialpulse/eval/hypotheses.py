"""The hypotheses of the pre-registration that are not a comparison of two models.

    uv run python -m trialpulse.eval.hypotheses h3 --origins dev
    uv run python -m trialpulse.eval.hypotheses h3 --origins test --unlock-test
    uv run python -m trialpulse.eval.hypotheses h5 --unlock-test

**H3: operational stops are more predictable than scientific stops.** The champion's
scores are evaluated twice at each origin and horizon, on the rows of every landmark index.
The labels and the censoring weights of a horizon are those of the main metric: estimated
on all the evaluation rows of the origin, with the censoring groups the harness stored
(ADR 0017). Only then are rows left out: for the operational score, the rows with an early
stop within the horizon whose reason is not operational (a scientific stop, reason
"other", or no reason text); for the scientific score, the same with the kinds swapped.
Cases are the early stops of the kind within the horizon; controls are the rows with no
early stop by the horizon. The statistic is the difference, operational minus scientific,
in IPCW AUC, as the mean over the origins of the difference at each origin, with a paired
cluster bootstrap: trials are resampled once across the origins, and the weights and both
scores are computed again on each resample.

**H5: the 2020 origin lowers the AUC.** The champion's pooled AUC at 12 months at the 2020
origin minus the mean of its pooled AUC at 12 months at the 2018 and 2019 origins, each with
the weights of the main metric. A trial can have evaluation rows at all three origins, so
the interval comes from one cluster bootstrap that resamples trials once across the three
origins and computes both sides on each resample (ADR 0025).

**The reason labels** live in the uncommitted `data/` folder. Every H3 result records the
SHA-256 of the distilled model file and of the reasons table. On a locked origin, H3 runs
only if both hashes equal those the registration names (lines of the form
`distilled.joblib sha256:<hash>` and `early_stop_reasons.parquet sha256:<hash>` in
docs/preregistration.md at the tag prereg-v1).

**The test lock.** Locked origins need `--unlock-test` and every condition of ADR 0004,
checked before any prediction file is read; H5 reads locked origins only.
"""

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import time as clock
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import numpy.typing as npt

from trialpulse import tracking
from trialpulse.cli import RefusedError, run
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.eval.bootstrap import BootstrapError, cluster_bootstrap_many
from trialpulse.eval.compare import DEV, TEST, Paired, load_pair
from trialpulse.eval.ipcw import horizon_labels_and_weights
from trialpulse.eval.lock import PREREG_PATH, TAG, TestLockError, Unlock, require_unlock
from trialpulse.eval.metrics import weighted_auc
from trialpulse.eval.walkforward import PREDICTIONS_DIR, select_origins

RESULTS_DIR = REPO_ROOT / "data" / "results" / "hypotheses"
# Where trialpulse.nlp.distill writes them (held equal by a test; not imported here, so
# that the evaluation does not load the NLP stack).
MODEL_PATH = REPO_ROOT / "data" / "nlp" / "models" / "distilled.joblib"
REASONS_PATH = REPO_ROOT / "data" / "nlp" / "reasons" / "early_stop_reasons.parquet"
STRESS = "stress"
OPERATIONAL, SCIENTIFIC = "operational", "scientific"
NO_REASON = "none"  # an early stop without a reason text
CHAMPION = "m4"
H5_MONTHS = 12
HASHED_FILES = {"model_sha256": MODEL_PATH.name, "reasons_sha256": REASONS_PATH.name}
HASH_LINES = {
    name: re.compile(re.escape(file) + r" sha256:([0-9a-f]{64})")
    for name, file in HASHED_FILES.items()
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reason_hashes(
    model_path: Path = MODEL_PATH, reasons_path: Path = REASONS_PATH
) -> dict[str, str]:
    for path in (model_path, reasons_path):
        if not path.is_file():
            raise RefusedError(
                f"{path} is missing; build the reason labels first: uv run python -m "
                "trialpulse.nlp.distill"
            )
    return {"model_sha256": sha256(model_path), "reasons_sha256": sha256(reasons_path)}


def registered_hashes(text: str) -> dict[str, str]:
    """The hashes of the reason labels a registration names; refused if one is missing."""
    found = {}
    for name, pattern in HASH_LINES.items():
        match = pattern.search(text)
        if match is None:
            raise RefusedError(
                f"{PREREG_PATH} at {TAG} names no {HASHED_FILES[name]} hash, so the reason "
                "labels of H3 cannot be checked against the registration"
            )
        found[name] = match.group(1)
    return found


def check_reason_hashes(current: dict[str, str], registered: dict[str, str]) -> None:
    for name, value in registered.items():
        if current[name] != value:
            raise RefusedError(
                f"the reason labels differ from the registration ({name} {current[name][:12]} "
                f"here, {value[:12]} registered): H3 runs only with the registered labels"
            )


def reasons_of(trial_id: npt.NDArray[Any], reasons_path: Path) -> npt.NDArray[np.str_]:
    """The reason group of each row's trial; NO_REASON for a trial without a reason row."""
    with duckdb.connect() as con:
        table = con.execute(
            f"SELECT nct_id, reason_group FROM read_parquet('{reasons_path.as_posix()}')"
        ).fetchnumpy()
    kind = dict(zip(table["nct_id"].tolist(), table["reason_group"].tolist(), strict=True))
    return np.array([kind.get(str(t), NO_REASON) for t in trial_id], dtype=str)


def cause_aucs(
    time: npt.NDArray[np.float64],
    event: npt.NDArray[np.int64],
    score: npt.NDArray[np.float64],
    horizon: npt.NDArray[np.float64],
    groups: npt.NDArray[Any],
    reason: npt.NDArray[np.str_],
) -> dict[str, float]:
    """On the rows of one origin: the IPCW AUC with operational and with scientific early
    stops as the cases. The labels and weights come from all these rows first; then the
    rows with an early stop of another kind within the horizon are left out of each score."""
    y, w = horizon_labels_and_weights(time, event, horizon, groups)
    out = {}
    for kind in (OPERATIONAL, SCIENTIFIC):
        keep = (y == 0) | (reason == kind)
        out[kind] = weighted_auc(y[keep], score[keep], w[keep])
    return out


def h3_statistic(
    rows: Paired, months: int, reason: npt.NDArray[np.str_], idx: npt.NDArray[np.int64]
) -> dict[str, float]:
    """The two AUCs and their difference, each the mean over the origins of the value at
    each origin, on some rows (a resample of trials)."""
    per_origin = []
    origin = rows.origin[idx]
    for position in np.unique(rows.origin):
        i = idx[origin == position]
        per_origin.append(
            cause_aucs(rows.time[i], rows.event[i], rows.model[months][i],
                       rows.horizon[months][i], rows.groups[i], reason[i])
        )  # fmt: skip
    op = float(np.mean([p[OPERATIONAL] for p in per_origin]))
    sci = float(np.mean([p[SCIENTIFIC] for p in per_origin]))
    return {OPERATIONAL: op, SCIENTIFIC: sci, "difference": op - sci}


def h3(
    rows: Paired,
    reason: npt.NDArray[np.str_],
    origins: Sequence[dt.date],
    cfg: ProjectConfig,
    n_resamples: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {"horizons": {}}
    every = np.arange(len(rows), dtype=np.int64)

    def statistic(months: int) -> Callable[[npt.NDArray[np.int64]], dict[str, float]]:
        return lambda idx: h3_statistic(rows, months, reason, idx)

    for months in cfg.horizons_months:
        by_origin = {}
        for position, origin in enumerate(origins):
            i = every[rows.origin == position]
            stop = (rows.event[i] == 1) & (rows.time[i] <= rows.horizon[months][i])
            by_origin[origin.isoformat()] = {
                "n_rows": len(i),
                "cases": {
                    kind: int(np.sum(stop & (reason[i] == kind)))
                    for kind in (OPERATIONAL, SCIENTIFIC, "other", NO_REASON)
                },
                **cause_aucs(rows.time[i], rows.event[i], rows.model[months][i],
                             rows.horizon[months][i], rows.groups[i], reason[i]),
            }  # fmt: skip
        out["horizons"][str(months)] = {
            "by_origin": by_origin,
            **cluster_bootstrap_many(
                rows.trial_id,
                statistic(months),
                n_resamples,
                cfg.seeds.default,
                cfg.evaluation.confidence_level,
            ),
        }
    return out


def pooled_auc(rows: Paired, months: int, idx: npt.NDArray[np.int64]) -> float:
    y, w = horizon_labels_and_weights(
        rows.time[idx], rows.event[idx], rows.horizon[months][idx], rows.groups[idx]
    )
    return weighted_auc(y, rows.model[months][idx], w)


def h5_statistic(
    rows: Paired, stress: int, test: Sequence[int], idx: npt.NDArray[np.int64]
) -> dict[str, float]:
    """The pooled AUC at the stress origin minus the mean of the pooled AUC at the test
    origins, each origin with the weights of its own rows (a resample of trials)."""
    origin = rows.origin[idx]
    auc = {p: pooled_auc(rows, H5_MONTHS, idx[origin == p]) for p in (stress, *test)}
    mean_test = float(np.mean([auc[p] for p in test]))
    return {"stress": auc[stress], "test_mean": mean_test, "difference": auc[stress] - mean_test}


def h5(rows: Paired, origins: Sequence[dt.date], stress: dt.date, cfg: ProjectConfig,
       n_resamples: int) -> dict[str, Any]:  # fmt: skip
    position = {origin: p for p, origin in enumerate(origins)}
    test = [position[o] for o in origins if o != stress]
    every = np.arange(len(rows), dtype=np.int64)
    return {
        "months": H5_MONTHS,
        "by_origin": {
            o.isoformat(): {
                "n_rows": int(np.sum(rows.origin == p)),
                "auc": pooled_auc(rows, H5_MONTHS, every[rows.origin == p]),
            }
            for o, p in position.items()
        },
        **cluster_bootstrap_many(
            rows.trial_id,
            lambda idx: h5_statistic(rows, position[stress], test, idx),
            n_resamples,
            cfg.seeds.default,
            cfg.evaluation.confidence_level,
        ),
    }


def concat(parts: Sequence[Paired], offsets: Sequence[int]) -> Paired:
    """Rows of several loads in one table; `offsets` shifts each load's origin positions."""
    months = list(parts[0].horizon)
    return Paired(
        np.concatenate([p.origin + o for p, o in zip(parts, offsets, strict=True)]),
        np.concatenate([p.trial_id for p in parts]),
        np.concatenate([p.landmark_index for p in parts]),
        np.concatenate([p.time for p in parts]),
        np.concatenate([p.event for p in parts]),
        np.concatenate([p.groups for p in parts]),
        {m: np.concatenate([p.horizon[m] for p in parts]) for m in months},
        {m: np.concatenate([p.model[m] for p in parts]) for m in months},
        {m: np.concatenate([p.baseline[m] for p in parts]) for m in months},
    )


def registration_text(repo: Path) -> str:
    shown = subprocess.run(
        ["git", "-C", str(repo), "show", f"{TAG}:{PREREG_PATH}"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )  # fmt: skip
    if shown.returncode != 0:
        raise RefusedError(f"{PREREG_PATH} is not in the commit tagged {TAG}")
    return shown.stdout


def _unlock_record(unlock: Unlock | None) -> dict[str, str] | None:
    return (
        None if unlock is None else {**asdict(unlock), "registered": unlock.registered.isoformat()}
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="H3 and H5 of the pre-registration.")
    parser.add_argument("hypothesis", choices=["h3", "h5"])
    parser.add_argument("--origins", default=DEV, choices=[DEV, TEST], help="H3 only")
    parser.add_argument("--unlock-test", action="store_true", help="see ADR 0004")
    parser.add_argument("--model", default=CHAMPION)
    parser.add_argument("--predictions-dir", type=Path, default=PREDICTIONS_DIR)
    parser.add_argument("--reasons", type=Path, default=REASONS_PATH)
    parser.add_argument("--reasons-model", type=Path, default=MODEL_PATH)
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--resamples", type=int, default=None)
    tracking.add_arguments(parser)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    started = tracking.git_state()
    if args.hypothesis == "h3":
        selected = select_origins(cfg, args.origins)
    else:
        selected = select_origins(cfg, TEST) + select_origins(cfg, STRESS)
    try:  # before any prediction file is read
        unlock = require_unlock({o.role for o in selected}, REPO_ROOT, args.unlock_test)
    except TestLockError as exc:
        raise RefusedError(str(exc)) from exc
    n = cfg.evaluation.bootstrap_resamples if args.resamples is None else args.resamples
    months = tuple(cfg.horizons_months)
    begun = clock.perf_counter()
    result: dict[str, Any] = {
        "hypothesis": args.hypothesis,
        "model": args.model,
        "origins": [o.date.isoformat() for o in selected],
        "bootstrap_resamples": n,
        "unlock": _unlock_record(unlock),
    }
    try:
        if args.hypothesis == "h3":
            hashes = reason_hashes(args.reasons_model, args.reasons)
            if unlock is not None:
                check_reason_hashes(hashes, registered_hashes(registration_text(REPO_ROOT)))
            origins = [o.date for o in selected]
            rows = load_pair(args.predictions_dir, args.model, args.model, origins, months,
                             args.origins)  # fmt: skip
            reason = reasons_of(rows.trial_id, args.reasons)
            result.update({"origins_spec": args.origins, "reason_labels": hashes,
                           **h3(rows, reason, origins, cfg, n)})  # fmt: skip
            name = f"h3-{args.origins}"
        else:
            test = [o.date for o in selected if o.role == TEST]
            stress = [o.date for o in selected if o.role == STRESS]
            parts = [
                load_pair(args.predictions_dir, args.model, args.model, test, months, TEST),
                load_pair(args.predictions_dir, args.model, args.model, stress, months, STRESS),
            ]
            rows = concat(parts, [0, len(test)])
            result.update({"origins_spec": "test and stress",
                           **h5(rows, [*test, *stress], stress[0], cfg, n)})  # fmt: skip
            name = "h5"
    except BootstrapError as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 3
    result["seconds"] = round(clock.perf_counter() - begun, 1)
    out = args.out_dir / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    d = (result["horizons"][str(min(months))] if args.hypothesis == "h3" else result)["difference"]
    print(f"{name}: difference {d['estimate']:+.4f} ({d['ci_low']:+.4f} to {d['ci_high']:+.4f})")
    if not args.no_track:
        metrics = (
            {
                f"difference_{m}m": result["horizons"][str(m)]["difference"]["estimate"]
                for m in months
            }
            if args.hypothesis == "h3"
            else {"difference_12m": result["difference"]["estimate"]}
        )
        lock_tags = {} if unlock is None else {
            "test_lock": "unlocked", "unlock_tag_object": unlock.tag_object,
            "unlock_commit": unlock.commit,
        }  # fmt: skip
        try:
            logged = tracking.log_run(
                name,
                {"model": args.model, "bootstrap_resamples": n,
                 "origins": ",".join(result["origins"])},
                metrics,
                [out],
                cfg,
                tags={"step": "11", "kind": "hypothesis", "hypothesis": args.hypothesis,
                      **lock_tags},
                local=args.local_tracking,
                state=started,
            )  # fmt: skip
        except tracking.TrackingError as exc:
            print(f"failed: the run was not logged to MLflow ({exc}); its result is in {out}",
                  file=sys.stderr)  # fmt: skip
            return tracking.FAILED_EXIT_CODE
        result["tracking"] = logged
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"logged to {logged['store_description']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
