"""The two parts of the seed (ADR 0037, the bootstrap's streaming plan): the sponsor pass fed
one month at a time gives the file of one call over every state, and `seed` is the per-trial
part over each trial's versions plus that pass."""

import datetime as dt
from itertools import groupby

import pytest

from trialpulse.live.seed import seed, seed_trial, sponsor_pass, state_of
from trialpulse.live.sponsors import QUEUED, SponsorFile

from .conftest import CFG, KEEP, RULES, compact_history

ALIASES = (CFG.features.sponsor_alias_min_trials, CFG.features.sponsor_alias_wait_days)
HISTORY = compact_history(300)


def _states(day: dt.date) -> list[tuple[object, ...]]:
    rows = sorted((r for r in HISTORY if r["effective_date"] <= day),
                  key=lambda v: (v["nct_id"], v["effective_date"], v["nct_version"]))  # fmt: skip
    out = []
    for _, group in groupby(rows, key=lambda v: v["nct_id"]):
        out += [state_of(s) for s in seed_trial(list(group), day, RULES, KEEP).states]
    return out


def _one_call(states: list[tuple[object, ...]], day: dt.date) -> SponsorFile:
    """The pass as `seed` first ran it: one `advance` over every state."""
    sponsors = SponsorFile.empty(*ALIASES, dt.date.min)
    sponsors.advance([dict(zip(QUEUED, s, strict=True)) for s in states], day, RULES)
    return sponsors


@pytest.mark.parametrize(
    "day",
    [
        dt.date(2019, 8, 31),  # a posting day at a month's end: its states are queued
        dt.date(2019, 9, 1),  # the first of a month, the day after a posting day
        dt.date(2019, 6, 15),  # the middle of a month
        dt.date(2026, 9, 25),  # long after the last posting day
    ],
)
def test_the_monthly_pass_gives_the_file_of_one_call(day: dt.date) -> None:
    states = _states(day)
    assert states
    monthly = sponsor_pass(states, day, RULES, *ALIASES)
    single = _one_call(states, day)
    assert monthly.to_bytes() == single.to_bytes()
    assert monthly.through_day == day - dt.timedelta(days=1)
    assert len(monthly.queue) == sum(1 for s in states if s[1] == day)


def test_the_pass_takes_its_states_in_any_order() -> None:
    day = dt.date(2019, 8, 31)
    states = _states(day)
    assert (
        sponsor_pass(states[::-1], day, RULES, *ALIASES).to_bytes()
        == sponsor_pass(states, day, RULES, *ALIASES).to_bytes()
    )


def test_the_pass_refuses_a_state_posted_after_its_day() -> None:
    day = dt.date(2019, 6, 15)
    later = _states(dt.date(2019, 8, 31))
    with pytest.raises(ValueError, match="after the seed date"):
        sponsor_pass(later, day, RULES, *ALIASES)


def test_the_seed_is_the_trials_parts_and_the_pass() -> None:
    day = dt.date(2019, 6, 15)
    seeded = seed(HISTORY, day, RULES, KEEP, *ALIASES)
    rows = sorted((r for r in HISTORY if r["effective_date"] <= day),
                  key=lambda v: (v["nct_id"], v["effective_date"], v["nct_version"]))  # fmt: skip
    parts = {n: seed_trial(list(g), day, RULES, KEEP)
             for n, g in groupby(rows, key=lambda v: v["nct_id"])}  # fmt: skip
    assert seeded.wides == {n: p.wide for n, p in parts.items() if p.wide is not None}
    assert seeded.markers == {n: p.markers for n, p in parts.items() if p.wide is not None}
    assert seeded.sponsors.to_bytes() == _one_call(_states(day), day).to_bytes()
    ended = [p for p in parts.values() if p.wide is None]
    assert ended
    assert all(p.markers is None for p in ended)
