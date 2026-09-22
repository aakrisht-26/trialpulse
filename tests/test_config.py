"""Tests for configuration loading (trialpulse.config)."""

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from trialpulse.config import PROJECT_CONFIG_PATH, ProjectConfig, Secrets, load_project_config

SECRET_NAMES = (
    "HF_TOKEN",
    "DATABASE_URL",
    "NEON_DATABASE_URL",
    "MLFLOW_TRACKING_URI",
    "MLFLOW_TRACKING_USERNAME",
    "MLFLOW_TRACKING_PASSWORD",
    "GROQ_API_KEY",
)


@pytest.fixture
def valid_values() -> dict[str, Any]:
    """The committed project config as plain data, for building invalid variants."""
    return load_project_config().model_dump()


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Remove any real secrets from the environment so tests are deterministic."""
    for name in SECRET_NAMES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _set_nested(values: dict[str, Any], dotted_key: str, new_value: Any) -> None:
    *parents, leaf = dotted_key.split(".")
    target = values
    for key in parents:
        target = target[key]
    target[leaf] = new_value


def test_committed_config_matches_locked_definitions() -> None:
    """Smoke test: the committed config loads and matches CLAUDE.md Section 6.

    These values are locked. If this test fails, the change needs an approved
    ADR before the test is updated.
    """
    cfg = load_project_config()

    assert cfg.dataset.repo_id == "brbk/clinical_trials_history"
    assert cfg.dataset.config_name == "core"
    assert cfg.population.study_type == "INTERVENTIONAL"
    assert cfg.population.min_first_post_date == dt.date(2008, 1, 1)
    assert cfg.statuses.early_stop == ("TERMINATED", "WITHDRAWN")
    assert cfg.statuses.competing == ("COMPLETED",)
    assert cfg.statuses.unknown == ("UNKNOWN",)
    assert set(cfg.statuses.open) == {
        "NOT_YET_RECRUITING",
        "RECRUITING",
        "ENROLLING_BY_INVITATION",
        "ACTIVE_NOT_RECRUITING",
        "SUSPENDED",
    }
    assert cfg.stop_reasons.operational == (
        "accrual",
        "business",
        "funding",
        "administrative",
        "covid19",
    )
    assert cfg.stop_reasons.scientific == ("safety", "efficacy")
    assert (cfg.landmarks.spacing_months, cfg.landmarks.max_index) == (6, 6)
    assert cfg.horizons_months == (12, 24)
    assert (cfg.discrete_time.interval_months, cfg.discrete_time.n_intervals) == (6, 4)
    assert cfg.walk_forward.eval_window_months == 12
    assert [(origin.date, origin.role) for origin in cfg.walk_forward.origins] == [
        (dt.date(2016, 1, 1), "dev"),
        (dt.date(2017, 1, 1), "dev"),
        (dt.date(2018, 1, 1), "test"),
        (dt.date(2019, 1, 1), "test"),
        (dt.date(2020, 1, 1), "stress"),
    ]
    assert cfg.evaluation.bootstrap_resamples == 1000
    assert cfg.evaluation.confidence_level == pytest.approx(0.95)
    assert cfg.evaluation.calibration_bins == 10
    assert cfg.evaluation.lift_top_fraction == pytest.approx(0.10)


def test_environment_cannot_override_project_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HORIZONS_MONTHS", "[6]")
    monkeypatch.setenv("SEEDS", '{"default": 7}')
    monkeypatch.setenv("SEEDS__DEFAULT", "7")

    cfg = load_project_config()

    assert cfg.horizons_months == (12, 24)
    assert cfg.seeds.default == 42


def test_project_config_is_immutable() -> None:
    cfg = load_project_config()
    with pytest.raises(ValidationError, match="frozen"):
        cfg.seeds.default = 7  # type: ignore[misc]


def test_missing_config_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="project config not found"):
        load_project_config(tmp_path / "missing.yaml")


def test_unknown_key_in_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "project.yaml"
    text = PROJECT_CONFIG_PATH.read_text(encoding="utf-8") + "\nunexpected_key: 1\n"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValidationError, match="unexpected_key"):
        load_project_config(path)


@pytest.mark.parametrize(
    ("dotted_key", "new_value", "match"),
    [
        ("horizons_months", [12, 36], "exceeds the discrete-time follow-up"),
        ("horizons_months", [9], "not a multiple of interval_months"),
        ("horizons_months", [24, 12], "increasing order"),
        ("statuses.early_stop", ["TERMINATED", "COMPLETED"], "appears in"),
        ("stop_reasons.operational", ["accrual", "safety"], "appears in"),
        ("evaluation.confidence_level", 1.5, "less than 1"),
        ("landmarks.spacing_month", 6, "Extra inputs are not permitted"),
    ],
)
def test_invalid_values_are_rejected(
    valid_values: dict[str, Any], dotted_key: str, new_value: Any, match: str
) -> None:
    _set_nested(valid_values, dotted_key, new_value)
    with pytest.raises(ValidationError, match=match):
        ProjectConfig(**valid_values)


def test_origins_out_of_order_are_rejected(valid_values: dict[str, Any]) -> None:
    origins = valid_values["walk_forward"]["origins"]
    valid_values["walk_forward"]["origins"] = list(reversed(origins))
    with pytest.raises(ValidationError, match="increasing order"):
        ProjectConfig(**valid_values)


def test_unknown_origin_role_is_rejected(valid_values: dict[str, Any]) -> None:
    valid_values["walk_forward"]["origins"][0]["role"] = "holdout"
    with pytest.raises(ValidationError, match="role"):
        ProjectConfig(**valid_values)


def test_secrets_come_from_environment_and_are_masked(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("HF_TOKEN", "hf_example_value")

    secrets = Secrets(_env_file=None)

    assert secrets.hf_token is not None
    assert secrets.hf_token.get_secret_value() == "hf_example_value"
    assert "hf_example_value" not in repr(secrets)
    assert "hf_example_value" not in str(secrets)
    assert secrets.groq_api_key is None


def test_secrets_env_file_handling(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "HF_TOKEN=from_file\nDATABASE_URL=\nUNRELATED_VARIABLE=1\n", encoding="utf-8"
    )

    secrets = Secrets(_env_file=env_file)

    assert secrets.hf_token is not None
    assert secrets.hf_token.get_secret_value() == "from_file"
    assert secrets.database_url is None  # an empty value counts as unset


def test_environment_takes_precedence_over_env_file(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("HF_TOKEN=from_file\n", encoding="utf-8")
    clean_env.setenv("HF_TOKEN", "from_environment")

    secrets = Secrets(_env_file=env_file)

    assert secrets.hf_token is not None
    assert secrets.hf_token.get_secret_value() == "from_environment"
