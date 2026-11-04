"""Live state from a full version history, as of a date (the core of the bootstrap, ADR 0037).

Given the compact rows of every version (each with its `nct_version`) and a date c, `seed`
gives what the live system holds after c: a wide row for every trial whose state on c is
not terminal, the sponsor file through c minus one day with the states of c queued, and the
amendment markers. It applies the offline rules exactly:

- the version count counts every version posted by c, several on one day included;
- the version-0 values are those of the version first in (post date, version number);
- the state of a post date is its highest version number, and the latest state is the wide
  row's latest-record part;
- a status reversal is an open or UNKNOWN status after the first terminal version, by
  version number (Section 6);
- the recent post dates are those of every version in the kept window.

It works in two parts, so that the bootstrap can stream the warehouse through the same code
(`trialpulse.live.bootstrap`): `seed_trial` makes one trial's wide row, markers and daily
states from its own versions, and `sponsor_pass` runs the sponsor file over every trial's
states one month at a time in date order. The monthly pass gives the same file as one call
of `SponsorFile.advance` over every state (tested): events are derived and applied in date
order either way, and a month's events never reach back into an earlier month.

The bootstrap (laptop only, ADR 0040) feeds it the warehouse's versions; the tests feed it
synthetic histories. Nothing here reads a file.
"""

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import groupby
from typing import Any

from trialpulse.cohort.rules import CohortRules
from trialpulse.live.contract import marker_code
from trialpulse.live.sponsors import QUEUED, SponsorFile
from trialpulse.live.state import FIRST_FIELDS, Wide, keeps_wide_row, lapse_date

# A daily state as the sponsor pass reads it: the fields of `sponsors.QUEUED`, in that order.
State = tuple[Any, ...]


@dataclass
class Seed:
    wides: dict[str, Wide]
    sponsors: SponsorFile
    markers: dict[str, list[tuple[dt.date, int]]]


@dataclass(frozen=True)
class TrialSeed:
    """What one trial gives the seed: its wide row and markers (None for a trial whose
    latest state is terminal) and its daily states, one per post date."""

    wide: Wide | None
    markers: list[tuple[dt.date, int]] | None
    states: list[Mapping[str, Any]]


def state_of(row: Mapping[str, Any]) -> State:
    """The fields of a compact row that the sponsor pass reads, as a tuple."""
    return tuple(row.get(name) for name in QUEUED)


def seed_trial(
    history: Sequence[Mapping[str, Any]], day: dt.date, rules: CohortRules, keep_days: int
) -> TrialSeed:
    """One trial's part of the seed. `history` is every version of the trial posted on or
    before `day`, sorted by (post date, version number), each a compact row with its
    `nct_version`."""
    by_date = [list(g)[-1] for _, g in groupby(history, key=lambda v: v["effective_date"])]
    latest = by_date[-1]
    if not keeps_wide_row(rules, str(latest["status"])):
        return TrialSeed(None, None, by_date)
    reopened = (*rules.open_statuses, *rules.unknown_labels)
    since = day - dt.timedelta(days=keep_days)
    by_number = sorted(history, key=lambda v: v["nct_version"])
    terminal = [v["nct_version"] for v in by_number if v["status"] in rules.terminal]
    reversal = bool(terminal) and any(
        v["status"] in reopened for v in by_number if v["nct_version"] > terminal[0]
    )
    row = {k: v for k, v in latest.items() if k != "nct_version"}
    wide = Wide(
        row=row,
        first={name: history[0].get(source) for name, source in FIRST_FIELDS.items()},
        n_versions=len(history),
        ever_suspended=any(v["status"] == "SUSPENDED" for v in history),
        recent_post_dates=tuple(
            v["effective_date"] for v in history if v["effective_date"] > since
        ),
        lapse_from=lapse_date(rules, row),
        reversal=reversal,
    )
    marks = []
    before: Mapping[str, Any] | None = None
    for state in by_date:
        code = marker_code(before, state)
        if code:
            marks.append((state["effective_date"], code))
        before = state
    return TrialSeed(wide, marks, by_date)


def sponsor_pass(
    states: Iterable[State],
    day: dt.date,
    rules: CohortRules,
    alias_min_trials: int,
    alias_wait_days: int,
) -> SponsorFile:
    """The sponsor file through `day` minus one day, with the states posted on `day` queued,
    from daily states (`state_of`) posted on or before `day`, in any order. They are fed one
    calendar month at a time in date order, so that only a month's states are held as the
    mappings the pass reads."""
    months: dict[tuple[int, int], list[State]] = defaultdict(list)
    position = QUEUED.index("effective_date")
    for state in states:
        posted: dt.date = state[position]
        if posted > day:
            raise ValueError("a state posted after the seed date")
        months[(posted.year, posted.month)].append(state)
    sponsors = SponsorFile.empty(alias_min_trials, alias_wait_days, dt.date.min)
    for year, month in sorted(months):
        following = dt.date(year + month // 12, month % 12 + 1, 1)
        batch = [dict(zip(QUEUED, s, strict=True)) for s in months.pop((year, month))]
        sponsors.advance(batch, min(following, day), rules)
    sponsors.advance([], day, rules)  # through `day` minus one day, if no state reached it
    return sponsors


def seed(
    versions: Iterable[Mapping[str, Any]],
    day: dt.date,
    rules: CohortRules,
    keep_days: int,
    alias_min_trials: int,
    alias_wait_days: int,
) -> Seed:
    """Live state after `day` from compact version rows that carry `nct_version`."""
    rows = sorted(
        (v for v in versions if v["effective_date"] <= day),
        key=lambda v: (v["nct_id"], v["effective_date"], v["nct_version"]),
    )
    wides: dict[str, Wide] = {}
    markers: dict[str, list[tuple[dt.date, int]]] = {}
    states: list[State] = []
    for nct_id, group in groupby(rows, key=lambda v: v["nct_id"]):
        part = seed_trial(list(group), day, rules, keep_days)
        states += [state_of(s) for s in part.states]
        if part.wide is not None and part.markers is not None:
            wides[nct_id] = part.wide
            markers[nct_id] = part.markers
    sponsors = sponsor_pass(states, day, rules, alias_min_trials, alias_wait_days)
    return Seed(wides, sponsors, markers)
