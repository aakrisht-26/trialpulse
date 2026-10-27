"""The population of score(as_of_date) and the reason codes (ADR 0034), on hand-built
version histories. No real data, no model."""

import datetime as dt

import duckdb
import numpy as np
import pandas as pd

from trialpulse.cohort.outcomes import build_states as build_cohort_states
from trialpulse.dates import add_months
from trialpulse.features.states import expose_versions
from trialpulse.scoring import score

from .conftest import feature_helpers as fh

D = dt.date
RULES = fh.RULES
DAY = D(2019, 6, 30)


def _population(rows: list[dict]) -> pd.DataFrame:
    with duckdb.connect() as con:
        fh.load_versions(con, rows, "all_versions")
        expose_versions(con, "all_versions", RULES)
        build_cohort_states(con, RULES)
        return score.population(con, RULES, DAY, source="all_versions")


def test_the_population_is_the_open_interventional_trials_on_the_day() -> None:
    v = fh.version
    rows = [
        v("NCT1", 1, D(2018, 1, 10), "RECRUITING"),  # open
        v("NCT2", 1, D(2018, 1, 10), "RECRUITING"),
        v("NCT2", 2, D(2019, 3, 1), "COMPLETED"),  # completed before the day
        v("NCT3", 1, D(2018, 1, 10), "RECRUITING"),
        v("NCT3", 2, D(2019, 7, 2), "TERMINATED"),  # terminated after the day: open on it
        v("NCT4", 1, D(2019, 7, 1), "RECRUITING"),  # first posted after the day
        v("NCT5", 1, D(2007, 6, 1), "RECRUITING"),  # first posted before 2008
        v("NCT6", 1, D(2018, 1, 10), "RECRUITING", study_type="OBSERVATIONAL"),
        v("NCT7", 1, D(2015, 1, 10), "RECRUITING", completion_date=D(2016, 1, 1)),  # lapsed
        v("NCT8", 1, D(2017, 1, 10), "SUSPENDED"),  # suspended counts as open
    ]
    people = _population(rows)
    assert people["trial_id"].tolist() == ["NCT1", "NCT3", "NCT8"]
    assert people.set_index("trial_id")["content_hash"].isna().all()  # none set in the rows
    assert not people["reversal"].any()


def test_a_reversal_counts_only_when_it_is_visible_by_the_day() -> None:
    v = fh.version
    rows = [
        v("NCT1", 1, D(2017, 1, 10), "RECRUITING"),
        v("NCT1", 2, D(2018, 1, 10), "TERMINATED"),
        v("NCT1", 3, D(2018, 6, 10), "RECRUITING"),  # reopened before the day
        v("NCT2", 1, D(2017, 1, 10), "RECRUITING", content_hash="h1"),
        v("NCT2", 2, D(2019, 8, 1), "TERMINATED"),  # the terminal version comes later
        v("NCT2", 3, D(2019, 9, 1), "RECRUITING"),
    ]
    people = _population(rows).set_index("trial_id")
    assert people.loc["NCT1", "reversal"]
    assert not people.loc["NCT2", "reversal"]  # what happens after the day is not read
    assert people.loc["NCT2", "content_hash"] == "h1"


def _decisions(served_42: bool, served_60: bool) -> dict:
    return {
        "between_landmarks": "B",
        "bands": {"42 to 59 months": {"served": served_42},
                  "60 to 119 months": {"served": served_60}},
    }  # fmt: skip


def test_each_open_trial_is_scored_flagged_or_given_a_reason() -> None:
    people = pd.DataFrame(
        {
            "trial_id": ["A", "B", "C", "D", "E", "F"],
            "t0": pd.to_datetime(["2018-01-15", "2016-03-31", "2015-06-01", "2012-01-01",
                                  "2009-01-01", "2018-01-15"]),
            "reversal": [False, False, False, False, False, True],
            "content_hash": ["a", "b", "c", "d", "e", "f"],
            "sponsor_class": ["INDUSTRY"] * 6,
        }
    )  # fmt: skip
    out = score.classify(people, DAY, RULES, _decisions(True, False)).set_index("trial_id")
    # A: 17 months, landmark 2. B: 39 months, landmark 6 unflagged (between landmarks 6 and 7).
    # C: 48 months, band 42 to 59 (served, flagged). D: 89 months, band 60 to 119 (not served).
    # E: 125 months. F: a reversal.
    assert out["true_index"].tolist() == [2, 6, 8, 14, 20, 2]
    assert out["k_star"].tolist() == [2, 6, 6, 6, 6, 2]
    assert out["flagged"].tolist() == [False, False, True, False, False, False]
    assert out["reason"].tolist() == [None, None, None, score.BAND, score.OLDEST, score.REVERSAL]


def test_population_edges_stops_lapses_and_the_first_post_floor() -> None:
    v = fh.version
    rows = [
        v("NCT1", 1, D(2018, 1, 10), "RECRUITING"),
        v("NCT1", 2, D(2019, 3, 1), "TERMINATED"),  # stopped early before the day
        v("NCT2", 1, D(2018, 1, 10), "RECRUITING"),
        v("NCT2", 2, D(2019, 3, 1), "WITHDRAWN"),
        v("NCT3", 1, D(2008, 1, 1), "RECRUITING"),  # first posted on the floor: in
        # Lapsed from the day itself (completion 29 June, verified long ago): out.
        v("NCT4", 1, D(2015, 1, 10), "RECRUITING", completion_date=D(2019, 6, 29),
          status_verified_date=D(2015, 1, 1)),
        # Lapsed from the day after: still open on the day.
        v("NCT5", 1, D(2015, 1, 10), "RECRUITING", completion_date=D(2019, 6, 30),
          status_verified_date=D(2015, 1, 1)),
        # Terminated, then UNKNOWN whose submitted status is open: open, with a reversal.
        v("NCT6", 1, D(2016, 1, 10), "RECRUITING"),
        v("NCT6", 2, D(2017, 1, 10), "TERMINATED"),
        v("NCT6", 3, D(2018, 1, 10), "UNKNOWN", last_known_status="RECRUITING"),
    ]  # fmt: skip
    people = _population(rows).set_index("trial_id")
    assert people.index.tolist() == ["NCT3", "NCT5", "NCT6"]
    assert people["reversal"].to_dict() == {"NCT3": False, "NCT5": False, "NCT6": True}


def test_the_band_edges_follow_the_true_index_on_and_before_a_landmark() -> None:
    day = D(2019, 6, 15)  # mid-month, so no clamped day moves a landmark onto it
    on = add_months(np.full(3, np.datetime64(day, "D")), -6 * np.array([7, 10, 20]))
    # Each pair: landmark k falls on the day, then on the next day.
    t0 = np.ravel(np.column_stack([on, on + np.timedelta64(1, "D")]))
    people = pd.DataFrame({"trial_id": list("ABCDEF"), "t0": t0,
                           "reversal": False, "content_hash": "h",
                           "sponsor_class": "INDUSTRY"})  # fmt: skip
    served = score.classify(people, day, RULES, _decisions(True, True))
    assert served["true_index"].tolist() == [7, 6, 10, 9, 20, 19]
    assert served["flagged"].tolist() == [True, False, True, True, False, True]
    assert served["reason"].tolist() == [None, None, None, None, score.OLDEST, None]
    partly = score.classify(people, day, RULES, _decisions(False, True))
    assert partly["reason"].tolist() == [score.BAND, None, None, score.BAND, score.OLDEST, None]
    assert partly["flagged"].tolist() == [False, False, True, False, False, True]
