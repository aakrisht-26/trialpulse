"""What an amendment marker says (ADR 0043, amended 2026-11-18): one table of words for the
API's timeline and the risk memo, and the one rule for a trial's first posting.

A marker is a post date and a code, the sum of the groups of fields a feature reads that
changed on that date (`live.contract.MARKER_GROUPS`, whose codes these are). A trial's first
version has the code 1, the recruitment status group, although nothing changed: it is the
first posting.
When markers are read, a marker is named "first posted", and left out of every count of
changes, only when its post date equals the trial's first-post date from the live state
(`live.trial_state.study_first_post_date`, which the API reads through the view
`serving.trial_first_posts`). Every other marker is named by its code, so a set of markers
whose oldest is not the first posting (trimmed markers, or a first version posted on another
day) is shown as stored.

The dates group covers the start, primary completion and completion dates (with their
precision and type) and the status verified date, and its code cannot tell them apart, so it
is named "dates or status verification", everywhere (since memo_v5, ADR 0085 amended: one
name per group, in the memo as on the timeline). The status group is named "recruitment
status", as the memo's driver sentences name the field.

This module imports nothing from the project, so the API's image may use it (Step 15).
"""

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

STATUS = 1
DATES = 2
ENROLLMENT = 4
DESIGN = 8
SPONSOR = 16
INTERVENTIONS = 32
NAMES: dict[int, str] = {
    STATUS: "recruitment status",
    DATES: "dates or status verification",
    ENROLLMENT: "enrollment",
    DESIGN: "design",
    SPONSOR: "sponsor",
    INTERVENTIONS: "intervention types or title phase",
}
UNKNOWN = "record"  # a code with no known group (never written, kept for safety)
FIRST_POSTED = "first posted"


@dataclass(frozen=True)
class Marker:
    """One stored marker as it is read: its post date, its code, and whether it is the
    trial's first posting."""

    post_date: dt.date
    kind: int
    first_posted: bool

    def changed(self) -> list[str]:
        """The groups that changed on this date in words; none for the first posting."""
        return [] if self.first_posted else names(self.kind)


def names(kind: int) -> list[str]:
    """The groups of a marker's code in words, in the order of `NAMES`."""
    found = [name for bit, name in NAMES.items() if int(kind) & bit]
    return found or [UNKNOWN]


def read(
    post_dates: Sequence[dt.date], kinds: Sequence[int], first_post_date: dt.date | None
) -> list[Marker]:
    """A trial's stored markers, oldest first. A marker is its first posting only when its
    post date equals `first_post_date`, the trial's first-post date from the live state;
    without that date every marker is named by its code."""
    pairs = sorted(zip(post_dates, kinds, strict=True))
    return [
        Marker(day, int(kind), first_post_date is not None and day == first_post_date)
        for day, kind in pairs
    ]


def changes(markers: Iterable[Marker]) -> list[Marker]:
    """The markers that count as changes: every marker but the first posting."""
    return [m for m in markers if not m.first_posted]
