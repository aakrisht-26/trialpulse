"""The cohort rules on hand-built mini histories (CLAUDE.md Step 4 acceptance, ADRs 0013 and
0014). T0 is 2015-01-01; month(n) is T0 plus n calendar months."""

import datetime as dt
from dataclasses import replace
from typing import Any

import duckdb
import pytest

from trialpulse.cohort.rules import CohortRules

from .conftest import CUTOFF, T0, cohort, month, version

D = dt.date
MINI: list[dict[str, Any]] = [
    # A. Withdrawn at month 3.
    version("NCT00000001", 0, month(0), "NOT_YET_RECRUITING"),
    version("NCT00000001", 1, month(3), "WITHDRAWN"),
    # B. Terminated at month 20.
    version("NCT00000002", 0, month(0), "RECRUITING"),
    version("NCT00000002", 1, month(12), "RECRUITING"),
    version("NCT00000002", 2, month(20), "TERMINATED"),
    # C. Completed at month 8.
    version("NCT00000003", 0, month(0), "RECRUITING"),
    version("NCT00000003", 1, month(8), "COMPLETED"),
    # D. UNKNOWN at month 30: the latest version shows the registry's UNKNOWN over the
    # submitted RECRUITING; verified in June 2015, completion passed, so it lapses on
    # 2017-07-01 and the trial is censored at its verification.
    version("NCT00000004", 0, month(0), "RECRUITING", completion_date=month(12)),
    version("NCT00000004", 1, month(5), "UNKNOWN", last_known_status="RECRUITING",
            completion_date=month(12)),
    # E. Open at the cutoff, first posted 2024-06-01.
    version("NCT00000005", 0, D(2024, 6, 1), "RECRUITING", study_first_post_date=D(2024, 6, 1)),
    # F. A reversal: completed, then recruiting again.
    version("NCT00000006", 0, month(0), "RECRUITING"),
    version("NCT00000006", 1, month(10), "COMPLETED"),
    version("NCT00000006", 2, month(14), "RECRUITING"),
    # G. Registered after it started (start two years before t0), no sponsor class.
    version("NCT00000007", 0, month(0), "ACTIVE_NOT_RECRUITING", start_date=D(2013, 1, 1),
            lead_sponsor_class=None),
    version("NCT00000007", 1, month(9), "COMPLETED", start_date=D(2013, 1, 1),
            lead_sponsor_class=None),
    # H1. Missing dates: no completion date of either kind, never updated. Both missing
    # counts as passed, so the verification lapse alone makes it unknown.
    version("NCT00000008", 0, month(0), "RECRUITING", completion_date=None,
            completion_date_precision=None),
    # H2. Missing verification date (falls back to the post date); the lapse from
    # 2017-02-01 is resolved by a completion posted on 2017-07-01.
    version("NCT00000009", 0, month(0), "RECRUITING", status_verified_date=None,
            completion_date=month(6)),
    version("NCT00000009", 1, month(30), "COMPLETED"),
    # H3. Missing first-post date.
    version("NCT00000019", 0, month(0), "RECRUITING", study_first_post_date=None),
    # I. Unknown by the derived rule, with no UNKNOWN version at all.
    version("NCT00000010", 0, month(0), "RECRUITING", completion_date=month(12)),
    version("NCT00000010", 1, month(18), "RECRUITING", completion_date=month(12)),
    # J. Study type changes: observational at registration, interventional from month 4.
    version("NCT00000011", 0, month(0), "RECRUITING", study_type="OBSERVATIONAL"),
    version("NCT00000011", 1, month(4), "RECRUITING"),
    version("NCT00000011", 2, month(15), "TERMINATED"),
    # K. Lapsed from 2017-02-01 until a new verification on 2018-05-01, then completed.
    version("NCT00000012", 0, month(0), "RECRUITING", completion_date=month(6)),
    version("NCT00000012", 1, month(40), "RECRUITING", completion_date=month(6)),
    version("NCT00000012", 2, month(50), "COMPLETED", completion_date=month(6)),
    # L. First version posted four days after t0.
    version("NCT00000013", 0, D(2015, 1, 5), "RECRUITING"),
    # M. Terminated and reopened in two versions posted the same day: a reversal.
    version("NCT00000014", 0, month(0), "RECRUITING"),
    version("NCT00000014", 1, month(7), "TERMINATED"),
    version("NCT00000014", 2, month(7), "RECRUITING"),
    # N. Suspended: the registry's rule never applies to it.
    version("NCT00000015", 0, month(0), "SUSPENDED", completion_date=month(6)),
    # O. First posted before 2008.
    version("NCT00000016", 0, D(2007, 6, 1), "RECRUITING", study_first_post_date=D(2007, 6, 1)),
    # P. Terminated exactly 12 months after registration.
    version("NCT00000017", 0, month(0), "RECRUITING"),
    version("NCT00000017", 1, month(12), "TERMINATED"),
]  # fmt: skip


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    from trialpulse.config import load_project_config

    return cohort(MINI, CohortRules.from_config(load_project_config(), cutoff=CUTOFF))


def landmarks(con: duckdb.DuckDBPyConnection, nct: str) -> list[tuple[Any, ...]]:
    return con.execute(
        """SELECT landmark_index, landmark_date, event, event_date, stratum
        FROM cohort_landmarks WHERE trial_id = ? ORDER BY landmark_index""",
        [nct],
    ).fetchall()


def outcome(con: duckdb.DuckDBPyConnection, nct: str) -> dict[str, Any]:
    cursor = con.execute("SELECT * FROM cohort_outcomes WHERE trial_id = ?", [nct])
    names = [d[0] for d in cursor.description]
    return dict(zip(names, cursor.fetchone() or (), strict=True))


def excluded(con: duckdb.DuckDBPyConnection, nct: str) -> dict[int, str | None]:
    rows = con.execute(
        "SELECT landmark_index, excluded FROM cohort_landmark_candidates WHERE trial_id = ?",
        [nct],
    ).fetchall()
    return dict(sorted(rows))


def person_period(con: duckdb.DuckDBPyConnection, nct: str, k: int) -> list[tuple[Any, ...]]:
    return con.execute(
        """SELECT interval, interval_start, interval_end, outcome FROM cohort_person_period
        WHERE trial_id = ? AND landmark_index = ? ORDER BY interval""",
        [nct, k],
    ).fetchall()


def test_withdrawn_at_month_3(con: duckdb.DuckDBPyConnection) -> None:
    assert landmarks(con, "NCT00000001") == [(0, T0, 1, month(3), "INDUSTRY")]
    assert person_period(con, "NCT00000001", 0) == [(1, T0, month(6), 1)]
    assert excluded(con, "NCT00000001")[1] == "ended_or_censored"


def test_terminated_at_month_20(con: duckdb.DuckDBPyConnection) -> None:
    rows = landmarks(con, "NCT00000002")
    assert [r[0] for r in rows] == [0, 1, 2, 3]
    assert {(r[2], r[3]) for r in rows} == {(1, month(20))}
    assert person_period(con, "NCT00000002", 0) == [
        (1, month(0), month(6), 0),
        (2, month(6), month(12), 0),
        (3, month(12), month(18), 0),
        (4, month(18), month(24), 1),
    ]
    assert person_period(con, "NCT00000002", 3) == [(1, month(18), month(24), 1)]


def test_completed_at_month_8(con: duckdb.DuckDBPyConnection) -> None:
    assert [(r[0], r[2], r[3]) for r in landmarks(con, "NCT00000003")] == [
        (0, 2, month(8)),
        (1, 2, month(8)),
    ]
    assert [r[3] for r in person_period(con, "NCT00000003", 0)] == [0, 2]


def test_unknown_at_month_30_is_censored_at_its_verification(
    con: duckdb.DuckDBPyConnection,
) -> None:
    o = outcome(con, "NCT00000004")
    assert (o["event"], o["event_date"], o["censor_reason"]) == (0, month(5), "unknown")
    assert o["lapse_date"] == month(30)
    assert o["registry_unknown"] is True
    # The version shown as UNKNOWN is read as its submitted status.
    status = con.execute(
        "SELECT status FROM cohort_states WHERE nct_id = 'NCT00000004' AND nct_version = 1"
    ).fetchone()
    assert status == ("RECRUITING",)
    assert landmarks(con, "NCT00000004") == [(0, T0, 0, month(5), "INDUSTRY")]
    # Censored inside the first interval: no complete interval, so no person-period row.
    assert person_period(con, "NCT00000004", 0) == []


def test_open_at_the_cutoff(con: duckdb.DuckDBPyConnection) -> None:
    o = outcome(con, "NCT00000005")
    assert (o["event"], o["event_date"], o["censor_reason"]) == (0, CUTOFF, "cutoff")
    rows = landmarks(con, "NCT00000005")
    assert [r[1] for r in rows] == [
        D(2024, 6, 1), D(2024, 12, 1), D(2025, 6, 1), D(2025, 12, 1), D(2026, 6, 1)
    ]  # fmt: skip
    assert excluded(con, "NCT00000005")[5] == "ended_or_censored"  # 2026-12-01, after the cutoff
    assert [r[0] for r in person_period(con, "NCT00000005", 0)] == [1, 2, 3, 4]
    assert person_period(con, "NCT00000005", 3) == [(1, D(2025, 12, 1), D(2026, 6, 1), 0)]
    assert person_period(con, "NCT00000005", 4) == []


def test_a_reversal_is_excluded_and_counted(con: duckdb.DuckDBPyConnection) -> None:
    for nct in ("NCT00000006", "NCT00000014"):  # the second reopened on the same day
        o = outcome(con, nct)
        assert o["reversal"] is True
        assert landmarks(con, nct) == []
        assert excluded(con, nct) == {}  # no candidates at all


def test_registration_after_start_counts_from_registration(
    con: duckdb.DuckDBPyConnection,
) -> None:
    assert landmarks(con, "NCT00000007") == [
        (0, T0, 2, month(9), "MISSING"),
        (1, month(6), 2, month(9), "MISSING"),
    ]


def test_missing_dates(con: duckdb.DuckDBPyConnection) -> None:
    # No completion date of either kind: the verification lapse alone decides.
    o = outcome(con, "NCT00000008")
    assert (o["censor_reason"], o["event_date"], o["lapse_date"]) == ("unknown", T0, D(2017, 2, 1))
    assert landmarks(con, "NCT00000008") == []  # censored at its only verification, t0
    # No verification date: the post date stands in; the lapse is resolved by a completion.
    o = outcome(con, "NCT00000009")
    assert (o["event"], o["event_date"], o["censor_reason"]) == (2, month(30), None)
    assert [r[0] for r in landmarks(con, "NCT00000009")] == [0, 1, 2, 3, 4]
    # No first-post date: outside the population.
    assert outcome(con, "NCT00000019")["in_window"] is None
    assert landmarks(con, "NCT00000019") == []


def test_unknown_by_the_derived_rule_without_an_unknown_version(
    con: duckdb.DuckDBPyConnection,
) -> None:
    o = outcome(con, "NCT00000010")
    assert (o["event"], o["event_date"], o["censor_reason"]) == (0, month(18), "unknown")
    assert o["lapse_date"] == D(2018, 8, 1)
    assert o["registry_unknown"] is False
    assert [r[0] for r in landmarks(con, "NCT00000010")] == [0, 1, 2]


def test_study_type_is_decided_at_each_landmark(con: duckdb.DuckDBPyConnection) -> None:
    assert excluded(con, "NCT00000011")[0] == "not_interventional"
    assert [(r[0], r[2], r[3]) for r in landmarks(con, "NCT00000011")] == [
        (1, 1, month(15)),
        (2, 1, month(15)),
    ]
    assert outcome(con, "NCT00000011")["ever_in_population"] is True


def test_a_lapsed_state_has_no_landmark_until_a_later_version_resolves_it(
    con: duckdb.DuckDBPyConnection,
) -> None:
    reasons = excluded(con, "NCT00000012")
    assert (reasons[5], reasons[6]) == ("lapsed", "lapsed")
    assert [r[0] for r in landmarks(con, "NCT00000012")] == [0, 1, 2, 3, 4]
    o = outcome(con, "NCT00000012")
    assert (o["event"], o["event_date"], o["lapse_date"]) == (2, month(50), None)


def test_no_version_public_yet_means_no_landmark(con: duckdb.DuckDBPyConnection) -> None:
    assert excluded(con, "NCT00000013")[0] == "no_version_yet"
    assert [r[0] for r in landmarks(con, "NCT00000013")] == [1, 2, 3, 4, 5, 6]


def test_suspended_never_lapses(con: duckdb.DuckDBPyConnection) -> None:
    o = outcome(con, "NCT00000015")
    assert (o["censor_reason"], o["lapse_date"]) == ("cutoff", None)
    assert len(landmarks(con, "NCT00000015")) == 7


def test_first_posted_before_2008_is_outside_the_population(
    con: duckdb.DuckDBPyConnection,
) -> None:
    assert outcome(con, "NCT00000016")["in_window"] is False
    assert landmarks(con, "NCT00000016") == []


def test_an_event_on_the_interval_end_belongs_to_that_interval(
    con: duckdb.DuckDBPyConnection,
) -> None:
    assert person_period(con, "NCT00000017", 0) == [
        (1, month(0), month(6), 0),
        (2, month(6), month(12), 1),
    ]


def test_every_landmark_row_is_open_before_its_event(con: duckdb.DuckDBPyConnection) -> None:
    bad = con.execute(
        "SELECT count(*) FROM cohort_landmarks WHERE event_date <= landmark_date"
    ).fetchone()
    assert bad == (0,)
    candidates, kept = con.execute(
        """SELECT count(*), count(*) FILTER (WHERE excluded IS NULL)
        FROM cohort_landmark_candidates"""
    ).fetchone() or (0, 0)
    assert kept == con.execute("SELECT count(*) FROM cohort_landmarks").fetchone()[0]  # type: ignore[index]
    assert candidates == 7 * 14  # 14 trials in the window without a reversal


def test_a_bare_unknown_is_lapsed_from_its_post_date(rules: CohortRules) -> None:
    """UNKNOWN without last_known_status: none in the pinned dataset, possible in a live row."""
    con = cohort(
        [
            version("NCT00000101", 0, month(0), "RECRUITING"),
            version("NCT00000101", 1, month(5), "UNKNOWN"),
            # After a terminal version it is a reversal: the registry shows UNKNOWN only over
            # an open status.
            version("NCT00000102", 0, month(0), "RECRUITING"),
            version("NCT00000102", 1, month(8), "COMPLETED"),
            version("NCT00000102", 2, month(40), "UNKNOWN"),
        ],
        rules,
    )
    o = outcome(con, "NCT00000101")
    assert (o["event"], o["event_date"], o["censor_reason"]) == (0, month(5), "unknown")
    assert o["lapse_date"] == month(5)
    assert landmarks(con, "NCT00000101") == [(0, T0, 0, month(5), "INDUSTRY")]
    assert outcome(con, "NCT00000102")["reversal"] is True
    assert landmarks(con, "NCT00000102") == []


def test_versions_posted_out_of_order_are_refused(rules: CohortRules) -> None:
    """Events follow version numbers and states follow post dates; they must agree."""
    from trialpulse.cli import RefusedError

    rows = [
        version("NCT00000201", 0, month(0), "RECRUITING"),
        version("NCT00000201", 1, month(20), "COMPLETED"),
        version("NCT00000201", 2, month(8), "TERMINATED"),  # posted before version 1
    ]
    with pytest.raises(RefusedError, match="posted earlier than a lower-numbered version"):
        cohort(rows, rules)
    # Versions sharing a post date are in order.
    same_day = [
        version("NCT00000202", 0, month(0), "RECRUITING"),
        version("NCT00000202", 1, month(0), "RECRUITING"),
    ]
    assert len(landmarks(cohort(same_day, rules), "NCT00000202")) == 7


# Cases added after the Step 4 review: each pins a rule that a mutated implementation
# previously survived.


def test_the_first_terminal_version_decides_when_more_follow(rules: CohortRules) -> None:
    con = cohort(
        [
            version("NCT00000301", 0, month(0), "RECRUITING"),
            version("NCT00000301", 1, month(8), "COMPLETED"),
            version("NCT00000301", 2, month(14), "COMPLETED"),
            version("NCT00000302", 0, month(0), "RECRUITING"),
            version("NCT00000302", 1, month(10), "TERMINATED"),
            version("NCT00000302", 2, month(13), "COMPLETED"),
        ],
        rules,
    )
    o = outcome(con, "NCT00000301")
    assert (o["event"], o["event_date"], o["reversal"]) == (2, month(8), False)
    o = outcome(con, "NCT00000302")
    assert (o["event"], o["event_date"], o["reversal"]) == (1, month(10), False)
    assert [r[0] for r in landmarks(con, "NCT00000302")] == [0, 1]


def test_the_stratum_is_the_sponsor_class_at_the_landmark(rules: CohortRules) -> None:
    con = cohort(
        [
            version("NCT00000303", 0, month(0), "RECRUITING", lead_sponsor_class="INDUSTRY"),
            version("NCT00000303", 1, month(9), "RECRUITING", lead_sponsor_class="OTHER"),
        ],
        rules,
    )
    assert [r[4] for r in landmarks(con, "NCT00000303")] == ["INDUSTRY"] * 2 + ["OTHER"] * 5


def test_versions_posted_the_same_day_the_higher_number_is_the_state(
    rules: CohortRules,
) -> None:
    con = cohort(
        [
            version("NCT00000304", 0, month(0), "RECRUITING", study_type="OBSERVATIONAL",
                    lead_sponsor_class="OTHER"),
            version("NCT00000304", 1, month(0), "RECRUITING", lead_sponsor_class="INDUSTRY"),
        ],
        rules,
    )  # fmt: skip
    assert landmarks(con, "NCT00000304")[0] == (0, T0, 0, CUTOFF, "INDUSTRY")


def test_outcomes_use_the_whole_history_after_a_relabel(rules: CohortRules) -> None:
    """ADR 0013: interventional at L0 and L1, relabeled observational, then terminated."""
    con = cohort(
        [
            version("NCT00000305", 0, month(0), "RECRUITING"),
            version("NCT00000305", 1, month(10), "RECRUITING", study_type="OBSERVATIONAL"),
            version("NCT00000305", 2, month(15), "TERMINATED", study_type="OBSERVATIONAL"),
        ],
        rules,
    )
    assert [(r[0], r[2], r[3]) for r in landmarks(con, "NCT00000305")] == [
        (0, 1, month(15)),
        (1, 1, month(15)),
    ]
    assert excluded(con, "NCT00000305")[2] == "not_interventional"


def test_a_reversal_stays_a_reversal_when_the_trial_ends_again(rules: CohortRules) -> None:
    con = cohort(
        [
            version("NCT00000306", 0, month(0), "RECRUITING"),
            version("NCT00000306", 1, month(6), "COMPLETED"),
            version("NCT00000306", 2, month(7), "RECRUITING"),
            version("NCT00000306", 3, month(12), "COMPLETED"),
        ],
        rules,
    )
    assert outcome(con, "NCT00000306")["reversal"] is True
    assert landmarks(con, "NCT00000306") == []


def test_a_lapse_resolved_by_a_new_verification_censors_nothing(rules: CohortRules) -> None:
    con = cohort(
        [
            version("NCT00000307", 0, month(0), "RECRUITING", completion_date=month(6)),
            version("NCT00000307", 1, month(40), "RECRUITING"),  # verified again, far completion
        ],
        rules,
    )
    o = outcome(con, "NCT00000307")
    assert (o["event"], o["event_date"], o["censor_reason"], o["lapse_date"]) == (
        0, CUTOFF, "cutoff", None,
    )  # fmt: skip
    assert [r[0] for r in landmarks(con, "NCT00000307")] == [0, 1, 2, 3, 4]


def test_a_landmark_on_the_first_lapse_day_is_lapsed(rules: CohortRules) -> None:
    con = cohort(
        [
            # Verified December 2014: lapses from 2017-01-01, which is landmark 4.
            version("NCT00000308", 0, month(0), "RECRUITING", status_verified_date=D(2014, 12, 1),
                    completion_date=month(6)),
            version("NCT00000308", 1, month(30), "COMPLETED"),
        ],
        rules,
    )  # fmt: skip
    reasons = excluded(con, "NCT00000308")
    assert (reasons[3], reasons[4]) == (None, "lapsed")


def test_censoring_falls_back_to_the_post_date_without_a_verification(
    rules: CohortRules,
) -> None:
    con = cohort(
        [
            version("NCT00000309", 0, month(0), "RECRUITING", completion_date=month(6)),
            version("NCT00000309", 1, month(8), "RECRUITING", completion_date=month(6),
                    status_verified_date=None),
        ],
        rules,
    )  # fmt: skip
    o = outcome(con, "NCT00000309")
    assert (o["event"], o["event_date"], o["censor_reason"], o["lapse_date"]) == (
        0, month(8), "unknown", D(2017, 10, 1),
    )  # fmt: skip


def test_a_status_that_is_not_open_gives_no_landmark(rules: CohortRules) -> None:
    con = cohort(
        [
            version("NCT00000310", 0, month(0), "RECRUITING"),
            version("NCT00000310", 1, month(4), "WITHHELD"),
            version("NCT00000310", 2, month(10), "RECRUITING"),
        ],
        rules,
    )
    reasons = excluded(con, "NCT00000310")
    assert (reasons[0], reasons[1], reasons[2]) == (None, "not_open", None)


def test_the_population_window_starts_on_its_first_day(rules: CohortRules) -> None:
    first, before = D(2008, 1, 1), D(2007, 12, 31)
    con = cohort(
        [
            version("NCT00000311", 0, first, "RECRUITING", study_first_post_date=first),
            version("NCT00000312", 0, before, "RECRUITING", study_first_post_date=before),
        ],
        rules,
    )
    assert outcome(con, "NCT00000311")["in_window"] is True
    assert len(landmarks(con, "NCT00000311")) == 7
    assert outcome(con, "NCT00000312")["in_window"] is False


def test_the_cutoff_bounds_what_the_build_can_see(rules: CohortRules) -> None:
    rows = [
        # Lapses from 2017-02-01 (verified January 2015, completion passed).
        version("NCT00000313", 0, month(0), "RECRUITING", completion_date=month(6)),
        # Terminated in September 2016.
        version("NCT00000314", 0, month(0), "RECRUITING"),
        version("NCT00000314", 1, month(20), "TERMINATED"),
    ]
    on_lapse_day = cohort(rows, replace(rules, cutoff=D(2017, 2, 1)))
    assert outcome(on_lapse_day, "NCT00000313")["censor_reason"] == "unknown"
    day_before = cohort(rows, replace(rules, cutoff=D(2017, 1, 31)))
    assert outcome(day_before, "NCT00000313")["censor_reason"] == "cutoff"
    # A version posted after the cutoff does not exist yet.
    early = cohort(rows, replace(rules, cutoff=D(2016, 1, 1)))
    o = outcome(early, "NCT00000314")
    assert (o["event"], o["event_date"], o["censor_reason"]) == (0, D(2016, 1, 1), "cutoff")
    assert [r[0] for r in landmarks(early, "NCT00000314")] == [0, 1]
    # Neither does a later verification: the state at the cutoff is still the lapsed one.
    reverified = [*rows, version("NCT00000313", 1, month(40), "RECRUITING")]
    mid = cohort(reverified, replace(rules, cutoff=D(2017, 6, 1)))
    assert outcome(mid, "NCT00000313")["censor_reason"] == "unknown"
