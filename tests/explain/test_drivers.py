"""The drivers of M4's predictions (Step 12, ADR 0033), on a small M4 fitted to simulated
rows whose early stops a not-yet-recruiting status, a company sponsor and a small enrollment
target drive. No real data."""

import numpy as np
import pytest
from scipy.stats import spearmanr

from trialpulse.config import ProjectConfig
from trialpulse.eval import EVENT_STOP
from trialpulse.eval.rows import LandmarkRows
from trialpulse.explain import drivers
from trialpulse.models.discrete_time import INTERVAL, OUTCOME_CONTINUE, DiscreteTimeBoosting

from .conftest import simulated_rows

pytestmark = pytest.mark.slow  # fitting the small M4 takes a few seconds; CI runs every test


@pytest.fixture(scope="module")
def probe() -> LandmarkRows:
    rows, _, _ = simulated_rows(800, seed=7)
    return rows


def test_the_values_add_up_to_the_explained_log_odds(
    m4: DiscreteTimeBoosting, probe: LandmarkRows
) -> None:
    for months in (12, 24):
        explanation = drivers.explain(m4, probe, months)
        assert explanation.values.shape == (800, 54)
        assert INTERVAL not in explanation.features
        assert explanation.features == m4.features
        gap = np.abs(explanation.target - drivers.log_odds(m4, probe, months))
        assert gap.max() < 1e-9


def test_the_target_is_the_stop_against_continue_log_odds_averaged_over_the_intervals(
    m4: DiscreteTimeBoosting, probe: LandmarkRows
) -> None:
    assert m4.booster is not None
    landmark = m4.tree_landmark_matrix(probe.features)
    per_interval = []
    for j in (1, 2):
        x = np.column_stack([landmark, np.full(len(landmark), float(j), dtype=np.float32)])
        raw = m4.booster.predict(x, raw_score=True)
        per_interval.append(raw[:, EVENT_STOP] - raw[:, OUTCOME_CONTINUE])
    six = drivers.explain(m4, probe, 6)
    twelve = drivers.explain(m4, probe, 12)
    assert np.allclose(six.target, per_interval[0], atol=1e-9)
    assert np.allclose(twelve.target, (per_interval[0] + per_interval[1]) / 2, atol=1e-9)
    # The interval column is kept apart, and its value is the same whatever the trial's.
    assert np.ptp(six.base) == pytest.approx(0.0)


def test_the_simulated_drivers_rank_first_with_their_direction(
    m4: DiscreteTimeBoosting, probe: LandmarkRows
) -> None:
    explanation = drivers.explain(m4, probe, 12)
    ranked = list(drivers.importance(explanation))
    assert {"status", "sponsor_class", "enrollment_count"} <= set(ranked[:6])
    values = list(drivers.importance(explanation).values())
    assert values == sorted(values, reverse=True)
    direction = drivers.direction_of(explanation, probe.features)
    assert direction["enrollment_count"] == "higher lowers"  # small targets stop more
    assert "status" not in direction  # a category gets no direction


def test_the_top_drivers_of_a_row_are_its_largest_values(
    m4: DiscreteTimeBoosting, probe: LandmarkRows
) -> None:
    explanation = drivers.explain(m4, probe, 12)
    features = probe.features
    for row in (0, 1, 2):
        found = drivers.top_drivers(explanation, features, row)
        assert len(found) == drivers.TOP
        sizes = [abs(d.contribution) for d in found]
        assert sizes == sorted(sizes, reverse=True)
        assert sizes[-1] >= np.sort(np.abs(explanation.values[row]))[-drivers.TOP]
        for d in found:
            assert d.direction == ("raises" if d.contribution > 0 else "lowers")
            assert d.value == drivers.shown(d.feature, features[d.feature][row])
    text = drivers.drivers_text(found)
    assert text.count(";") == drivers.TOP - 1


def test_a_feature_with_no_effect_is_not_a_driver() -> None:
    explanation = drivers.Explanation(
        ("status", "enrollment_count", "healthy_volunteers"),
        np.array([[0.5, 0.0, -0.2]]),
        np.zeros(1),
        np.zeros(1),
        12,
    )
    values = {
        "status": np.array(["RECRUITING"], dtype=object),
        "enrollment_count": np.array([120.0]),
        "healthy_volunteers": np.array([np.nan]),
    }
    found = drivers.top_drivers(explanation, values, 0)
    assert [(d.feature, d.value, d.direction) for d in found] == [
        ("status", "RECRUITING", "raises"),
        ("healthy_volunteers", "not given", "lowers"),
    ]


def test_values_are_shown_by_their_kind() -> None:
    assert drivers.shown("status", "RECRUITING") == "RECRUITING"
    assert drivers.shown("status", None) == "not given"
    assert drivers.shown("healthy_volunteers", 1.0) == "yes"
    assert drivers.shown("healthy_volunteers", 0.0) == "no"
    assert drivers.shown("enrollment_count", 1234.0) == "1.23e+03"
    assert drivers.shown("planned_duration_months", 36.0) == "36"
    assert drivers.shown("sponsor_stop_rate", 0.08765) == "0.0877"
    assert drivers.shown("planned_duration_months", float("nan")) == "not given"


def test_the_rank_agreement_is_spearmans() -> None:
    rng = np.random.default_rng(3)
    a = rng.normal(size=500)
    b = a + rng.normal(size=500)
    b[:50] = b[0]  # ties
    assert drivers.agreement(a, b) == pytest.approx(spearmanr(a, b).statistic, abs=1e-12)
    assert drivers.agreement(a, np.exp(a)) == pytest.approx(1.0)


def test_a_horizon_must_be_whole_intervals_and_the_model_fitted(
    cfg: ProjectConfig, m4: DiscreteTimeBoosting, probe: LandmarkRows
) -> None:
    with pytest.raises(ValueError, match="not a whole number of intervals"):
        drivers.explain(m4, probe, 9)
    with pytest.raises(ValueError, match="not a whole number of intervals"):
        drivers.explain(m4, probe, 30)
    unfitted = DiscreteTimeBoosting(cfg)
    with pytest.raises(RuntimeError, match="not fitted"):
        drivers.explain(unfitted, probe, 12)
