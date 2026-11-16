"""The promotion gate (`monitor.gate`, ADR 0080) on synthetic data with known answers."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from trialpulse.config import load_project_config
from trialpulse.monitor import gate, grading

CFG = load_project_config().retraining
CUTOFF = dt.date(2026, 9, 25)
WINDOW = gate.Window(dt.date(2026, 9, 26), dt.date(2027, 6, 30))
DATES = [dt.date(2026, 10, 26), dt.date(2026, 11, 26), dt.date(2026, 12, 26)]
DATA_DATE = dt.date(2028, 1, 31)  # every date's 12-month horizon has passed


def _window(challenger: str, data_rows: int = 3000) -> pd.DataFrame:
    """Both models' predictions on the same synthetic trials of each date. The champion
    predicts each trial's true 12-month risk of an early stop; the challenger predicts it
    too ('same'), predicts noise ('random'), or ranks as the truth but three times too
    steeply on the logit scale ('overconfident': a calibration slope of 1/3)."""
    frames = []
    for k, day in enumerate(DATES):
        rng = np.random.default_rng(100 + k)
        p = rng.uniform(0.02, 0.5, data_rows)  # the true risk within 12 months
        stop = rng.random(data_rows) < p
        time = np.where(stop, rng.integers(1, 365, data_rows),
                        rng.integers(1, 1000, data_rows)).astype(float)  # fmt: skip
        event = np.where(stop, 1, np.where(time < 365, 2, 0))  # open trials: past the horizon
        risk = {"champion": p, "same": p, "random": rng.random(data_rows),
                "overconfident": 1 / (1 + np.exp(-3 * np.log(p / (1 - p))))}  # fmt: skip
        for model, name in ((gate.CHAMPION, "champion"), (gate.CHALLENGER, challenger)):
            frames.append(pd.DataFrame({
                "nct_id": [f"NCT{i:08d}" for i in range(data_rows)], "prediction_date": day,
                "risk_12m": risk[name], "risk_24m": np.minimum(risk[name] * 1.3, 1.0),
                "sponsor_class": "OTHER", "landmark_index": 0, "flagged": False,
                "model_version": model, "time_days": time, "event": event,
            }))  # fmt: skip
    return pd.concat(frames, ignore_index=True)


@pytest.mark.parametrize(
    ("challenger", "promoted", "says"),
    [
        ("same", True, "promoted: AUC"),
        ("random", False, "is below the champion's"),
        ("overconfident", False, "calibration slope"),
    ],
)
def test_the_window_comparison_promotes_refuses_on_auc_and_refuses_on_slope(
    challenger: str, promoted: bool, says: str
) -> None:
    grades = grading.grade(_window(challenger), DATA_DATE, grading.WINDOW, 200, 0.1)
    found = gate.compare(grades, CFG.horizon_months)
    assert found.dates == len(DATES)
    assert found.champion_auc == pytest.approx(0.70, abs=0.02)  # the truth's own AUC here
    expected_auc, expected_slope = {
        "same": (found.champion_auc, 1.0),
        "random": (0.5, None),
        "overconfident": (found.champion_auc, 1 / 3),  # the same ranking, a third of the slope
    }[challenger]
    assert found.challenger_auc == pytest.approx(expected_auc, abs=0.03)
    if expected_slope is not None:
        assert found.challenger_slope == pytest.approx(expected_slope, abs=0.1)
    decision = gate.decide(CFG, WINDOW, found.champion_auc, found.challenger_auc,
                           found.challenger_slope)  # fmt: skip
    assert decision.promoted is promoted
    assert says in decision.reason


def test_the_comparison_reads_matured_horizons_only() -> None:
    early = dt.date(2027, 6, 30)  # the dates' 12-month horizons have not passed
    grades = grading.grade(_window("same", 500), early, grading.WINDOW, 200, 0.1)
    found = gate.compare(grades, CFG.horizon_months)
    assert found == gate.Comparison(None, None, None, 0)
    assert not gate.decide(CFG, WINDOW, None, None, None).promoted


def test_the_window_dates_are_its_latest_months_oldest_first() -> None:
    window = gate.Window(dt.date(2026, 9, 26), dt.date(2027, 2, 15))
    assert gate.window_dates(window, 3) == [
        dt.date(2026, 12, 15), dt.date(2027, 1, 15), dt.date(2027, 2, 15)]  # fmt: skip
    short = gate.Window(dt.date(2026, 9, 26), dt.date(2026, 10, 30))
    assert gate.window_dates(short, 3) == [dt.date(2026, 9, 30), dt.date(2026, 10, 30)]
    first = gate.fair_window(CUTOFF, dt.date(2027, 9, 26), 12)
    assert first is not None
    assert gate.window_dates(first, 3) == [dt.date(2026, 9, 26)]


def test_the_settings_are_section_14s() -> None:
    assert (CFG.auc_margin, CFG.slope_low, CFG.slope_high, CFG.horizon_months) == (
        0.005, 0.8, 1.2, 12)  # fmt: skip
    assert CFG.window_dates == 3


def test_no_window_exists_before_a_horizon_after_the_training_cutoff_has_passed() -> None:
    assert gate.fair_window(CUTOFF, dt.date(2026, 11, 16), 12) is None
    assert gate.fair_window(CUTOFF, dt.date(2027, 9, 25), 12) is None  # predicted the day after
    first = gate.fair_window(CUTOFF, dt.date(2027, 9, 26), 12)
    assert first == gate.Window(dt.date(2026, 9, 26), dt.date(2026, 9, 26))


def test_without_a_window_nothing_is_promoted_whatever_the_numbers() -> None:
    decision = gate.decide(CFG, None, 0.70, 0.90, 1.0)
    assert decision == gate.Decision(False, gate.NO_WINDOW)


@pytest.mark.parametrize(
    ("champion", "challenger", "slope", "promoted", "says"),
    [
        (0.700, 0.700, 1.00, True, "promoted"),
        (0.700, 0.695, 0.80, True, "promoted"),  # exactly at the margin and the low end
        (0.700, 0.6949, 1.00, False, "AUC 0.6949 is below"),
        (0.700, 0.720, 0.79, False, "calibration slope 0.790 is outside"),
        (0.700, 0.720, 1.21, False, "calibration slope 1.210 is outside"),
    ],
)
def test_promote_refuse_on_auc_and_refuse_on_slope(
    champion: float, challenger: float, slope: float, promoted: bool, says: str
) -> None:
    decision = gate.decide(CFG, WINDOW, champion, challenger, slope)
    assert decision.promoted is promoted
    assert says in decision.reason


def test_a_window_without_metrics_refuses() -> None:
    assert not gate.decide(CFG, WINDOW, None, 0.7, 1.0).promoted
