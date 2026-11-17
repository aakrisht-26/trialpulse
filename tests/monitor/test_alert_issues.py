"""The alert issues of ADR 0081 (`monitor.alert_issues`): a comment only when an alert's state
or details change, never twice for the same state, on an in-memory GitHub."""

import json
import subprocess
import sys
from collections.abc import Sequence
from typing import Any

import pytest

from trialpulse.config import REPO_ROOT
from trialpulse.monitor import alert_issues as ai

URL = "https://example.invalid/run/1"
BOT = {"login": "app/github-actions"}


class FakeGitHub:
    """The four GitHub CLI calls the helper makes, on issues held in memory."""

    def __init__(self) -> None:
        self.issues: list[dict[str, Any]] = []
        self.writes: list[tuple[str, str]] = []  # (verb, title)

    def __call__(self, args: Sequence[str]) -> str:
        verb = args[1]
        if verb == "list":
            return json.dumps([{"number": i["number"], "title": i["title"]}
                               for i in self.issues if i["state"] == "open"])  # fmt: skip
        if verb == "view":
            issue = next(i for i in self.issues if i["number"] == int(args[2]))
            return json.dumps({k: issue[k] for k in ("number", "body", "author", "comments")})
        if verb == "create":
            number = len(self.issues) + 1
            self.issues.append({"number": number, "title": args[3], "body": args[5],
                                "author": BOT, "comments": [], "state": "open"})  # fmt: skip
            self.writes.append(("create", args[3]))
            return ""
        if verb == "comment":
            issue = next(i for i in self.issues if i["number"] == int(args[2]))
            issue["comments"].append({"author": BOT, "body": args[4]})
            self.writes.append(("comment", issue["title"]))
            return ""
        raise AssertionError(args)

    def issue(self, kind: str) -> dict[str, Any]:
        return next(i for i in self.issues if i["title"] == ai.title(kind))


def states(**raised: str | None) -> dict[str, dict[str, Any]]:
    """Every kind not raised, except those given, raised with that key."""
    return {k: {"raised": k in raised, "key": raised.get(k) or "",
                "detail": f"detail of {k}" if k in raised else ""} for k in ai.KINDS}  # fmt: skip


def run_once(github: FakeGitHub, current: dict[str, Any]) -> None:
    env = {"ALERT_STATES": json.dumps(current), "RUN_URL": URL}
    assert ai.main(env, github) == 0


def test_a_lasting_alert_opens_one_issue_and_never_comments_again() -> None:
    github = FakeGitHub()
    for _ in range(6):  # three days, two runs a day
        run_once(github, states(drift="top features enrollment_count"))
    assert github.writes == [("create", "Alert: drift")]


def test_raised_changed_cleared_and_raised_again_each_comment_once() -> None:
    github = FakeGitHub()
    days = (
        [states(drift="top features enrollment_count")] * 3
        + [states(drift="top features sponsor_class")] * 3  # a different top feature
        + [states()] * 3  # cleared
        + [states(drift="top features sponsor_class")] * 3  # raised again, same details
    )
    for current in days:
        run_once(github, current)
    assert github.writes == [("create", "Alert: drift")] + [("comment", "Alert: drift")] * 3
    comments = [c["body"] for c in github.issue("drift")["comments"]]
    assert "changed the alert's details drift" in comments[0]
    assert "detail of drift" in comments[0]  # the new details are written
    assert "the alert drift cleared" in comments[1]
    assert "raised the alert again after it cleared drift" in comments[2]


def test_each_kind_keeps_its_own_issue_and_state() -> None:
    github = FakeGitHub()
    run_once(github, states(watermark_lag="data as of 2026-11-13", quarantine=""))
    run_once(github, states(watermark_lag="data as of 2026-11-13"))  # quarantine cleared
    run_once(github, states(watermark_lag="data as of 2026-11-14"))  # still stale, a new date
    assert github.writes == [
        ("create", "Alert: watermark_lag"),
        ("create", "Alert: quarantine"),
        ("comment", "Alert: quarantine"),  # cleared
        ("comment", "Alert: watermark_lag"),  # a new stale date
    ]


def test_a_closed_issue_is_not_written_to_and_a_new_raise_opens_another() -> None:
    github = FakeGitHub()
    run_once(github, states(quarantine=""))
    github.issue("quarantine")["state"] = "closed"
    run_once(github, states())  # nothing open: a clear writes nothing
    run_once(github, states(quarantine=""))
    assert github.writes == [("create", "Alert: quarantine")] * 2


@pytest.mark.parametrize(
    ("before", "current", "expected"),
    [
        (None, {"raised": False, "key": ""}, None),
        (None, {"raised": True, "key": "k"}, "create"),
        ({"raised": True, "key": "k"}, {"raised": True, "key": "k"}, None),
        ({"raised": True, "key": "k"}, {"raised": True, "key": "j"}, "comment"),
        ({"raised": False, "key": ""}, {"raised": True, "key": "k"}, "comment"),
        ({"raised": True, "key": "k"}, {"raised": False, "key": ""}, "comment"),
        ({"raised": False, "key": ""}, {"raised": False, "key": ""}, None),
    ],
)
def test_the_decision_table(
    before: dict[str, Any] | None, current: dict[str, Any], expected: str | None
) -> None:
    issue = None if before is None else {
        "number": 7, "author": BOT, "comments": [],
        "body": "opened\n\n" + ai.marker(before["raised"], before["key"])}  # fmt: skip
    action = ai.decide("drift", current, issue, URL)
    assert (action.verb if action else None) == expected
    if action is not None:
        assert ai.MARKER.search(action.body)  # every write records the state it saw
        assert action.issue == (None if issue is None else 7)


def test_an_issue_from_before_the_helper_is_recorded_once_then_quiet() -> None:
    """The old job's issues carry no marker: they count as raised with an unknown key."""
    github = FakeGitHub()
    old_body = "Run x raised the alert drift (ADR 0081); its log has the numbers."
    github.issues.append({"number": 1, "title": "Alert: drift", "author": BOT, "comments": [],
                          "state": "open", "body": old_body})  # fmt: skip
    for _ in range(3):
        run_once(github, states(drift="share over the threshold"))
    assert github.writes == [("comment", "Alert: drift")]


def test_a_marker_written_by_anyone_else_is_ignored() -> None:
    issue = {"number": 3, "author": BOT, "body": "x\n" + ai.marker(True, "k"),
             "comments": [{"author": {"login": "someone"}, "body": ai.marker(False, "")},
                          {"author": BOT, "body": "a note with no marker"}]}  # fmt: skip
    assert ai.last_recorded(issue) == {"raised": True, "key": "k"}
    assert ai.decide("drift", {"raised": True, "key": "k"}, issue, URL) is None


@pytest.mark.parametrize("raw", ["", "not json", "[]", "null"])
def test_missing_or_unreadable_states_change_nothing(raw: str) -> None:
    github = FakeGitHub()
    github.issues.append({"number": 1, "title": "Alert: drift", "author": BOT, "comments": [],
                          "state": "open", "body": ai.marker(True, "k")})  # fmt: skip
    assert ai.main({"ALERT_STATES": raw, "RUN_URL": URL}, github) == 0
    assert github.writes == []  # never read as a cleared alert


def test_an_issue_with_a_similar_title_is_not_used() -> None:
    github = FakeGitHub()
    github.issues.append({"number": 1, "title": "Alert: drift (old)", "author": BOT,
                          "comments": [], "state": "open", "body": ""})  # fmt: skip
    run_once(github, states(drift="k"))
    assert github.writes == [("create", "Alert: drift")]


def test_the_helper_loads_the_standard_library_only() -> None:
    """The alerts job installs nothing: the module may import nothing outside the standard
    library but the trialpulse package itself."""
    code = (
        "import sys; sys.path.insert(0, 'src'); before = set(sys.modules); "
        "import trialpulse.monitor.alert_issues; "
        "new = {m.split('.')[0] for m in set(sys.modules) - before}; "
        "print(sorted(new - set(sys.stdlib_module_names) - {'trialpulse'}))"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                         check=True, cwd=REPO_ROOT)  # fmt: skip
    assert out.stdout.strip() == "[]"
