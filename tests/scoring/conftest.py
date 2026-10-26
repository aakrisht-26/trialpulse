"""Helpers for the scoring tests: the feature tests' hand-built versions, loaded by path."""

import importlib.util
from pathlib import Path
from types import ModuleType


def _load(name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / relative)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


feature_helpers = _load("feature_helpers", "features/conftest.py")
