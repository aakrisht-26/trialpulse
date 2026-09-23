"""The test lock (ADR 0004). Every repository here is a throwaway one under pytest's
temporary directory; the project repository is never tagged."""

import subprocess
from pathlib import Path

import pytest

from trialpulse.eval.lock import (
    TAG,
    TestLockError,
    check_unlock,
    find_placeholders,
    require_unlock,
)

# The exact header stub committed in Step 1. It must never satisfy the lock.
STEP1_STUB = (
    "# Pre-registration\n\n"
    "Status: not yet registered. Hypotheses with numeric predictions, acceptable ranges and "
    "empty results blocks are drafted and committed in Step 11, before the test lock is "
    "opened (CLAUDE.md Section 10).\n"
)
COMPLETE = (
    "# Pre-registration\n\n"
    "Registered: 2026-10-01\n\n"
    "## H1. M4 beats M1 at L0\n\n"
    "Prediction: AUC difference at 12 months of 0.03; acceptable range 0.01 to 0.06.\n\n"
    "Result:\n"
)


def _git(repo: Path, *args: str) -> str:
    # Hermetic test repos: fixed identity, and no signing even if the machine enables it.
    base = [
        "git",
        "-C",
        str(repo),
        "-c",
        "user.name=Lock Test",
        "-c",
        "user.email=lock-test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "tag.gpgsign=false",
    ]
    return subprocess.run([*base, *args], capture_output=True, text=True, check=True).stdout


def _repo_with(tmp_path: Path, text: str, annotated: bool = True) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "docs" / "preregistration.md").write_text(text, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "prereg")
    if annotated:
        _git(repo, "tag", "-a", TAG, "-m", "registration")
    else:
        _git(repo, "tag", TAG)
    return repo


def test_step1_stub_never_satisfies_the_lock(tmp_path: Path) -> None:
    repo = _repo_with(tmp_path, STEP1_STUB)
    with pytest.raises(TestLockError, match="Registered"):
        check_unlock(repo, unlock_flag=True)
    assert "not yet registered" in find_placeholders(STEP1_STUB)


def test_flag_is_required(tmp_path: Path) -> None:
    repo = _repo_with(tmp_path, COMPLETE)
    with pytest.raises(TestLockError, match="--unlock-test"):
        check_unlock(repo, unlock_flag=False)


def test_missing_tag_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    with pytest.raises(TestLockError, match="does not exist"):
        check_unlock(repo, unlock_flag=True)


def test_lightweight_tag_is_refused(tmp_path: Path) -> None:
    repo = _repo_with(tmp_path, COMPLETE, annotated=False)
    with pytest.raises(TestLockError, match="lightweight"):
        check_unlock(repo, unlock_flag=True)


@pytest.mark.parametrize(
    "extra",
    ["Prediction: TBD", "TODO: add ranges", "Range: <prediction>", "Please fill in later"],
)
def test_placeholders_are_refused(tmp_path: Path, extra: str) -> None:
    repo = _repo_with(tmp_path, COMPLETE + extra + "\n")
    with pytest.raises(TestLockError, match="placeholder"):
        check_unlock(repo, unlock_flag=True)


def test_invalid_registered_date_is_refused(tmp_path: Path) -> None:
    repo = _repo_with(tmp_path, COMPLETE.replace("2026-10-01", "2026-13-01"))
    with pytest.raises(TestLockError, match="not a valid date"):
        check_unlock(repo, unlock_flag=True)


def test_tag_must_be_an_ancestor_of_head(tmp_path: Path) -> None:
    repo = _repo_with(tmp_path, COMPLETE)
    _git(repo, "tag", "-d", TAG)
    _git(repo, "switch", "-q", "-c", "side")
    (repo / "docs" / "preregistration.md").write_text(COMPLETE + "Side note.\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "side registration")
    _git(repo, "tag", "-a", TAG, "-m", "registration on a side branch")
    _git(repo, "switch", "-q", "main")
    with pytest.raises(TestLockError, match="not an ancestor"):
        check_unlock(repo, unlock_flag=True)


def test_complete_registration_unlocks_and_reports_hashes(tmp_path: Path) -> None:
    repo = _repo_with(tmp_path, COMPLETE)
    # Later commits (for example, filling in results) keep the lock satisfied.
    (repo / "docs" / "preregistration.md").write_text(COMPLETE + "0.04\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "results")

    unlock = check_unlock(repo, unlock_flag=True)

    assert unlock.commit == _git(repo, "rev-parse", f"{TAG}^{{commit}}").strip()
    assert unlock.tag_object == _git(repo, "rev-parse", TAG).strip()
    assert unlock.tag_object != unlock.commit
    assert unlock.registered.isoformat() == "2026-10-01"


def test_development_origins_need_no_lock(tmp_path: Path) -> None:
    assert require_unlock({"dev"}, tmp_path, unlock_flag=False) is None
    with pytest.raises(TestLockError):
        require_unlock({"dev", "stress"}, tmp_path, unlock_flag=False)
