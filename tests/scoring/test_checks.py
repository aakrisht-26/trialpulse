"""The development checks of ADR 0034: the candidate rows, which origin they belong to, and
the decisions from the metrics. No real data, no model."""

import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from trialpulse.cli import RefusedError
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


def _labelled(n_stops: int = 30, n_others: int = 70) -> pd.DataFrame:
    """Rows dated 2016-06-01 whose landmark lies two years earlier: stops at 100 days, one
    stop exactly at the 12-month horizon, completions and censorings after it."""
    day = np.datetime64("2016-06-01", "D")
    horizon = int(checks.horizon_days(np.array([day]), checks.HORIZON_MONTHS)[0])
    n = n_stops + n_others
    after = np.where(np.arange(n_others) % 2 == 0, 400, 500)
    return pd.DataFrame(
        {
            "trial_id": [f"T{i}" for i in range(n)],
            "landmark_date": np.full(n, day),
            "base_date": np.full(n, day - np.timedelta64(730, "D")),
            "event": [1] * n_stops + [2 if a == 500 else 0 for a in after],
            "event_date": np.concatenate([
                day + np.r_[np.full(n_stops - 1, 100), horizon].astype("timedelta64[D]"),
                day + after.astype("timedelta64[D]"),
            ]),
            "stratum": "INDUSTRY",
        }
    )  # fmt: skip


def test_check_1_compares_b_and_a_on_follow_up_from_the_rows_date() -> None:
    rows = _labelled()
    stops = (rows["event"] == 1).to_numpy(dtype=np.float64)
    result = checks.between_check(rows, stops, np.full(len(rows), 0.5), load_project_config(), 5)
    # The stop exactly at 12 months counts, and follow-up runs from the row's own date.
    assert result["early_stops_within_12_months"] == 30
    assert result["auc_b"]["estimate"] == pytest.approx(1.0)
    assert result["auc_a"]["estimate"] == pytest.approx(0.5)
    assert result["difference"]["estimate"] == pytest.approx(0.5)  # B minus A


def test_check_2_reads_the_groups_it_is_given() -> None:
    rows = _labelled()
    score = (rows["event"] == 1).to_numpy(dtype=np.float64)
    groups = np.zeros(len(rows), dtype=np.int64)
    result = checks.band_check(rows, score, groups, load_project_config(), 5)
    assert result["early_stops_within_12_months"] == 30
    assert result["auc"]["estimate"] == pytest.approx(1.0)
    assert result["mean_predicted_12"] == pytest.approx(0.3)


def test_landmark_6_and_option_a_are_read_at_12_months(tmp_path, monkeypatch) -> None:
    by_index = {"12": 0.7, "24": 0.6}
    record = {"origins": [{"origin": "2016-01-01", "horizons": {
        h: {"by_landmark_index": {"6": {"auc": {"estimate": v}}}} for h, v in by_index.items()
    }}]}  # fmt: skip
    (tmp_path / "m4_2016.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(checks, "RESULTS_DIR", tmp_path)
    assert checks.landmark6_auc(ORIGINS[0]) == 0.7
    mid = pd.DataFrame({"trial_id": ["A", "B"], "landmark_index": [2, 3]})
    saved = pd.DataFrame({"trial_id": ["B", "A"], "landmark_index": [3, 2],
                          "score_12": [0.2, 0.1], "score_24": [0.4, 0.3]})  # fmt: skip
    assert checks.saved_scores(mid, saved).tolist() == [0.1, 0.2]
    with pytest.raises(RefusedError, match="1 mid-landmark rows have no saved A score"):
        checks.saved_scores(mid, saved.iloc[:1])


def test_rows_at_the_window_end_or_before_the_origin_belong_to_no_origin() -> None:
    rows = pd.DataFrame(
        {
            "trial_id": ["A", "B", "C"],
            "landmark_index": [2, 8, 8],
            # A: its landmark 2017-10-01 is in the 2017 window, but d is that window's end.
            "landmark_date": pd.to_datetime(["2018-01-01", "2015-12-31", "2017-12-31"]),
            "base_date": pd.to_datetime(["2017-10-01", "2015-12-31", "2017-12-31"]),
            "t0": pd.to_datetime(["2016-10-01", "2011-12-31", "2013-12-31"]),
            "kind": [checks.BETWEEN, checks.BEYOND, checks.BEYOND],
        }
    )  # fmt: skip
    landmarks = pd.DataFrame({"trial_id": ["A"], "landmark_index": [2],
                              "landmark_date": np.array(["2017-10-01"],
                                                        dtype="datetime64[D]")})  # fmt: skip
    out = checks.assign_origins(rows, landmarks, ORIGINS, 12)
    assert out.set_index("trial_id")["origin"].to_dict() == {"C": "2017-01-01"}
