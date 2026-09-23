"""The test lock (CLAUDE.md Section 10, ADR 0004).

Locked origins (roles "test" and "stress") are evaluated only when every condition holds:

1. the run was started with --unlock-test;
2. an annotated git tag prereg-v1 exists (lightweight tags are refused);
3. in the tagged commit, docs/preregistration.md has a "Registered: YYYY-MM-DD" line and
   no placeholder text;
4. the tagged commit is an ancestor of HEAD.

The tag object hash and the tagged commit hash are returned so the caller records them
with the results. This module only reads git state; it never creates tags.
"""

import datetime as dt
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

TAG = "prereg-v1"
PREREG_PATH = "docs/preregistration.md"
LOCKED_ROLES = frozenset({"test", "stress"})

# Placeholder text that must not appear in a registration (ADR 0004).
PLACEHOLDER_PATTERNS: tuple[str, ...] = (
    r"not yet registered",
    r"\bTODO\b",
    r"\bTBD\b",
    r"\bTBC\b",
    r"\bFIXME\b",
    r"\bXXX\b",
    r"placeholder",
    r"\bfill in\b",
    r"lorem ipsum",
    r"<[A-Za-z_][A-Za-z0-9_ -]*>",
)
_REGISTERED = re.compile(r"^\s*Registered:\s*(\S+)\s*$", re.MULTILINE)


class TestLockError(RuntimeError):
    """Locked origins were requested but a lock condition is not met."""

    __test__ = False  # not a pytest test class


@dataclass(frozen=True)
class Unlock:
    tag: str
    tag_object: str
    commit: str
    registered: dt.date


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def find_placeholders(text: str) -> list[str]:
    """Every placeholder match in text, case-insensitive."""
    found: list[str] = []
    for pattern in PLACEHOLDER_PATTERNS:
        found += [m.group(0) for m in re.finditer(pattern, text, flags=re.IGNORECASE)]
    return found


def registered_date(text: str) -> dt.date:
    match = _REGISTERED.search(text)
    if match is None:
        raise TestLockError(f"{PREREG_PATH} at {TAG} has no 'Registered: YYYY-MM-DD' line")
    try:
        return dt.date.fromisoformat(match.group(1))
    except ValueError as exc:
        raise TestLockError(f"'Registered:' value {match.group(1)!r} is not a valid date") from exc


def check_unlock(repo: Path, unlock_flag: bool) -> Unlock:
    """Return the unlock record, or raise TestLockError naming the failed condition."""
    if not unlock_flag:
        raise TestLockError("locked origins need the --unlock-test flag")
    kind = _git(repo, "cat-file", "-t", TAG)
    if kind.returncode != 0:
        raise TestLockError(f"git tag {TAG} does not exist")
    if kind.stdout.strip() != "tag":
        raise TestLockError(f"git tag {TAG} is a lightweight tag; an annotated tag is required")
    show = _git(repo, "show", f"{TAG}:{PREREG_PATH}")
    if show.returncode != 0:
        raise TestLockError(f"{PREREG_PATH} is not in the commit tagged {TAG}")
    text = show.stdout
    registered = registered_date(text)
    placeholders = find_placeholders(text)
    if placeholders:
        raise TestLockError(
            f"{PREREG_PATH} at {TAG} contains placeholder text: {sorted(set(placeholders))}"
        )
    commit = _git(repo, "rev-parse", f"{TAG}^{{commit}}").stdout.strip()
    ancestor = _git(repo, "merge-base", "--is-ancestor", commit, "HEAD")
    if ancestor.returncode != 0:
        raise TestLockError(f"the commit tagged {TAG} is not an ancestor of HEAD")
    tag_object = _git(repo, "rev-parse", TAG).stdout.strip()
    return Unlock(tag=TAG, tag_object=tag_object, commit=commit, registered=registered)


def require_unlock(roles: set[str], repo: Path, unlock_flag: bool) -> Unlock | None:
    """None when only development origins are requested; otherwise check_unlock."""
    if not roles & LOCKED_ROLES:
        return None
    return check_unlock(repo, unlock_flag)
