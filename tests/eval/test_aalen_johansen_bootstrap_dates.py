"""Aalen-Johansen (M0), the cluster bootstrap, and calendar-month arithmetic."""

import numpy as np
import pytest

from trialpulse.dates import add_months, days_between
from trialpulse.eval.bootstrap import cluster_bootstrap
from trialpulse.models.aalen_johansen import AalenJohansenModel, aalen_johansen


def test_aalen_johansen_hand_example() -> None:
    # t=1 stop (5 at risk), t=2 completion (4), t=3 censored, t=4 stop (2), t=5 censored.
    time = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    event = np.array([1, 2, 0, 1, 0])
    curve = aalen_johansen(time, event)
    # CIF(1) = 1/5. S after t=2: (4/5)(3/4) = 3/5. CIF(4) = 1/5 + (3/5)(1/2) = 1/2.
    assert curve.at(np.array([0.5, 1.0, 3.9, 4.0, 9.0])) == pytest.approx([0, 0.2, 0.2, 0.5, 0.5])
    completion = aalen_johansen(time, event, cause=2)
    assert completion.at(9.0) == pytest.approx(0.2)


def test_aalen_johansen_without_censoring_is_the_empirical_share() -> None:
    rng = np.random.default_rng(0)
    time = rng.uniform(0, 1000, 500)
    event = rng.choice([1, 2], size=500)
    curve = aalen_johansen(time, event)
    assert curve.at(400.0) == pytest.approx(np.mean((time <= 400) & (event == 1)))


def test_m0_predicts_per_stratum_and_falls_back_to_pooled() -> None:
    time = np.array([1.0, 2.0, 1.0, 2.0])
    event = np.array([1, 1, 2, 2])
    model = AalenJohansenModel().fit(time, event, {"stratum": np.array(["a", "a", "b", "b"])})

    pred = model.predict_cif(5.0, {"stratum": np.array(["a", "b", "unseen"])})

    assert pred == pytest.approx([1.0, 0.0, 0.5])


def test_m0_requires_fit() -> None:
    with pytest.raises(RuntimeError, match="fit"):
        AalenJohansenModel().predict_cif(1.0, {"stratum": np.array(["a"])})


def test_cluster_bootstrap_keeps_clusters_whole() -> None:
    clusters = np.array(["t1", "t1", "t2", "t3", "t3", "t3"])
    seen: list[np.ndarray] = []

    def statistic(idx: np.ndarray) -> float:
        seen.append(idx)
        return float(len(idx))

    result = cluster_bootstrap(clusters, statistic, n_resamples=50, seed=1, confidence=0.95)

    assert result["estimate"] == 6.0
    for idx in seen[1:]:
        counts = np.bincount(idx, minlength=6)
        assert counts[0] == counts[1]  # rows of t1 always travel together
        assert counts[3] == counts[4] == counts[5]


def test_cluster_bootstrap_is_reproducible_and_brackets_the_estimate() -> None:
    rng = np.random.default_rng(3)
    values = rng.normal(10, 2, 400)
    clusters = np.arange(400)

    def mean(idx: np.ndarray) -> float:
        return float(values[idx].mean())

    first = cluster_bootstrap(clusters, mean, 300, seed=42, confidence=0.95)
    second = cluster_bootstrap(clusters, mean, 300, seed=42, confidence=0.95)

    assert first == second
    assert first["ci_low"] < first["estimate"] < first["ci_high"]
    assert first["ci_high"] - first["ci_low"] == pytest.approx(4 * 2 / np.sqrt(400), rel=0.25)


def test_cluster_bootstrap_counts_invalid_resamples() -> None:
    result = cluster_bootstrap(np.arange(5), lambda idx: float("nan"), 10, seed=0, confidence=0.9)
    assert result["valid_resamples"] == 0
    assert np.isnan(result["ci_low"])


def test_add_months_clamps_to_month_end() -> None:
    dates = np.array(
        ["2020-01-31", "2019-01-31", "2020-08-15", "2021-12-31"], dtype="datetime64[D]"
    )
    assert add_months(dates, 1).astype(str).tolist() == [
        "2020-02-29",
        "2019-02-28",
        "2020-09-15",
        "2022-01-31",
    ]
    assert add_months(dates[:1], 12).astype(str).tolist() == ["2021-01-31"]
    assert days_between(dates[:1], add_months(dates[:1], 12)).tolist() == [366.0]
