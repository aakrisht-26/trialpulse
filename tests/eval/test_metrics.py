"""IPCW weights and metrics on synthetic data with known answers (Step 8 acceptance)."""

import numpy as np
import pytest

from trialpulse.eval.ipcw import censoring_survival, horizon_labels_and_weights, kaplan_meier
from trialpulse.eval.metrics import (
    calibration_slope_intercept,
    calibration_table,
    ipcw_auc,
    ipcw_brier,
    lift_at,
    weighted_auc,
)

# A constructed censoring pattern, horizon H = 5 (days):
#   A: early stop at 2         (case)
#   B: censored at 3           (unknown at H: weight 0)
#   C: completion at 1         (control, completions count as controls)
#   D: censored at 8           (event-free at H: control)
#   E: early stop at 6         (after H: control)
# Censoring survival G: at t = 3, rows at risk are B, E, D, so G(3) = 2/3; G(8) = 0.
TIME = np.array([2.0, 3.0, 1.0, 8.0, 6.0])
EVENT = np.array([1, 0, 2, 0, 1])
SCORE = np.array([0.5, 0.9, 0.2, 0.4, 0.7])
H = 5.0


def test_kaplan_meier_hand_example() -> None:
    km = kaplan_meier(
        np.array([1.0, 2.0, 2.0, 3.0, 4.0]), np.array([True, False, True, True, False])
    )
    # S(1) = 4/5; at 2 one event of 4 at risk: 4/5 * 3/4 = 3/5; at 3: 3/5 * 1/2 = 3/10.
    assert km.at(np.array([0.5, 1.0, 2.0, 3.0, 10.0])) == pytest.approx([1.0, 0.8, 0.6, 0.3, 0.3])
    assert km.before(np.array([1.0, 2.0])) == pytest.approx([1.0, 0.8])


def test_censoring_survival_on_constructed_pattern() -> None:
    g = censoring_survival(TIME, EVENT)
    assert g.at(np.array([2.9, 3.0, 5.0, 8.0])) == pytest.approx([1.0, 2 / 3, 2 / 3, 0.0])


def test_weights_on_constructed_pattern() -> None:
    y, w = horizon_labels_and_weights(TIME, EVENT, H)
    assert y.tolist() == [1, 0, 0, 0, 0]
    assert w == pytest.approx([1.0, 0.0, 1.0, 1.5, 1.5])


def test_auc_and_brier_on_constructed_pattern() -> None:
    # Case A (0.5) beats C (0.2, weight 1) and D (0.4, weight 1.5), loses to E (0.7, 1.5):
    # AUC = (1 + 1.5) / (1 + 1.5 + 1.5) = 0.625, while the unweighted value would be 2/3.
    assert ipcw_auc(TIME, EVENT, SCORE, H) == pytest.approx(0.625)
    # Brier = (0.25 + 0 + 0.04 + 1.5 * 0.16 + 1.5 * 0.49) / 5 = 0.253.
    assert ipcw_brier(TIME, EVENT, SCORE, H) == pytest.approx(0.253)


def _synthetic(n: int, seed: int, censor: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    stop = rng.exponential(900, n)
    complete = rng.exponential(700, n)
    cens = rng.exponential(1500, n) if censor else np.full(n, np.inf)
    time = np.minimum.reduce([stop, complete, cens])
    event = np.select([time == stop, time == complete], [1, 2], default=0)
    return time, event, stop


def test_no_censoring_makes_ipcw_equal_to_unweighted() -> None:
    time, event, _ = _synthetic(3000, 1, censor=False)
    score = np.random.default_rng(2).uniform(size=time.size)
    y = ((event == 1) & (time <= 365)).astype(float)

    _, w = horizon_labels_and_weights(time, event, 365.0)
    assert np.all(w == 1.0)
    pos, neg = score[y == 1], score[y == 0]
    unweighted_auc = np.mean(pos[:, None] > neg[None, :]) + 0.5 * np.mean(
        pos[:, None] == neg[None, :]
    )
    assert ipcw_auc(time, event, score, 365.0) == pytest.approx(unweighted_auc)
    assert ipcw_brier(time, event, score, 365.0) == pytest.approx(np.mean((y - score) ** 2))


def test_perfect_scores_give_auc_one() -> None:
    time, event, _ = _synthetic(5000, 3, censor=True)
    perfect = ((event == 1) & (time <= 365)).astype(float)  # 1 exactly for the cases
    assert ipcw_auc(time, event, perfect, 365.0) == pytest.approx(1.0)


def test_random_scores_give_auc_near_half() -> None:
    time, event, _ = _synthetic(20000, 4, censor=True)
    score = np.random.default_rng(5).uniform(size=time.size)
    assert ipcw_auc(time, event, score, 365.0) == pytest.approx(0.5, abs=0.02)


def test_weighted_auc_ties_and_degenerate_inputs() -> None:
    y = np.array([1, 0], dtype=np.int8)
    assert weighted_auc(y, np.array([0.3, 0.3]), np.ones(2)) == pytest.approx(0.5)
    assert np.isnan(weighted_auc(np.array([1, 1], dtype=np.int8), np.ones(2), np.ones(2)))
    assert np.isnan(weighted_auc(y, np.ones(2), np.zeros(2)))


def test_lift_on_known_values() -> None:
    time = np.array([10.0] * 10)
    event = np.array([1, 1, 1, 2, 2, 2, 2, 2, 2, 2])
    score = np.linspace(1.0, 0.1, 10)  # the first rows are ranked riskiest
    # Overall rate 3/10; the top 20% (two rows) are both stops, so lift = 1 / 0.3.
    assert lift_at(time, event, score, 30.0, 0.2) == pytest.approx(1 / 0.3)


def test_calibration_of_a_calibrated_model() -> None:
    rng = np.random.default_rng(6)
    p = rng.uniform(0.02, 0.6, 20000)
    stopped = rng.uniform(size=p.size) < p
    time = np.where(stopped, 100.0, 400.0)
    event = np.where(stopped, 1, 0)  # everyone else is event-free past the horizon

    slope, intercept = calibration_slope_intercept(time, event, p, 365.0)
    table = calibration_table(time, event, p, 365.0, bins=10)

    assert slope == pytest.approx(1.0, abs=0.1)
    assert intercept == pytest.approx(0.0, abs=0.05)
    assert len(table) == 10
    for row in table:
        assert row["observed_cif"] == pytest.approx(row["mean_predicted"], abs=0.05)


def test_calibration_detects_overconfidence() -> None:
    rng = np.random.default_rng(7)
    true_p = rng.uniform(0.05, 0.5, 20000)
    stopped = rng.uniform(size=true_p.size) < true_p
    time = np.where(stopped, 100.0, 400.0)
    event = np.where(stopped, 1, 0)
    logit = np.log(true_p / (1 - true_p))
    overconfident = 1 / (1 + np.exp(-2 * logit))  # predictions twice as extreme

    slope, _ = calibration_slope_intercept(time, event, overconfident, 365.0)
    assert slope == pytest.approx(0.5, abs=0.07)
