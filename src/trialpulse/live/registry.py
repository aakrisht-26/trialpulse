"""Fetch a registered model's bundle for the runner (ADR 0045).

    uv run python -m trialpulse.live.registry --alias champion --to bundle
    uv run python -m trialpulse.live.registry --attach-decisions

Downloads the files of `trialpulse-m4@<alias>` from the MLflow registry (DagsHub) into a
folder, where `scoring.bundle.load_bundle` checks every file against its recorded digest.
The daily job and the timing job read the bundle from that folder; the decisions of ADR 0034
(`step13_checks.json`) must be among the registered files (registered on the laptop).

`--attach-decisions` (laptop only) logs Step 13's result file to the run the champion's
version was registered from, so the fetched bundle holds it under the same model version (a
new version would change the version every stored score carries). It refuses a file whose
SHA-256 is not that of the file the day-0 check validated.
"""

import argparse
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from trialpulse import tracking
from trialpulse.cli import RefusedError, run
from trialpulse.scoring.bundle import MODEL_NAME, sha256_of

DECISIONS = "step13_checks.json"
# Step 13's result file (data/results/checks/step13_checks.json), as the day-0 check of
# Step 14 phase A validated it.
DECISIONS_SHA256 = "dae12abbd54096e3ac4f8d76282fe61bc63e1f3526f7d57df0cdbd44cbe9a928"


def fetch(alias: str, folder: Path) -> list[str]:
    import mlflow

    mlflow.set_tracking_uri(tracking.tracking_target().uri)
    folder.mkdir(parents=True, exist_ok=True)
    local = Path(mlflow.artifacts.download_artifacts(f"models:/{MODEL_NAME}@{alias}",
                                                     dst_path=str(folder)))  # fmt: skip
    return sorted(p.name for p in local.iterdir())


def attach_decisions(path: Path, alias: str = "champion", client: Any = None) -> dict[str, str]:
    """Log the ADR 0034 decisions to the run of `alias`'s version (see the module text)."""
    if os.environ.get("GITHUB_ACTIONS"):
        raise RefusedError("the decisions are attached on the laptop, never on a runner")
    if path.name != DECISIONS or not path.is_file():
        raise RefusedError(f"no {DECISIONS} at {path}")
    if sha256_of(path) != DECISIONS_SHA256:
        raise RefusedError(f"{path} is not the file the day-0 check validated")
    if client is None:
        import mlflow
        from mlflow.tracking import MlflowClient

        mlflow.set_tracking_uri(tracking.tracking_target().uri)
        client = MlflowClient()
    version = client.get_model_version_by_alias(MODEL_NAME, alias)
    client.log_artifact(version.run_id, str(path))
    return {"model": MODEL_NAME, "version": str(version.version), "run_id": version.run_id}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download a registered model's bundle.")
    parser.add_argument("--alias", default="champion")
    parser.add_argument("--to", type=Path)
    parser.add_argument("--attach-decisions", action="store_true",
                        help="log Step 13's decisions to the alias's run (laptop)")  # fmt: skip
    args = parser.parse_args(argv)
    if args.attach_decisions:
        from trialpulse.scoring.checks import RESULT_PATH

        attached = attach_decisions(RESULT_PATH, args.alias)
        print(f"{DECISIONS} attached to {attached['model']} version {attached['version']}")
        return 0
    if args.to is None:
        parser.error("--to is required unless --attach-decisions is given")
    names = fetch(args.alias, args.to)
    print(f"{MODEL_NAME}@{args.alias}: {len(names)} files in {args.to}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
