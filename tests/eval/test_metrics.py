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


def test_calibration_by_group_is_the_weighted_rate_of_each_group() -> None:
    """Two groups without censoring: the observed rate of a group is its share of early
    stops by the horizon, and the mean prediction is taken over its own rows."""
    from trialpulse.eval.metrics import calibration_by_group

    time = np.array([1.0, 2.0, 9.0, 9.0, 3.0, 9.0])
    event = np.array([1, 2, 0, 0, 1, 0])
    score = np.array([0.5, 0.1, 0.2, 0.2, 0.8, 0.4])
    groups = np.array(["a", "a", "a", "a", "b", "b"])
    table = calibration_by_group(time, event, score, 5.0, groups)
    assert [row["group"] for row in table] == ["a", "b"]  # the larger group first
    assert table[0] == {
        "group": "a",
        "n": 4,
        "mean_predicted": pytest.approx(0.25),
        "observed": pytest.approx(0.25),
        "censored_before_horizon": 0.0,
    }
    assert table[1]["observed"] == pytest.approx(0.5)
    assert table[1]["mean_predicted"] == pytest.approx(0.6)
    # A row censored before the horizon has no label, and the group says how many there
    # are. The row still open at the horizon stands in for it: one of two rows at risk was
    # censored on day 4, so it weighs 2, and the rate is 1 of 1 + 1 + 2, not 1 of 3.
    time[2], event[2] = 4.0, 0
    censored = calibration_by_group(time, event, score, 5.0, groups)
    assert censored[0]["censored_before_horizon"] == pytest.approx(0.25)
    assert censored[0]["observed"] == pytest.approx(0.25)
    time[3] = 4.5  # both of the last rows censored before the horizon: stops 1 of 2
    assert calibration_by_group(time, event, score, 5.0, groups)[0]["observed"] == pytest.approx(
        0.5
    )


def test_calibration_can_be_reported_by_another_label_than_the_censoring_group() -> None:
    """By registration year, say. The weights still come from the censoring groups: here
    group "a" has a censored row, so its other rows weigh more, whatever their year."""
    from trialpulse.eval.metrics import calibration_by_group

    time = np.array([1.0, 2.0, 4.0, 9.0, 3.0, 9.0, 9.0, 9.0])
    event = np.array([1, 2, 0, 0, 1, 0, 0, 0])
    score = np.array([0.5, 0.1, 0.2, 0.2, 0.8, 0.4, 0.3, 0.3])
    groups = np.array(["a", "a", "a", "a", "b", "b", "b", "b"])
    years = np.array([2016, 2015, 2015, 2016, 2016, 2015, 2015, 2016])
    table = calibration_by_group(time, event, score, 5.0, groups, by=years)
    assert [row["group"] for row in table] == ["2015", "2016"]  # in the order of the labels
    assert [row["n"] for row in table] == [4, 4]
    assert table[0]["mean_predicted"] == pytest.approx((0.1 + 0.2 + 0.4 + 0.3) / 4)
    assert table[0]["censored_before_horizon"] == pytest.approx(0.25)
    # 2016 holds two early stops among four rows, none censored. In group "a" one row is
    # censored on day 4, when two of its rows are still followed, so its control of 2016
    # (still open at the horizon) weighs 2 and its early stop on day 1 weighs 1. The rows
    # of group "b" weigh 1. Observed: (1 + 1) / (1 + 2 + 1 + 1).
    assert table[1]["observed"] == pytest.approx(2 / 5)
    assert table[1]["censored_before_horizon"] == 0.0
    by_group = calibration_by_group(time, event, score, 5.0, groups)
    assert [row["group"] for row in by_group] == ["a", "b"]  # unchanged without `by`


def test_a_model_far_from_calibrated_still_gets_its_slope_and_intercept() -> None:
    """Predicted risks of 0.1% to 2% against observed risks near 30%, as when the champion
    scores the UNKNOWN variant (a dry run on 2026-10-21 stopped on a singular matrix): the
    fit converges to the maximum of the likelihood, here near the slope and intercept that
    made the outcomes."""
    from trialpulse.eval.metrics import _weighted_logistic

    rng = np.random.default_rng(11)
    p = rng.uniform(0.001, 0.02, 40_000)
    x = np.log(p / (1 - p))
    true_a, true_b = 0.9, 0.45
    y = (rng.random(len(x)) < 1 / (1 + np.exp(-(true_a + true_b * x)))).astype(np.float64)
    w = rng.uniform(0.5, 2.0, len(x))
    a, b = _weighted_logistic(x, y, w, fit_slope=True)
    assert a == pytest.approx(true_a, abs=0.15)
    assert b == pytest.approx(true_b, abs=0.04)
    offset, slope = _weighted_logistic(x, y, w, fit_slope=False)
    assert slope == 1.0
    # With the slope fixed at 1, the intercept matches the observed share on average.
    fitted = 1 / (1 + np.exp(-(offset + x)))
    assert np.sum(w * fitted) == pytest.approx(np.sum(w * y), rel=1e-6)
