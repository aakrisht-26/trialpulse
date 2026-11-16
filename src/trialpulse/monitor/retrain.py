"""The quarterly retrain and its promotion gate, on the laptop (Section 14, ADRs 0040 and 0080).

    uv run python -m trialpulse.monitor.retrain --target neon

Laptop only: it reads the dataset's rows. In order:

1. **Data** (ADR 0080, decision 1): the pinned rows, the gap rows (the rebootstrap's combined
   warehouse, ADR 0074) and the live versions since go-live through ADR 0037's export adapter
   (with the retention's exports), built as parity layer 5a builds them, in data/retrain/. The
   data date D is the latest succeeded daily run's. ADR 0073's rule for versions that failed
   review is kept, and recorded with the run.
2. **The challenger:** the final cohort of that warehouse with the data cutoff set to D, its
   features by the feature build's own code (no text: M4 reads none, ADR 0026), and M4 with
   the tuned settings fitted on every landmark row with the origin the day after D, as the
   served champion was fitted (ADR 0034).
3. **Scores:** the challenger and the champion each score every open trial on D with Step 13's
   `score()` (no drivers); their agreement is logged as a description, not a result.
4. **The gate** (`monitor.gate`): a fair window needs matured 12-month outcomes of predictions
   made after the champion's training cutoff (read from the champion's bundle); before one
   exists the decision is "not promoted" with its reason. Once one exists, M4 with the tuned
   settings is fitted as of the window's first graded date, as a walk-forward origin is
   (ADR 0016, the replay's code), so it has seen no outcome the window grades; it and the
   champion score the window's dates, both are graded against the final cohort's outcomes
   (status reversals left out), and the gate reads the means. The challenger that would be
   promoted is the one fitted on all the data: the window judges its method on data neither
   model saw. This command never registers or serves a model: on a promotion the champion
   alias is moved by hand (docs/deploy.md, Part 5), because the served model's statement
   (ADR 0048) changes with it.
5. **Logs:** one MLflow run (the bundle, the decision, the data) and one row in
   `monitoring.promotions` on the target.

It prints counts and the decision only, never a trial id or a name.
"""

import argparse
import datetime as dt
import json
import time as clock
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from trialpulse import tracking
from trialpulse.cli import RefusedError, run
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import (
    PROJECT_CONFIG_PATH,
    REPO_ROOT,
    ProjectConfig,
    Secrets,
    load_project_config,
)
from trialpulse.monitor import gate

RETRAIN_DIR = REPO_ROOT / "data" / "retrain"
FAILED_REVIEW_RULE = "ADR 0073's rule for versions that failed review, kept (ADR 0080)"
BY_HAND = " (the served model changes when the champion alias is moved: docs/deploy.md, Part 5)"


def as_of(cfg: ProjectConfig, day: dt.date) -> ProjectConfig:
    """The configuration whose data reach `day` (the data cutoff moved, nothing else)."""
    return cfg.model_copy(update={"dataset": cfg.dataset.model_copy(update={"cutoff": day})})


def build_cohort_and_features(cfg: ProjectConfig, warehouse: Path, interventions: str,
                              out_dir: Path) -> dict[str, Any]:  # fmt: skip
    """The final cohort of `warehouse` and its features, in `out_dir` (never the main ones)."""
    from trialpulse.cohort.build import OUTCOMES_PATH, attach_warehouse, build_cohort, write_outputs
    from trialpulse.features import build as features
    from trialpulse.features.registry import KEY_COLUMNS, built_columns
    from trialpulse.parquet import write_table
    from trialpulse.warehouse.build import TEMP_DIR

    rules = CohortRules.from_config(cfg)
    cohort_dir = out_dir / "cohort"
    landmarks = cohort_dir / "landmarks.parquet"
    with duckdb.connect() as con:
        attach_warehouse(con, warehouse)
        build_cohort(con, rules)
        outputs = write_outputs(con, cohort_dir, landmarks)
    features_dir = out_dir / "features"
    features_dir.mkdir(parents=True, exist_ok=True)
    with features.connect(TEMP_DIR) as con:
        features.prepare(con, warehouse, interventions, {"final": landmarks}, rules)
        features.compute_features(con, rules, cfg, text=False)
        written = write_table(con, features.FEATURE_TABLE, features_dir / features.FEATURES_NAME,
                              [*KEY_COLUMNS, *built_columns()], features.ORDER)  # fmt: skip
    return {"cohort": {name: rows for name, rows, _ in outputs}, "feature_rows": written[0],
            "landmarks": landmarks, "outcomes": cohort_dir / OUTCOMES_PATH.name,
            "features_dir": features_dir}  # fmt: skip


def fit_challenger(cfg: ProjectConfig, day: dt.date, landmarks: Path,
                   features_dir: Path, folder: Path) -> tuple[Any, dict[str, Any]]:  # fmt: skip
    from trialpulse.models import discrete_time
    from trialpulse.models.discrete_time import DiscreteTimeBoosting
    from trialpulse.scoring.bundle import sha256_of
    from trialpulse.scoring.champion import save_bundle, training_matrix

    origin = day + dt.timedelta(days=1)
    rows, time, event, priors = training_matrix(cfg, origin, features_dir=features_dir,
                                                landmarks=landmarks)  # fmt: skip
    model = discrete_time.build("m4", cfg)
    assert isinstance(model, DiscreteTimeBoosting)
    model.fit_rows(rows, time, event, origin)
    model._scored = None
    meta = save_bundle(model, priors, {
        **challenger_record(day, origin), "settings": model.settings,
        "summary": dict(model.summary), "config_sha256": sha256_of(PROJECT_CONFIG_PATH),
    }, folder)  # fmt: skip
    return model, meta


def challenger_artifacts(folder: Path, meta: dict[str, Any]) -> list[Path]:
    """The files logged with the challenger's run, the four `scoring.champion` logs: the
    bundle and its meta.json, which `live.registry.fetch` and `load_bundle` read, so a
    registered challenger fetches as the champion does (review finding F3)."""
    from trialpulse.scoring.bundle import META

    return [folder / name for name in (*meta["files"], META)]


def challenger_record(day: dt.date, origin: dt.date) -> dict[str, str]:
    """What the challenger's meta.json records besides its fit. No "version" key: a
    registered bundle takes its version from the registry when it is fetched, and
    `live.registry.fetch` refuses a meta.json that names another (ADR 0045)."""
    return {"name": f"trialpulse-m4-challenger-{day.isoformat()}",
            "kind": "challenger (ADR 0080)", "origin": origin.isoformat(),
            "data_cutoff": day.isoformat(), "data": "pinned + gap + exported live rows (ADR 0080)",
            "failed_review_rule": FAILED_REVIEW_RULE}  # fmt: skip


def fit_window_challenger(cfg: ProjectConfig, origin: dt.date, warehouse: Path,
                          interventions: str, built: dict[str, Any],
                          folder: Path) -> dict[str, Any]:  # fmt: skip
    """M4 with the tuned settings fitted as of `origin` (the window's first graded date) as a
    walk-forward origin is: the cohort as of the origin (ADR 0016), features for rows the
    retrain's feature build does not hold, the sponsor priors on those rows, early stopping
    as ADR 0025. Saved to `folder`/model; graded on the window only, never registered."""
    from trialpulse.models import discrete_time
    from trialpulse.models.discrete_time import DiscreteTimeBoosting
    from trialpulse.monitor import replay
    from trialpulse.scoring.champion import save_bundle, training_matrix

    training_dir = folder / "training"
    summary: dict[str, Any] = {"training_cohort": replay.build_training(
        cfg, origin, warehouse=warehouse, training_dir=training_dir,
        final_landmarks=built["landmarks"])}  # fmt: skip
    landmarks = replay.landmarks_path(origin, training_dir)
    extra = folder / "features_extra.parquet"
    summary["extra_feature_rows"] = replay.extra_features(
        cfg, landmarks=landmarks, features_dir=built["features_dir"], warehouse=warehouse,
        interventions=interventions, out=extra)  # fmt: skip
    rows, time, event, priors = training_matrix(
        cfg, origin, features_dir=built["features_dir"], landmarks=landmarks,
        extra_features=(extra,) if extra.is_file() else ())  # fmt: skip
    model = discrete_time.build("m4", cfg)
    assert isinstance(model, DiscreteTimeBoosting)
    model.fit_rows(rows, time, event, origin)
    model._scored = None
    save_bundle(model, priors, {
        "name": f"trialpulse-m4-window-{origin.isoformat()}",
        "kind": "window challenger (ADR 0080): graded on the window only, never registered",
        "origin": origin.isoformat(), "settings": model.settings, "summary": dict(model.summary),
    }, folder / "model")  # fmt: skip
    summary["model"] = {k: model.summary[k] for k in ("landmark_rows", "rounds", "fit_seconds")}
    return summary


def score_on_dates(cfg: ProjectConfig, days: Sequence[dt.date], bundles: dict[str, Any],
                   warehouse: Path, interventions: str) -> pd.DataFrame:  # fmt: skip
    """Every open trial's scores on each date by each bundle, as predictions for
    `monitor.grading` with the bundle's role (`gate.CHAMPION` or `gate.CHALLENGER`) as its
    model_version, and the sponsor class in effect on the date."""
    from trialpulse.monitor import replay
    from trialpulse.scoring.score import score, served_decisions

    decisions = served_decisions()
    frames = []
    for day in days:
        classes = replay.sponsor_classes(day, warehouse)
        for role, bundle in bundles.items():
            scored = score(cfg, day, bundle, decisions, warehouse=warehouse,
                           interventions=interventions, with_drivers=False).scores  # fmt: skip
            frames.append(pd.DataFrame({
                "nct_id": scored["nct_id"].astype(str), "prediction_date": day,
                "risk_12m": scored["risk_12m"], "risk_24m": scored["risk_24m"],
                "sponsor_class": scored["nct_id"].map(classes).fillna("MISSING"),
                "landmark_index": scored["landmark_index"], "flagged": scored["flagged"],
                "model_version": role,
            }))  # fmt: skip
    return pd.concat(frames, ignore_index=True)


def window_comparison(
    cfg: ProjectConfig, dated: ProjectConfig, window: gate.Window, warehouse: Path,
    interventions: str, built: dict[str, Any], champion: Any, out_dir: Path,
) -> tuple[gate.Comparison, dict[str, Any]]:  # fmt: skip
    """The gate's numbers on `window` (see the module text), and what was graded."""
    from trialpulse.monitor import grading
    from trialpulse.scoring.bundle import load_bundle

    days = gate.window_dates(window, cfg.retraining.window_dates)
    folder = out_dir / "window"
    fitted = fit_window_challenger(dated, days[0], warehouse, interventions, built, folder)
    bundles = {gate.CHAMPION: champion, gate.CHALLENGER: load_bundle(dated, folder / "model")}
    predictions = score_on_dates(dated, days, bundles, warehouse, interventions)
    outcomes = pd.read_parquet(built["outcomes"],
                               columns=["trial_id", "event", "event_date", "reversal"])  # fmt: skip
    data_date = dated.dataset.cutoff
    assert data_date is not None
    graded = grading.grade(grading.cohort_outcomes(predictions, outcomes, data_date), data_date,
                           grading.WINDOW, cfg.evaluation.censoring_min_rows,
                           cfg.evaluation.lift_top_fraction)  # fmt: skip
    graded.to_csv(folder / "window_grades.csv", index=False)
    found = gate.compare(graded, cfg.retraining.horizon_months)
    return found, {"dates": [d.isoformat() for d in days], "window_challenger": fitted,
                   "predictions": len(predictions), "dates_compared": found.dates}  # fmt: skip


def compare_scores(champion: pd.DataFrame, challenger: pd.DataFrame) -> dict[str, float]:
    """How the two models' served risks agree on the trials both score: a description of the
    challenger, never a result (no outcome has matured)."""
    both = champion.merge(challenger, on="nct_id", suffixes=("_champion", "_challenger"))
    out: dict[str, float] = {"trials_both_scored": float(len(both))}
    for h in ("12m", "24m"):
        a, b = both[f"risk_{h}_champion"], both[f"risk_{h}_challenger"]
        out[f"spearman_{h}"] = float(a.rank().corr(b.rank())) if len(both) > 1 else float("nan")
        out[f"mean_risk_{h}_champion"] = float(a.mean())
        out[f"mean_risk_{h}_challenger"] = float(b.mean())
    return out


def champion_training_cutoff(meta: dict[str, Any], cfg: ProjectConfig) -> dt.date:
    """The last day of the champion's training data, as its bundle records it (the pinned
    cutoff for the first champion, the data date for a promoted challenger)."""
    recorded = meta.get("data_cutoff")
    if recorded:
        return dt.date.fromisoformat(str(recorded))
    if cfg.dataset.cutoff is None:
        raise RefusedError("the champion records no training cutoff, nor does the config")
    return cfg.dataset.cutoff


def window_metrics(found: gate.Comparison) -> dict[str, float | None]:
    return {"window_champion_auc_12m": found.champion_auc,
            "window_challenger_auc_12m": found.challenger_auc,
            "window_challenger_calibration_slope_12m": found.challenger_slope,
            "window_dates_compared": float(found.dates)}  # fmt: skip


def last_5a() -> tuple[dt.date, Path, str, dict[str, Any]]:
    """The data date, warehouse, interventions glob and counts of the last parity 5a run: the
    same rows (pinned, gap and Neon's live versions) read from the laptop, for a retrain on a
    network that cannot reach Neon."""
    from trialpulse.live import parity_5a

    record = parity_5a.OUT_DIR / "parity_5a.json"
    warehouse = parity_5a.OUT_DIR / "warehouse.duckdb"
    if not (record.is_file() and warehouse.is_file()):
        raise RefusedError("no parity 5a run to reuse; run: uv run python -m "
                           "trialpulse.live.parity_5a")  # fmt: skip
    found = json.loads(record.read_text(encoding="utf-8"))
    glob = (parity_5a.OUT_DIR / "interventions" / "*.parquet").as_posix()
    return dt.date.fromisoformat(found["day"]), warehouse, glob, dict(found["warehouse"])


def write_promotion(dsn: str, row: dict[str, Any]) -> None:
    import psycopg

    columns = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    with (
        psycopg.connect(dsn, autocommit=True, connect_timeout=30) as con,
        con.transaction(),
    ):
        con.execute(f"INSERT INTO monitoring.promotions ({columns}) VALUES ({marks})",
                    tuple(row.values()))  # fmt: skip


def retrain(
    cfg: ProjectConfig, dsn: str, out_dir: Path = RETRAIN_DIR, from_5a: bool = False
) -> dict[str, Any]:
    from trialpulse.gap import rebootstrap
    from trialpulse.live import parity_5a
    from trialpulse.live.daily import _dsn as live_dsn
    from trialpulse.scoring.bundle import CHAMPION_DIR, load_bundle
    from trialpulse.scoring.score import score, served_decisions

    state = tracking.git_state()
    started = clock.perf_counter()
    seconds: dict[str, float] = {}
    key = Secrets().sponsor_id_key
    if key is None:
        raise RefusedError("SPONSOR_ID_KEY is not set (environment or .env)")
    if cfg.dataset.cutoff is None:
        raise RefusedError("config/project.yaml records no data cutoff")
    if from_5a:  # the warehouse parity 5a built from Neon's live rows, reused as it is
        day, warehouse, interventions, counts = last_5a()
    else:
        neon = parity_5a.read_neon(live_dsn("neon"))  # read-only snapshot of the live versions
        day = neon["day"]
        versions = parity_5a.with_exported(neon["versions"])
        warehouse, interventions, counts = parity_5a.build_warehouse(
            rebootstrap.COMBINED_WAREHOUSE, rebootstrap.combined_interventions(), versions,
            key.get_secret_value().encode("utf-8"), out_dir / "warehouse")  # fmt: skip
    seconds["data"] = clock.perf_counter() - started
    dated = as_of(cfg, day)
    built = build_cohort_and_features(dated, warehouse, interventions, out_dir)
    seconds["cohort_and_features"] = clock.perf_counter() - started - sum(seconds.values())
    model, meta = fit_challenger(dated, day, built["landmarks"], built["features_dir"],
                                 out_dir / "challenger")  # fmt: skip
    seconds["fit"] = clock.perf_counter() - started - sum(seconds.values())
    decisions = served_decisions()
    challenger = load_bundle(dated, out_dir / "challenger")
    champion = load_bundle(cfg, CHAMPION_DIR)
    scored = {name: score(dated, day, bundle, decisions, warehouse=warehouse,
                          interventions=interventions, with_drivers=False).scores
              for name, bundle in (("champion", champion), ("challenger", challenger))}  # fmt: skip
    agreement = compare_scores(scored["champion"], scored["challenger"])
    seconds["score"] = clock.perf_counter() - started - sum(seconds.values())
    champion_cutoff = champion_training_cutoff(champion.meta, cfg)
    window = gate.fair_window(champion_cutoff, day, cfg.retraining.horizon_months)
    found, graded = gate.Comparison(None, None, None, 0), dict[str, Any]()
    if window is not None:
        found, graded = window_comparison(cfg, dated, window, warehouse, interventions, built,
                                          champion, out_dir)  # fmt: skip
        seconds["window"] = clock.perf_counter() - started - sum(seconds.values())
    decision = gate.decide(cfg.retraining, window, found.champion_auc, found.challenger_auc,
                           found.challenger_slope)  # fmt: skip
    reason = decision.reason + (BY_HAND if decision.promoted else "")
    summary = dict(model.summary)
    logged: Any
    try:
        logged = tracking.log_run(
            "retrain-challenger",
            {"model": "m4", "data_date": day.isoformat(), "origin": meta["origin"],
             "rounds": summary["rounds"], "landmark_rows": summary["landmark_rows"],
             "live_versions": counts["live_versions"], "promoted": decision.promoted},
            {**agreement, **{k: v for k, v in window_metrics(found).items() if v is not None}},
            challenger_artifacts(out_dir / "challenger", meta), cfg,
            tags={"kind": "challenger", "registered": "no", "gate": reason,
                  "failed_review_rule": FAILED_REVIEW_RULE},
            state=state,
        )  # fmt: skip
        run_id = logged.get("run_id")
    except tracking.TrackingError as exc:
        logged, run_id = f"not logged: {exc}", None
    row = {"champion_version": champion.version, "challenger_version": meta["name"],
           "window_start": window.start if window else None,
           "window_end": window.end if window else None,
           "champion_auc_12m": found.champion_auc, "challenger_auc_12m": found.challenger_auc,
           "challenger_calibration_slope": found.challenger_slope,
           "promoted": decision.promoted, "reason": reason, "mlflow_run_id": run_id}  # fmt: skip
    write_promotion(dsn, row)
    result = {
        "data_date": day.isoformat(), "data_source": "parity 5a's warehouse" if from_5a
        else "Neon's live rows, read now", "warehouse": counts, "cohort": built["cohort"],
        "feature_rows": built["feature_rows"],
        "fit": {k: summary[k] for k in ("landmark_rows", "person_period_rows", "rounds",
                                        "fit_seconds")},
        "agreement_with_the_champion": agreement, "champion_training_cutoff": champion_cutoff,
        "window": graded or None, "comparison": window_metrics(found), "decision": reason,
        "promoted": decision.promoted, "failed_review_rule": FAILED_REVIEW_RULE,
        "tracking": logged, "seconds": {k: round(v, 1) for k, v in seconds.items()},
        "total_seconds": round(clock.perf_counter() - started, 1),
    }  # fmt: skip
    (out_dir / "retrain.json").write_text(json.dumps(result, indent=2, default=str), "utf-8")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    from trialpulse.ingest.history import refuse_on_a_runner
    from trialpulse.live.daily import _dsn

    parser = argparse.ArgumentParser(description="The quarterly retrain and gate (ADR 0080).")
    parser.add_argument("--target", choices=("local", "neon"), required=True,
                        help="where the decision is written (monitoring.promotions)")  # fmt: skip
    parser.add_argument("--from-5a", action="store_true",
                        help="reuse the warehouse of the last parity 5a run instead of reading "
                             "Neon (a network that blocks port 5432)")  # fmt: skip
    args = parser.parse_args(argv)
    refuse_on_a_runner()  # the retrain reads the dataset (ADR 0040)
    result = retrain(load_project_config(), _dsn(args.target), from_5a=args.from_5a)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
