"""The registry's UNKNOWN rule (ADR 0014): edge dates, and the SQL form against its twin."""

import datetime as dt
import itertools

import duckdb
import pytest

from trialpulse.cohort.rules import (
    CohortRules,
    add_months,
    completion_ref,
    completion_ref_sql,
    is_lapsed,
    lapse_from,
    lapse_from_sql,
    period_end,
    submitted_status,
    submitted_status_sql,
)

D = dt.date


def test_verification_counts_by_month(rules: CohortRules) -> None:
    # Verified in September 2024 (any day): still within 2 years on 2026-09-30.
    for day in (1, 15, 30):
        args = (rules, "RECRUITING", D(2024, 9, day), D(2025, 1, 1), D(2024, 9, day))
        assert not is_lapsed(*args, on=D(2026, 9, 30))
        assert is_lapsed(*args, on=D(2026, 10, 1))


def test_completion_counts_at_the_end_of_its_period(rules: CohortRules) -> None:
    month_precision = period_end(D(2026, 3, 1), "month")
    assert month_precision == D(2026, 3, 31)
    assert period_end(D(2026, 1, 1), "year") == D(2026, 12, 31)
    assert period_end(D(2026, 3, 9), "day") == D(2026, 3, 9)
    old_verification = D(2020, 1, 1)
    assert lapse_from(rules, "RECRUITING", D(2020, 1, 1), month_precision, old_verification) == D(
        2026, 4, 1
    )
    # Both completion dates missing: the completion condition holds.
    assert lapse_from(rules, "RECRUITING", D(2020, 1, 1), None, old_verification) == D(2022, 2, 1)


def test_only_the_rule_statuses_lapse(rules: CohortRules) -> None:
    for status in ("SUSPENDED", "COMPLETED", "TERMINATED", "WITHDRAWN", "WITHHELD"):
        assert lapse_from(rules, status, D(2010, 1, 1), D(2010, 6, 1), D(2010, 1, 1)) is None
    # A bare UNKNOWN (no last_known_status) is the registry saying the trial is lapsed.
    assert lapse_from(rules, "UNKNOWN", D(2010, 1, 1), D(2030, 1, 1), D(2010, 1, 1)) == D(
        2010, 1, 1
    )
    for status in rules.lapse_statuses:
        assert lapse_from(rules, status, D(2010, 1, 1), D(2010, 6, 1), D(2010, 1, 1)) == D(
            2012, 2, 1
        )


def test_a_lapse_never_starts_before_the_state(rules: CohortRules) -> None:
    posted = D(2024, 5, 20)
    assert lapse_from(rules, "RECRUITING", posted, D(2019, 1, 1), D(2019, 1, 1)) == posted


def test_submitted_status_restores_the_status_behind_unknown(rules: CohortRules) -> None:
    assert submitted_status(rules, "UNKNOWN", "RECRUITING") == "RECRUITING"
    assert submitted_status(rules, "UNKNOWN", None) == "UNKNOWN"
    assert submitted_status(rules, "COMPLETED", None) == "COMPLETED"


def test_add_months_clamps_like_the_harness() -> None:
    import numpy as np

    from trialpulse.dates import add_months as numpy_add_months

    days = [D(2020, 1, 31), D(2020, 2, 29), D(2019, 8, 31), D(2021, 12, 15)]
    for day, months in itertools.product(days, (1, 6, 12, 18, 24)):
        expected = numpy_add_months(np.array([day], dtype="datetime64[D]"), months)[0]
        assert add_months(day, months) == expected.astype(D)


def test_sql_forms_agree_with_their_python_twins(rules: CohortRules) -> None:
    statuses = ["RECRUITING", "ACTIVE_NOT_RECRUITING", "SUSPENDED", "COMPLETED", "UNKNOWN"]
    posted = [D(2016, 2, 29), D(2020, 7, 15), D(2012, 1, 15)]
    completions = [
        (None, None, None, None),
        (D(2017, 3, 1), "month", None, None),
        (None, None, D(2018, 1, 1), "year"),
        (D(2016, 1, 31), "day", D(2015, 5, 1), "month"),
        # Late completion dates, so that the completion reference decides the lapse day.
        (D(2022, 6, 1), "month", D(2021, 3, 1), "month"),
        (None, None, D(2021, 3, 1), "month"),
        (D(2022, 1, 1), "year", None, None),
        (D(2022, 6, 15), "day", D(2023, 1, 1), "month"),
    ]
    verified = [None, D(2016, 1, 31), D(2019, 12, 1)]
    cases = list(itertools.product(statuses, posted, completions, verified))
    with duckdb.connect() as con:
        con.execute(
            """CREATE TABLE v (overall_status VARCHAR, last_known_status VARCHAR,
            effective_date DATE, completion_date DATE, completion_date_precision VARCHAR,
            primary_completion_date DATE, primary_completion_date_precision VARCHAR,
            status_verified_date DATE)"""
        )
        con.executemany(
            "INSERT INTO v VALUES (?, NULL, ?, ?, ?, ?, ?, ?)",
            [(s, p, *c, ver) for s, p, c, ver in cases],
        )
        sql = lapse_from_sql(
            rules,
            submitted_status_sql(rules),
            "v.effective_date",
            completion_ref_sql(),
            "coalesce(v.status_verified_date, v.effective_date)",
        )
        got = con.execute(f"SELECT {sql} FROM v").fetchall()
    for (status, day, completion, ver), (value,) in zip(cases, got, strict=True):
        expected = lapse_from(rules, status, day, completion_ref(*completion), ver)
        assert value == expected, (status, day, completion, ver)


def test_a_month_offset_of_the_rule_is_configurable(rules: CohortRules) -> None:
    from dataclasses import replace

    shorter = replace(rules, lapse_months=18)
    assert lapse_from(shorter, "RECRUITING", D(2020, 1, 1), None, D(2020, 1, 15)) == D(2021, 8, 1)
    assert add_months(D(2020, 1, 15), 18) == D(2021, 7, 15)


@pytest.mark.parametrize("status", ["NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"])
def test_every_open_status_the_registry_names_can_lapse(rules: CohortRules, status: str) -> None:
    assert is_lapsed(rules, status, D(2015, 1, 1), D(2016, 1, 1), D(2015, 1, 1), D(2020, 1, 1))


@pytest.mark.parametrize(
    ("completion", "expected"),
    [
        ((D(2022, 6, 1), "month", D(2021, 3, 1), "month"), D(2022, 7, 1)),  # completion first
        ((None, None, D(2021, 3, 1), "month"), D(2021, 4, 1)),  # else primary completion
        ((D(2022, 1, 1), "year", None, None), D(2023, 1, 1)),  # the end of the year
        ((D(2022, 6, 15), "day", D(2023, 1, 1), "month"), D(2022, 6, 16)),  # a full date
    ],
)
def test_the_completion_reference_decides_when_it_is_the_latest(
    rules: CohortRules, completion: tuple[dt.date | None, str | None, dt.date | None, str | None],
    expected: dt.date,
) -> None:  # fmt: skip
    """Posted and verified in January 2016: the verification lapses on 2018-02-01, earlier
    than every completion reference here, so the reference alone sets the lapse day."""
    posted = D(2016, 1, 15)
    assert lapse_from(rules, "RECRUITING", posted, completion_ref(*completion), posted) == expected
    with duckdb.connect() as con:
        con.execute(
            """CREATE TABLE v AS SELECT 'RECRUITING' AS overall_status,
              NULL::VARCHAR AS last_known_status, ?::DATE AS effective_date,
              ?::DATE AS completion_date, ?::VARCHAR AS completion_date_precision,
              ?::DATE AS primary_completion_date,
              ?::VARCHAR AS primary_completion_date_precision,
              ?::DATE AS status_verified_date""",
            [posted, *completion, posted],
        )
        sql = lapse_from_sql(
            rules, submitted_status_sql(rules), "v.effective_date", completion_ref_sql(),
            "v.status_verified_date",
        )  # fmt: skip
        assert con.execute(f"SELECT {sql} FROM v").fetchone() == (expected,)
