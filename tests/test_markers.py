"""The words of the amendment markers and the rule for a trial's first posting (ADR 0043,
amended 2026-11-18), shared by the API's timeline and the risk memo."""

import datetime as dt

from trialpulse import markers as mk
from trialpulse.live.contract import MARKER_GROUPS

D = dt.date


def test_every_marker_group_has_a_name_and_no_other() -> None:
    assert set(mk.NAMES) == set(MARKER_GROUPS)
    assert mk.NAMES[mk.DATES] == "dates or status verification"
    assert "status_verified_date" in MARKER_GROUPS[mk.DATES]  # why the dates group says so


def test_a_code_is_named_by_its_groups() -> None:
    assert mk.names(1) == ["recruitment status"]
    assert mk.names(2 | 4) == ["dates or status verification", "enrollment"]
    assert mk.names(32) == ["intervention types or title phase"]
    assert mk.names(64) == ["record"]


def test_the_marker_on_the_first_post_date_is_the_first_posting() -> None:
    read = mk.read([D(2026, 3, 2), D(2025, 1, 10)], [6, 1], D(2025, 1, 10))
    assert [(m.post_date, m.first_posted, m.changed()) for m in read] == [
        (D(2025, 1, 10), True, []),
        (D(2026, 3, 2), False, ["dates or status verification", "enrollment"]),
    ]
    assert [m.post_date for m in mk.changes(read)] == [D(2026, 3, 2)]


def test_trimmed_markers_are_shown_as_stored() -> None:
    """The oldest stored marker is a status change, not the first posting (the markers before
    it were trimmed): it is never renamed, and it counts."""
    read = mk.read([D(2025, 6, 2), D(2025, 9, 1)], [1, 2], D(2024, 2, 1))
    assert [(m.first_posted, m.changed()) for m in read] == [
        (False, ["recruitment status"]), (False, ["dates or status verification"])
    ]  # fmt: skip
    assert len(mk.changes(read)) == 2


def test_without_a_first_post_date_every_marker_is_named_by_its_code() -> None:
    read = mk.read([D(2025, 1, 10)], [1], None)
    assert [(m.first_posted, m.changed()) for m in read] == [(False, ["recruitment status"])]


def test_a_status_change_on_another_day_is_never_the_first_posting() -> None:
    """A reopened trial's marker has the code of a first version (live.state) on a later day."""
    read = mk.read([D(2024, 5, 1), D(2026, 1, 5)], [1, 1], D(2024, 5, 1))
    assert [m.changed() for m in read] == [[], ["recruitment status"]]
