"""The decision table of the live state (ADR 0037), on records worked by hand: the cases a
same-date replacement and a late record have no offline twin for (ADR 0038, item 2)."""

import datetime as dt
from typing import Any

import pytest

from trialpulse.live import engine, state
from trialpulse.live.seed import seed

from .conftest import CFG, KEEP, RULES, compact_history_of, version

D = dt.date
DAY = D(2026, 10, 20)


def _record(nct: str, posted: dt.date, status: str = "RECRUITING", **fields: Any) -> dict[str, Any]:
    (row,) = compact_history_of([version(nct, 99, posted, status, t0=D(2024, 1, 5), **fields)])
    row.pop("nct_version")
    return row


SEED_DAY = D(2026, 9, 25)


def _memory(*rows: dict[str, Any], day: dt.date = SEED_DAY) -> engine.Memory:
    history = compact_history_of(list(rows))
    seeded = seed(history, day, RULES, KEEP, CFG.features.sponsor_alias_min_trials,
                  CFG.features.sponsor_alias_wait_days)  # fmt: skip
    return engine.Memory(seeded.wides, seeded.sponsors, markers=seeded.markers)


def _seeded() -> engine.Memory:
    return _memory(
        version("NCT00000001", 0, D(2024, 1, 5), "RECRUITING", enrollment_count=100),
        version("NCT00000001", 1, D(2026, 9, 1), "RECRUITING", t0=D(2024, 1, 5),
                enrollment_count=80),
        version("NCT00000002", 0, D(2024, 1, 5), "RECRUITING"),
        version("NCT00000002", 1, D(2026, 5, 1), "TERMINATED", t0=D(2024, 1, 5)),
    )  # fmt: skip


def test_the_seed_counts_versions_and_keeps_version_0() -> None:
    memory = _seeded()
    wide = memory.wides["NCT00000001"]
    assert wide.n_versions == 2
    assert wide.first["first_enrollment_count"] == 100
    assert wide.row["enrollment_count"] == 80
    assert "NCT00000002" not in memory.wides  # it ended
    assert memory.sponsors.through_day == D(2026, 9, 24)


def test_a_new_version_counts_and_an_overlap_refetch_does_nothing() -> None:
    memory = _seeded()
    later = _record("NCT00000001", D(2026, 10, 2), enrollment_count=60)
    done = engine.ingest(memory, [later], RULES, DAY, KEEP)
    assert done.counts == {state.NEW_VERSION: 1}
    wide = memory.wides["NCT00000001"]
    assert (wide.n_versions, wide.first["first_enrollment_count"]) == (3, 100)
    assert wide.recent_post_dates == (D(2026, 9, 1), D(2026, 10, 2))
    # Enrollment changed, and the verification date with the helper's defaults.
    assert done.markers["NCT00000001"][-1] == (D(2026, 10, 2), 2 | 4)
    again = engine.ingest(memory, [later], RULES, DAY, KEEP)
    assert again.counts == {state.OVERLAP: 1}
    assert again.versions == []


def test_a_same_date_replacement_keeps_the_count_and_version_0() -> None:
    """Hand-worked: a record with the stored post date and another hash replaces the latest
    fields; the count stays 3, version 0 stays, ever suspended is an OR, one marker a day."""
    memory = _seeded()
    engine.ingest(memory, [_record("NCT00000001", D(2026, 10, 2))], RULES, DAY, KEEP)
    replaced = _record("NCT00000001", D(2026, 10, 2), "SUSPENDED")
    done = engine.ingest(memory, [replaced], RULES, DAY, KEEP)
    assert done.counts == {state.REPLACEMENT: 1}
    wide = memory.wides["NCT00000001"]
    assert (wide.n_versions, wide.ever_suspended, wide.row["status"]) == (3, True, "SUSPENDED")
    assert [e.to_status for e in done.events] == ["SUSPENDED"]
    assert len(done.versions) == 1
    back = engine.ingest(memory, [_record("NCT00000001", D(2026, 10, 2))], RULES, DAY, KEEP)
    assert memory.wides["NCT00000001"].ever_suspended  # once suspended, always
    assert back.counts == {state.REPLACEMENT: 1}
    assert [d for d, _ in memory.markers["NCT00000001"]].count(D(2026, 10, 2)) == 1


def test_an_earlier_record_is_ignored_and_counted() -> None:
    memory = _seeded()
    done = engine.ingest(memory, [_record("NCT00000001", D(2026, 8, 1))], RULES, DAY, KEEP)
    assert done.counts == {state.EARLIER: 1}
    assert memory.wides["NCT00000001"].n_versions == 2


def test_a_closed_trial_refetched_is_nothing_and_reopened_is_a_reversal() -> None:
    memory = _seeded()
    refetch = _record("NCT00000002", D(2026, 5, 1), "TERMINATED")
    assert engine.ingest(memory, [refetch], RULES, DAY, KEEP).counts == {state.OVERLAP: 1}
    reopened = _record("NCT00000002", D(2026, 10, 5))
    done = engine.ingest(memory, [reopened], RULES, DAY, KEEP)
    assert done.counts == {state.CLOSED: 1}
    wide = memory.wides["NCT00000002"]
    assert wide.reversal
    assert [e.kind for e in done.events] == [state.REOPENED]
    engine.advance(memory, done.versions, DAY, RULES, 1)
    people, _ = engine.features_on(memory, DAY, RULES, CFG, _DECISIONS)
    reasons = dict(zip(people["trial_id"], people["reason"], strict=True))
    assert reasons["NCT00000002"] == "reversal"


def test_a_trial_that_ends_leaves_the_state_with_an_event() -> None:
    memory = _seeded()
    done = engine.ingest(memory, [_record("NCT00000001", D(2026, 10, 3), "COMPLETED")], RULES,
                         DAY, KEEP)  # fmt: skip
    assert done.dropped == {"NCT00000001"}
    assert "NCT00000001" not in memory.wides
    assert [(e.from_status, e.to_status) for e in done.events] == [("RECRUITING", "COMPLETED")]


def test_a_new_trial_is_its_own_version_0() -> None:
    memory = _seeded()
    fresh = _record("NCT00000009", D(2026, 10, 10), sponsor_key="brand new sponsor")
    done = engine.ingest(memory, [fresh], RULES, DAY, KEEP)
    assert done.counts == {state.NEW_TRIAL: 1}
    wide = memory.wides["NCT00000009"]
    assert (wide.n_versions, wide.reversal) == (1, False)
    # Its sponsor is first seen today: no record yet, and the pass knows no such key.
    engine.advance(memory, done.versions, DAY, RULES, 1)
    assert memory.sponsors.answer(DAY, wide.row["sponsor_id"], "NCT00000009") == (0, 0, 0)


def test_a_record_posted_after_the_run_date_is_refused() -> None:
    memory = _seeded()
    with pytest.raises(ValueError, match="after the run's date"):
        engine.ingest(memory, [_record("NCT00000001", DAY + dt.timedelta(days=1))], RULES, DAY,
                      KEEP)  # fmt: skip


def test_a_lapse_exit_is_an_event_on_its_day() -> None:
    """Completion passed in 2025 and verification 24 months old by the end of October 2026:
    the trial lapses on 1 November 2026 and leaves the population that day."""
    memory = _memory(
        version("NCT00000003", 0, D(2024, 9, 10), "RECRUITING", completion_date=D(2025, 1, 1),
                status_verified_date=D(2024, 9, 1)),
    )  # fmt: skip
    wide = memory.wides["NCT00000003"]
    assert wide.lapse_from == D(2026, 10, 1)
    exits = engine.lapse_exits(memory, D(2026, 9, 25), D(2026, 10, 20))
    assert [(e.nct_id, e.kind, e.effective_date) for e in exits] == [
        ("NCT00000003", state.LAPSED, D(2026, 10, 1))
    ]
    assert engine.lapse_exits(memory, D(2026, 10, 1), D(2026, 10, 20)) == []
    people, _ = engine.features_on(memory, D(2026, 10, 20), RULES, CFG, _DECISIONS)
    assert people.empty


def test_a_late_record_applies_on_the_day_after_the_pass() -> None:
    """A record posted on a day the pass already passed (it was missed by the API until
    later) is applied on the pass's next day: counts catch up, nothing is lost."""
    memory = _seeded()
    engine.advance(memory, [], D(2026, 10, 10), RULES, 1)  # the pass is through 9 October
    late = _record("NCT00000007", D(2026, 10, 1), sponsor_key="acme pharma")
    done = engine.ingest(memory, [late], RULES, DAY, KEEP)
    engine.advance(memory, done.versions, DAY, RULES, 2)
    assert memory.sponsors.through_day == DAY - dt.timedelta(days=1)
    sponsor = memory.wides["NCT00000001"].row["sponsor_id"]
    assert memory.sponsors.answer(DAY, sponsor, "NCT00000001")[0] == 2  # 0002 and 0007


_DECISIONS: dict[str, Any] = {
    "between_landmarks": "B",
    "bands": {"42 to 59 months": {"served": True}, "60 to 119 months": {"served": True}},
}
