"""One live run in memory (ADR 0037): ingest, the sponsor pass and the features of day d.

`Memory` is what a run reads from the live tables: the wide rows, the sponsor file and, per
trial, the latest compact row the live system appended (which tells a refetch of a closed
trial from a replacement). `ingest` applies a run's records and returns everything to
write; `advance` moves the sponsor pass through d minus one day; `features_on` builds the
served features of day d. `trialpulse.live.store` loads and saves `Memory` in Postgres, and
the tests drive the same functions on synthetic histories.
"""

import datetime as dt
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import pandas as pd

from trialpulse.cohort.rules import CohortRules
from trialpulse.config import ProjectConfig
from trialpulse.live import features as live_features
from trialpulse.live.sponsors import SponsorFile, from_day_number
from trialpulse.live.state import Closed, Event, Outcome, Wide, apply_record, lapse_events
from trialpulse.scoring.score import classify


@dataclass
class Memory:
    wides: dict[str, Wide]
    sponsors: SponsorFile
    last_live: dict[str, dict[str, Any]] = field(default_factory=dict)
    markers: dict[str, list[tuple[dt.date, int]]] = field(default_factory=dict)


@dataclass
class Ingested:
    """What a run's ingest writes."""

    counts: Counter[str]
    versions: list[dict[str, Any]]
    upserted: dict[str, Wide]
    dropped: set[str]
    events: list[Event]
    markers: dict[str, list[tuple[dt.date, int]]]  # the new markers per trial


def closed_state(memory: Memory, nct_id: str) -> Closed | None:
    """What is known of a trial without a wide row, or None for an unknown trial."""
    live = memory.last_live.get(nct_id)
    thin = memory.sponsors.thin.get(nct_id)
    queued = [q for q in memory.sponsors.queue if q["nct_id"] == nct_id]
    posts = [d for d in (
        live["effective_date"] if live else None,
        from_day_number(thin.last_post) if thin else None,
        *(q["effective_date"] for q in queued),
    ) if d is not None]  # fmt: skip
    if not posts:
        return None
    last = max(posts)
    same = live is not None and live["effective_date"] == last
    return Closed(
        last_post=last,
        last_hash=live["feature_hash"] if same and live else None,
        last_status=str(live["status"]) if live else None,
    )


def ingest(
    memory: Memory,
    records: Iterable[dict[str, Any]],
    rules: CohortRules,
    day: dt.date,
    keep_days: int,
) -> Ingested:
    """Apply the records of one run (compact rows posted on or before `day`), in post-date
    order, to `memory`, and return what to write. Memory is updated in place."""
    counts: Counter[str] = Counter()
    result = Ingested(counts, [], {}, set(), [], {})
    for record in sorted(records, key=lambda r: (r["effective_date"], r["nct_id"])):
        nct_id = str(record["nct_id"])
        wide = memory.wides.get(nct_id)
        closed = None if wide is not None else closed_state(memory, nct_id)
        outcome: Outcome = apply_record(rules, record, wide, closed, day, keep_days)
        counts[outcome.action] += 1
        if outcome.version is not None:
            result.versions.append(outcome.version)
            memory.last_live[nct_id] = outcome.version
        if outcome.wide is not None:
            memory.wides[nct_id] = outcome.wide
            result.upserted[nct_id] = outcome.wide
            result.dropped.discard(nct_id)
        elif outcome.drop_wide:
            memory.wides.pop(nct_id, None)
            result.upserted.pop(nct_id, None)
            result.dropped.add(nct_id)
        result.events += outcome.events
        if outcome.marker is not None:
            marks = memory.markers.setdefault(nct_id, [])
            if marks and marks[-1][0] == outcome.marker[0]:  # a replacement: one marker a day
                marks[-1] = (outcome.marker[0], marks[-1][1] | outcome.marker[1])
            else:
                marks.append(outcome.marker)
            result.markers[nct_id] = marks
    # Recent post dates older than the kept window go, also on rows without a record today.
    since = day - dt.timedelta(days=keep_days)
    for nct_id, wide in memory.wides.items():
        if wide.recent_post_dates and wide.recent_post_dates[0] <= since:
            kept = tuple(d for d in wide.recent_post_dates if d > since)
            memory.wides[nct_id] = result.upserted[nct_id] = replace(wide, recent_post_dates=kept)
    return result


def lapse_exits(memory: Memory, since: dt.date, day: dt.date) -> list[Event]:
    return lapse_events(list(memory.wides.values()), since, day)


def advance(
    memory: Memory, versions: Iterable[Mapping[str, Any]], day: dt.date, rules: CohortRules,
    run_id: int | None,
) -> None:  # fmt: skip
    """The sponsor pass through `day` minus one day, over the versions of runs it has not
    consumed (one state per trial and post date: the last of a replacement wins on its
    day, and a key change is a replacement on the day seen)."""
    memory.sponsors.advance(list(versions), day, rules)
    memory.sponsors.last_run_id = run_id


def features_on(
    memory: Memory,
    day: dt.date,
    rules: CohortRules,
    cfg: ProjectConfig,
    decisions: Mapping[str, Any],
    only: set[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The classified population of `day` and the served features of its scored trials
    (all of them, or those in `only`)."""
    wides = list(memory.wides.values())
    people = classify(live_features.population(rules, wides, day), day, rules, decisions)
    scored = people[people["reason"].isna()]
    if only is not None:
        scored = scored[scored["trial_id"].isin(only)]
    scored = scored.sort_values("trial_id").reset_index(drop=True)
    keys = live_features.served_keys(scored, day, rules, cfg)
    frame = live_features.compute_live(
        memory.wides, keys, memory.sponsors, live_features.open_count(rules, wides, day), rules
    )
    return people, frame
