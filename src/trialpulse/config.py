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


class UnknownRuleConfig(_Section):
    """The registry's UNKNOWN rule on versioned fields (ADR 0014)."""

    statuses: tuple[str, ...] = Field(min_length=1)
    verification_lapse_months: PositiveInt


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
    # ADR 0017: one censoring curve per lead sponsor class; a class with fewer rows than
    # this among the evaluation rows of an origin shares one pooled curve with the other
    # small classes, at every landmark index.
    censoring_min_rows: PositiveInt


class FeatureConfig(_Section):
    """Constants of the point-in-time features (CLAUDE.md Section 8)."""

    recent_versions_months: PositiveInt
    sponsor_prior_weight: PositiveInt
    sponsor_alias_min_trials: PositiveInt
    sponsor_alias_wait_days: NonNegativeInt
    text_components: PositiveInt
    text_hash_bits: int = Field(ge=10, le=24)
    leakage_test_landmarks: PositiveInt


class TuningConfig(_Section):
    """The tuning budget (CLAUDE.md Section 9)."""

    n_trials: PositiveInt
    trial_sample_share: float = Field(gt=0.0, le=1.0)


class SeedConfig(_Section):
    """Random seeds. Later steps add named seeds here."""

    default: NonNegativeInt


class LiveConfig(_Section):
    """Run control of the live system (ADR 0045)."""

    overlap_days: PositiveInt  # the pull starts at the watermark minus this many days
    null_gate_share: float = Field(gt=0, lt=1)  # stop when a field turned NULL in more
    max_missing_overlap: NonNegativeInt  # stored overlap rows the pull may miss
    full_run_days: PositiveInt  # a full run is due after this many days
    lease_stale_minutes: PositiveInt
    alarm_minutes: PositiveInt
    kill_minutes: PositiveInt
    recent_post_days: PositiveInt  # post dates kept on the wide row
    page_size: int = Field(gt=0, le=1000)
    requests_per_minute: float = Field(gt=0, le=40)
    retention_alert_mb: tuple[PositiveInt, ...] = Field(min_length=1)
    retention_months: PositiveInt  # history rows older than this are exported, then deleted
    version_retention_months: PositiveInt  # the same for live.study_versions (ADR 0069)
    keepalive_seconds: float = Field(gt=0, le=240)  # ADR 0072: under Neon's 5 minutes

    @model_validator(mode="after")
    def _kill_after_alarm(self) -> Self:
        if self.kill_minutes <= self.alarm_minutes:
            raise ValueError("live.kill_minutes must be later than live.alarm_minutes")
        return self


class ApiConfig(_Section):
    """The read-only API of Step 15 (ADR 0049)."""

    data_status_refresh_minutes: PositiveInt  # the data-as-of date is read at most this often
    connect_timeout_seconds: PositiveInt
    retry_wait_seconds: float = Field(ge=0)  # before the one retry of a waking database
    unavailable_retry_after_seconds: PositiveInt
    rate_limit_per_minute: PositiveInt
    trusted_proxies: NonNegativeInt  # proxies that append to X-Forwarded-For (Spaces: 1)
    cache_entries: PositiveInt
    page_size_max: PositiveInt


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
    unknown_rule: UnknownRuleConfig
    stop_reasons: StopReasonConfig
    landmarks: LandmarkConfig
    horizons_months: tuple[PositiveInt, ...] = Field(min_length=1)
    discrete_time: DiscreteTimeConfig
    walk_forward: WalkForwardConfig
    evaluation: EvaluationConfig
    features: FeatureConfig
    tuning: TuningConfig
    seeds: SeedConfig
    live: LiveConfig
    api: ApiConfig

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
    def _unknown_rule_statuses_are_open(self) -> Self:
        outside = set(self.unknown_rule.statuses) - set(self.statuses.open)
        if outside:
            raise ValueError(f"unknown_rule statuses must be open statuses: {sorted(outside)}")
        return self

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
    sponsor_id_key: SecretStr | None = None  # ADR 0044: the key of the sponsor ids
    api_database_url: SecretStr | None = None  # ADR 0049: the API's read-only pooled URL
