"""Helpers for the scoring tests: the feature tests' hand-built versions and the explanation
tests' small simulated M4, loaded by path."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from trialpulse.config import ProjectConfig, load_project_config


def _load(name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / relative)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


feature_helpers = _load("feature_helpers", "features/conftest.py")
explain_helpers = _load("explain_helpers", "explain/conftest.py")
RULES = feature_helpers.RULES


@pytest.fixture(scope="session")
def cfg() -> ProjectConfig:
    return load_project_config()
