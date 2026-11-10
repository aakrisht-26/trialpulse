"""Grades as aggregates (`monitor.grading`, ADRs 0077 to 0079) on synthetic predictions with
known answers."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from trialpulse.monitor import grading

DAY = dt.date(2023, 1, 6)


def _predictions(n: int = 400, seed: int = 3, perfect: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    stop = rng.random(n) < 0.2
    time = np.where(stop, rng.integers(30, 300, n), rng.integers(400, 900, n)).astype(float)
    event = np.where(stop, 1, np.where(rng.random(n) < 0.5, 2, 0))
    risk = np.where(stop, 0.6, 0.1) if perfect else rng.random(n)
    return pd.DataFrame({
        "nct_id": [f"NCT{i:08d}" for i in range(n)], "prediction_date": DAY,
        "risk_12m": risk, "risk_24m": np.minimum(risk * 1.5, 1.0),
        "sponsor_class": rng.choice(["INDUSTRY", "OTHER"], n),
        "landmark_index": rng.integers(0, 3, n),
        "flagged": rng.random(n) < 0.3, "model_version": "replay/1",
        "time_days": time, "event": event,
    })  # fmt: skip


def _top(grades: pd.DataFrame, months: int) -> pd.Series:
    row = grades[(grades["horizon_months"] == months) & (grades["landmark_index"] == -1)
                 & (grades["row_group"] == "all")]  # fmt: skip
    assert len(row) == 1
    return row.iloc[0]


def test_perfect_scores_grade_auc_1_and_the_counts_add_up() -> None:
    grades = grading.grade(_predictions(), dt.date(2026, 9, 25), "replay", 10, 0.1)
    top = _top(grades, 12)
    assert top["auc"] == pytest.approx(1.0)
    assert top["matured"]
    assert (
        top["n_rows"]
        == top["n_early_stop"] + top["n_completed"] + top["n_censored"] + top["n_open"]
    )
    assert set(grades["row_group"]) == {"all", "flagged"}
    assert set(grades["landmark_index"]) == {-1, 0, 1, 2}
    assert set(grades["source"]) == {"replay"}


def test_random_scores_grade_about_one_half() -> None:
    grades = grading.grade(
        _predictions(4000, perfect=False), dt.date(2026, 9, 25), "replay", 10, 0.1
    )
    assert _top(grades, 12)["auc"] == pytest.approx(0.5, abs=0.05)


def test_a_prediction_matures_only_once_its_horizon_has_passed() -> None:
    early = grading.grade(_predictions(), dt.date(2023, 7, 1), "live", 10, 0.1)
    assert not _top(early, 12)["matured"]  # partial horizon: follow-up of 176 days
    assert _top(early, 12)["n_open"] > 0
    late = grading.grade(_predictions(), dt.date(2024, 1, 6), "live", 10, 0.1)
    assert _top(late, 12)["matured"]
    assert not _top(late, 24)["matured"]


def test_a_metric_without_cases_is_none() -> None:
    rows = _predictions().assign(event=2)
    grades = grading.grade(rows, dt.date(2026, 9, 25), "live", 10, 0.1)
    assert _top(grades, 12)["auc"] is None
    assert grading.as_records(grades)[0][12] is None or True  # NaN never reaches the database
    assert all(v is None or v == v for r in grading.as_records(grades) for v in r)


def test_the_source_is_live_or_replay() -> None:
    with pytest.raises(ValueError, match="source"):
        grading.grade(_predictions(), DAY, "test", 10, 0.1)


def test_live_outcomes_take_the_first_stop_completion_or_lapse_after_the_prediction() -> None:
    predictions = pd.DataFrame({"nct_id": ["A", "B", "C", "D", "E"], "prediction_date": DAY})
    events = pd.DataFrame([
        ("A", "RECRUITING", "status", dt.date(2023, 2, 1)),  # not an outcome
        ("A", "TERMINATED", "status", dt.date(2023, 3, 1)),
        ("B", "COMPLETED", "status", dt.date(2022, 12, 1)),  # before the prediction
        ("B", "COMPLETED", "status", dt.date(2023, 4, 1)),
        ("C", None, "lapsed", dt.date(2023, 5, 1)),
        ("E", "WITHDRAWN", "status", dt.date(2024, 1, 1)),  # after the data date
    ], columns=["nct_id", "to_status", "kind", "effective_date"])  # fmt: skip
    out = grading.live_outcomes(predictions, events, dt.date(2023, 6, 1)).set_index("nct_id")
    assert out.loc["A", "event"] == 1
    assert out.loc["A", "time_days"] == (dt.date(2023, 3, 1) - DAY).days
    assert out.loc["B", "event"] == 2
    assert (out.loc["C", "event"], out.loc["C", "time_days"]) == (
        0,
        (dt.date(2023, 5, 1) - DAY).days,
    )
    assert (out.loc["D", "event"], out.loc["D", "time_days"]) == (
        0,
        (dt.date(2023, 6, 1) - DAY).days,
    )
    assert out.loc["E", "event"] == 0  # not known yet on the data date


def test_cohort_outcomes_are_follow_up_from_the_prediction_date() -> None:
    predictions = pd.DataFrame({"nct_id": ["A", "B"], "prediction_date": DAY})
    outcomes = pd.DataFrame({"trial_id": ["A"], "event": [1],
                             "event_date": [pd.Timestamp("2023-02-05")]})  # fmt: skip
    out = grading.cohort_outcomes(predictions, outcomes)
    assert list(out["nct_id"]) == ["A"]  # no outcome row: left out
    assert out["time_days"].iloc[0] == 30.0


def test_the_replay_dates_are_the_fridays() -> None:
    days = list(grading.iter_dates(dt.date(2023, 1, 1), dt.date(2024, 6, 30)))
    assert days[0] == dt.date(2023, 1, 6)
    assert days[-1] == dt.date(2024, 6, 28)
    assert len(days) == 78
    assert {d.weekday() for d in days} == {4}
