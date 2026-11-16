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
   made after the champion's training cutoff; before one exists the decision is "not
   promoted" with its reason. The challenger is never registered or served by this command.
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


def as_of(cfg: ProjectConfig, day: dt.date) -> ProjectConfig:
    """The configuration whose data reach `day` (the data cutoff moved, nothing else)."""
    return cfg.model_copy(update={"dataset": cfg.dataset.model_copy(update={"cutoff": day})})


def build_cohort_and_features(cfg: ProjectConfig, warehouse: Path, interventions: str,
                              out_dir: Path) -> dict[str, Any]:  # fmt: skip
    """The final cohort of `warehouse` and its features, in `out_dir` (never the main ones)."""
    from trialpulse.cohort.build import attach_warehouse, build_cohort, write_outputs
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
            "landmarks": landmarks, "features_dir": features_dir}  # fmt: skip


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
    version = f"trialpulse-m4-challenger-{day.isoformat()}"
    meta = save_bundle(model, priors, {
        "version": version, "kind": "challenger (ADR 0080): not registered, not served",
        "origin": origin.isoformat(), "data_date": day.isoformat(),
        "data": "pinned + gap + exported live rows (ADR 0080)",
        "failed_review_rule": FAILED_REVIEW_RULE, "settings": model.settings,
        "summary": dict(model.summary), "config_sha256": sha256_of(PROJECT_CONFIG_PATH),
    }, folder)  # fmt: skip
    return model, meta


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


def retrain(cfg: ProjectConfig, dsn: str, out_dir: Path = RETRAIN_DIR) -> dict[str, Any]:
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
    neon = parity_5a.read_neon(live_dsn("neon"))  # read-only snapshot of the live versions
    day: dt.date = neon["day"]
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
    window = gate.fair_window(cfg.dataset.cutoff, day, cfg.retraining.horizon_months)
    decision = gate.decide(cfg.retraining, window, None, None, None)
    summary = dict(model.summary)
    logged: Any
    try:
        logged = tracking.log_run(
            "retrain-challenger",
            {"model": "m4", "data_date": day.isoformat(), "origin": meta["origin"],
             "rounds": summary["rounds"], "landmark_rows": summary["landmark_rows"],
             "live_versions": counts["live_versions"], "promoted": decision.promoted},
            agreement,
            [out_dir / "challenger" / name for name in meta["files"]], cfg,
            tags={"kind": "challenger", "registered": "no", "gate": decision.reason,
                  "failed_review_rule": FAILED_REVIEW_RULE},
            state=state,
        )  # fmt: skip
        run_id = logged.get("run_id")
    except tracking.TrackingError as exc:
        logged, run_id = f"not logged: {exc}", None
    row = {"champion_version": champion.version, "challenger_version": challenger.version,
           "window_start": window.start if window else None,
           "window_end": window.end if window else None,
           "champion_auc_12m": None, "challenger_auc_12m": None,
           "challenger_calibration_slope": None, "promoted": decision.promoted,
           "reason": decision.reason, "mlflow_run_id": run_id}  # fmt: skip
    write_promotion(dsn, row)
    result = {
        "data_date": day.isoformat(), "warehouse": counts, "cohort": built["cohort"],
        "feature_rows": built["feature_rows"],
        "fit": {k: summary[k] for k in ("landmark_rows", "person_period_rows", "rounds",
                                        "fit_seconds")},
        "agreement_with_the_champion": agreement, "decision": decision.reason,
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
    args = parser.parse_args(argv)
    refuse_on_a_runner()  # the retrain reads the dataset (ADR 0040)
    result = retrain(load_project_config(), _dsn(args.target))
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
