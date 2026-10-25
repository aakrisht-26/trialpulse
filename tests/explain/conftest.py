"""A small M4 fitted on simulated rows, for the explanation tests. No real data.

The simulation is the model tests' own (tests/models/conftest.py), loaded by path: early
stops are driven by a not-yet-recruiting status, a company sponsor and a small enrollment
target."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from trialpulse.config import ProjectConfig, load_project_config
from trialpulse.models.discrete_time import DiscreteTimeBoosting, build


def _simulation() -> ModuleType:
    path = Path(__file__).parents[1] / "models" / "conftest.py"
    spec = importlib.util.spec_from_file_location("model_simulation", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


simulation = _simulation()
ORIGIN = simulation.ORIGIN
simulated_rows = simulation.simulated_rows


@pytest.fixture(scope="session")
def cfg() -> ProjectConfig:
    return load_project_config()


def _fitted(cfg: ProjectConfig, seed: int) -> DiscreteTimeBoosting:
    rows, time, event = simulated_rows(6_000, seed=seed, lapse_share=0.3)
    model = build("m4", cfg, {"learning_rate": 0.1})
    assert isinstance(model, DiscreteTimeBoosting)
    return model.fit_rows(rows, time, event, ORIGIN)


@pytest.fixture(scope="session")
def m4(cfg: ProjectConfig) -> DiscreteTimeBoosting:
    return _fitted(cfg, seed=1)


@pytest.fixture(scope="session")
def m4_other(cfg: ProjectConfig) -> DiscreteTimeBoosting:
    """A second small M4 on other simulated trials: a stand-in for another origin's model."""
    return _fitted(cfg, seed=2)
