"""Downloading a config of the pinned dataset: no network, a stand-in for the hub client."""

from pathlib import Path
from typing import Any

import httpx
import pytest
from huggingface_hub.errors import GatedRepoError

from trialpulse.cli import RefusedError
from trialpulse.config import load_project_config
from trialpulse.ingest import history


def test_a_config_is_downloaded_to_its_own_folder_at_the_pinned_revision(tmp_path: Path) -> None:
    cfg = load_project_config()
    calls: list[dict[str, Any]] = []

    def downloader(**kwargs: Any) -> None:
        calls.append(kwargs)
        folder = Path(kwargs["local_dir"]) / "interventions"
        folder.mkdir(parents=True)
        (folder / "part-0.parquet").write_bytes(b"abc")
        (folder / "part-1.parquet").write_bytes(b"defgh")

    result = history.download_config(cfg, "interventions", tmp_path, "a-token", downloader)
    assert calls == [
        {
            "repo_id": "brbk/clinical_trials_history",
            "repo_type": "dataset",
            "revision": cfg.dataset.revision,
            "allow_patterns": ["interventions/*.parquet"],
            "local_dir": tmp_path / str(cfg.dataset.revision),
            "token": "a-token",
        }
    ]
    assert result["files"] == 2
    assert result["bytes"] == 8
    assert result["revision"] == cfg.dataset.revision
    expected = tmp_path / str(cfg.dataset.revision) / "interventions"
    assert result["path"] == expected.as_posix()
    glob = history.config_glob(tmp_path, str(cfg.dataset.revision), "interventions")
    assert glob == (expected / "*.parquet").as_posix()
    assert "a-token" not in str(result)  # the token is passed on and never reported


def test_refusals(tmp_path: Path) -> None:
    cfg = load_project_config()
    with pytest.raises(RefusedError, match="unknown config"):
        history.download_config(cfg, "locations", tmp_path, None, lambda **_: None)
    with pytest.raises(RefusedError, match="holds no file for the config interventions"):
        history.download_config(cfg, "interventions", tmp_path, None, lambda **_: None)

    def gated(**_: Any) -> None:
        request = httpx.Request("GET", "https://huggingface.co/api/datasets/x")
        raise GatedRepoError("403", response=httpx.Response(403, request=request))

    with pytest.raises(RefusedError, match="access to the gated dataset"):
        history.download_config(cfg, "interventions", tmp_path, None, gated)

    def broken(**_: Any) -> None:
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):  # anything else is not an expected refusal
        history.download_config(cfg, "interventions", tmp_path, None, broken)

    dataset = cfg.dataset.model_copy(update={"revision": None})
    unpinned = cfg.model_copy(update={"dataset": dataset})
    with pytest.raises(RefusedError, match="pins no dataset revision"):
        history.download_config(unpinned, "interventions", tmp_path, None, lambda **_: None)


def test_the_dataset_is_never_downloaded_on_a_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0040: under GitHub Actions the download refuses before anything else."""
    from trialpulse.cli import RefusedError
    from trialpulse.config import load_project_config
    from trialpulse.ingest.history import download_config

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(RefusedError, match="never read on a GitHub runner"):
        download_config(load_project_config(), "core", tmp_path, None, downloader=None)
