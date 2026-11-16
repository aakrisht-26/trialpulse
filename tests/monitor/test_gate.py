"""The promotion gate (`monitor.gate`, ADR 0080) on synthetic numbers with known answers."""

import datetime as dt

import pytest

from trialpulse.config import load_project_config
from trialpulse.monitor import gate

CFG = load_project_config().retraining
CUTOFF = dt.date(2026, 9, 25)
WINDOW = gate.Window(dt.date(2026, 9, 26), dt.date(2027, 6, 30))


def test_the_settings_are_section_14s() -> None:
    assert (CFG.auc_margin, CFG.slope_low, CFG.slope_high, CFG.horizon_months) == (
        0.005, 0.8, 1.2, 12)  # fmt: skip


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
