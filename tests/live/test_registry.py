"""Attaching the ADR 0034 decisions to the champion's run (`live.registry`), with a stand-in
for the MLflow client: only the file the day-0 check validated, only on the laptop."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from trialpulse.cli import RefusedError
from trialpulse.live import registry


class FakeClient:
    def __init__(self) -> None:
        self.logged: list[tuple[str, str]] = []

    def get_model_version_by_alias(self, name: str, alias: str) -> Any:
        assert (name, alias) == (registry.MODEL_NAME, "champion")
        return SimpleNamespace(version="1", run_id="run-of-version-1")

    def log_artifact(self, run_id: str, path: str) -> None:
        self.logged.append((run_id, Path(path).name))


def test_the_validated_decisions_file_is_logged_to_the_champions_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    path = tmp_path / registry.DECISIONS
    path.write_bytes(b"decisions")
    monkeypatch.setattr(registry, "DECISIONS_SHA256", registry.sha256_of(path))
    client = FakeClient()
    attached = registry.attach_decisions(path, client=client)
    assert attached == {"model": registry.MODEL_NAME, "version": "1",
                        "run_id": "run-of-version-1"}  # fmt: skip
    assert client.logged == [("run-of-version-1", registry.DECISIONS)]


def test_another_file_or_a_runner_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    path = tmp_path / registry.DECISIONS
    path.write_bytes(b"edited decisions")
    client = FakeClient()
    with pytest.raises(RefusedError, match="not the file the day-0 check validated"):
        registry.attach_decisions(path, client=client)
    with pytest.raises(RefusedError, match=r"no step13_checks.json"):
        registry.attach_decisions(tmp_path / "other.json", client=client)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(RefusedError, match="never on a runner"):
        registry.attach_decisions(path, client=client)
    assert client.logged == []
