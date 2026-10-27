"""The model step of score(as_of_date) and the served keys (ADR 0034), on a small M4 fitted
on simulated rows (tests/explain): the risks, the sponsor priors, the drivers row for row,
the keys' windows and the refusals to publish. No real data."""

import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from trialpulse.config import ProjectConfig
from trialpulse.dates import add_months
from trialpulse.eval.rows import LandmarkRows
from trialpulse.features.frame import as_arrays
from trialpulse.features.transforms import SponsorPriors
from trialpulse.models.discrete_time import DiscreteTimeBoosting
from trialpulse.scoring import features as served_features
from trialpulse.scoring import score

from .conftest import RULES, explain_helpers

DAY = dt.date(2019, 6, 30)
PRIORS = SponsorPriors(10, 0.08, {"INDUSTRY": 0.21, "OTHER": 0.04, "NIH": 0.06}, {}, {})


def _inputs(n: int = 40) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, _, _ = explain_helpers.simulated_rows(n, seed=7, lapse_share=0.0)
    rng = np.random.default_rng(3)
    ids = [f"NCT{i:08d}" for i in range(n)]
    features = pd.DataFrame(dict(rows.features))
    features.insert(0, "trial_id", ids)
    features.insert(1, "true_index", rng.integers(0, 15, n))
    features["sponsor_prior_ended"] = rng.integers(0, 30, n).astype(float)
    features["sponsor_prior_stopped"] = np.minimum(rng.integers(0, 5, n),
                                                   features["sponsor_prior_ended"])  # fmt: skip
    features.loc[::7, ["sponsor_prior_ended", "sponsor_prior_stopped"]] = np.nan
    scored = features[["trial_id", "true_index"]].copy()
    scored["k_star"] = np.minimum(scored["true_index"], 6)
    scored["flagged"] = scored["true_index"] >= 7
    scored["content_hash"] = [f"h{i}" for i in range(n)]
    return scored, features


def _bundle(model: DiscreteTimeBoosting) -> score.Bundle:
    return score.Bundle(model, PRIORS, "trialpulse-m4", "trialpulse-m4/0",
                        {"data_cutoff": "2026-09-25"})  # fmt: skip


def _expected(model: DiscreteTimeBoosting, features: pd.DataFrame, months: int) -> np.ndarray:
    smoothed = features.copy()
    smoothed["sponsor_stop_rate"] = PRIORS.rate(
        features["sponsor_class"].to_numpy(dtype=object),
        features["sponsor_prior_ended"].to_numpy(dtype=np.float64),
        features["sponsor_prior_stopped"].to_numpy(dtype=np.float64),
    )
    columns = ["trial_id", "landmark_index", *[f for f in model.features if f != "landmark_index"]]
    stamp = np.full(len(features), np.datetime64(DAY, "D"))
    rows = LandmarkRows(features["trial_id"].to_numpy(dtype=object),
                        np.zeros(len(features), dtype=np.int64), stamp,
                        np.zeros(len(features), dtype=np.int64), stamp,
                        as_arrays(smoothed[columns]))  # fmt: skip
    out: np.ndarray = model.predict_months(rows, months)
    return out


@pytest.fixture(scope="module")
def small_m4(cfg: ProjectConfig) -> DiscreteTimeBoosting:
    model: DiscreteTimeBoosting = explain_helpers._fitted(cfg, seed=1)
    return model


def test_the_risks_are_the_models_on_features_smoothed_with_the_bundles_priors(
    small_m4: DiscreteTimeBoosting,
) -> None:
    scored, features = _inputs()
    out, _ = score.predict(scored, features, _bundle(small_m4), DAY, with_drivers=False)
    assert out["nct_id"].tolist() == scored["trial_id"].tolist()
    for months in score.HORIZONS:
        np.testing.assert_allclose(out[f"risk_{months}m"], _expected(small_m4, features, months),
                                   rtol=0, atol=1e-12)  # fmt: skip
    assert not np.allclose(out["risk_12m"], out["risk_24m"])
    assert out["landmark_index"].tolist() == scored["k_star"].tolist()
    assert out["true_index"].tolist() == scored["true_index"].tolist()
    assert (out["as_of_date"] == pd.Timestamp(DAY)).all()


def test_each_row_gets_its_own_drivers(small_m4: DiscreteTimeBoosting) -> None:
    scored, features = _inputs(12)
    out, timing = score.predict(scored, features, _bundle(small_m4), DAY, with_drivers=True)
    assert "drivers" in timing
    assert out["drivers"].nunique() > 1
    for i in (0, 5, 11):
        alone, _ = score.predict(scored.iloc[[i]].reset_index(drop=True), features,
                                 _bundle(small_m4), DAY, with_drivers=True)  # fmt: skip
        assert out["drivers"][i] == alone["drivers"][0]
        payload = json.loads(out["drivers"][i])
        assert set(payload) == {"12", "24"}
        assert all(len(top) == 5 for top in payload.values())


def test_a_scored_trial_without_features_is_refused(small_m4: DiscreteTimeBoosting) -> None:
    scored, features = _inputs(5)
    with pytest.raises(RuntimeError, match="1 scored trials have no served features"):
        score.predict(scored, features.iloc[1:], _bundle(small_m4), DAY, with_drivers=False)


def test_the_sponsor_rate_is_smoothed_with_the_given_priors() -> None:
    frame = pd.DataFrame({"sponsor_class": ["INDUSTRY", "OTHER"],
                          "sponsor_prior_ended": [20.0, np.nan],
                          "sponsor_prior_stopped": [5.0, np.nan],
                          "sponsor_stop_rate": [0.5, 0.5]})  # fmt: skip
    out = served_features.with_priors(frame, PRIORS)
    assert out["sponsor_stop_rate"].tolist() == pytest.approx([(5 + 10 * 0.21) / (20 + 10), 0.04])
    assert frame["sponsor_stop_rate"].tolist() == [0.5, 0.5]  # the input is left as it was


def test_served_keys_slide_the_gap_of_the_capped_index() -> None:
    t0 = np.array(["2014-08-31"] * 4, dtype="datetime64[D]")
    index = np.array([3, 6, 7, 15])
    day = add_months(t0, 6 * index) + np.timedelta64(40, "D")
    keys = served_features.keys_for(np.array(["A", "B", "C", "D"]), t0, index, day, RULES, 6)
    assert keys["landmark_index"].tolist() == [3, 6, 7, 15]  # the row's own index is kept
    k_star = np.minimum(index, 6)
    gap = (add_months(t0, 6 * k_star) - add_months(t0, 6 * k_star - 6)).astype(np.int64)
    window = (day - keys["window_start"].to_numpy(dtype="datetime64[D]")).astype(np.int64)
    assert window.tolist() == gap.tolist()
    # Beyond the last landmark the window is landmark 6's gap (28 Feb 2017 to 31 Aug 2017).
    assert window[2] == window[3] == 184


def test_runs_without_drivers_and_in_sample_dates_are_not_published(
    small_m4: DiscreteTimeBoosting,
) -> None:
    bundle = _bundle(small_m4)
    assert score.publish_refusal(dt.date(2026, 9, 25), bundle, with_drivers=True) is None
    assert score.publish_refusal(dt.date(2026, 10, 1), bundle, with_drivers=True) is None
    assert "without drivers" in str(score.publish_refusal(dt.date(2026, 9, 25), bundle, False))
    assert "in-sample" in str(score.publish_refusal(dt.date(2026, 9, 24), bundle, True))
