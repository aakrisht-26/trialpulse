"""The alert issues of ADR 0081: one issue per alert kind, commented only when its state changes.

    PYTHONPATH=src python3 -m trialpulse.monitor.alert_issues   (daily.yml's alerts job)

The alerts step writes `alert_states` (`monitor.alerts.states`): for every kind, whether it is
raised and a key of its details. For each kind this compares that with the last state recorded
on the kind's open issue ("Alert: <kind>") and acts only on a change:

- raised, no open issue: open one;
- raised, its open issue last recorded the same key: nothing;
- raised, the last record was raised with another key: a comment, details changed;
- raised, the last record was cleared: a comment, raised again;
- not raised, the last record was raised: a comment, cleared (the issue stays open for a
  person to close; while it is open, a later raise is recorded on it);
- not raised, no open issue or the last record was cleared: nothing.

Each issue or comment it writes ends with a hidden marker holding the state it records, and only
markers written by GitHub Actions count, so a comment from anyone else cannot change the
state. An issue without a marker (opened before this helper) counts as raised with an unknown
key. A missing or unreadable `alert_states` does nothing: a step that could not read the
monitoring tables never reads as a cleared alert.

It uses the standard library and the GitHub CLI only, so the job needs no installed
dependencies; it never prints a token.
"""

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

KINDS = ("watermark_lag", "quarantine", "drift", "matured_auc")
MARKER = re.compile(r"<!-- trialpulse-alert-state (\{.*?\}) -->")
BOT_LOGINS = frozenset({"github-actions", "github-actions[bot]", "app/github-actions"})
Gh = Callable[[Sequence[str]], str]


@dataclass(frozen=True)
class Action:
    """What to write for one kind: a new issue or a comment on its open one."""

    kind: str
    verb: str  # "create" or "comment"
    body: str
    issue: int | None = None


def title(kind: str) -> str:
    return f"Alert: {kind}"


def marker(raised: bool, key: str) -> str:
    return ("<!-- trialpulse-alert-state "
            + json.dumps({"raised": raised, "key": key}, sort_keys=True) + " -->")  # fmt: skip


def last_recorded(issue: Mapping[str, Any]) -> dict[str, Any] | None:
    """The last state recorded on an issue by GitHub Actions (its body, then its comments in
    order), or None when it holds no such marker."""
    texts = [issue, *list(issue.get("comments") or [])]
    found: dict[str, Any] | None = None
    for item in texts:
        if (item.get("author") or {}).get("login") not in BOT_LOGINS:
            continue
        for raw in MARKER.findall(item.get("body") or ""):
            try:
                value = json.loads(raw)
            except ValueError:
                continue
            if isinstance(value, dict) and isinstance(value.get("raised"), bool):
                found = {"raised": value["raised"], "key": str(value.get("key", ""))}
    return found


def decide(
    kind: str, current: Mapping[str, Any], issue: Mapping[str, Any] | None, run_url: str
) -> Action | None:
    """The action for one kind, given its current state and its open issue (None if none)."""
    raised, key = bool(current.get("raised")), str(current.get("key") or "")
    detail = str(current.get("detail") or "")
    line = f": {detail}" if detail else ""
    if issue is None:
        if not raised:
            return None
        body = (f"Run {run_url} raised the alert {kind} (ADR 0081){line}.\n\n"
                + marker(True, key))  # fmt: skip
        return Action(kind, "create", body)
    before = last_recorded(issue) or {"raised": True, "key": None}  # an issue from before
    number = int(issue["number"])
    if raised:
        if before["raised"] and before["key"] == key:
            return None
        what = ("raised the alert again after it cleared" if not before["raised"]
                else "changed the alert's details")  # fmt: skip
        body = f"Run {run_url} {what} {kind}{line}.\n\n" + marker(True, key)
        return Action(kind, "comment", body, number)
    if not before["raised"]:
        return None
    body = (f"Run {run_url}: the alert {kind} cleared. While this issue is open, a later raise "
            "is recorded here; once it is closed, a later raise opens a new one.\n\n"
            + marker(False, ""))  # fmt: skip
    return Action(kind, "comment", body, number)


def gh(args: Sequence[str]) -> str:
    out = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return out.stdout


def open_issue(kind: str, run: Gh = gh) -> dict[str, Any] | None:
    """The open issue titled exactly "Alert: <kind>", with its body and comments."""
    search = f'in:title "{title(kind)}"'
    listed = json.loads(run(["issue", "list", "--state", "open", "--search", search,
                             "--json", "number,title"]) or "[]")  # fmt: skip
    numbers = sorted(int(i["number"]) for i in listed if i.get("title") == title(kind))
    if not numbers:
        return None
    issue: dict[str, Any] = json.loads(run(["issue", "view", str(numbers[0]), "--json",
                                            "number,body,author,comments"]))  # fmt: skip
    return issue


def apply(action: Action, run: Gh = gh) -> None:
    if action.verb == "create":
        run(["issue", "create", "--title", title(action.kind), "--body", action.body])
    else:
        run(["issue", "comment", str(action.issue), "--body", action.body])


def main(environ: Mapping[str, str] = os.environ, run: Gh = gh) -> int:
    raw = environ.get("ALERT_STATES", "").strip()
    try:
        current = json.loads(raw) if raw else None
    except ValueError:
        current = None
    if not isinstance(current, dict):
        print("no alert states: nothing compared")
        return 0
    run_url = environ.get("RUN_URL", "this run")
    for kind in KINDS:
        if not isinstance(current.get(kind), dict):
            print(f"{kind}: no state")
            continue
        action = decide(kind, current[kind], open_issue(kind, run), run_url)
        if action is None:
            print(f"{kind}: no change")
            continue
        apply(action, run)
        print(f"{kind}: {action.verb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
