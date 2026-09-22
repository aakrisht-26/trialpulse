"""Configuration loading.

Two kinds of configuration are kept apart on purpose:

- ProjectConfig holds the locked definitions and tunable constants from
  CLAUDE.md Section 6. It is read only from config/project.yaml, which is
  version controlled, so a commit fully determines the constants a run used.
  Environment variables and .env files cannot override it, so a stray shell
  variable can never silently change a locked definition.
- Secrets holds credentials and connection strings. It is read from the
  environment or a local .env file that is never committed. Values that grant
  access are SecretStr, so they are masked in reprs and logs.
"""

import datetime as dt
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_CONFIG_PATH = REPO_ROOT / "config" / "project.yaml"
ENV_FILE_PATH = REPO_ROOT / ".env"

OriginRole = Literal["dev", "test", "stress"]


def _require_disjoint(groups: dict[str, tuple[str, ...]]) -> None:
    """Raise ValueError if any value appears more than once across the groups."""
    seen: dict[str, str] = {}
    for group, values in groups.items():
        for value in values:
            if value in seen:
                raise ValueError(f"{value!r} appears in {seen[value]!r} and again in {group!r}")
            seen[value] = group


class _Section(BaseModel):
    """Base for config sections: immutable, and unknown keys are errors."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DatasetConfig(_Section):
    """The version-history dataset on Hugging Face (CLAUDE.md Section 7)."""

    repo_id: str
    config_name: str
    revision: str | None
    cutoff: dt.date | None


class PopulationConfig(_Section):
    """Cohort inclusion rule."""

    study_type: str
    min_first_post_date: dt.date


class StatusConfig(_Section):
    """Overall-status groups that define events, as canonical API v2 enum names."""

    open: tuple[str, ...] = Field(min_length=1)
    early_stop: tuple[str, ...] = Field(min_length=1)
    competing: tuple[str, ...] = Field(min_length=1)
    unknown: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _groups_are_disjoint(self) -> Self:
        _require_disjoint(
            {
                "open": self.open,
                "early_stop": self.early_stop,
                "competing": self.competing,
                "unknown": self.unknown,
            }
        )
        return self


class StopReasonConfig(_Section):
    """Stop-reason groups for the cause-specific targets."""

    operational: tuple[str, ...] = Field(min_length=1)
    scientific: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _groups_are_disjoint(self) -> Self:
        _require_disjoint({"operational": self.operational, "scientific": self.scientific})
        return self


class LandmarkConfig(_Section):
    """Landmarks L_k = t0 + spacing_months * k, for k = 0 to max_index."""

    spacing_months: PositiveInt
    max_index: NonNegativeInt


class DiscreteTimeConfig(_Section):
    """Follow-up after each landmark, split into n_intervals of interval_months."""

    interval_months: PositiveInt
    n_intervals: PositiveInt


class Origin(_Section):
    """One walk-forward origin T and its role."""

    date: dt.date
    role: OriginRole


class WalkForwardConfig(_Section):
    """Walk-forward origins. Evaluation rows have T <= L < T + eval_window_months."""

    eval_window_months: PositiveInt
    origins: tuple[Origin, ...] = Field(min_length=1)

    @field_validator("origins")
    @classmethod
    def _dates_strictly_increasing(cls, origins: tuple[Origin, ...]) -> tuple[Origin, ...]:
        dates = [origin.date for origin in origins]
        if dates != sorted(set(dates)):
            raise ValueError("origin dates must be unique and in increasing order")
        return origins


class EvaluationConfig(_Section):
    """Constants of the evaluation metrics."""

    bootstrap_resamples: PositiveInt
    confidence_level: float = Field(gt=0, lt=1)
    calibration_bins: PositiveInt
    lift_top_fraction: float = Field(gt=0, le=1)


class SeedConfig(_Section):
    """Random seeds. Later steps add named seeds here."""

    default: NonNegativeInt


class ProjectConfig(BaseSettings):
    """Locked definitions and tunable constants (CLAUDE.md Section 6).

    Build it with load_project_config(). The class derives from BaseSettings only
    so that the pydantic-settings YAML source can read into it. Environment and
    .env sources are switched off in settings_customise_sources.
    """

    model_config = SettingsConfigDict(extra="forbid", frozen=True)

    dataset: DatasetConfig
    population: PopulationConfig
    statuses: StatusConfig
    stop_reasons: StopReasonConfig
    landmarks: LandmarkConfig
    horizons_months: tuple[PositiveInt, ...] = Field(min_length=1)
    discrete_time: DiscreteTimeConfig
    walk_forward: WalkForwardConfig
    evaluation: EvaluationConfig
    seeds: SeedConfig

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Only values passed in explicitly (from the YAML file) count.
        return (init_settings,)

    @model_validator(mode="after")
    def _horizons_fit_discrete_time_grid(self) -> Self:
        # The discrete-time CIF is defined only at interval boundaries, so every
        # horizon must land on one, inside the modeled follow-up.
        horizons = self.horizons_months
        if list(horizons) != sorted(set(horizons)):
            raise ValueError("horizons_months must be unique and in increasing order")
        interval = self.discrete_time.interval_months
        for horizon in horizons:
            if horizon % interval != 0:
                raise ValueError(
                    f"horizon {horizon} is not a multiple of interval_months ({interval})"
                )
        follow_up = interval * self.discrete_time.n_intervals
        if horizons[-1] > follow_up:
            raise ValueError(
                f"horizon {horizons[-1]} exceeds the discrete-time follow-up of {follow_up} months"
            )
        return self


def load_project_config(path: Path = PROJECT_CONFIG_PATH) -> ProjectConfig:
    """Read and validate the project configuration from a YAML file.

    Raises FileNotFoundError if the file is missing (the pydantic-settings YAML
    source would otherwise skip it without an error) and pydantic.ValidationError
    if a value is missing, unknown or out of range.
    """
    if not path.is_file():
        raise FileNotFoundError(f"project config not found: {path}")
    values = YamlConfigSettingsSource(ProjectConfig, yaml_file=path, yaml_file_encoding="utf-8")()
    return ProjectConfig(**values)


class Secrets(BaseSettings):
    """Credentials and connection strings, from the environment or the local .env file.

    Every field is optional because each roadmap step needs a different subset.
    Environment variables take precedence over .env, and empty values count as
    unset. Unrelated variables in .env are ignored.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE_PATH,
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
    )

    hf_token: SecretStr | None = None
    database_url: SecretStr | None = None
    neon_database_url: SecretStr | None = None
    mlflow_tracking_uri: str | None = None
    mlflow_tracking_username: str | None = None
    mlflow_tracking_password: SecretStr | None = None
    groq_api_key: SecretStr | None = None
