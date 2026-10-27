"""The served champion (CLAUDE.md Step 13, ADR 0034 decision 5).

    uv run python -m trialpulse.scoring.champion

M4 with the tuned settings, fitted on all available data: the final cohort's landmark rows
with the origin set to the day after the data cutoff, so every interval that ended by the
cutoff is a training row (ADRs 0015 and 0016). Its sponsor priors are fitted on the same
rows and saved with it. It is a new fit, kept apart from the registered per-origin models:
no metric of it is reported as a result.

The bundle (the trees as LightGBM text, the category levels, the priors and a record of
what it was fitted on) is written to data/scoring/champion/ and logged to MLflow as a run;
the run's artifacts (the bundle files) are registered as a version of the model
`trialpulse-m4` and given
the alias `champion`. A challenger (Step 17) gets the alias `challenger` the same way.
"""

import argparse
import datetime as dt
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd

from trialpulse import tracking
from trialpulse.cli import RefusedError, run
from trialpulse.config import PROJECT_CONFIG_PATH, REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.eval.rows import LANDMARKS_PATH, LandmarkRows, load_landmark_rows
from trialpulse.eval.walkforward import FEATURES_DIR, training_rows
from trialpulse.features.build import FEATURES_NAME
from trialpulse.features.frame import as_arrays
from trialpulse.features.transforms import SponsorPriors
from trialpulse.models import discrete_time
from trialpulse.models.discrete_time import DiscreteTimeBoosting
from trialpulse.scoring.score import Bundle

CHAMPION_DIR = REPO_ROOT / "data" / "scoring" / "champion"
MODEL_NAME = "trialpulse-m4"
CHAMPION_ALIAS, CHALLENGER_ALIAS = "champion", "challenger"
BOOSTER, LEVELS, PRIORS, META = "model.txt", "levels.json", "sponsor_priors.json", "meta.json"


def served_origin(cfg: ProjectConfig) -> dt.date:
    """The day after the data cutoff: the end of observation of the served fit."""
    if cfg.dataset.cutoff is None:
        raise RefusedError("config/project.yaml records no data cutoff")
    return cfg.dataset.cutoff + dt.timedelta(days=1)


def training_matrix(
    cfg: ProjectConfig, origin: dt.date, features_dir: Path = FEATURES_DIR
) -> tuple[LandmarkRows, np.ndarray, np.ndarray, SponsorPriors]:
    """The final cohort's landmark rows with their features, as training rows of `origin`,
    and the sponsor priors fitted on them."""
    rows, time, event = training_rows(
        load_landmark_rows(LANDMARKS_PATH), np.datetime64(origin, "D")
    )
    keys = pd.DataFrame({"trial_id": rows.trial_id.astype(str),  # noqa: F841
                         "landmark_index": rows.landmark_index})  # fmt: skip
    with duckdb.connect() as con:
        frame = con.execute(
            f"""SELECT f.* FROM keys k LEFT JOIN
            read_parquet('{(features_dir / FEATURES_NAME).as_posix()}') f
              USING (trial_id, landmark_index)
            ORDER BY k.trial_id, k.landmark_index"""
        ).df()
    order = np.lexsort((rows.landmark_index, rows.trial_id.astype(str)))
    if not (frame["trial_id"].to_numpy(dtype=str) == rows.trial_id.astype(str)[order]).all():
        raise RefusedError("the feature build does not hold every landmark row of the cohort")
    rows = LandmarkRows(
        rows.trial_id[order], rows.landmark_index[order], rows.landmark_date[order],
        rows.event[order], rows.event_date[order], {k: v[order] for k, v in rows.features.items()},
    )  # fmt: skip
    time, event = time[order], event[order]
    priors = SponsorPriors.fit(
        rows.trial_id, rows.landmark_index, rows.landmark_date, rows.event, rows.event_date,
        frame["sponsor_class"].to_numpy(dtype=object), origin,
        cfg.features.sponsor_prior_weight,
    )  # fmt: skip
    frame["sponsor_stop_rate"] = priors.rate(
        frame["sponsor_class"].to_numpy(dtype=object),
        frame["sponsor_prior_ended"].to_numpy(dtype=np.float64, na_value=np.nan),
        frame["sponsor_prior_stopped"].to_numpy(dtype=np.float64, na_value=np.nan),
    )
    names = discrete_time.feature_names(discrete_time.FAMILY_SETS["m4"])
    columns = ["trial_id", "landmark_index", *[n for n in names if n != "landmark_index"]]
    return rows.with_features(as_arrays(frame[columns])), time, event, priors


def fit_served(cfg: ProjectConfig) -> tuple[DiscreteTimeBoosting, SponsorPriors, dt.date]:
    origin = served_origin(cfg)
    rows, time, event, priors = training_matrix(cfg, origin)
    model = discrete_time.build("m4", cfg)
    assert isinstance(model, DiscreteTimeBoosting)
    model.fit_rows(rows, time, event, origin)
    model._scored = None  # the hazards of the training rows are not part of the bundle
    return model, priors, origin


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_bundle(
    model: DiscreteTimeBoosting,
    priors: SponsorPriors,
    meta: dict[str, Any],
    folder: Path = CHAMPION_DIR,
) -> dict[str, Any]:
    assert model.booster is not None
    folder.mkdir(parents=True, exist_ok=True)
    model.booster.save_model(str(folder / BOOSTER))
    (folder / LEVELS).write_text(json.dumps({k: list(v) for k, v in model.levels.items()},
                                            indent=2), encoding="utf-8")  # fmt: skip
    (folder / PRIORS).write_text(json.dumps(priors.to_json(), indent=2), encoding="utf-8")
    meta = {**meta, "files": {name: sha256_of(folder / name) for name in (BOOSTER, LEVELS,
                                                                           PRIORS)}}  # fmt: skip
    (folder / META).write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return meta


def load_bundle(cfg: ProjectConfig, folder: Path = CHAMPION_DIR) -> Bundle:
    """The served champion from its folder, refused if a file is not the one recorded."""
    meta_path = folder / META
    if not meta_path.is_file():
        raise RefusedError(
            f"{meta_path} not found: fit the served champion with: "
            "uv run python -m trialpulse.scoring.champion"
        )
    meta: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
    for name, digest in meta["files"].items():
        if sha256_of(folder / name) != digest:
            raise RefusedError(f"{folder / name} is not the file the champion was saved with")
    model = discrete_time.build("m4", cfg)
    assert isinstance(model, DiscreteTimeBoosting)
    model.booster = lgb.Booster(model_file=str(folder / BOOSTER))
    levels = json.loads((folder / LEVELS).read_text(encoding="utf-8"))
    model.levels = {name: tuple(values) for name, values in levels.items()}
    model.summary = meta.get("summary", {})
    priors = SponsorPriors.from_json(json.loads((folder / PRIORS).read_text(encoding="utf-8")))
    return Bundle(model, priors, MODEL_NAME, str(meta.get("version", meta["files"][BOOSTER][:12])),
                  meta)  # fmt: skip


def register(run_id: str, alias: str, cfg: ProjectConfig) -> dict[str, str]:
    """Register the run's artifacts (the bundle files) as a version of MODEL_NAME and give
    it `alias`."""
    import mlflow
    from mlflow.tracking import MlflowClient

    target = tracking.tracking_target()
    mlflow.set_tracking_uri(target.uri)
    client = MlflowClient()
    try:
        client.get_registered_model(MODEL_NAME)
    except Exception:
        client.create_registered_model(
            MODEL_NAME, description="TrialPulse served M4 (Step 13, ADR 0034)"
        )
    version = client.create_model_version(
        MODEL_NAME, source=f"runs:/{run_id}", run_id=run_id,
        tags={"dataset_revision": str(cfg.dataset.revision), "alias": alias},
    )  # fmt: skip
    client.set_registered_model_alias(MODEL_NAME, alias, version.version)
    return {"model": MODEL_NAME, "version": str(version.version), "alias": alias}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fit and register the served champion.")
    parser.add_argument("--folder", type=Path, default=CHAMPION_DIR)
    tracking.add_arguments(parser)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    started = tracking.git_state()
    model, priors, origin = fit_served(cfg)
    summary = dict(model.summary)
    meta = save_bundle(
        model, priors,
        {"origin": origin.isoformat(), "dataset_revision": cfg.dataset.revision,
         "data_cutoff": str(cfg.dataset.cutoff), "settings": model.settings, "summary": summary,
         "config_sha256": sha256_of(PROJECT_CONFIG_PATH), **started},
        args.folder,
    )  # fmt: skip
    print(f"fitted on {summary['landmark_rows']:,} landmark rows "
          f"({summary['person_period_rows']:,} intervals), {summary['rounds']} rounds, "
          f"{summary['fit_seconds']:.0f} s; saved to {args.folder}")  # fmt: skip
    if args.no_track:
        return 0
    try:
        logged = tracking.log_run(
            "served-champion",
            {"model": "m4", "origin": origin.isoformat(), "rounds": summary["rounds"],
             "landmark_rows": summary["landmark_rows"]},
            {},  # no metric of the served champion is a result (ADR 0034)
            [args.folder / name for name in (BOOSTER, LEVELS, PRIORS, META)], cfg,
            tags={"step": "13", "kind": "served-champion", "adr": "0034"},
            local=args.local_tracking, state=started,
        )  # fmt: skip
        registered = register(logged["run_id"], CHAMPION_ALIAS, cfg)
    except tracking.TrackingError as exc:
        print(f"failed: the champion was not logged to MLflow ({exc}); it is in {args.folder}")
        return tracking.FAILED_EXIT_CODE
    except Exception as exc:  # the registry call: the run is logged, say what failed
        print(f"failed: the run was logged but not registered ({type(exc).__name__}: "
              f"{tracking.without_address(str(exc), tracking.tracking_target().uri)})")  # fmt: skip
        return tracking.FAILED_EXIT_CODE
    meta.update({"tracking": logged, "registry": registered,
                 "version": f"{MODEL_NAME}/{registered['version']}"})  # fmt: skip
    (args.folder / META).write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print(f"logged and registered: {registered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
