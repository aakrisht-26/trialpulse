"""Person-period expansion and administrative censoring at an origin."""

import datetime as dt
from dataclasses import replace

import numpy as np

from trialpulse.cohort.person_period import PERSON_PERIOD_COLUMNS, expand, training_rows
from trialpulse.cohort.rules import CohortRules
from trialpulse.dates import add_months, days_between
from trialpulse.models.aalen_johansen import aalen_johansen


def _landmarks(rows: list[tuple[str, str, int, str]]) -> dict[str, np.ndarray]:
    return {
        "trial_id": np.array([r[0] for r in rows]),
        "landmark_index": np.zeros(len(rows), dtype=np.int64),
        "landmark_date": np.array([r[1] for r in rows], dtype="datetime64[D]"),
        "event": np.array([r[2] for r in rows], dtype=np.int64),
        "event_date": np.array([r[3] for r in rows], dtype="datetime64[D]"),
        "stratum": np.array(["INDUSTRY"] * len(rows)),
    }


def _pairs(out: dict[str, np.ndarray], trial: str) -> list[tuple[int, int]]:
    mask = out["trial_id"] == trial
    return list(zip(out["interval"][mask].tolist(), out["outcome"][mask].tolist(), strict=True))


def test_censoring_on_an_interval_end_keeps_that_interval(rules: CohortRules) -> None:
    out = expand(
        _landmarks(
            [
                ("A", "2019-01-01", 0, "2020-01-01"),  # censored exactly at the end of j2
                ("B", "2019-01-01", 0, "2019-12-31"),  # one day earlier: j2 is dropped
                ("C", "2019-01-01", 0, "2030-01-01"),  # followed through all four
                ("D", "2019-01-01", 2, "2019-07-01"),  # completion on the end of j1
                ("E", "2019-01-01", 1, "2019-07-02"),  # early stop the day after
            ]
        ),
        rules,
    )
    assert set(out) == set(PERSON_PERIOD_COLUMNS)
    assert _pairs(out, "A") == [(1, 0), (2, 0)]
    assert _pairs(out, "B") == [(1, 0)]
    assert _pairs(out, "C") == [(1, 0), (2, 0), (3, 0), (4, 0)]
    assert _pairs(out, "D") == [(1, 2)]
    assert _pairs(out, "E") == [(1, 0), (2, 1)]
    ends = out["interval_end"][out["trial_id"] == "C"]
    assert ends.astype(str).tolist() == ["2019-07-01", "2020-01-01", "2020-07-01", "2021-01-01"]


def test_intervals_use_calendar_months_with_clamping(rules: CohortRules) -> None:
    out = expand(_landmarks([("A", "2019-08-31", 0, "2030-01-01")]), rules)
    assert out["interval_end"].astype(str).tolist() == [
        "2020-02-29",
        "2020-08-31",
        "2021-02-28",
        "2021-08-31",
    ]


def test_an_interval_ending_after_the_cutoff_is_dropped_whatever_happened(
    rules: CohortRules,
) -> None:
    """Otherwise the newest intervals would hold events and no continuations."""
    early = replace(rules, cutoff=dt.date(2020, 3, 1))
    out = expand(
        _landmarks(
            [
                ("A", "2019-01-01", 1, "2019-11-01"),  # stop in j2, which ended before the cutoff
                ("B", "2019-07-01", 1, "2020-02-01"),  # stop in j2, which ends after the cutoff
                ("C", "2019-07-01", 0, "2020-03-01"),  # open at the cutoff, inside j2
                ("D", "2019-09-01", 2, "2020-03-01"),  # completion on the cutoff, the end of j1
            ]
        ),
        early,
    )
    assert _pairs(out, "A") == [(1, 0), (2, 1)]
    assert _pairs(out, "B") == [(1, 0)]  # the stop itself is in an unfinished interval
    assert _pairs(out, "C") == [(1, 0)]
    assert _pairs(out, "D") == [(1, 2)]


def test_training_rows_censor_administratively_at_the_origin(rules: CohortRules) -> None:
    out = expand(
        _landmarks(
            [
                ("A", "2015-01-01", 1, "2016-03-01"),  # stop in j3, after the origin
                ("B", "2015-01-01", 1, "2015-10-01"),  # stop in j2, before the origin
                ("C", "2015-01-01", 2, "2016-01-01"),  # completion on the origin, end of j2
                ("D", "2016-02-01", 0, "2030-01-01"),  # landmark after the origin
                ("E", "2015-09-01", 2, "2015-11-01"),  # completion before T, in j1 ending after T
                ("F", "2015-09-01", 0, "2030-01-01"),  # still open: the same unfinished j1
            ]
        ),
        rules,
    )
    train = training_rows(out, np.datetime64("2016-01-01"))
    assert _pairs(train, "A") == [(1, 0), (2, 0)]  # j3 ends after T
    assert _pairs(train, "B") == [(1, 0), (2, 1)]
    assert _pairs(train, "C") == [(1, 0), (2, 0)]  # an event dated T is not known at T
    assert _pairs(train, "D") == []
    # j1 of E and F ends after T: neither the event row nor the continuation is kept.
    assert _pairs(train, "E") == []
    assert _pairs(train, "F") == []
    assert set(train) == set(PERSON_PERIOD_COLUMNS)


def _discrete_cif(rows: dict[str, np.ndarray], intervals: int) -> float:
    survival, cif = 1.0, 0.0
    for j in range(1, intervals + 1):
        outcome = rows["outcome"][rows["interval"] == j]
        stop, complete = float((outcome == 1).mean()), float((outcome == 2).mean())
        cif += survival * stop
        survival *= 1.0 - stop - complete
    return cif


def test_a_no_covariate_discrete_cif_matches_aalen_johansen(rules: CohortRules) -> None:
    """Staggered entry and one cutoff, as in the real cohort: the hazards from person-period
    rows, pooled without covariates, give Aalen-Johansen's CIF at the interval boundaries
    (the property Section 9 requires of the discrete-time models)."""
    rng = np.random.default_rng(7)
    n = 200_000
    start = np.datetime64("2019-01-01") + rng.integers(0, 4 * 365, n).astype("timedelta64[D]")
    stop = rng.exponential(1500, n)
    complete = rng.exponential(900, n)
    days = np.floor(np.minimum(stop, complete)).astype(np.int64) + 1
    event = np.where(stop < complete, 1, 2)
    event_date = start + days.astype("timedelta64[D]")
    cutoff = np.datetime64("2023-06-30")
    late = event_date > cutoff
    landmarks = {
        "trial_id": np.arange(n).astype(str),
        "landmark_index": np.zeros(n, dtype=np.int64),
        "landmark_date": start,
        "event": np.where(late, 0, event).astype(np.int64),
        "event_date": np.where(late, cutoff, event_date),
        "stratum": np.array(["X"] * n),
    }
    synthetic = replace(rules, cutoff=cutoff.astype(dt.date))
    rows = expand(landmarks, synthetic)
    curve = aalen_johansen(
        days_between(landmarks["landmark_date"], landmarks["event_date"]), landmarks["event"]
    )
    for intervals in (2, 4):
        # Evaluate Aalen-Johansen at a typical interval boundary (calendar months vary a day).
        boundary = days_between(start, add_months(start, 6 * intervals))
        expected = float(np.mean(curve.at(boundary)))
        assert abs(_discrete_cif(rows, intervals) - expected) < 0.003
    # The biased variant (keeping events in unfinished intervals) would miss this tolerance.
    assert _discrete_cif(rows, 2) > 0.05
