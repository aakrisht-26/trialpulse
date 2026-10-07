"""The cohort as of a walk-forward origin (ADR 0016).

Training rows for origin T come from the cohort as it would have been built on T: only
versions posted before T are known, and observation ends on T. So a training label cannot
depend on anything posted on or after its origin. The mini histories are those of
test_cohort.py (T0 is 2015-01-01), where the final cohort knows several things an origin
did not: a later termination, a later reversal, a lapse resolved later, an UNKNOWN
censoring that had not happened yet.
"""

import datetime as dt
from typing import Any

import duckdb
import pytest

from trialpulse.cli import RefusedError
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import load_project_config

from .conftest import CUTOFF, T0, cohort, month, version
from .test_cohort import MINI, landmarks, outcome

D = dt.date
ORIGINS = (D(2016, 1, 1), D(2017, 1, 1), D(2018, 1, 1), D(2019, 1, 1))
DAY = dt.timedelta(days=1)


@pytest.fixture(scope="module")
def rules() -> CohortRules:
    return CohortRules.from_config(load_project_config(), cutoff=CUTOFF)


def as_of(
    rows: list[dict[str, Any]], rules: CohortRules, origin: dt.date
) -> duckdb.DuckDBPyConnection:
    return cohort(rows, rules.as_of(origin))


def dump(con: duckdb.DuckDBPyConnection) -> dict[str, list[tuple[Any, ...]]]:
    """Every training row of a build: the landmark rows and the person-period rows."""
    return {
        "landmarks": con.execute(
            "SELECT * FROM cohort_landmarks ORDER BY trial_id, landmark_index"
        ).fetchall(),
        "person_period": con.execute(
            "SELECT * FROM cohort_person_period ORDER BY trial_id, landmark_index, interval"
        ).fetchall(),
    }


def test_the_rules_as_of_an_origin(rules: CohortRules) -> None:
    then = rules.as_of(D(2016, 1, 1))
    assert then.cutoff == D(2016, 1, 1)  # observation ends on the origin
    assert then.last_post_date == D(2015, 12, 31)  # versions posted before it are known
    assert rules.last_post_date == rules.cutoff == CUTOFF
    assert then.lapse_months == rules.lapse_months


def test_a_terminal_version_is_known_only_if_posted_before_the_origin(rules: CohortRules) -> None:
    """B is terminated in a version posted at month 20 (2016-09-01)."""
    terminated = month(20)
    for origin, known in ((D(2016, 1, 1), False), (terminated, False), (terminated + DAY, True)):
        con = as_of(MINI, rules, origin)
        result = outcome(con, "NCT00000002")
        if known:
            assert (result["event"], result["event_date"]) == (1, terminated)
        else:  # still open as far as the origin knows: censored on the origin
            assert (result["event"], result["event_date"]) == (0, origin)
            assert result["censor_reason"] == "cutoff"
        assert result["terminal_status"] == ("TERMINATED" if known else None)


def test_a_reversal_posted_after_the_origin_does_not_exclude_the_trial(rules: CohortRules) -> None:
    """F completes at month 10 and is recruiting again at month 14. On 2016-01-01 the
    reopening is not posted yet: the trial is a completion with two landmark rows. The final
    cohort, which knows the reversal, has no row for it."""
    then = as_of(MINI, rules, D(2016, 1, 1))
    assert outcome(then, "NCT00000006")["reversal"] is False
    assert [(r[0], r[2], r[3]) for r in landmarks(then, "NCT00000006")] == [
        (0, 2, month(10)),
        (1, 2, month(10)),
    ]
    later = as_of(MINI, rules, month(14) + DAY)
    assert outcome(later, "NCT00000006")["reversal"] is True
    assert landmarks(later, "NCT00000006") == []
    assert landmarks(cohort(MINI, rules), "NCT00000006") == []


def test_a_lapse_resolved_after_the_origin_is_still_a_lapse_on_the_origin(
    rules: CohortRules,
) -> None:
    """H2 is lapsed from 2017-02-01 and completes in a version posted at month 30
    (2017-07-01). On 2017-06-01 nobody knows it will report again: it is censored under the
    UNKNOWN rule, at its last verification (the registration), so it has no training row.
    The final cohort has its completion."""
    then = as_of(MINI, rules, D(2017, 6, 1))
    result = outcome(then, "NCT00000009")
    assert (result["event"], result["censor_reason"], result["event_date"]) == (0, "unknown", T0)
    assert landmarks(then, "NCT00000009") == []
    final = cohort(MINI, rules)
    assert outcome(final, "NCT00000009")["event"] == 2
    assert len(landmarks(final, "NCT00000009")) > 0
    # Before the lapse begins, the origin sees an open trial.
    before = outcome(as_of(MINI, rules, D(2017, 2, 1) - DAY), "NCT00000009")
    assert (before["event"], before["censor_reason"]) == (0, "cutoff")


def test_an_unknown_censoring_that_had_not_happened_yet_is_not_applied(rules: CohortRules) -> None:
    """I is censored in the final cohort at its last verification, 2016-07-01, because it
    lapsed on 2018-08-01. On 2018-01-01 it had not lapsed: it is an open trial, censored on
    the origin, with the landmark rows the final cohort drops."""
    final = cohort(MINI, rules)
    assert outcome(final, "NCT00000010")["censor_reason"] == "unknown"
    assert [r[0] for r in landmarks(final, "NCT00000010")] == [0, 1, 2]
    origin = D(2018, 1, 1)
    then = as_of(MINI, rules, origin)
    result = outcome(then, "NCT00000010")
    assert (result["event"], result["censor_reason"], result["event_date"]) == (0, "cutoff", origin)
    assert [r[0] for r in landmarks(then, "NCT00000010")] == [0, 1, 2, 3, 4, 5]  # L6 is the origin
    # Once it has lapsed, an origin sees what the final cohort sees.
    after = outcome(as_of(MINI, rules, D(2018, 8, 1)), "NCT00000010")
    assert (after["censor_reason"], after["event_date"]) == ("unknown", month(18))


def test_a_study_type_posted_after_the_origin_is_not_known(rules: CohortRules) -> None:
    """J is observational at registration and interventional from month 4."""
    assert outcome(as_of(MINI, rules, month(4)), "NCT00000011")["ever_in_population"] is False
    assert landmarks(as_of(MINI, rules, month(4)), "NCT00000011") == []
    assert outcome(as_of(MINI, rules, month(4) + DAY), "NCT00000011")["ever_in_population"] is True


@pytest.mark.parametrize("origin", ORIGINS)
def test_training_rows_hold_nothing_dated_after_their_origin(
    rules: CohortRules, origin: dt.date
) -> None:
    rows = dump(as_of(MINI, rules, origin))
    assert len(rows["landmarks"]) > 0
    for _, _, landmark_date, event, event_date, _ in rows["landmarks"]:
        assert landmark_date < origin
        assert event_date <= origin
        assert event == 0 or event_date < origin  # an event dated on the origin is not known
    assert len(rows["person_period"]) > 0
    for row in rows["person_period"]:
        interval_end, row_outcome, event_date = row[5], row[6], row[7]
        assert interval_end <= origin  # ADR 0015: only intervals that ended in the window
        assert row_outcome == 0 or event_date < origin


def _rewritten_from(rows: list[dict[str, Any]], origin: dt.date) -> list[dict[str, Any]]:
    """The same histories with everything posted on or after `origin` rewritten: statuses,
    study types, dates and sponsor classes change, some versions vanish, and every trial
    gains a termination and a reopening. For a trial with no later version, the termination
    is posted on the origin itself."""
    statuses = ("TERMINATED", "COMPLETED", "RECRUITING", "WITHDRAWN", "SUSPENDED", "UNKNOWN")
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        if row["effective_date"] < origin:
            out.append(dict(row))
        elif i % 3:  # one in three later versions is dropped
            status = statuses[i % len(statuses)]
            out.append(
                {
                    **row,
                    "overall_status": status,
                    "last_known_status": "RECRUITING" if status == "UNKNOWN" else None,
                    "study_type": "OBSERVATIONAL" if i % 2 else "INTERVENTIONAL",
                    "status_verified_date": D(2010, 1, 1),
                    "completion_date": D(2012, 1, 1),
                    "primary_completion_date": D(2011, 1, 1),
                    "lead_sponsor_class": "NIH",
                }
            )
    for nct in sorted({row["nct_id"] for row in rows}):
        first_post = next(r["study_first_post_date"] for r in rows if r["nct_id"] == nct)
        common = {"study_first_post_date": first_post, "lead_sponsor_class": "FED"}
        # After the trial's other versions, so that the final build accepts the order.
        posted = max([origin, *[r["effective_date"] for r in out if r["nct_id"] == nct]])
        out.append(version(nct, 900, posted, "TERMINATED", **common))
        out.append(version(nct, 901, posted + dt.timedelta(days=40), "RECRUITING", **common))
    return out


@pytest.mark.parametrize("origin", ORIGINS)
def test_a_training_label_cannot_change_when_later_versions_are_rewritten(
    rules: CohortRules, origin: dt.date
) -> None:
    """ADR 0016, the property itself: rewrite every version posted on or after the origin,
    add later versions to every trial, and the training rows of the origin are the same
    rows, bit for bit."""
    rewritten = _rewritten_from(MINI, origin)
    assert rewritten != MINI
    assert dump(as_of(rewritten, rules, origin)) == dump(as_of(MINI, rules, origin))


def test_the_final_cohort_does_change_when_those_versions_are_rewritten(rules: CohortRules) -> None:
    """The control: the same rewrite changes the final cohort, and it changes the training
    rows of a later origin, which is allowed to know those versions."""
    origin = D(2016, 1, 1)
    rewritten = _rewritten_from(MINI, origin)
    assert dump(cohort(rewritten, rules)) != dump(cohort(MINI, rules))
    assert dump(as_of(rewritten, rules, D(2018, 1, 1))) != dump(as_of(MINI, rules, D(2018, 1, 1)))


def test_a_version_posted_the_day_before_the_origin_is_known(rules: CohortRules) -> None:
    origin = D(2016, 1, 1)
    rows = [
        version("NCT00000101", 0, month(0), "RECRUITING"),
        version("NCT00000101", 1, origin - DAY, "TERMINATED"),
        version("NCT00000102", 0, month(0), "RECRUITING"),
        version("NCT00000102", 1, origin, "TERMINATED"),
    ]
    con = as_of(rows, rules, origin)
    assert (outcome(con, "NCT00000101")["event"], outcome(con, "NCT00000102")["event"]) == (1, 0)
    assert outcome(con, "NCT00000101")["event_date"] == origin - DAY
    assert outcome(con, "NCT00000102")["event_date"] == origin


def test_versions_out_of_order_after_the_origin_do_not_stop_an_earlier_build(
    rules: CohortRules,
) -> None:
    """The build refuses versions posted earlier than a lower-numbered one. For a build as of
    an origin that check looks at the versions the origin knows, and at no other."""
    rows = [
        version("NCT00000201", 0, month(0), "RECRUITING"),
        version("NCT00000201", 1, month(30), "RECRUITING"),
        version("NCT00000201", 2, month(20), "TERMINATED"),  # posted before version 1
    ]
    con = as_of(rows, rules, D(2016, 1, 1))
    assert outcome(con, "NCT00000201")["event"] == 0


def test_a_version_posted_on_the_origin_day_is_not_checked_for_order(rules: CohortRules) -> None:
    """Version 1 is posted on the origin day, so the origin does not know it. Version 2,
    posted a month earlier, is in order among the versions the origin does know."""
    origin = D(2016, 1, 1)
    rows = [
        version("NCT00000202", 0, month(0), "RECRUITING"),
        version("NCT00000202", 1, origin, "RECRUITING"),
        version("NCT00000202", 2, month(11), "TERMINATED"),
    ]
    con = as_of(rows, rules, origin)
    assert outcome(con, "NCT00000202")["event"] == 1
    assert outcome(con, "NCT00000202")["event_date"] == month(11)
    with pytest.raises(RefusedError, match="posted earlier than a lower-numbered"):
        cohort(rows, rules)  # the final build knows all three
