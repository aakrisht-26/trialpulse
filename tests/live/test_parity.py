"""Parity C on synthetic histories (ADR 0038, layer 1): for a feed that holds at most one
record per trial and post date, the live path equals the offline path on that feed, on all
served features and every row, on several dates."""

import datetime as dt
from typing import Any

import pandas as pd
import pytest

from trialpulse.features.registry import model_columns
from trialpulse.live import engine

from .conftest import (
    CFG,
    DECISIONS,
    RULES,
    compact_history,
    feed_rows,
    offline_on,
    replay,
)

START = dt.date(2019, 6, 15)


def _run_days(history: list[dict[str, Any]], every: int = 1) -> list[dt.date]:
    """With every=1, a run on each posting day and on the day after it: on the day after,
    the sponsor record must already count the day's versions, and nothing later."""
    days = sorted({r["effective_date"] for r in history if r["effective_date"] > START})
    if every == 1:
        return sorted({*days, *(d + dt.timedelta(days=1) for d in days)})
    return days[::every]


def _checked(days: list[dt.date], every: int) -> set[dt.date]:
    """With a run every posting day, every run is checked; with missed runs, dates spread
    over the replay: early, the middle, the last, a month's last day and a weekend if any."""
    if every == 1:  # the days after a posting day
        posted = set(days)
        return {d for d in days if d - dt.timedelta(days=1) in posted}
    picks = {days[len(days) // 10], days[len(days) // 2], days[-1]}
    picks |= {d for d in days if (d + dt.timedelta(days=1)).day == 1}
    picks |= {d for d in days if d.weekday() >= 5}
    return picks


def _compare(day: dt.date, memory: engine.Memory, fed: list[dict[str, Any]],
             history: list[dict[str, Any]]) -> int:  # fmt: skip
    people, live = engine.features_on(memory, day, RULES, CFG, DECISIONS)
    scored = people[people["reason"].isna()].sort_values("trial_id").reset_index(drop=True)
    from trialpulse.live import features as live_features

    keys = live_features.served_keys(scored, day, RULES, CFG)
    offline_people, offline = offline_on(feed_rows(history, START, fed), day, keys)
    assert people["trial_id"].tolist() == offline_people["trial_id"].tolist(), day
    assert people["reversal"].tolist() == offline_people["reversal"].tolist(), day
    served = [c for c in model_columns() if c in offline.columns]
    assert len(served) >= 50
    assert list(live.columns) == list(offline.columns)
    pd.testing.assert_frame_equal(
        live.reset_index(drop=True), offline.reset_index(drop=True), check_exact=True
    )
    return len(live)


@pytest.mark.slow  # about a minute; CI runs every test
@pytest.mark.parametrize("every", [1, 30])
def test_live_equals_offline_on_the_same_feed(every: int) -> None:
    """Every posting day a run (every=1), and with most runs missed (every=30: a run every
    30th posting day, so the pull covers many days and records arrive late for the pass)."""
    history = compact_history()
    days = _run_days(history, every)
    checked = _checked(days, every)
    rows_checked: list[int] = []

    def check(day: dt.date, memory: engine.Memory, state: Any) -> None:
        if day in checked:
            rows_checked.append(_compare(day, memory, state.fed, history))

    replay(history, START, days, on_run=check)
    assert len(rows_checked) == len(checked)
    assert min(rows_checked) > 0
    assert sum(rows_checked) > 100


def test_a_rename_after_the_seed_links_on_the_offline_day() -> None:
    """Sponsor "old pharma" hands three trials to "new pharma" in July 2019, after the seed
    date. The link counts once the first replacement is more than 365 days old (ADR 0018),
    so the record of a "new pharma" trial grows on one exact day; live and offline agree on
    every day around it, runs on every day."""
    from .conftest import compact_history_of, version

    d = dt.date
    rows = []
    for i, trial in enumerate(("NCT00000101", "NCT00000102", "NCT00000103", "NCT00000104")):
        rows.append(version(trial, 0, d(2018, 1, 10 + i), "RECRUITING",
                            sponsor_key="old pharma"))  # fmt: skip
    for i, trial in enumerate(("NCT00000101", "NCT00000102", "NCT00000103")):
        rows.append(version(trial, 1, d(2019, 7, 1 + i), "RECRUITING",
                            t0=d(2018, 1, 10 + i), sponsor_key="new pharma"))  # fmt: skip
    rows.append(version("NCT00000105", 0, d(2019, 8, 1), "RECRUITING", sponsor_key="new pharma"))
    rows.append(version("NCT00000106", 0, d(2018, 2, 1), "RECRUITING", sponsor_key="old pharma"))
    rows.append(version("NCT00000106", 1, d(2019, 5, 1), "COMPLETED", t0=d(2018, 2, 1),
                        sponsor_key="old pharma"))  # fmt: skip
    history = compact_history_of(rows)
    run_days = [START + dt.timedelta(days=i) for i in range(1, 400)]
    checked = {d(2020, 6, 29) + dt.timedelta(days=i) for i in range(8)}
    seen: list[tuple[dt.date, Any]] = []

    def check(day: dt.date, memory: engine.Memory, state: Any) -> None:
        if day in checked:
            _compare(day, memory, state.fed, history)
            _, live = engine.features_on(memory, day, RULES, CFG, DECISIONS)
            row = live[live["trial_id"] == "NCT00000105"]
            seen.append((day, int(row["sponsor_prior_registrations"].iloc[0])))

    replay(history, START, run_days, on_run=check)
    counts = [n for _, n in seen]
    assert counts[0] < counts[-1]  # the link starts counting inside the checked days
