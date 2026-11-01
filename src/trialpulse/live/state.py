"""State accumulation and event derivation of the live path (ADR 0037).

Each incoming compact record (`trialpulse.live.contract`) is held against what is stored for
its trial: the wide row of `live.trial_state` when the trial can still be scored or counted,
otherwise the trial's thin data in the sponsor file. The decision table of the Step 14
options report (Part 1):

| Record against state | Action |
| --- | --- |
| Unknown trial | A new registration: its values are version 0 |
| Later post date, wide row | A new version: count + 1, post date kept, latest fields updated |
| Later post date, closed trial | Thin data only; a wide row (a reversal) if it reopened |
| Same post date, same hash | An overlap refetch: nothing |
| Same post date, new hash | A replacement: latest fields replaced, count and version 0 kept |
| Earlier post date | Ignored and counted |

A closed trial with no stored hash for the date (its last version came from the dataset)
cannot tell a refetch from a replacement, so the record counts as a refetch.

Every record that is kept (a new trial, a new version, a replacement, a closed trial's new
record) is appended to `live.study_versions`; the sponsor pass reads them all. A change of
the submitted status is an outcome event; a trial that ends leaves `live.trial_state`.

Everything here is pure: no database. `trialpulse.live.store` reads and writes the tables.
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from trialpulse.cohort.rules import CohortRules, completion_ref, lapse_from
from trialpulse.live.contract import marker_code

FIRST_FIELDS: dict[str, str] = {
    "first_start_date": "start_date",
    "first_primary_completion_date": "primary_completion_date",
    "first_completion_date": "completion_date",
    "first_enrollment_count": "enrollment_count",
    "first_enrollment_type": "enrollment_type",
}
NEW_TRIAL, NEW_VERSION, CLOSED, OVERLAP, REPLACEMENT, EARLIER = (
    "new_trial", "new_version", "closed_trial", "overlap", "replacement", "earlier",
)  # fmt: skip
STATUS, LAPSED, REOPENED = "status", "lapsed", "reopened"


@dataclass(frozen=True)
class Wide:
    """One wide row of `live.trial_state`."""

    row: dict[str, Any]  # nct_id, the compact fields and feature_hash
    first: dict[str, Any]
    n_versions: int
    ever_suspended: bool
    recent_post_dates: tuple[dt.date, ...]
    lapse_from: dt.date | None
    reversal: bool

    @property
    def nct_id(self) -> str:
        return str(self.row["nct_id"])

    @property
    def effective_date(self) -> dt.date:
        value: dt.date = self.row["effective_date"]
        return value


@dataclass(frozen=True)
class Closed:
    """What is known of a trial without a wide row, which ended (from the sponsor file and
    the live versions): its last post date, the feature hash of that date if a live version
    holds it, and its last submitted status if a live version holds it."""

    last_post: dt.date
    last_hash: str | None
    last_status: str | None


@dataclass(frozen=True)
class Event:
    nct_id: str
    kind: str  # status, lapsed or reopened
    from_status: str | None
    to_status: str
    effective_date: dt.date


@dataclass
class Outcome:
    """What one record does."""

    action: str
    wide: Wide | None = None  # the new wide row, if the trial keeps or gets one
    drop_wide: bool = False  # the trial leaves live.trial_state
    version: dict[str, Any] | None = None  # the compact row to append to live.study_versions
    events: list[Event] = field(default_factory=list)
    marker: tuple[dt.date, int] | None = None  # an amendment marker (ADR 0043)


def lapse_date(rules: CohortRules, row: Mapping[str, Any]) -> dt.date | None:
    """The first day the state of a compact row is lapsed (ADR 0014), or None."""
    reference = completion_ref(
        row.get("completion_date"), row.get("completion_date_precision"),
        row.get("primary_completion_date"), row.get("primary_completion_date_precision"),
    )  # fmt: skip
    return lapse_from(
        rules, str(row["status"]), row["effective_date"], reference, row.get("status_verified_date")
    )


def keeps_wide_row(rules: CohortRules, status: str) -> bool:
    """A trial keeps a wide row while its latest status is not terminal."""
    return status not in rules.terminal


def _recent(dates: tuple[dt.date, ...], added: dt.date, day: dt.date, keep_days: int) -> tuple[
    dt.date, ...
]:  # fmt: skip
    since = day - dt.timedelta(days=keep_days)
    return tuple(sorted(d for d in (*dates, added) if d > since))


def _pruned(dates: tuple[dt.date, ...], day: dt.date, keep_days: int) -> tuple[dt.date, ...]:
    since = day - dt.timedelta(days=keep_days)
    return tuple(d for d in dates if d > since)


def new_trial(rules: CohortRules, row: dict[str, Any], day: dt.date, keep_days: int) -> Wide:
    """The wide row of a trial whose first known record is `row` (its version 0)."""
    return Wide(
        row=dict(row),
        first={name: row.get(source) for name, source in FIRST_FIELDS.items()},
        n_versions=1,
        ever_suspended=row["status"] == "SUSPENDED",
        recent_post_dates=_recent((), row["effective_date"], day, keep_days),
        lapse_from=lapse_date(rules, row),
        reversal=False,
    )


def apply_record(
    rules: CohortRules,
    record: dict[str, Any],
    wide: Wide | None,
    closed: Closed | None,
    day: dt.date,
    keep_days: int,
) -> Outcome:
    """What one compact record does, against the trial's wide row or its closed data (at
    most one of them; neither for an unknown trial). `day` is the run's date d; records
    posted after d never reach this function."""
    posted: dt.date = record["effective_date"]
    if posted > day:
        raise ValueError(f"{record['nct_id']}: a record posted after the run's date {day}")
    status = str(record["status"])
    nct_id = str(record["nct_id"])
    if wide is None and closed is None:
        events = [Event(nct_id, STATUS, None, status, posted)] if status in rules.terminal else []
        made = new_trial(rules, record, day, keep_days)
        keep = keeps_wide_row(rules, status)
        return Outcome(NEW_TRIAL, made if keep else None, version=record, events=events,
                       marker=(posted, 1) if keep else None)  # fmt: skip
    if wide is not None:
        if posted < wide.effective_date:
            return Outcome(EARLIER)
        same_day = posted == wide.effective_date
        if same_day and record["feature_hash"] == wide.row["feature_hash"]:
            return Outcome(OVERLAP)
        before = wide.row
        events = []
        if status != before["status"]:
            events.append(Event(nct_id, STATUS, str(before["status"]), status, posted))
        code = marker_code(before, record)
        if same_day:
            made = replace(
                wide,
                row=dict(record),
                ever_suspended=wide.ever_suspended or status == "SUSPENDED",
                lapse_from=lapse_date(rules, record),
                recent_post_dates=_pruned(wide.recent_post_dates, day, keep_days),
            )
            action = REPLACEMENT
        else:
            made = replace(
                wide,
                row=dict(record),
                n_versions=wide.n_versions + 1,
                ever_suspended=wide.ever_suspended or status == "SUSPENDED",
                recent_post_dates=_recent(wide.recent_post_dates, posted, day, keep_days),
                lapse_from=lapse_date(rules, record),
            )
            action = NEW_VERSION
        marker = (posted, code) if code else None
        if not keeps_wide_row(rules, status):
            return Outcome(action, None, drop_wide=True, version=record, events=events,
                           marker=marker)  # fmt: skip
        return Outcome(action, made, version=record, events=events, marker=marker)
    assert closed is not None
    if posted < closed.last_post:
        return Outcome(EARLIER)
    if posted == closed.last_post and closed.last_hash in (None, record["feature_hash"]):
        return Outcome(OVERLAP)
    events = []
    if closed.last_status is not None and status != closed.last_status:
        events.append(Event(nct_id, STATUS, closed.last_status, status, posted))
    if not keeps_wide_row(rules, status):
        return Outcome(CLOSED, version=record, events=events)
    # A closed trial shows an open status again: a reversal, counted but never scored. Its
    # version-0 values and count are not known here.
    reopened = Event(nct_id, REOPENED, closed.last_status, status, posted)
    made = Wide(
        row=dict(record),
        first=dict.fromkeys(FIRST_FIELDS),
        n_versions=0,
        ever_suspended=status == "SUSPENDED",
        recent_post_dates=_recent((), posted, day, keep_days),
        lapse_from=lapse_date(rules, record),
        reversal=True,  # a trial without a wide row ended: an open status now reverses it
    )
    return Outcome(CLOSED, made, version=record, events=[*events, reopened],
                   marker=(posted, marker_code(None, record)))  # fmt: skip


def lapse_events(wides: list[Wide], since: dt.date, day: dt.date) -> list[Event]:
    """The lapse exits of a run: trials whose state became lapsed after `since` and on or
    before `day` (ADR 0014); they leave the population on that day."""
    return [
        Event(w.nct_id, LAPSED, str(w.row["status"]), "LAPSED", w.lapse_from)
        for w in wides
        if w.lapse_from is not None and since < w.lapse_from <= day
    ]
