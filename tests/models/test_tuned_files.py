"""The settings committed in config/tuned (ADR 0025): the files every later run reads, the
locked test of Step 11 included. The other tests never read them (`default_model_settings`
in tests/conftest.py); this one reads them on purpose, from the repository."""

import json

import pytest

from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.models import discrete_time, tuning

COMMITTED = REPO_ROOT / "config" / "tuned"


@pytest.fixture(scope="module")
def cfg() -> ProjectConfig:
    return load_project_config()


@pytest.mark.parametrize("model", tuning.TUNABLE)
def test_a_committed_settings_file_is_what_the_tuning_of_this_configuration_writes(
    cfg: ProjectConfig, model: str
) -> None:
    """A file left from another search space, budget, seed or dataset revision would be read
    without complaint, so it is held against the configuration here. A missing file would
    make the model run on its defaults, also in the locked test."""
    path = COMMITTED / f"{model}.json"
    assert path.is_file(), f"{model} has no committed settings: run the tuning and commit {path}"
    record = json.loads(path.read_text(encoding="utf-8"))
    space = tuning.space(model)
    assert record["model"] == model
    assert set(record["settings"]) == set(space) == set(tuning.defaults(model))
    for name, value in record["settings"].items():
        low, high, scale = space[name]
        assert low <= value <= high, name
        assert isinstance(value, int) == (scale == "log-int"), name
    assert record["search_space"] == {name: list(bounds) for name, bounds in space.items()}
    assert record["default_settings"] == tuning.defaults(model)
    assert record["trials"] == cfg.tuning.n_trials
    assert record["trial_sample_share"] == cfg.tuning.trial_sample_share
    assert record["seed"] == cfg.seeds.default
    assert record["dataset_revision"] == cfg.dataset.revision
    assert record["objective"] == tuning.OBJECTIVE.format(months=min(cfg.horizons_months))
    assert 0.5 < record["default_auc"] <= record["auc"] < 1.0  # the default is the first trial
    assert sorted(record["by_origin"]) == sorted(
        o.date.isoformat() for o in cfg.walk_forward.origins if o.role == "dev"
    )
    # The models read exactly these settings from the repository's folder.
    assert discrete_time.tuned_settings(model, COMMITTED) == record["settings"]


def test_the_ablation_rungs_read_the_committed_settings_of_m4() -> None:
    chosen = discrete_time.tuned_settings("m4", COMMITTED)
    assert chosen
    for rung in ("m4-design", "m4-amendments", "m4-sponsor"):
        assert discrete_time.tuned_settings(rung, COMMITTED) == chosen


def test_no_other_test_reads_the_committed_settings(cfg: ProjectConfig) -> None:
    """The session fixture points the models at an empty folder, so a model built in a test
    has the default settings whatever is committed."""
    assert discrete_time.TUNED_DIR != COMMITTED
    assert not any(discrete_time.TUNED_DIR.iterdir())
    assert discrete_time.build("m4", cfg).settings == discrete_time.BOOSTING_DEFAULTS
    assert discrete_time.build("m3", cfg).settings == discrete_time.LOGISTIC_DEFAULTS
