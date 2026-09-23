"""Differential tests: our estimators against lifelines and scikit-learn on seeded random
data with censoring and competing events.

lifelines breaks tied times by adding jitter in its Aalen-Johansen fitter, so that
comparison uses continuous times. Kaplan-Meier is also compared on integer times with ties,
which lifelines handles exactly.
"""

import numpy as np
import pytest
from lifelines import AalenJohansenFitter, KaplanMeierFitter
from sklearn.metrics import roc_auc_score

from trialpulse.eval.ipcw import censoring_survival, horizon_labels_and_weights, kaplan_meier
from trialpulse.eval.metrics import ipcw_auc, ipcw_brier
from trialpulse.models.aalen_johansen import aalen_johansen

HORIZON = 365.0


def _data(seed: int, n: int = 3000, integer_times: bool = False) -> tuple[np.ndarray, ...]:
    """Early stops, completions and censoring as competing exponential clocks."""
    rng = np.random.default_rng(seed)
    stop = rng.exponential(900, n)
    complete = rng.exponential(700, n)
    censor = rng.exponential(1200, n)
    time = np.minimum.reduce([stop, complete, censor])
    if integer_times:
        time = np.ceil(time)
    event = np.select([stop <= np.minimum(complete, censor), complete <= censor], [1, 2], 0)
    score = rng.uniform(size=n) + 0.3 * (event == 1)  # informative but imperfect
    return time, event.astype(np.int64), score


@pytest.mark.parametrize("integer_times", [False, True])
def test_kaplan_meier_matches_lifelines(integer_times: bool) -> None:
    time, event, _ = _data(1, integer_times=integer_times)
    grid = np.unique(np.concatenate([time, np.linspace(0, time.max() * 1.1, 200)]))

    for flag in (event != 0, event == 0):  # event survival, and the censoring survival G
        ours = kaplan_meier(time, flag).at(grid)
        theirs = KaplanMeierFitter().fit(time, event_observed=flag).predict(grid).to_numpy()
        np.testing.assert_allclose(ours, theirs, rtol=0, atol=1e-12)

    g = censoring_survival(time, event).at(grid)
    theirs_g = KaplanMeierFitter().fit(time, event_observed=event == 0).predict(grid).to_numpy()
    np.testing.assert_allclose(g, theirs_g, rtol=0, atol=1e-12)


@pytest.mark.parametrize("cause", [1, 2])
def test_aalen_johansen_matches_lifelines_on_continuous_times(cause: int) -> None:
    time, event, _ = _data(2)
    assert len(np.unique(time)) == len(time)  # no ties, so lifelines adds no jitter

    fitter = AalenJohansenFitter(calculate_variance=False)
    fitter.fit(time, event, event_of_interest=cause)
    theirs = fitter.cumulative_density_
    grid = theirs.index.to_numpy()

    ours = aalen_johansen(time, event, cause=cause).at(grid)
    np.testing.assert_allclose(ours, theirs.iloc[:, 0].to_numpy(), rtol=0, atol=1e-10)


@pytest.mark.parametrize("rounded_scores", [False, True])
def test_ipcw_auc_matches_sklearn_with_ipcw_sample_weights(rounded_scores: bool) -> None:
    time, event, score = _data(3)
    if rounded_scores:
        score = np.round(score, 1)  # many ties, which both count as half
    y, w = horizon_labels_and_weights(time, event, HORIZON)
    keep = w > 0
    assert (~keep).any()  # some rows are censored before the horizon

    theirs = roc_auc_score(y[keep], score[keep], sample_weight=w[keep])

    assert ipcw_auc(time, event, score, HORIZON) == pytest.approx(theirs, abs=1e-12)


def test_ipcw_weights_and_brier_match_a_direct_computation() -> None:
    time, event, score = _data(4)
    p = np.clip(score / score.max(), 0, 1)
    # G from lifelines. With continuous times no censoring coincides with an event, so
    # G(T-) equals G evaluated just below T.
    kmf = KaplanMeierFitter().fit(time, event_observed=event == 0)
    g_before = kmf.predict(time - 1e-9).to_numpy()
    g_horizon = float(kmf.predict(HORIZON))
    within = time <= HORIZON
    case = within & (event == 1)
    competed = within & (event == 2)
    survived = ~within
    weights = np.where(case | competed, 1 / g_before, np.where(survived, 1 / g_horizon, 0.0))
    direct_brier = np.sum(weights * (case.astype(float) - p) ** 2) / len(p)

    y, w = horizon_labels_and_weights(time, event, HORIZON)
    np.testing.assert_allclose(w, weights, rtol=1e-12, atol=0)
    np.testing.assert_array_equal(y, case.astype(np.int8))
    assert ipcw_brier(time, event, p, HORIZON) == pytest.approx(direct_brier, rel=1e-12)
