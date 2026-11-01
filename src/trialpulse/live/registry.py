"""Fetch a registered model's bundle for the runner (ADR 0045).

    uv run python -m trialpulse.live.registry --alias champion --to bundle

Downloads the files of `trialpulse-m4@<alias>` from the MLflow registry (DagsHub) into a
folder, where `scoring.bundle.load_bundle` checks every file against its recorded digest.
The daily job and the timing job read the bundle from that folder; the decisions of ADR 0034
(`step13_checks.json`) must be among the registered files (registered on the laptop).
"""

import argparse
from collections.abc import Sequence
from pathlib import Path

from trialpulse import tracking
from trialpulse.cli import run
from trialpulse.scoring.bundle import MODEL_NAME


def fetch(alias: str, folder: Path) -> list[str]:
    import mlflow

    mlflow.set_tracking_uri(tracking.tracking_target().uri)
    folder.mkdir(parents=True, exist_ok=True)
    local = Path(mlflow.artifacts.download_artifacts(f"models:/{MODEL_NAME}@{alias}",
                                                     dst_path=str(folder)))  # fmt: skip
    return sorted(p.name for p in local.iterdir())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download a registered model's bundle.")
    parser.add_argument("--alias", default="champion")
    parser.add_argument("--to", type=Path, required=True)
    args = parser.parse_args(argv)
    names = fetch(args.alias, args.to)
    print(f"{MODEL_NAME}@{args.alias}: {len(names)} files in {args.to}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
