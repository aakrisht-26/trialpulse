"""Run control of the daily job (ADR 0045): pure rules, no database.

- A Saturday or Sunday date does nothing (the registry posts on weekdays).
- The watermark is the earlier of the latest stored post date and the run's date d; the
  pull starts `overlap_days` before it.
- The NULL gate: among pulled records whose trial and post date match a stored row, a field
  that was set in the stored row and is NULL in the record, in more than `share` of them,
  stops the run before any write. API shape changes that rename a field look like this. A
  quarantined record that matches a stored row counts as having lost every field the stored
  row had: a shape change that makes records fail their contract stops the run the same way.
- The overlap check: stored live rows posted in the pull's window must come back with their
  date or a later one; more than `allowed` missing fails the run.
- A full run (every open trial rescored) is due when the last full run is `days` old.
- The scope of a run: what `serving.publish` may write and remove.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from trialpulse.live.contract import LIVE_FIELDS


class GateError(RuntimeError):
    """The run stops before any write."""


def is_weekend(day: dt.date) -> bool:
    return day.weekday() >= 5


def watermark(latest_post: dt.date | None, day: dt.date) -> dt.date:
    return day if latest_post is None else min(latest_post, day)


def pull_since(mark: dt.date, overlap_days: int) -> dt.date:
    return mark - dt.timedelta(days=overlap_days)


def null_gate(
    records: Sequence[Mapping[str, Any]],
    stored: Mapping[tuple[str, dt.date], Mapping[str, Any]],
    share: float,
    quarantined: Sequence[tuple[str | None, dt.date | None]] = (),
) -> dict[str, float]:
    """The fields that turned NULL in more than `share` of the matched records (none: {}).
    `quarantined` holds the (trial, post date) of the run's quarantined records."""
    matched: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = [
        (r, stored[(r["nct_id"], r["effective_date"])]) for r in records
        if (r["nct_id"], r["effective_date"]) in stored
    ]  # fmt: skip
    lost_rows = [stored[key] for key in quarantined if key in stored]  # type: ignore[index]
    total = len(matched) + len(lost_rows)
    if not total:
        return {}
    out = {}
    for name in LIVE_FIELDS:
        lost = sum(1 for new, old in matched if old.get(name) is not None and new.get(name) is None)
        lost += sum(1 for old in lost_rows if old.get(name) is not None)
        if lost / total > share:
            out[name] = lost / total
    return out


def missing_overlap(
    stored: Mapping[str, dt.date], pulled: Mapping[str, dt.date], since: dt.date
) -> list[str]:
    """Trials with a stored live row posted on or after `since` that the pull did not return
    with that date or a later one."""
    return sorted(
        nct for nct, posted in stored.items()
        if posted >= since and (nct not in pulled or pulled[nct] < posted)
    )  # fmt: skip


def full_run_due(last_full: dt.date | None, day: dt.date, days: int) -> bool:
    return last_full is None or (day - last_full).days >= days


@dataclass(frozen=True)
class Scope:
    trials: set[str]  # every trial whose serving rows the run may write or remove
    rescore: set[str]  # the scored trials of the run


def scope(
    people: pd.DataFrame,
    latest: Mapping[str, tuple[str, int, str]],
    not_scored: Mapping[str, tuple[str, int, str]],
    model_version: str,
    full: bool,
) -> Scope:
    """`people`: the classified population (trial_id, true_index, reason, content_hash).
    `latest`: per trial, the served (content hash, true index, model version); `not_scored`:
    per trial, the stored (content hash, true index, reason). A full run rescores every
    scored trial; otherwise a trial is rescored when it has no served row, another hash, another
    true index or another model."""
    rescore: set[str] = set()
    touched: set[str] = set()
    for row in people.itertuples(index=False):
        nct, index, digest = str(row.trial_id), int(row.true_index), str(row.content_hash)
        if row.reason is None or (isinstance(row.reason, float) and pd.isna(row.reason)):
            if full or latest.get(nct) != (digest, index, model_version) or nct in not_scored:
                rescore.add(nct)
        elif not_scored.get(nct) != (digest, index, str(row.reason)) or nct in latest:
            touched.add(nct)
    present = set(people["trial_id"].astype(str))
    gone = (set(latest) | set(not_scored)) - present
    return Scope(trials=rescore | touched | gone, rescore=rescore)
