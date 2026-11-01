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

The bootstrap (laptop only, ADR 0040) feeds it the warehouse's versions; the tests feed it
synthetic histories. Nothing here reads a file.
"""

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import groupby
from typing import Any

from trialpulse.cohort.rules import CohortRules
from trialpulse.live.contract import marker_code
from trialpulse.live.sponsors import SponsorFile
from trialpulse.live.state import FIRST_FIELDS, Wide, keeps_wide_row, lapse_date


@dataclass
class Seed:
    wides: dict[str, Wide]
    sponsors: SponsorFile
    markers: dict[str, list[tuple[dt.date, int]]]


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
    states: list[Mapping[str, Any]] = []
    reopened = (*rules.open_statuses, *rules.unknown_labels)
    since = day - dt.timedelta(days=keep_days)
    for nct_id, group in groupby(rows, key=lambda v: v["nct_id"]):
        history = list(group)
        by_date = [list(g)[-1] for _, g in groupby(history, key=lambda v: v["effective_date"])]
        states += by_date
        latest = by_date[-1]
        if not keeps_wide_row(rules, str(latest["status"])):
            continue
        by_number = sorted(history, key=lambda v: v["nct_version"])
        terminal = [v["nct_version"] for v in by_number if v["status"] in rules.terminal]
        reversal = bool(terminal) and any(
            v["status"] in reopened for v in by_number if v["nct_version"] > terminal[0]
        )
        row = {k: v for k, v in latest.items() if k != "nct_version"}
        wides[nct_id] = Wide(
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
        markers[nct_id] = marks
    sponsors = SponsorFile.empty(alias_min_trials, alias_wait_days, dt.date.min)
    sponsors.advance(states, day, rules)  # through c - 1, the states of c queued
    return Seed(wides, sponsors, markers)
