"""The class rates of the sponsor track record, fitted on one origin's training rows."""

import datetime as dt

import numpy as np
import pytest

from trialpulse.features.transforms import MISSING_CLASS, SponsorPriors

D = np.datetime64
ORIGIN = dt.date(2016, 1, 1)


def _rows() -> dict[str, np.ndarray]:
    """Six trials, one row per trial but T1, which has two landmark rows. By the origin four
    trials have ended, two of them early; industry has 1 stop of 3 ended, academia 1 of 1."""
    table = [
        ("T1", 0, "2014-01-01", 1, "2014-09-01", "INDUSTRY"),
        ("T1", 1, "2014-07-01", 1, "2014-09-01", "INDUSTRY"),
        ("T2", 0, "2014-01-01", 2, "2015-03-01", "INDUSTRY"),
        ("T3", 0, "2014-06-01", 2, "2015-08-01", "INDUSTRY"),
        ("T4", 0, "2015-01-01", 0, "2016-01-01", "INDUSTRY"),
        ("T5", 0, "2014-02-01", 1, "2014-12-01", "OTHER"),
        ("T6", 0, "2015-05-01", 0, "2015-06-01", "NIH"),
    ]
    columns = list(zip(*table, strict=True))
    return {
        "trial_id": np.array(columns[0]),
        "landmark_index": np.array(columns[1], dtype=np.int64),
        "landmark_date": np.array(columns[2], dtype="datetime64[D]"),
        "event": np.array(columns[3], dtype=np.int64),
        "event_date": np.array(columns[4], dtype="datetime64[D]"),
        "sponsor_class": np.array(columns[5]),
    }


def test_class_rates_by_hand() -> None:
    priors = SponsorPriors.fit(**_rows(), origin=ORIGIN, weight=10)
    assert priors.overall == pytest.approx(2 / 4)  # T1 counted once
    assert priors.ended == {"INDUSTRY": 3, "NIH": 0, "OTHER": 1}
    assert priors.stopped == {"INDUSTRY": 1, "NIH": 0, "OTHER": 1}
    # Each class rate is smoothed toward the rate of all classes with the same weight.
    assert priors.classes["INDUSTRY"] == pytest.approx((1 + 10 * 0.5) / (3 + 10))
    assert priors.classes["OTHER"] == pytest.approx((1 + 10 * 0.5) / (1 + 10))
    assert priors.classes["NIH"] == pytest.approx(0.5)
    assert priors.prior("FED") == priors.prior(None) == priors.prior(MISSING_CLASS) == 0.5


def test_the_smoothed_rate_and_the_fallback_to_the_class_rate() -> None:
    priors = SponsorPriors.fit(**_rows(), origin=ORIGIN, weight=10)
    classes = np.array(["INDUSTRY", "INDUSTRY", "OTHER", "FED"], dtype=object)
    ended = np.array([20.0, np.nan, 0.0, np.nan])
    stopped = np.array([5.0, np.nan, 0.0, np.nan])
    rate = priors.rate(classes, ended, stopped)
    industry = priors.classes["INDUSTRY"]
    assert rate[0] == pytest.approx((5 + 10 * industry) / 30)
    assert rate[1] == pytest.approx(industry)  # no identity: the class rate
    assert rate[2] == pytest.approx(priors.classes["OTHER"])  # no ended trial yet: the prior
    assert rate[3] == pytest.approx(0.5)  # a class the training rows never saw


def test_rows_dated_on_or_after_the_origin_are_refused() -> None:
    rows = _rows()
    late = dict(rows)
    late["event_date"] = rows["event_date"].copy()
    late["event_date"][0] = D("2016-01-01")  # an event on the origin day is not known to it
    with pytest.raises(ValueError, match="training rows only"):
        SponsorPriors.fit(**late, origin=ORIGIN, weight=10)
    late = dict(rows)
    late["landmark_date"] = rows["landmark_date"].copy()
    late["landmark_date"][3] = D("2016-01-01")
    with pytest.raises(ValueError, match="1 rows are dated on or after the origin"):
        SponsorPriors.fit(**late, origin=ORIGIN, weight=10)
    late = dict(rows)
    late["event_date"] = rows["event_date"].copy()
    late["event_date"][4] = D("2016-01-02")  # censored after the origin
    with pytest.raises(ValueError, match="training rows only"):
        SponsorPriors.fit(**late, origin=ORIGIN, weight=10)


def test_the_rates_survive_a_round_trip_through_json() -> None:
    priors = SponsorPriors.fit(**_rows(), origin=ORIGIN, weight=10)
    again = SponsorPriors.from_json(priors.to_json())
    assert again == priors
    assert priors.to_json()["prior_weight"] == 10
