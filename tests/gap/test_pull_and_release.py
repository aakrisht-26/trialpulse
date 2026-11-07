"""The gap's API pull and release download (`gap.pull`, `gap.release`, ADR 0041) with stand-ins
for API v2 and Hugging Face: what is pulled from when, what is kept, the pinned commit, and
the refusals on a runner. The pages are the recorded fixtures of tests/live."""

import datetime as dt
import gzip
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from trialpulse.cli import RefusedError
from trialpulse.config import ProjectConfig, load_project_config
from trialpulse.gap import pull, release
from trialpulse.ingest.ctgov_api import STUDIES_URL, VERSION_URL

FIXTURES = Path(__file__).parent.parent / "live" / "fixtures"
CFG = load_project_config()
SECRET = b"a throwaway key for the gap tests"


class PagesApi:
    """API v2 in memory: the version, then the two recorded delta pages."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        params = dict(params or {})
        self.requests.append({"url": url, **params})
        if url == VERSION_URL:
            return json.loads((FIXTURES / "version.json").read_text(encoding="utf-8"))
        assert url == STUDIES_URL
        name = "delta_page_2.json" if "pageToken" in params else "delta_page_1.json"
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_the_pull_starts_at_the_cutoff_minus_the_overlap_and_keeps_what_it_pulled(
    tmp_path: Path,
) -> None:
    api = PagesApi()
    summary = pull.run_pull(api, CFG, SECRET, tmp_path)
    assert CFG.dataset.cutoff is not None
    assert summary["since"] == (CFG.dataset.cutoff - dt.timedelta(days=3)).isoformat()
    first = next(r for r in api.requests if r["url"] == STUDIES_URL)
    assert summary["since"] in str(first["filter.advanced"])
    out = tmp_path / "api" / summary["data_date"]
    with gzip.open(out / "studies.jsonl.gz", "rt", encoding="utf-8") as handle:
        kept = [json.loads(line) for line in handle]
    assert len(kept) == summary["records"] == summary["api_total"]
    assert summary["pages"] == 2
    assert all("contactsLocationsModule" not in s.get("protocolSection", {}) for s in kept)
    canonical = pd.read_parquet(out / "canonical.parquet")
    compact = pd.read_parquet(out / "compact.parquet")
    assert len(canonical) == len(compact) == summary["converted"]
    assert summary["quarantined"] == 0
    assert (out / "texts.parquet").is_file()
    assert json.loads((out / "summary.json").read_text(encoding="utf-8")) == summary


def test_the_pull_refuses_on_a_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(RefusedError, match="ADR 0040"):
        pull.run_pull(PagesApi(), CFG, SECRET, tmp_path)


def _refs(tag: str, commit: str) -> Any:
    return SimpleNamespace(
        list_repo_refs=lambda repo, repo_type: SimpleNamespace(
            tags=[SimpleNamespace(name=tag, target_commit=commit)]
        )
    )


def _cfg() -> ProjectConfig:
    assert CFG.gap_read is not None
    return CFG


def test_the_release_downloads_the_two_configs_at_the_pinned_commit(tmp_path: Path) -> None:
    gap = _cfg().gap_read
    assert gap is not None
    seen: dict[str, Any] = {}

    def downloader(**kwargs: Any) -> None:
        seen.update(kwargs)
        for name in gap.configs:
            folder = Path(kwargs["local_dir"]) / name
            folder.mkdir(parents=True)
            (folder / "part-0.parquet").write_bytes(b"x")

    out = release.download(_cfg(), tmp_path, None, _refs(gap.tag, gap.commit), downloader)
    assert seen["revision"] == gap.commit
    assert seen["allow_patterns"] == ["core/*.parquet", "interventions/*.parquet", "README.md"]
    assert out["configs"] == {"core": {"files": 1, "bytes": 1},
                              "interventions": {"files": 1, "bytes": 1}}  # fmt: skip


def test_a_tag_that_moved_or_a_runner_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gap = _cfg().gap_read
    assert gap is not None
    never = lambda **kwargs: pytest.fail("downloaded")  # noqa: E731
    with pytest.raises(RefusedError, match="no longer resolves"):
        release.download(_cfg(), tmp_path, None, _refs(gap.tag, "0" * 40), never)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(RefusedError, match="ADR 0040"):
        release.download(_cfg(), tmp_path, None, _refs(gap.tag, gap.commit), never)


def test_the_pinned_commit_is_a_full_hash() -> None:
    from pydantic import ValidationError

    from trialpulse.config import GapReadConfig

    with pytest.raises(ValidationError):
        GapReadConfig(repo_id="r", tag="v1", commit="abc123", configs=("core",))
