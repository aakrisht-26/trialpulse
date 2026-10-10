"""Experiment tracking with MLflow (CLAUDE.md Step 9 and Section 15).

Every model run is logged with the git commit it ran from, the dataset revision, the
configuration, its metrics and its result files.

**Where.** If MLFLOW_TRACKING_URI is set (in the environment or .env), runs go to that
server: the project's MLflow on DagsHub, with MLFLOW_TRACKING_USERNAME and
MLFLOW_TRACKING_PASSWORD as its credentials. If it is not set, runs go to a local SQLite
store in mlruns/, which git ignores (MLflow's plain-file store no longer accepts runs). A
local store is a stand-in, not the record: a results file that names it says so, and the
run is logged again once the server is configured. `--local-tracking` asks for the local
store although a server is configured, for the time the server cannot take runs.

**When logging fails.** A command writes its results before it logs them. If the server
refuses or cannot be reached, `log_run` raises `TrackingError`, and the command reports it
and exits with code 5: the results stay on disk, and the run is not silently logged
somewhere else.

**What is never logged.** No data rows, no trial texts, no sponsor names and no secrets:
parameters are settings and identifiers, metrics are numbers, and artifacts are the result
files a command already wrote (aggregates only) and config/project.yaml.
"""

import argparse
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trialpulse.config import PROJECT_CONFIG_PATH, REPO_ROOT, ProjectConfig, Secrets

LOCAL_STORE = REPO_ROOT / "mlruns"
EXPERIMENT = "trialpulse-development"
REMOTE = "remote"
LOCAL = "local"
NOT_SET = "MLFLOW_TRACKING_URI is not set"
ASKED_FOR = "--local-tracking was given, so the server named by MLFLOW_TRACKING_URI was not used"
FAILED_EXIT_CODE = 5


class TrackingError(RuntimeError):
    """A run could not be logged. The results of the command are on disk already."""


@dataclass(frozen=True)
class Target:
    """Where runs are logged."""

    kind: str  # "remote" (the configured server) or "local" (mlruns/, temporary)
    uri: str
    artifacts: str = ""  # where a local store keeps artifact files; a server decides its own
    why_local: str = NOT_SET

    @property
    def description(self) -> str:
        if self.kind == REMOTE:
            return "the MLflow server named by MLFLOW_TRACKING_URI"
        return f"a local store in mlruns/ (temporary: {self.why_local})"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The tracking options of every command that logs a run."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--no-track", action="store_true", help="do not log the run to MLflow")
    group.add_argument(
        "--local-tracking",
        action="store_true",
        help="log the run to the local store in mlruns/ even if a server is configured "
        "(temporary: log it to the server again later)",
    )


def tracking_target(
    secrets: Secrets | None = None, local_store: Path = LOCAL_STORE, local: bool = False
) -> Target:
    """The configured server if there is one, else the local store; the local store also
    when `local` asks for it. Credentials for the server are handed to MLflow through the
    environment of this process; they are never printed, logged or written anywhere."""
    if local:
        return local_target(local_store, ASKED_FOR)
    secrets = secrets or Secrets()
    if not secrets.mlflow_tracking_uri:
        return local_target(local_store)
    if secrets.mlflow_tracking_username:
        os.environ["MLFLOW_TRACKING_USERNAME"] = secrets.mlflow_tracking_username
    if secrets.mlflow_tracking_password:
        os.environ["MLFLOW_TRACKING_PASSWORD"] = secrets.mlflow_tracking_password.get_secret_value()
    return Target(REMOTE, secrets.mlflow_tracking_uri)


def local_target(store: Path, why: str = NOT_SET) -> Target:
    """A SQLite store in a folder, with the artifact files beside the database."""
    store = store.resolve()
    store.mkdir(parents=True, exist_ok=True)
    return Target(
        LOCAL, "sqlite:///" + (store / "mlflow.db").as_posix(), (store / "artifacts").as_uri(), why
    )


def explain(exc: BaseException, target: Target) -> str:
    """What went wrong, on one line, without the password of the server. A 404 from a server
    gets the usual cause: MLflow reports it as a failed request and says no more."""
    text = " ".join(str(exc).split()) or type(exc).__name__
    password = os.environ.get("MLFLOW_TRACKING_PASSWORD")
    if password:
        text = text.replace(password, "[password]")
    if target.kind == REMOTE and "404" in text:
        text += (
            ". A 404 from the server usually means that the repository named in "
            "MLFLOW_TRACKING_URI does not exist there, or that these credentials cannot see it"
        )
    return text


def git_state(repo: Path = REPO_ROOT) -> dict[str, str]:
    """The commit a run started from, and whether the working tree had uncommitted changes
    to tracked files (a run from a dirty tree is not reproducible from its commit alone)."""

    def git(*args: str) -> str:
        try:
            done = subprocess.run(
                ["git", *args], cwd=repo, capture_output=True, text=True, check=False
            )
        except OSError:  # no git, or no such folder: the commit is unknown, not an error
            return ""
        return done.stdout.strip() if done.returncode == 0 else ""

    commit = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    return {"git_commit": commit or "unknown", "git_dirty": "yes" if dirty else "no"}


def log_run(
    name: str,
    params: Mapping[str, Any],
    metrics: Mapping[str, float],
    artifacts: Sequence[Path],
    cfg: ProjectConfig,
    tags: Mapping[str, str] | None = None,
    target: Target | None = None,
    repo: Path = REPO_ROOT,
    local: bool = False,
    state: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Log one run and return where it went: the kind of store, the experiment, the run id
    and the commit. `state` is the git state the run started from (a command reads it before
    its work, so a commit made while it runs is not credited with the run); without it the
    state is read now. Metrics that are not finite are left out (MLflow refuses them on some
    stores) and counted in a tag. Raises TrackingError if the store does not take the run."""
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")  # no advice lines in the output
    import mlflow

    target = target or tracking_target(local=local)
    state = dict(state) if state is not None else git_state(repo)
    finite = {k: float(v) for k, v in metrics.items() if v == v and abs(v) != float("inf")}
    try:
        mlflow.set_tracking_uri(target.uri)
        if target.artifacts and mlflow.get_experiment_by_name(EXPERIMENT) is None:
            mlflow.create_experiment(EXPERIMENT, artifact_location=target.artifacts)
        mlflow.set_experiment(EXPERIMENT)
        with mlflow.start_run(run_name=name) as active:
            mlflow.set_tags(
                {
                    **state,
                    "dataset_revision": str(cfg.dataset.revision),
                    "data_cutoff": str(cfg.dataset.cutoff),
                    "metrics_not_finite": str(len(metrics) - len(finite)),
                    **(tags or {}),
                }
            )
            mlflow.log_params({k: str(v) for k, v in params.items()})
            if finite:
                mlflow.log_metrics(finite)
            for path in (PROJECT_CONFIG_PATH, *artifacts):
                if Path(path).is_file():
                    mlflow.log_artifact(str(path))
            run_id = active.info.run_id
    except Exception as exc:  # whatever the client raises: the caller keeps its results
        raise TrackingError(explain(exc, target)) from exc
    return {
        "store": target.kind,
        "store_description": target.description,
        "experiment": EXPERIMENT,
        "run_id": run_id,
        "run_name": name,
        **state,
    }


def walkforward_metrics(results: Mapping[str, Any]) -> dict[str, float]:
    """The pooled metrics of a walk-forward result as flat names: one value per origin and
    horizon, and the mean over origins (the quantity the selection rule of Section 9 reads)."""
    out: dict[str, float] = {}
    sums: dict[str, list[float]] = {}
    for origin in results["origins"]:
        year = origin["origin"][:4]
        for months, by in origin["horizons"].items():
            pooled = by["pooled"]
            values = {
                "auc": pooled["auc"]["estimate"],
                "auc_ci_low": pooled["auc"]["ci_low"],
                "auc_ci_high": pooled["auc"]["ci_high"],
                "brier": pooled["brier"]["estimate"],
                "lift": pooled["lift"]["estimate"],
                "calibration_slope": pooled["calibration_slope"],
                "calibration_intercept": pooled["calibration_intercept"],
            }
            for metric, value in values.items():
                out[f"{metric}_{months}m_{year}"] = float(value)
                if not metric.startswith("auc_ci"):
                    sums.setdefault(f"{metric}_{months}m_mean", []).append(float(value))
        out[f"n_eval_rows_{year}"] = float(origin["n_eval_rows"])
        out[f"n_train_rows_{year}"] = float(origin["n_train_rows"])
    for name, per_origin in sums.items():
        out[name] = float(sum(per_origin) / len(per_origin))
    return out
