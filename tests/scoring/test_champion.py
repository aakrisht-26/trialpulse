"""The served champion's bundle (ADR 0034 decision 5): saved and loaded back it scores the
same, a changed file is refused, and the served fit ends the day after the data cutoff. On a
small M4 fitted on simulated rows; no real data, no MLflow."""

import datetime as dt

import numpy as np
import pytest

from trialpulse.cli import RefusedError
from trialpulse.config import ProjectConfig
from trialpulse.features.transforms import SponsorPriors
from trialpulse.models.discrete_time import DiscreteTimeBoosting
from trialpulse.scoring import champion

from .conftest import explain_helpers

PRIORS = SponsorPriors(
    10,
    0.08,
    {"INDUSTRY": 0.21, "OTHER": 0.04},
    {"INDUSTRY": 30, "OTHER": 50},
    {"INDUSTRY": 7, "OTHER": 2},
)


@pytest.fixture(scope="module")
def small_m4(cfg: ProjectConfig) -> DiscreteTimeBoosting:
    model: DiscreteTimeBoosting = explain_helpers._fitted(cfg, seed=1)
    return model


def test_a_saved_bundle_loads_back_and_scores_the_same(
    cfg: ProjectConfig, small_m4: DiscreteTimeBoosting, tmp_path
) -> None:
    champion.save_bundle(small_m4, PRIORS, {"data_cutoff": "2026-09-25"}, tmp_path)
    bundle = champion.load_bundle(cfg, tmp_path)
    rows, _, _ = explain_helpers.simulated_rows(300, seed=9, lapse_share=0.0)
    for months in (12, 24):
        np.testing.assert_array_equal(bundle.model.predict_months(rows, months),
                                      small_m4.predict_months(rows, months))  # fmt: skip
    assert bundle.priors == PRIORS
    assert bundle.meta["data_cutoff"] == "2026-09-25"


@pytest.mark.parametrize("name", [champion.BOOSTER, champion.LEVELS, champion.PRIORS])
def test_a_changed_file_is_refused(
    cfg: ProjectConfig, small_m4: DiscreteTimeBoosting, tmp_path, name: str
) -> None:
    champion.save_bundle(small_m4, PRIORS, {}, tmp_path)
    path = tmp_path / name
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(RefusedError, match="not the file the champion was saved with"):
        champion.load_bundle(cfg, tmp_path)


def test_a_missing_bundle_says_how_to_make_it(cfg: ProjectConfig, tmp_path) -> None:
    with pytest.raises(RefusedError, match=r"trialpulse\.scoring\.champion"):
        champion.load_bundle(cfg, tmp_path)


def test_the_served_fit_ends_the_day_after_the_data_cutoff(cfg: ProjectConfig) -> None:
    assert cfg.dataset.cutoff is not None
    assert champion.served_origin(cfg) == cfg.dataset.cutoff + dt.timedelta(days=1)
