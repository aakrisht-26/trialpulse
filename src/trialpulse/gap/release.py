"""The newer release of the dataset, read once for the gap (ADR 0041, decision 1).

    uv run python -m trialpulse.gap.release

Laptop only (ADR 0040). It checks that the tag in `gap_read` of config/project.yaml still
resolves to its pinned commit (tags can move), then downloads only the configs named there
(`core` and `interventions`) at that commit into data/raw/history/<commit>/, which is
gitignored, with HF_TOKEN from .env, never printed. The card at the commit is saved beside
them for its license and terms.
"""

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from trialpulse.cli import RefusedError, run
from trialpulse.config import GapReadConfig, ProjectConfig, Secrets, load_project_config
from trialpulse.ingest.history import RAW_HISTORY_DIR, refuse_on_a_runner


def gap_config(cfg: ProjectConfig) -> GapReadConfig:
    if cfg.gap_read is None:
        raise RefusedError("config/project.yaml has no gap_read section (ADR 0041)")
    return cfg.gap_read


def release_dir(cfg: ProjectConfig, raw_dir: Path = RAW_HISTORY_DIR) -> Path:
    return raw_dir / gap_config(cfg).commit


def release_glob(cfg: ProjectConfig, config_name: str, raw_dir: Path = RAW_HISTORY_DIR) -> str:
    return (release_dir(cfg, raw_dir) / config_name / "*.parquet").as_posix()


def check_tag(gap: GapReadConfig, api: Any) -> None:
    """Refuse when the tag no longer resolves to the pinned commit."""
    refs = api.list_repo_refs(gap.repo_id, repo_type="dataset")
    found = {t.name: t.target_commit for t in refs.tags}
    if found.get(gap.tag) != gap.commit:
        raise RefusedError(f"the tag {gap.tag} no longer resolves to the pinned commit; pin "
                           "the release again (ADR 0041)")  # fmt: skip


def download(
    cfg: ProjectConfig,
    raw_dir: Path = RAW_HISTORY_DIR,
    token: str | None = None,
    api: Any = None,
    downloader: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Download the pinned configs and the card at the pinned commit (see the module text)."""
    refuse_on_a_runner()
    gap = gap_config(cfg)
    if api is None or downloader is None:
        from huggingface_hub import HfApi, snapshot_download

        api = api or HfApi(token=token)
        downloader = downloader or snapshot_download
    check_tag(gap, api)
    folder = release_dir(cfg, raw_dir)
    downloader(repo_id=gap.repo_id, repo_type="dataset", revision=gap.commit,
               allow_patterns=[*(f"{c}/*.parquet" for c in gap.configs), "README.md"],
               local_dir=folder, token=token)  # fmt: skip
    out: dict[str, Any] = {"tag": gap.tag, "commit": gap.commit, "configs": {}}
    for name in gap.configs:
        files = sorted((folder / name).glob("*.parquet"))
        if not files:
            raise RefusedError(f"the pinned commit holds no file for the config {name}")
        out["configs"][name] = {"files": len(files),
                                "bytes": sum(f.stat().st_size for f in files)}  # fmt: skip
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download the gap's release (ADR 0041).")
    parser.parse_args(argv)
    token = Secrets().hf_token
    result = download(load_project_config(), token=token.get_secret_value() if token else None)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
