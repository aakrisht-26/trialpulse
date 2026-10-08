"""Download a config of the version-history dataset at the pinned revision.

    uv run python -m trialpulse.ingest.history --config interventions

The dataset (`brbk/clinical_trials_history`, CC-BY-NC-4.0, gated) publishes several configs.
`core` was downloaded by the Step 2 spike. `interventions` holds the interventions of every
version, one row per (nct_id, nct_version, intervention): about 92 MB (ADR 0020). Files go to
data/raw/history/<revision>/<config>/, which is gitignored; the raw files are never
redistributed. HF_TOKEN comes from the environment or .env and is never printed.
"""

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from trialpulse.cli import RefusedError, run
from trialpulse.config import REPO_ROOT, ProjectConfig, Secrets, load_project_config

RAW_HISTORY_DIR = REPO_ROOT / "data" / "raw" / "history"
KNOWN_CONFIGS: tuple[str, ...] = ("core", "interventions")
Downloader = Callable[..., Any]


def config_dir(raw_dir: Path, revision: str, config_name: str) -> Path:
    return raw_dir / revision / config_name


def config_glob(raw_dir: Path, revision: str, config_name: str) -> str:
    return (config_dir(raw_dir, revision, config_name) / "*.parquet").as_posix()


def download_config(
    cfg: ProjectConfig,
    config_name: str,
    raw_dir: Path,
    token: str | None,
    downloader: Downloader | None = None,
) -> dict[str, Any]:
    """Download the Parquet files of one config at the pinned revision (resumable)."""
    if cfg.dataset.revision is None:
        raise RefusedError("config/project.yaml pins no dataset revision yet (Step 2 pins it)")
    if config_name not in KNOWN_CONFIGS:
        raise RefusedError(f"unknown config {config_name!r}; known: {', '.join(KNOWN_CONFIGS)}")
    if downloader is None:
        from huggingface_hub import snapshot_download

        downloader = snapshot_download
    revision = cfg.dataset.revision
    try:
        downloader(
            repo_id=cfg.dataset.repo_id,
            repo_type="dataset",
            revision=revision,
            allow_patterns=[f"{config_name}/*.parquet"],
            local_dir=raw_dir / revision,
            token=token,
        )
    except Exception as exc:
        if type(exc).__name__ == "GatedRepoError":
            raise RefusedError(
                "access to the gated dataset has not been granted to this Hugging Face "
                "account, or HF_TOKEN is not set"
            ) from exc
        raise
    files = sorted(config_dir(raw_dir, revision, config_name).glob("*.parquet"))
    if not files:
        raise RefusedError(f"the revision {revision} holds no file for the config {config_name}")
    return {
        "config": config_name,
        "revision": revision,
        "files": len(files),
        "bytes": sum(f.stat().st_size for f in files),
        "path": config_dir(raw_dir, revision, config_name).as_posix(),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download a config of the pinned dataset.")
    parser.add_argument("--config", required=True, choices=KNOWN_CONFIGS)
    parser.add_argument("--raw-dir", type=Path, default=RAW_HISTORY_DIR)
    args = parser.parse_args(argv)
    secret = Secrets().hf_token
    result = download_config(
        load_project_config(),
        args.config,
        args.raw_dir,
        secret.get_secret_value() if secret else None,
    )
    print(
        f"{result['config']} at {result['revision']}: {result['files']} files, "
        f"{result['bytes']:,} bytes in {result['path']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
