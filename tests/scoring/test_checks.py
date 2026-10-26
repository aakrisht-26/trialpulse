"""The development checks of ADR 0034: the candidate rows, which origin they belong to, and
the decisions from the metrics. No real data, no model."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from trialpulse.cohort.rules import CohortRules
from trialpulse.config import Origin, load_project_config
from trialpulse.scoring import checks

RULES = CohortRules.from_config(load_project_config())
ORIGINS = [
    Origin(date=dt.date(2016, 1, 1), role="dev"),
    Origin(date=dt.date(2017, 1, 1), role="dev"),
]


def test_candidates_are_mid_landmark_and_pseudo_landmark_rows_in_the_period() -> None:
    t0 = np.array(["2014-03-31", "2008-01-15"], dtype="datetime64[D]")
    rows = checks.candidate_rows(
        np.array(["NCT1", "NCT2"]), t0, RULES, dt.date(2016, 1, 1), dt.date(2018, 1, 1)
    )
    one = rows[rows["trial_id"] == "NCT1"].set_index("landmark_index")
    # NCT1: landmark 4 is 2016-03-31, its mid date 2016-06-30 (30 June, clamped).
    assert one.loc[4, "kind"] == checks.BETWEEN
    assert one.loc[4, "landmark_date"] == pd.Timestamp("2016-06-30")
    assert one.loc[4, "base_date"] == pd.Timestamp("2016-03-31")
    two = rows[rows["trial_id"] == "NCT2"].set_index("landmark_index")
    # NCT2: first posted 2008-01-15, so 2016 and 2017 hold landmarks 16 to 19 (beyond).
    assert set(two["kind"]) == {checks.BEYOND}
    assert sorted(two.index) == [16, 17, 18, 19]
    assert (rows["landmark_date"] >= pd.Timestamp("2016-01-01")).all()
    assert (rows["landmark_date"] < pd.Timestamp("2018-01-01")).all()


def test_a_mid_landmark_row_needs_its_landmark_row_in_the_same_window() -> None:
    rows = pd.DataFrame(
        {
            "trial_id": ["A", "B", "C", "D"],
            "landmark_index": [2, 2, 3, 8],
            "landmark_date": pd.to_datetime(["2016-04-01", "2016-02-01", "2016-11-01",
                                             "2017-05-01"]),
            "base_date": pd.to_datetime(["2016-01-01", "2015-11-01", "2016-08-01",
                                         "2017-05-01"]),
            "t0": pd.to_datetime(["2015-01-01"] * 4),
            "kind": [checks.BETWEEN, checks.BETWEEN, checks.BETWEEN, checks.BEYOND],
        }
    )  # fmt: skip
    landmarks = pd.DataFrame(
        {
            "trial_id": ["A", "B"],
            "landmark_index": [2, 2],
            "landmark_date": pd.to_datetime(["2016-01-01", "2015-11-01"]).to_numpy(
                dtype="datetime64[D]"
            ),
        }
    )
    out = checks.assign_origins(rows, landmarks, ORIGINS, 12).set_index("trial_id")
    # A: landmark row in the 2016 window. B: landmark before the window (a training row).
    # C: no landmark row in the final cohort. D: a pseudo-landmark row dated in 2017.
    assert out["origin"].to_dict() == {"A": "2016-01-01", "D": "2017-01-01"}


def _between(d16: float, d17: float) -> dict:
    return {"2016-01-01": {"difference": {"estimate": d16}},
            "2017-01-01": {"difference": {"estimate": d17}}}  # fmt: skip


def _band(auc16: float, auc17: float, stops16: int, stops17: int) -> dict:
    key = "early_stops_within_12_months"
    return {
        "2016-01-01": {"auc": {"estimate": auc16}, key: stops16},
        "2017-01-01": {"auc": {"estimate": auc17}, key: stops17},
    }


L6 = {"2016-01-01": 0.70, "2017-01-01": 0.73}  # mean 0.715, band floor 0.685


@pytest.mark.parametrize(
    ("d16", "d17", "option"),
    [(0.0, 0.01, "B"), (-0.005, -0.005, "B"), (-0.0051, 0.02, "A"), (0.01, -0.006, "A")],
)
def test_option_b_is_served_only_if_it_is_within_the_margin_at_both_origins(
    d16: float, d17: float, option: str
) -> None:
    decided = checks.decide(_between(d16, d17), {}, L6)
    assert decided["between_landmarks"] == option


@pytest.mark.parametrize(
    ("aucs", "stops", "served"),
    [
        ((0.69, 0.68), (150, 120), True),  # mean 0.685, exactly the floor
        ((0.68, 0.689), (150, 120), False),  # mean 0.6845, below the floor
        ((0.75, 0.76), (99, 400), False),  # too few early stops at one origin
        ((0.75, 0.76), (100, 100), True),
        ((float("nan"), 0.76), (150, 150), False),  # no AUC at one origin
    ],
)
def test_a_band_is_served_on_its_averaged_auc_and_its_early_stops(
    aucs: tuple[float, float], stops: tuple[int, int], served: bool
) -> None:
    decided = checks.decide(_between(0, 0), {"42 to 59 months": _band(*aucs, *stops)}, L6)
    band = decided["bands"]["42 to 59 months"]
    assert band["served"] is served
    assert band["floor"] == pytest.approx(0.685)
    assert decided["landmark6_mean_auc"] == pytest.approx(0.715)
    assert decided["oldest_band"] == {"from_index": 20, "served": False}


def test_the_bands_cover_months_42_to_119_and_nothing_older() -> None:
    covered = [k for _, low, high in checks.BANDS for k in range(low, high + 1)]
    assert covered == list(range(7, 20))
    assert checks.OLDEST_INDEX * RULES.spacing_months == 120
