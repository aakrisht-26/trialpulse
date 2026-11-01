"""The workflows never bring the gated dataset to a runner (ADR 0040), and the daily job's
schedule and failure path are as ADR 0045 says.

- Every module a workflow starts with `python -m` is imported in a fresh interpreter, and
  none of the modules that read the dataset may load with it.
- No workflow names a Hugging Face address, the raw data folder or the token.
- Every action is pinned to a full commit SHA, and only known actions are used.
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from trialpulse.config import REPO_ROOT

WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
FORBIDDEN_MODULES = (
    "trialpulse.ingest.history",
    "trialpulse.warehouse.build",
    "trialpulse.features.build",
    "huggingface_hub",
)
FORBIDDEN_TEXT = ("hf://", "huggingface.co", "data/raw", "HF_TOKEN")
ACTIONS = ("actions/checkout", "astral-sh/setup-uv")


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def _modules() -> set[str]:
    found = set()
    for path in WORKFLOWS:
        found |= set(re.findall(r"python -m ([\w.]+)", path.read_text(encoding="utf-8")))
    return found


def test_the_live_workflows_start_modules() -> None:
    assert {"trialpulse.live.daily", "trialpulse.live.registry", "trialpulse.live.timing"} <= (
        _modules()
    )


@pytest.mark.parametrize("module", sorted(_modules()))
def test_no_workflow_module_loads_a_dataset_module(module: str) -> None:
    code = (
        f"import importlib, sys; importlib.import_module({module!r}); "
        f"print(','.join(m for m in {FORBIDDEN_MODULES!r} if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True, cwd=REPO_ROOT)  # fmt: skip
    assert out.stdout.strip() == "", f"{module} loads {out.stdout.strip()}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_names_the_dataset_or_its_token(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for word in FORBIDDEN_TEXT:
        assert word not in text, f"{path.name} names {word}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_known_and_pinned_to_a_commit(path: Path) -> None:
    for uses in re.findall(r"uses: (\S+)", path.read_text(encoding="utf-8")):
        name, _, ref = uses.partition("@")
        assert name in ACTIONS, uses
        assert re.fullmatch(r"[0-9a-f]{40}", ref), uses


def test_the_daily_job_has_two_crons_and_a_separate_failure_job() -> None:
    daily = _load(REPO_ROOT / ".github" / "workflows" / "daily.yml")
    on = daily[True] if True in daily else daily["on"]  # YAML 1.1 reads "on" as true
    assert [c["cron"] for c in on["schedule"]] == ["30 21 * * *", "30 0 * * *"]
    assert daily["permissions"] == {"contents": "read"}
    jobs = daily["jobs"]
    assert jobs["daily"]["timeout-minutes"] == 90
    assert "permissions" not in jobs["daily"]
    notify = jobs["notify"]
    assert notify["needs"] == "daily"
    assert "always()" in notify["if"]
    assert "needs.daily.result == 'failure'" in notify["if"]
    assert "needs.daily.result == 'cancelled'" in notify["if"]
    assert "refs/heads/main" in notify["if"]
    assert notify["permissions"] == {"contents": "read", "issues": "write"}
    assert daily["env"] == {"HF_HUB_OFFLINE": "1", "UV_NO_SYNC": "1"}
    assert daily["concurrency"]["cancel-in-progress"] is False


def test_ci_requires_postgres_for_the_database_tests() -> None:
    """Review finding 4: in CI an unreachable Postgres fails the database tests."""
    ci = _load(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    (tests,) = [s for s in ci["jobs"]["checks"]["steps"] if s.get("name") == "Tests"]
    assert tests["env"]["TRIALPULSE_REQUIRE_POSTGRES"] == "1"


def test_a_missing_postgres_skips_locally_and_fails_where_it_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "root_conftest", REPO_ROOT / "tests" / "conftest.py"
    )
    assert spec is not None
    assert spec.loader is not None
    root = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(root)
    REQUIRE_POSTGRES, no_postgres = root.REQUIRE_POSTGRES, root.no_postgres  # noqa: N806

    # Any outcome is caught and its type checked, so a helper that skipped where it must
    # fail could not turn this test itself into a skip.
    monkeypatch.delenv(REQUIRE_POSTGRES, raising=False)
    with pytest.raises(BaseException) as local:  # noqa: PT011 (the type is checked below)
        no_postgres("no Postgres answers")
    assert local.type is pytest.skip.Exception
    monkeypatch.setenv(REQUIRE_POSTGRES, "1")
    with pytest.raises(BaseException) as ci:  # noqa: PT011
        no_postgres("no Postgres answers")
    assert ci.type is pytest.fail.Exception
    assert "TRIALPULSE_REQUIRE_POSTGRES is set" in str(ci.value)
