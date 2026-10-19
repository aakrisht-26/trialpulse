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
    assert 0 <= record["chosen_trial"] < record["trials"]
    by_origin = list(record["by_origin"].values())
    assert record["auc"] == pytest.approx(sum(by_origin) / len(by_origin), abs=1e-12)
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
    for rung in ("m4-design", "m4-amendments", "m4-text"):
        assert discrete_time.tuned_settings(rung, COMMITTED) == chosen


def test_no_other_test_reads_the_committed_settings(cfg: ProjectConfig) -> None:
    """The session fixture points the models at an empty folder, so a model built in a test
    has the default settings whatever is committed."""
    assert discrete_time.TUNED_DIR != COMMITTED
    assert not any(discrete_time.TUNED_DIR.iterdir())
    assert discrete_time.build("m4", cfg).settings == discrete_time.BOOSTING_DEFAULTS
    assert discrete_time.build("m3", cfg).settings == discrete_time.LOGISTIC_DEFAULTS


@pytest.mark.slow  # reads the studies of this machine, where the tuning ran
@pytest.mark.parametrize("model", tuning.TUNABLE)
def test_the_committed_settings_are_the_choice_of_the_study(model: str) -> None:
    """A committed settings file is small enough to edit by hand, and a value changed inside
    its range passes every check above. Where the study that chose the settings is on disk
    (data/results is not committed), the two must agree: the settings are those of the
    chosen trial, and the chosen trial is the best one by the declared rule."""
    study_path = REPO_ROOT / "data" / "results" / "tuning" / f"{model}.json"
    if not study_path.is_file():
        pytest.skip("the study is not on this machine")
    study = json.loads(study_path.read_text(encoding="utf-8"))
    record = json.loads((COMMITTED / f"{model}.json").read_text(encoding="utf-8"))
    trials = study["all_trials"]
    assert len(trials) == record["trials"]
    chosen = trials[record["chosen_trial"]]
    assert chosen["number"] == record["chosen_trial"]
    assert chosen["settings"] == record["settings"]
    assert (chosen["auc"], chosen["brier"]) == (record["auc"], record["brier"])
    assert min(trials, key=tuning.rank)["number"] == record["chosen_trial"]
    assert trials[0]["settings"] == record["default_settings"]
    assert trials[0]["auc"] == record["default_auc"]
    written = {k: v for k, v in study.items() if k not in ("all_trials", "seconds", "tracking")}
    assert written == record
