"""The two development checks of ADR 0034 (Step 13).

    uv run python -m trialpulse.scoring.checks

Both run on the development origins only (2016-01-01 and 2017-01-01); no locked origin is
read. Their results are development evidence about the served scores, kept outside the
registered results (ADR 0032), and the decisions follow the ADR's rules mechanically.

**The models.** Each origin's champion is M4 fitted again exactly as the development run
fitted it (`trialpulse.explain.report.fit_champion`), and used only if it reproduces the
scores that run saved for every evaluation row, to 1e-12 (the models are cached in
data/scoring/dev_models/).

**Check 1, between landmarks.** Mid-landmark rows (trial, k, d), d = add_months(t0, 6k + 3),
k = 0 to 6, whose landmark row add_months(t0, 6k) is an evaluation row of the origin, with d
before the end of its window and the trial passing the cohort's landmark rules on d. B
scores the row as ADR 0034 decision 1 says; A is the same model's saved 12-month score of
the landmark row. Both are held against the trial's final-cohort outcome over the 12 months
after d, with IPCW censoring weights by the lead sponsor class on d. B is served if, at each
origin, its AUC is no more than 0.005 below A's.

**Check 2, beyond landmark 6.** Pseudo-landmark rows (trial, k, add_months(t0, 6k)), k = 7
to 19, in the origin's window and passing the cohort's landmark rules on their date, scored
as decision 1 says with k* = 6. Per band (months 42 to 59 and 60 to 119): the AUC at 12
months per origin and averaged, the early stops within 12 months, and the mean predicted
against the observed (Aalen-Johansen) risk. A band is served if its averaged AUC is at
least landmark 6's averaged AUC (the development runs' pooled landmark-6 AUC) minus 0.03,
with at least 100 early stops within 12 months at each origin.

The result file holds the decisions, which the scoring code reads; docs/scoring_checks.md
is written from it.
"""

import argparse
import datetime as dt
import json
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from trialpulse import tracking
from trialpulse.cli import RefusedError, run
from trialpulse.cohort.landmarks import classify_candidates, eligible_trials
from trialpulse.cohort.outcomes import build_states as build_cohort_states
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import REPO_ROOT, Origin, ProjectConfig, load_project_config
from trialpulse.dates import add_months, days_between
from trialpulse.eval.bootstrap import cluster_bootstrap_many
from trialpulse.eval.ipcw import censoring_groups
from trialpulse.eval.metrics import ipcw_auc
from trialpulse.eval.rows import (
    LANDMARKS_PATH,
    TRAINING_DIR,
    LandmarkRows,
    horizon_days,
    load_landmark_rows,
)
from trialpulse.eval.walkforward import (
    DEV_ROLE,
    EVALUATION,
    FEATURES_DIR,
    PREDICTIONS_DIR,
    RESULTS_DIR,
    FeatureLoader,
    attach_features,
    evaluation_rows,
    feature_loader,
    select_origins,
    sha256_of,
)
from trialpulse.explain.report import champion_for, fit_champion
from trialpulse.features.frame import as_arrays
from trialpulse.features.transforms import SponsorPriors
from trialpulse.ingest.history import RAW_HISTORY_DIR, config_glob
from trialpulse.models.aalen_johansen import aalen_johansen
from trialpulse.models.discrete_time import DiscreteTimeBoosting
from trialpulse.reports import markdown_table, write_text_if_changed
from trialpulse.scoring import features as served_features
from trialpulse.warehouse.build import WAREHOUSE_PATH

CHAMPION = "m4"
CHECKS_DIR = REPO_ROOT / "data" / "results" / "checks"
RESULT_PATH = CHECKS_DIR / "step13_checks.json"
DOC_PATH = REPO_ROOT / "docs" / "scoring_checks.md"
MODELS_DIR = REPO_ROOT / "data" / "scoring" / "dev_models"
OUTCOMES_PATH = LANDMARKS_PATH.parent / "outcomes.parquet"
MID_OFFSET_MONTHS = 3
HORIZON_MONTHS = 12
BETWEEN, BEYOND = "between", "beyond"
# The age bands of check 2, by the true landmark index (ADR 0034, decision 3).
BANDS: tuple[tuple[str, int, int], ...] = (("42 to 59 months", 7, 9), ("60 to 119 months", 10, 19))
OLDEST_INDEX = 20  # 120 months: no development row reaches it
Q1_MARGIN = 0.005
BAND_MARGIN = 0.03
MIN_STOPS = 100


# Rows --------------------------------------------------------------------------------------


def candidate_rows(
    trial_id: npt.NDArray[Any],
    t0: npt.NDArray[Any],
    rules: CohortRules,
    first_day: dt.date,
    end_day: dt.date,
) -> pd.DataFrame:
    """The mid-landmark and pseudo-landmark candidates dated in [first_day, end_day), one
    row per (trial, index): `base_date` is the landmark the row belongs to."""
    t0 = np.asarray(t0, dtype="datetime64[D]")
    ids = np.asarray(trial_id).astype(str)
    parts = []
    spacing = rules.spacing_months
    plans = [(BETWEEN, k, spacing * k + MID_OFFSET_MONTHS) for k in range(rules.max_index + 1)]
    plans += [(BEYOND, k, spacing * k) for k in range(rules.max_index + 1, OLDEST_INDEX)]
    for kind, k, months in plans:
        day = add_months(t0, months)
        keep = (day >= np.datetime64(first_day, "D")) & (day < np.datetime64(end_day, "D"))
        if not keep.any():
            continue
        parts.append(
            pd.DataFrame(
                {
                    "trial_id": ids[keep],
                    "landmark_index": np.full(int(keep.sum()), k, dtype=np.int64),
                    "landmark_date": day[keep],
                    "base_date": add_months(t0[keep], spacing * k),
                    "t0": t0[keep],
                    "kind": kind,
                }
            )
        )
    return pd.concat(parts, ignore_index=True)


def assign_origins(
    rows: pd.DataFrame,
    landmarks: pd.DataFrame,
    origins: Sequence[Origin],
    window_months: int,
) -> pd.DataFrame:
    """Each kept candidate with the origin it belongs to (dropped if none). A mid-landmark
    row needs its landmark row in the final cohort, dated in the window, and its own date
    before the window's end; a pseudo-landmark row needs its date in the window."""
    known = landmarks[["trial_id", "landmark_index", "landmark_date"]].rename(
        columns={"landmark_date": "final_landmark_date"}
    )
    out = rows.merge(known, on=["trial_id", "landmark_index"], how="left")
    out["origin"] = None
    base = out["base_date"].to_numpy(dtype="datetime64[D]")
    day = out["landmark_date"].to_numpy(dtype="datetime64[D]")
    final = out["final_landmark_date"].to_numpy(dtype="datetime64[D]")
    between = (out["kind"] == BETWEEN).to_numpy()
    for origin in origins:
        start = np.datetime64(origin.date, "D")
        end = add_months(np.array([start]), window_months)[0]
        mid = between & (base >= start) & (base < end) & (day < end) & (final == base)
        beyond = ~between & (day >= start) & (day < end)
        out.loc[mid | beyond, "origin"] = origin.date.isoformat()
    return out[out["origin"].notna()].drop(columns=["final_landmark_date"])


# Metrics -----------------------------------------------------------------------------------


def _auc(
    time: npt.NDArray[np.float64],
    event: npt.NDArray[np.int64],
    score: npt.NDArray[np.float64],
    horizon: npt.NDArray[np.float64],
    groups: npt.NDArray[Any],
) -> Callable[[npt.NDArray[np.int64]], float]:
    def statistic(idx: npt.NDArray[np.int64]) -> float:
        return ipcw_auc(time[idx], event[idx], score[idx], horizon[idx], groups[idx])

    return statistic


def stops_within(time: npt.NDArray[np.float64], event: npt.NDArray[np.int64],
                 horizon: npt.NDArray[np.float64]) -> int:  # fmt: skip
    return int(((event == 1) & (time <= horizon)).sum())


def between_check(
    rows: pd.DataFrame, b: npt.NDArray[np.float64], a: npt.NDArray[np.float64],
    cfg: ProjectConfig, n_resamples: int,
) -> dict[str, Any]:  # fmt: skip
    """B against A on the same mid-landmark rows of one origin."""
    day = rows["landmark_date"].to_numpy(dtype="datetime64[D]")
    time = days_between(day, rows["event_date"].to_numpy(dtype="datetime64[D]"))
    event = rows["event"].to_numpy(dtype=np.int64)
    horizon = horizon_days(day, HORIZON_MONTHS)
    groups = censoring_groups(rows["stratum"].to_numpy(dtype=object),
                              cfg.evaluation.censoring_min_rows)  # fmt: skip
    auc_b, auc_a = _auc(time, event, b, horizon, groups), _auc(time, event, a, horizon, groups)
    boot = cluster_bootstrap_many(
        rows["trial_id"].to_numpy(dtype=object),
        lambda idx: {"b": auc_b(idx), "a": auc_a(idx), "difference": auc_b(idx) - auc_a(idx)},
        n_resamples, cfg.seeds.default, cfg.evaluation.confidence_level,
    )  # fmt: skip
    everything = np.arange(len(rows))
    return {
        "n_rows": len(rows),
        "n_trials": int(rows["trial_id"].nunique()),
        "early_stops_within_12_months": stops_within(time, event, horizon),
        "auc_b": {**boot["b"], "estimate": auc_b(everything)},
        "auc_a": {**boot["a"], "estimate": auc_a(everything)},
        "difference": {**boot["difference"], "estimate": auc_b(everything) - auc_a(everything)},
    }


def band_check(
    rows: pd.DataFrame, score: npt.NDArray[np.float64], cfg: ProjectConfig, n_resamples: int
) -> dict[str, Any]:
    """One age band of one origin: AUC, early stops and predicted against observed risk."""
    day = rows["landmark_date"].to_numpy(dtype="datetime64[D]")
    time = days_between(day, rows["event_date"].to_numpy(dtype="datetime64[D]"))
    event = rows["event"].to_numpy(dtype=np.int64)
    horizon = horizon_days(day, HORIZON_MONTHS)
    groups = censoring_groups(rows["stratum"].to_numpy(dtype=object),
                              cfg.evaluation.censoring_min_rows)  # fmt: skip
    stops = stops_within(time, event, horizon)
    out: dict[str, Any] = {
        "n_rows": len(rows),
        "n_trials": int(rows["trial_id"].nunique()),
        "early_stops_within_12_months": stops,
        "mean_predicted_12": float(np.mean(score)) if len(rows) else float("nan"),
        "observed_12": float(aalen_johansen(time, event).at(float(np.median(horizon)))[()])
        if len(rows)
        else float("nan"),
    }
    if stops == 0 or len(rows) < 2:
        out["auc"] = {"estimate": float("nan")}
        return out
    auc = _auc(time, event, score, horizon, groups)
    boot = cluster_bootstrap_many(
        rows["trial_id"].to_numpy(dtype=object), lambda idx: {"auc": auc(idx)},
        n_resamples, cfg.seeds.default, cfg.evaluation.confidence_level,
    )  # fmt: skip
    out["auc"] = {**boot["auc"], "estimate": auc(np.arange(len(rows)))}
    return out


def decide(
    between: Mapping[str, Mapping[str, Any]],
    bands: Mapping[str, Mapping[str, Mapping[str, Any]]],
    landmark6: Mapping[str, float],
) -> dict[str, Any]:
    """The decisions of ADR 0034, from the point estimates."""
    differences = {o: float(r["difference"]["estimate"]) for o, r in between.items()}
    option = "B" if all(d >= -Q1_MARGIN for d in differences.values()) else "A"
    l6 = float(np.mean(list(landmark6.values())))
    served_bands = {}
    for band, by_origin in bands.items():
        aucs = [float(r["auc"]["estimate"]) for r in by_origin.values()]
        stops = [int(r["early_stops_within_12_months"]) for r in by_origin.values()]
        mean = float(np.mean(aucs))
        served_bands[band] = {
            "served": bool(np.isfinite(mean) and mean >= l6 - BAND_MARGIN
                           and min(stops) >= MIN_STOPS),
            "mean_auc": mean,
            "floor": l6 - BAND_MARGIN,
            "min_early_stops": min(stops),
        }  # fmt: skip
    return {
        "between_landmarks": option,
        "differences": differences,
        "landmark6_mean_auc": l6,
        "bands": served_bands,
        "oldest_band": {"from_index": OLDEST_INDEX, "served": False},
    }


# The models --------------------------------------------------------------------------------


def saved_dev_scores(origin: Origin) -> tuple[pd.DataFrame, Path]:
    path = PREDICTIONS_DIR / f"{CHAMPION}_{origin.date.year}" / f"origin_{origin.date}.parquet"
    if not path.is_file():
        raise RefusedError(f"{path} not found: run the development walk-forward run of M4")
    return pd.read_parquet(path), path


def landmark6_auc(origin: Origin) -> float:
    path = RESULTS_DIR / f"{CHAMPION}_{origin.date.year}.json"
    result = json.loads(path.read_text(encoding="utf-8"))
    (record,) = [o for o in result["origins"] if o["origin"] == origin.date.isoformat()]
    return float(record["horizons"]["12"]["by_landmark_index"]["6"]["auc"]["estimate"])


def dev_model(
    cfg: ProjectConfig, origin: Origin, every: LandmarkRows, loader: FeatureLoader
) -> tuple[DiscreteTimeBoosting, str, float, pd.DataFrame]:
    saved, _ = saved_dev_scores(origin)
    t = np.datetime64(origin.date, "D")
    ev = evaluation_rows(every, t, cfg.walk_forward.eval_window_months)[0]
    ev = attach_features(ev, loader(origin.date, EVALUATION, None), "evaluation rows")
    fit = partial(fit_champion, cfg, origin.date, loader, TRAINING_DIR)
    model, source, gap = champion_for(cfg, origin.date, ev, saved, MODELS_DIR, fit)
    return model, source, gap, saved


def predict(model: DiscreteTimeBoosting, rows: pd.DataFrame, frame: pd.DataFrame) -> np.ndarray:
    """The 12-month scores of the rows, from their served features."""
    keyed = rows[["trial_id", "landmark_index"]].rename(columns={"landmark_index": "true_index"})
    keyed = keyed.merge(frame, on=["trial_id", "true_index"], how="left", validate="one_to_one")
    columns = ["trial_id", "landmark_index", *[f for f in model.features if f != "landmark_index"]]
    arrays = as_arrays(keyed[columns])
    day = rows["landmark_date"].to_numpy(dtype="datetime64[D]")
    landmark_rows = LandmarkRows(
        rows["trial_id"].to_numpy(dtype=object),
        rows["landmark_index"].to_numpy(dtype=np.int64),
        day,
        np.zeros(len(rows), dtype=np.int64),
        day,
        arrays,
    )
    scores: np.ndarray = model.predict_months(landmark_rows, HORIZON_MONTHS)
    return scores


# The command -------------------------------------------------------------------------------


def run_checks(
    cfg: ProjectConfig, n_resamples: int, warehouse: Path = WAREHOUSE_PATH
) -> dict[str, Any]:
    origins = select_origins(cfg, DEV_ROLE)
    if {o.role for o in origins} != {DEV_ROLE}:
        raise RefusedError("the checks of ADR 0034 run on the development origins only")
    rules = CohortRules.from_config(cfg)
    loader = feature_loader(cfg, FEATURES_DIR, LANDMARKS_PATH.parent)
    every = load_landmark_rows(LANDMARKS_PATH)
    models, saved, provenance = {}, {}, {}
    for origin in origins:
        key = origin.date.isoformat()
        model, source, gap, scores = dev_model(cfg, origin, every, loader)
        models[key], saved[key] = model, scores
        _, path = saved_dev_scores(origin)
        provenance[key] = {"model": source, "parity": gap, "scores_sha256": sha256_of(path)}
        print(f"{key}: {source} model, largest difference from the saved scores {gap:.1e}")
    interventions = config_glob(RAW_HISTORY_DIR, str(cfg.dataset.revision), "interventions")
    con = served_features.open_session(rules, warehouse, interventions)
    build_cohort_states(con, rules)
    con.execute(
        "CREATE OR REPLACE TEMP TABLE cohort_outcomes AS SELECT * FROM "
        f"read_parquet('{OUTCOMES_PATH.as_posix()}')"
    )
    eligible = eligible_trials(con)
    first = min(o.date for o in origins)
    last = add_months(np.array([np.datetime64(max(o.date for o in origins), "D")]),
                      cfg.walk_forward.eval_window_months)[0]  # fmt: skip
    candidates = candidate_rows(eligible["trial_id"], eligible["t0"], rules, first,
                                pd.Timestamp(last).date())  # fmt: skip
    classify_candidates(con, rules, candidates, "check_candidates")
    kept = con.execute(
        """SELECT c.trial_id, c.landmark_index, c.landmark_date, c.event, c.event_date,
          coalesce(c.lead_sponsor_class, 'MISSING') AS stratum
        FROM check_candidates c WHERE c.excluded IS NULL"""
    ).df()
    rows = candidates.merge(kept, on=["trial_id", "landmark_index", "landmark_date"])
    landmarks = pd.read_parquet(LANDMARKS_PATH, columns=["trial_id", "landmark_index",
                                                         "landmark_date"])  # fmt: skip
    landmarks["landmark_date"] = landmarks["landmark_date"].to_numpy(dtype="datetime64[D]")
    rows = assign_origins(rows, landmarks, origins, cfg.walk_forward.eval_window_months)
    keys = served_features.keys_for(rows["trial_id"], rows["t0"], rows["landmark_index"],
                                    rows["landmark_date"], rules,
                                    cfg.features.recent_versions_months)  # fmt: skip
    served_features.register_keys(con, keys)
    frame = served_features.compute_served(con, rules, cfg)
    con.close()
    between: dict[str, Any] = {}
    bands: dict[str, dict[str, Any]] = {band: {} for band, _, _ in BANDS}
    for origin in origins:
        key = origin.date.isoformat()
        priors = SponsorPriors.from_json(json.loads(
            (FEATURES_DIR / f"origin_{key}" / "sponsor_priors.json").read_text(encoding="utf-8")
        ))  # fmt: skip
        mine = served_features.with_priors(frame, priors)
        own = rows[rows["origin"] == key].sort_values(["trial_id", "landmark_index"])
        mid = own[own["kind"] == BETWEEN].reset_index(drop=True)
        b = predict(models[key], mid, mine)
        a = mid[["trial_id", "landmark_index"]].merge(
            saved[key][["trial_id", "landmark_index", "score_12"]],
            on=["trial_id", "landmark_index"], how="left", validate="one_to_one",
        )["score_12"].to_numpy(dtype=np.float64)  # fmt: skip
        if np.isnan(a).any():
            raise RefusedError(f"{int(np.isnan(a).sum())} mid-landmark rows have no saved A score")
        between[key] = between_check(mid, b, a, cfg, n_resamples)
        old = own[own["kind"] == BEYOND].reset_index(drop=True)
        scores = predict(models[key], old, mine) if len(old) else np.array([])
        for band, low, high in BANDS:
            member = ((old["landmark_index"] >= low) & (old["landmark_index"] <= high)).to_numpy()
            bands[band][key] = band_check(old[member].reset_index(drop=True), scores[member],
                                          cfg, n_resamples)  # fmt: skip
    landmark6 = {o.date.isoformat(): landmark6_auc(o) for o in origins}
    return {
        "adr": "0034",
        "origins": [o.date.isoformat() for o in origins],
        "bootstrap_resamples": n_resamples,
        "dataset_revision": cfg.dataset.revision,
        "models": provenance,
        "between_landmarks": between,
        "beyond_landmark_6": bands,
        "landmark6_auc_12": landmark6,
        "decisions": decide(between, bands, landmark6),
    }


def _ci(d: Mapping[str, Any], digits: int = 4, signed: bool = False) -> str:
    fmt = f"{{:+.{digits}f}}" if signed else f"{{:.{digits}f}}"
    text = fmt.format(d["estimate"])
    if "ci_low" in d:
        text += f" ({fmt.format(d['ci_low'])} to {fmt.format(d['ci_high'])})"
    return text


def document(result: Mapping[str, Any], cfg: ProjectConfig) -> str:
    decisions = result["decisions"]
    lines = [
        "# Development checks of the served scores",
        "",
        "Research demo. Not medical advice. Not for patient decision-making.",
        "",
        "This file is generated by `uv run python -m trialpulse.scoring.checks` (CLAUDE.md Step "
        "13, ADR 0034). Do not edit it by hand. These are development checks on the 2016 and "
        "2017 origins: development evidence about how the served scores are computed, kept "
        "outside the registered results ([results.md](results.md), ADR 0032).",
        "",
        f"- **Source:** [ClinicalTrials.gov](https://clinicaltrials.gov) registry records, "
        f"through the version-history dataset `brbk/clinical_trials_history` (license "
        f"CC-BY-NC-4.0), revision {cfg.dataset.revision}. Data as of {cfg.dataset.cutoff}. "
        "TrialPulse normalizes the records and derives every number here; these are not part "
        "of ClinicalTrials.gov (ADR 0008).",
        "- **Models:** the development champions (M4 at each origin), fitted again and used "
        "because they reproduce every score the development runs saved ("
        + ", ".join(f"{o}: largest difference {m['parity']:.1e}"
                    for o, m in result["models"].items())
        + ").",
        "",
        "## Decisions",
        "",
        f"- **Between landmarks: option {decisions['between_landmarks']} is served.** The rule "
        f"serves B if, at each origin, its AUC at 12 months is no more than {Q1_MARGIN} below "
        "A's on the same rows; differences "
        + ", ".join(f"{o} {d:+.4f}" for o, d in decisions["differences"].items())
        + ".",
    ]  # fmt: skip
    for band, d in decisions["bands"].items():
        lines.append(
            f"- **{band}: {'served, flagged' if d['served'] else 'not served'}.** Averaged AUC "
            f"{d['mean_auc']:.4f} against the floor {d['floor']:.4f} (landmark 6's averaged AUC "
            f"{decisions['landmark6_mean_auc']:.4f} minus {BAND_MARGIN}); fewest early stops "
            f"within 12 months at an origin {d['min_early_stops']} (at least {MIN_STOPS} needed)."
        )
    lines += [
        "- **120 months or older: not served** (no development row can reach that age).",
        "",
        "## Check 1: between landmarks",
        "",
        "Mid-landmark rows (3 months after a landmark), B (features on the date, time features "
        "held at the last landmark) against A (the score of the last landmark row), on the same "
        "rows; IPCW AUC at 12 months from the row's date, with 95% cluster-bootstrap intervals "
        f"over trials ({result['bootstrap_resamples']:,} resamples).",
        "",
        *markdown_table(
            [
                "Origin",
                "Rows",
                "Trials",
                "Early stops within 12 months",
                "AUC, B",
                "AUC, A",
                "B minus A",
            ],
            [
                [
                    o,
                    f"{r['n_rows']:,}",
                    f"{r['n_trials']:,}",
                    f"{r['early_stops_within_12_months']:,}",
                    _ci(r["auc_b"]),
                    _ci(r["auc_a"]),
                    _ci(r["difference"], signed=True),
                ]
                for o, r in result["between_landmarks"].items()
            ],
        ),
        "",
        "## Check 2: beyond landmark 6",
        "",
        "Pseudo-landmark rows (every 6 months after landmark 6), scored on landmark 6 with "
        "every other feature on the row's date. The observed risk is the Aalen-Johansen "
        "cumulative incidence of an early stop at 12 months; it is reported beside the AUC "
        "with no rule.",
        "",
    ]
    table = []
    for band, by_origin in result["beyond_landmark_6"].items():
        for o, r in by_origin.items():
            table.append([band, o, f"{r['n_rows']:,}", f"{r['early_stops_within_12_months']:,}",
                          _ci(r["auc"]), f"{100 * r['mean_predicted_12']:.1f}%",
                          f"{100 * r['observed_12']:.1f}%"])  # fmt: skip
    lines += [
        *markdown_table(
            [
                "Band",
                "Origin",
                "Rows",
                "Early stops within 12 months",
                "AUC at 12 months",
                "Mean predicted risk",
                "Observed risk",
            ],
            table,
        ),
        "",
        "Landmark 6's AUC at 12 months in the development runs: "
        + ", ".join(f"{o} {v:.4f}" for o, v in result["landmark6_auc_12"].items())
        + ".",
        "",
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The development checks of ADR 0034.")
    parser.add_argument("--resamples", type=int, default=None)
    parser.add_argument("--out", type=Path, default=RESULT_PATH)
    parser.add_argument("--doc", type=Path, default=DOC_PATH)
    tracking.add_arguments(parser)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    started = tracking.git_state()
    n = cfg.evaluation.bootstrap_resamples if args.resamples is None else args.resamples
    result = run_checks(cfg, n)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    changed = write_text_if_changed(args.doc, document(result, cfg))
    print(f"wrote {args.out}")
    print(f"{args.doc}: {'written' if changed else 'up to date'}")
    if not args.no_track:
        decisions = result["decisions"]
        metrics = {f"difference_{o.replace('-', '_')}": d
                   for o, d in decisions["differences"].items()}  # fmt: skip
        for band, d in decisions["bands"].items():
            metrics[f"mean_auc_{band.split()[0]}_{band.split()[2]}"] = d["mean_auc"]
        try:
            result["tracking"] = tracking.log_run(
                "step13-checks",
                {"bootstrap_resamples": n, "origins": ",".join(result["origins"]),
                 "between_landmarks": decisions["between_landmarks"],
                 "bands_served": ",".join(b for b, d in decisions["bands"].items()
                                          if d["served"]) or "none"},
                metrics, [args.out, args.doc], cfg,
                tags={"step": "13", "kind": "check", "roles": DEV_ROLE, "adr": "0034"},
                local=args.local_tracking, state=started,
            )  # fmt: skip
        except tracking.TrackingError as exc:
            print(f"failed: the run was not logged to MLflow ({exc}); its results are in "
                  f"{args.out}")  # fmt: skip
            return tracking.FAILED_EXIT_CODE
        args.out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"logged to {result['tracking']['store_description']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
