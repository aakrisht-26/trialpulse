"""The drivers of M4's predictions (Step 12, ADR 0033), on a small M4 fitted to simulated
rows whose early stops a not-yet-recruiting status, a company sponsor and a small enrollment
target drive. No real data."""

import lightgbm as lgb
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
    # The expected value is the same for every row; the interval column, kept apart, varies
    # by row, so the two are not swapped (additivity alone would not tell them apart).
    assert np.ptp(six.base) == pytest.approx(0.0)
    assert np.ptp(six.interval) > 0


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
    assert drivers.shown("enrollment_count", 1234.0) == "1234"
    assert drivers.shown("registration_year", 2019.0) == "2019"
    assert drivers.shown("open_interventional_trials", 64612.0) == "64,612"
    assert drivers.shown("primary_completion_slip_months", -3.0) == "-3"
    assert drivers.shown("enrollment_target_ratio", 1234.6) == "1,235"
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


def test_both_horizons_from_one_set_of_passes_equal_separate_explanations(
    m4: DiscreteTimeBoosting, probe: LandmarkRows
) -> None:
    both = drivers.explain_horizons(m4, probe, (12, 24))
    for months in (12, 24):
        alone = drivers.explain(m4, probe, months)
        assert np.array_equal(both[months].values, alone.values)
        assert np.array_equal(both[months].target, alone.target)


def _every_tree(m4: DiscreteTimeBoosting, probe: LandmarkRows, months: int) -> np.ndarray:
    """The target's values by the definition: every tree of the model, one pass per interval."""
    landmark = m4.tree_landmark_matrix(probe.features)
    total = np.zeros((len(landmark), landmark.shape[1] + 2))
    count = months // 6
    for j in range(1, count + 1):
        x = np.column_stack([landmark, np.full(len(landmark), float(j), dtype=np.float32)])
        raw = np.asarray(m4.booster.predict(x, pred_contrib=True))
        per_class = raw.reshape(len(landmark), 3, landmark.shape[1] + 2)
        total += per_class[:, EVENT_STOP] - per_class[:, OUTCOME_CONTINUE]
    return total / count


def test_the_split_trees_give_the_values_of_every_tree(
    m4: DiscreteTimeBoosting, probe: LandmarkRows
) -> None:
    both = drivers.explain_horizons(m4, probe, (6, 12, 18, 24))
    for months, explanation in both.items():
        expected = _every_tree(m4, probe, months)
        width = explanation.values.shape[1]
        assert np.abs(explanation.values - expected[:, :width]).max() < 1e-9
        assert np.abs(explanation.interval - expected[:, width]).max() < 1e-9
        assert np.abs(explanation.base - expected[:, width + 1]).max() < 1e-9


def test_the_trees_are_grouped_by_class_and_by_the_interval_column(
    m4: DiscreteTimeBoosting,
) -> None:
    trees = drivers.split_trees(m4.booster)
    fixed = [group for group in trees.fixed if group is not None]
    moving = [group for group in trees.moving if group is not None]
    assert fixed  # the small model holds trees of both kinds; a group may still be empty
    assert moving
    # Every stop and continue tree lands in exactly one group, and none of the third class.
    assert m4.booster.num_trees() % 3 == 0
    assert sum(g.num_trees() for g in (*fixed, *moving)) == 2 * m4.booster.num_trees() // 3
    for group in fixed:
        assert INTERVAL not in set(group.trees_to_dataframe()["split_feature"])
    for group in moving:
        frame = group.trees_to_dataframe()
        splitting = frame[frame["split_feature"] == INTERVAL]["tree_index"].nunique()
        assert splitting == group.num_trees()


@pytest.fixture(scope="module")
def moving_booster() -> lgb.Booster:
    """A small model of three classes whose stop and continue classes both depend on the
    interval. Each tree sees two of the three columns, so some trees cannot split on it."""
    rng = np.random.default_rng(5)
    n = 3000
    interval = rng.integers(1, 5, n).astype(np.float32)
    x = np.column_stack([rng.normal(size=(n, 2)), interval]).astype(np.float32)
    odds = np.column_stack([1.5 * x[:, 0], 0.8 * interval, x[:, 1] - 0.8 * interval])
    labels = np.argmax(odds + rng.gumbel(size=(n, 3)), axis=1)
    settings = {
        "objective": "multiclass",
        "num_class": 3,
        "num_leaves": 7,
        "verbosity": -1,
        "feature_fraction": 0.67,
        "seed": 1,
        "deterministic": True,
        "num_threads": 1,
    }
    data = lgb.Dataset(x, label=labels, feature_name=["a", "b", INTERVAL])
    return lgb.train(settings, data, num_boost_round=30)


def test_the_split_holds_when_the_interval_moves_both_classes(
    moving_booster: lgb.Booster,
) -> None:
    trees = drivers.split_trees(moving_booster)
    assert all(group is not None for group in (*trees.fixed, *trees.moving))
    landmark = np.random.default_rng(6).normal(size=(200, 2)).astype(np.float32)
    fixed = drivers._difference(trees.fixed, drivers._at_interval(landmark, 1))
    for j in range(1, 5):
        x = drivers._at_interval(landmark, j)
        split = fixed + drivers._difference(trees.moving, x)
        assert np.abs(split - drivers._full_pass(moving_booster, x)).max() < 1e-12


def test_a_group_without_trees_contributes_nothing() -> None:
    x = np.ones((4, 3), dtype=np.float32)
    assert drivers._values(None, x).shape == (4, 4)
    assert not drivers._values(None, x).any()


@pytest.mark.parametrize(
    ("offset", "refused"),
    [(5e-10, False), (2e-9, True), (float("nan"), True), (float("inf"), True)],
)
def test_the_check_refuses_a_gap_above_its_tolerance_and_anything_not_a_number(
    moving_booster: lgb.Booster,
    monkeypatch: pytest.MonkeyPatch,
    offset: float,
    refused: bool,
) -> None:
    real = drivers._full_pass

    def off(booster: lgb.Booster, x: np.ndarray) -> np.ndarray:
        out = real(booster, x)
        if x[0, -1] == 4:  # the last interval only: the check must reach every interval
            out[-1, -1] += offset
        return out

    monkeypatch.setattr(drivers, "_full_pass", off)
    landmark = np.random.default_rng(6).normal(size=(8, 2)).astype(np.float32)
    trees = drivers.split_trees(moving_booster)
    if refused:
        with pytest.raises(RuntimeError, match="differ from the full pass"):
            drivers._check_split(moving_booster, trees, landmark, 4)
    else:
        drivers._check_split(moving_booster, trees, landmark, 4)


def test_a_model_of_another_shape_is_refused() -> None:
    x = np.random.default_rng(1).normal(size=(200, 2)).astype(np.float32)
    data = lgb.Dataset(x, label=(x[:, 0] > 0).astype(int), feature_name=["a", INTERVAL])
    two_classes = lgb.train({"objective": "binary", "verbosity": -1}, data, num_boost_round=3)
    with pytest.raises(RuntimeError, match="expected 3 trees a round"):
        drivers.split_trees(two_classes)


@pytest.mark.parametrize("wrong", ["swapped", "shifted"])
def test_a_split_that_differs_from_the_full_pass_is_refused(
    m4: DiscreteTimeBoosting, probe: LandmarkRows, monkeypatch: pytest.MonkeyPatch, wrong: str
) -> None:
    real = drivers.split_trees

    def broken(booster: object) -> drivers.TargetTrees:
        trees = real(booster)  # type: ignore[arg-type]
        if wrong == "swapped":  # the continue class's trees in the stop class's place
            return drivers.TargetTrees(trees.fixed[::-1], trees.moving[::-1])
        return drivers.TargetTrees(trees.fixed, (None, None))  # the moving trees lost

    monkeypatch.setattr(drivers, "split_trees", broken)
    with pytest.raises(RuntimeError, match="differ from the full pass"):
        drivers.explain_horizons(m4, probe, (12, 24))


def test_the_check_reaches_the_later_intervals_of_the_horizon(
    m4: DiscreteTimeBoosting, probe: LandmarkRows, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A moving tree filed as fixed shows no gap in interval 1, where the fixed pass is taken,
    so the call must check the later intervals of its horizons too."""
    monkeypatch.setattr(drivers, "_splits_on", lambda tree, column: False)
    with pytest.raises(RuntimeError, match="differ from the full pass"):
        drivers.explain_horizons(m4, probe, (12, 24))
