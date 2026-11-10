"""The replay of Step 17 (ADR 0078): monitoring history with matured outcomes, from the dataset.

    uv run python -m trialpulse.monitor.replay model
    uv run python -m trialpulse.monitor.replay score --quarter 2023Q1
    uv run python -m trialpulse.monitor.replay grade --target neon

Laptop only (ADR 0040): it reads the pinned warehouse and the cohort, and only aggregates
leave the laptop. In order:

1. **model:** the replay model, M4 with the tuned settings fitted as of origin 2023-01-01 as
   a walk-forward origin is: the cohort built as of the origin (ADR 0016, written to
   data/replay/training/), features for its landmark rows (the main build's, and for rows it
   does not hold the feature build's own code, without text, which M4 does not read), the
   sponsor priors fitted on those rows, early stopping as ADR 0025. Saved to
   data/replay/model/ and logged to MLflow as a replay model; never registered as champion or
   challenger. The served champion is not used: it was fitted on outcomes through
   2026-09-25, the replay's own outcomes among them.
2. **score:** every Friday from 2023-01-06 to 2024-06-28, Step 13's `score()` as of the date
   with the replay model, without drivers; one quarter per command, resumable (a date with
   a file in data/replay/scores/ is skipped).
3. **grade:** the scores of every date against the final cohort's outcomes (through the
   pinned cutoff; every 12- and 24-month horizon has matured by then), as aggregates in
   `monitoring.graded_predictions` with source 'replay'.

The replay is monitoring evidence of the champion's method, never a registered result. It
prints counts only, never a trial id.
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
from trialpulse.cohort.build import OUTCOMES_PATH, build_training_cohorts
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import PROJECT_CONFIG_PATH, REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.eval.rows import LANDMARKS_PATH
from trialpulse.monitor import grading

ORIGIN = dt.date(2023, 1, 1)
START, END = dt.date(2023, 1, 1), dt.date(2024, 6, 30)
REPLAY_DIR = REPO_ROOT / "data" / "replay"
TRAINING_DIR = REPLAY_DIR / "training"
MODEL_DIR = REPLAY_DIR / "model"
SCORES_DIR = REPLAY_DIR / "scores"
EXTRA_FEATURES = REPLAY_DIR / "features_extra.parquet"
VERSION = f"trialpulse-m4-replay-{ORIGIN.isoformat()}"
SCORE_COLUMNS = ("nct_id", "as_of_date", "risk_12m", "risk_24m", "sponsor_class",
                 "landmark_index", "flagged", "model_version")  # fmt: skip


def dates(quarter: str | None = None) -> list[dt.date]:
    """The replay's Fridays, all of them or one quarter's ('2023Q1')."""
    days = list(grading.iter_dates(START, END))
    if quarter is None:
        return days
    year, q = int(quarter[:4]), int(quarter[-1])
    if not (quarter[4] == "Q" and 1 <= q <= 4):
        raise RefusedError("a quarter is written like 2023Q1")
    return [d for d in days if d.year == year and (d.month - 1) // 3 + 1 == q]


def landmarks_path() -> Path:
    from trialpulse.features.build import origin_source

    return TRAINING_DIR / origin_source(ORIGIN) / "landmarks.parquet"


def build_training(cfg: ProjectConfig) -> dict[str, Any]:
    """The cohort as of the origin (ADR 0016), beside the main cohort, never in its place."""
    from trialpulse.cohort.build import attach_warehouse
    from trialpulse.warehouse.build import WAREHOUSE_PATH

    rules = CohortRules.from_config(cfg)
    with duckdb.connect() as con:
        attach_warehouse(con, WAREHOUSE_PATH)
        summaries = build_training_cohorts(con, rules, [ORIGIN], TRAINING_DIR, LANDMARKS_PATH)
    return {k: v for k, v in summaries[0].items() if k != "files"}


def extra_features(cfg: ProjectConfig) -> int:
    """Features of the origin's landmark rows the main build does not hold, by the feature
    build's own code without text (ADR 0026); the number of such rows."""
    from trialpulse.features import build as features
    from trialpulse.features.registry import KEY_COLUMNS, built_columns
    from trialpulse.parquet import write_table
    from trialpulse.warehouse.build import TEMP_DIR, WAREHOUSE_PATH, interventions_glob

    main = (features.FEATURES_DIR / features.FEATURES_NAME).as_posix()
    keys = REPLAY_DIR / "missing_keys.parquet"
    with duckdb.connect() as con:
        con.execute(
            f"""COPY (SELECT l.trial_id, l.landmark_index, l.landmark_date
                FROM read_parquet('{landmarks_path().as_posix()}') l
                ANTI JOIN read_parquet('{main}') f USING (trial_id, landmark_index)
                ORDER BY 1, 2) TO '{keys.as_posix()}' (FORMAT parquet)"""
        )
        missing = con.execute(f"SELECT count(*) FROM read_parquet('{keys.as_posix()}')").fetchone()
    count = int(missing[0]) if missing else 0
    EXTRA_FEATURES.unlink(missing_ok=True)
    if count == 0:
        return 0
    rules = CohortRules.from_config(cfg)
    with features.connect(TEMP_DIR) as con:
        features.prepare(con, WAREHOUSE_PATH, interventions_glob(cfg), {"replay": keys}, rules)
        features.compute_features(con, rules, cfg, text=False)
        write_table(con, features.FEATURE_TABLE, EXTRA_FEATURES,
                    [*KEY_COLUMNS, *built_columns()], features.ORDER)  # fmt: skip
    return count


def fit_model(cfg: ProjectConfig) -> dict[str, Any]:
    """Fit, save and log the replay model (see the module text)."""
    from trialpulse.models import discrete_time
    from trialpulse.models.discrete_time import DiscreteTimeBoosting
    from trialpulse.scoring.bundle import sha256_of
    from trialpulse.scoring.champion import save_bundle, training_matrix

    started_state = tracking.git_state()
    started = clock.perf_counter()
    summary: dict[str, Any] = {"training_cohort": build_training(cfg)}
    summary["extra_feature_rows"] = extra_features(cfg)
    summary["seconds_to_features"] = round(clock.perf_counter() - started, 1)
    extra = (EXTRA_FEATURES,) if EXTRA_FEATURES.is_file() else ()
    rows, time, event, priors = training_matrix(cfg, ORIGIN, landmarks=landmarks_path(),
                                                extra_features=extra)  # fmt: skip
    model = discrete_time.build("m4", cfg)
    assert isinstance(model, DiscreteTimeBoosting)
    model.fit_rows(rows, time, event, ORIGIN)
    model._scored = None
    meta = save_bundle(model, priors, {
        "version": VERSION, "kind": "replay (ADR 0078): never registered",
        "origin": ORIGIN.isoformat(), "dataset_revision": cfg.dataset.revision,
        "settings": model.settings, "summary": dict(model.summary),
        "config_sha256": sha256_of(PROJECT_CONFIG_PATH), **started_state,
    }, MODEL_DIR)  # fmt: skip
    summary["model"] = {k: model.summary[k] for k in ("landmark_rows", "person_period_rows",
                                                      "rounds", "fit_seconds")}  # fmt: skip
    summary["seconds"] = round(clock.perf_counter() - started, 1)
    try:
        summary["tracking"] = tracking.log_run(
            "replay-model",
            {"model": "m4", "origin": ORIGIN.isoformat(), "rounds": model.summary["rounds"],
             "landmark_rows": model.summary["landmark_rows"]},
            {},  # monitoring evidence, never a registered result (ADR 0078)
            [MODEL_DIR / name for name in meta["files"]], cfg,
            tags={"kind": "replay", "registered": "never"}, state=started_state,
        )  # fmt: skip
    except tracking.TrackingError as exc:
        summary["tracking"] = f"not logged: {exc}"
    (MODEL_DIR / "fit.json").write_text(json.dumps(summary, indent=2, default=str), "utf-8")
    return summary


def score_dates(cfg: ProjectConfig, days: Sequence[dt.date]) -> dict[str, Any]:
    """Score each date not scored yet with the replay model (see the module text)."""
    from trialpulse.scoring.bundle import load_bundle
    from trialpulse.scoring.score import score, served_decisions

    bundle = load_bundle(cfg, MODEL_DIR)
    if bundle.version != VERSION:
        raise RefusedError(f"{MODEL_DIR} does not hold the replay model; run the model step")
    decisions = served_decisions()
    SCORES_DIR.mkdir(parents=True, exist_ok=True)
    done, seconds = [], []
    for day in days:
        path = SCORES_DIR / f"{day.isoformat()}.parquet"
        if path.is_file():
            continue
        started = clock.perf_counter()
        scored = score(cfg, day, bundle, decisions, with_drivers=False)
        frame = scored.scores[list(SCORE_COLUMNS)].copy()
        frame["as_of_date"] = pd.to_datetime(frame["as_of_date"]).dt.date
        frame.to_parquet(path.with_suffix(".tmp"), index=False)
        path.with_suffix(".tmp").replace(path)  # a date is done only once its file is whole
        done.append(len(frame))
        seconds.append(clock.perf_counter() - started)
        print(f"{day}: {len(frame):,} scored in {seconds[-1]:.0f} s", flush=True)
    return {"dates_scored": len(done), "dates_already_scored": len(days) - len(done),
            "rows": sum(done), "seconds_per_date": round(sum(seconds) / len(seconds), 1)
            if seconds else None}  # fmt: skip


def predictions() -> pd.DataFrame:
    """Every scored date's predictions with their outcomes from the final cohort."""
    files = sorted(SCORES_DIR.glob("*.parquet"))
    if not files:
        raise RefusedError("no replay scores yet; run the score step")
    frame = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    frame = frame.rename(columns={"as_of_date": "prediction_date"})
    outcomes = pd.read_parquet(OUTCOMES_PATH, columns=["trial_id", "event", "event_date"])
    return grading.cohort_outcomes(frame, outcomes)


def grade_replay(cfg: ProjectConfig, dsn: str) -> dict[str, Any]:
    import psycopg

    if cfg.dataset.cutoff is None:
        raise RefusedError("config/project.yaml records no data cutoff")
    graded = grading.grade(predictions(), cfg.dataset.cutoff, "replay",
                           cfg.evaluation.censoring_min_rows,
                           cfg.evaluation.lift_top_fraction)  # fmt: skip
    with psycopg.connect(dsn, connect_timeout=30) as con, con.cursor() as cur:
        written = grading.upsert(cur, graded)
        con.commit()
    return {**grading.summary(graded), "written": written}


def main(argv: Sequence[str] | None = None) -> int:
    from trialpulse.ingest.history import refuse_on_a_runner

    parser = argparse.ArgumentParser(description="The replay of Step 17 (ADR 0078).")
    steps = parser.add_subparsers(dest="step", required=True)
    steps.add_parser("model", help="fit the replay model as of 2023-01-01")
    scoring = steps.add_parser("score", help="score the replay's Fridays")
    scoring.add_argument("--quarter", help="one quarter, like 2023Q1 (default: every date)")
    grader = steps.add_parser("grade", help="grade the scores into Neon or local Postgres")
    grader.add_argument("--target", choices=("local", "neon"), required=True)
    args = parser.parse_args(argv)
    refuse_on_a_runner()  # the dataset stays on the laptop (ADR 0040)
    cfg = load_project_config()
    if args.step == "model":
        result = fit_model(cfg)
    elif args.step == "score":
        result = score_dates(cfg, dates(args.quarter))
    else:
        from trialpulse.live.daily import _dsn

        result = grade_replay(cfg, _dsn(args.target))
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
