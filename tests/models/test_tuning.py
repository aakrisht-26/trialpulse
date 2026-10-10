"""Choosing a model's settings (CLAUDE.md Section 9), on simulated rows. No real data."""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np
import optuna
import pytest

from trialpulse.cli import RefusedError
from trialpulse.config import ProjectConfig
from trialpulse.eval.ipcw import censoring_groups
from trialpulse.models import discrete_time, tuning

from .conftest import ORIGIN, simulated_rows


def _origin_data(n_train: int = 5_000, n_eval: int = 2_500, seed: int = 0) -> tuning.OriginData:
    train, time, event = simulated_rows(n_train, seed=20 + seed)
    ev, ev_time, ev_event = simulated_rows(
        n_eval, seed=30 + seed, first=ORIGIN, last=dt.date(2017, 1, 1), end=None
    )
    groups = censoring_groups(ev.features["stratum"], 200)
    return tuning.OriginData(
        ORIGIN, train, time, event, ev, ev_time, ev_event, groups, n_train, n_train
    )


def test_a_share_of_the_trials_is_drawn_with_all_their_rows() -> None:
    """Tuning subsamples trials, never rows: a trial's landmark rows stay together."""
    ids = np.array([f"NCT{i:04d}" for i in range(2_000)]).repeat(3)
    keep = tuning.sample_trials(ids, 0.25, seed=42)
    kept_trials = np.unique(ids[keep])
    assert 0.2 < len(kept_trials) / 2_000 < 0.3
    assert keep.sum() == 3 * len(kept_trials)  # every row of a drawn trial
    assert np.array_equal(keep, tuning.sample_trials(ids, 0.25, seed=42))
    assert not np.array_equal(keep, tuning.sample_trials(ids, 0.25, seed=43))
    # The draw belongs to the trial, whatever the order of the rows.
    order = np.random.default_rng(1).permutation(len(ids))
    assert np.array_equal(tuning.sample_trials(ids[order], 0.25, seed=42), keep[order])
    assert tuning.sample_trials(ids, 1.0, seed=42).all()


def test_the_search_stays_inside_its_ranges() -> None:
    study = optuna.create_study(sampler=optuna.samplers.RandomSampler(seed=1))
    for _ in range(30):
        trial = study.ask()
        for model in ("m4", "m2"):
            for name, value in tuning.suggest(trial, model).items():
                low, high, scale = tuning.space(model)[name]
                assert low <= value <= high, (name, value)
                assert isinstance(value, int) == (scale == "log-int")
    assert set(tuning.space("m4")) == set(discrete_time.BOOSTING_DEFAULTS)
    assert set(tuning.space("m3")) == set(discrete_time.LOGISTIC_DEFAULTS) == {"C"}
    for model in ("m2", "m4"):  # the default setting lies inside the range searched
        for name, value in tuning.defaults(model).items():
            low, high, _ = tuning.space(model)[name]
            assert low <= value <= high


def test_settings_are_ranked_by_the_declared_rule() -> None:
    """Section 9: the best mean AUC at 12 months; ties by the Brier score, then by the
    calibration slope closest to 1."""
    trials = [
        {"number": 0, "auc": 0.70, "brier": 0.030, "calibration_slope": 1.00},
        {"number": 1, "auc": 0.71, "brier": 0.040, "calibration_slope": 1.60},
        {"number": 2, "auc": 0.71, "brier": 0.035, "calibration_slope": 1.30},
        {"number": 3, "auc": 0.71, "brier": 0.035, "calibration_slope": 0.85},
    ]
    assert [t["number"] for t in sorted(trials, key=tuning.rank)] == [3, 2, 1, 0]


def test_the_study_starts_with_the_default_setting_and_keeps_its_budget(
    cfg: ProjectConfig,
) -> None:
    seen: list[dict[str, Any]] = []

    def scorer(cfg: Any, model: str, settings: Any, data: Any) -> dict[str, Any]:
        seen.append(dict(settings))
        # Better the closer the number of leaves is to 60: something to find.
        value = 0.7 - abs(np.log(settings["num_leaves"] / 60.0)) / 50.0
        return {"auc": value, "brier": 0.03, "calibration_slope": 1.0, "by_origin": {}}

    study = tuning.tune(cfg, "m4", [], 12, scorer)
    assert len(study["trials"]) == len(seen) == 12
    assert seen[0] == discrete_time.BOOSTING_DEFAULTS  # the first trial is the default
    assert study["default"]["settings"] == discrete_time.BOOSTING_DEFAULTS
    assert study["best"]["auc"] == max(t["auc"] for t in study["trials"])
    assert study["best"]["auc"] >= study["default"]["auc"]
    assert len({json.dumps(s, sort_keys=True) for s in seen}) == 12  # twelve different settings
    again = tuning.tune(cfg, "m4", [], 12, scorer)
    assert [t["settings"] for t in again["trials"]] == [t["settings"] for t in study["trials"]]
    for name in ("m1", "m4-design", "m0"):
        with pytest.raises(RefusedError, match="no settings of its own"):
            tuning.tune(cfg, name, [], 2, scorer)


def test_a_trial_fits_and_scores_the_model_on_every_origin(cfg: ProjectConfig) -> None:
    data = [_origin_data(seed=0), _origin_data(seed=1)]
    result = tuning.score(cfg, "m3", {"C": 0.5}, data)
    assert set(result["by_origin"]) == {ORIGIN.isoformat()}  # both made-up origins share a date
    one = tuning.score(cfg, "m3", {"C": 0.5}, data[:1])
    here = one["by_origin"][ORIGIN.isoformat()]
    assert one["auc"] == here["auc"] > 0.6
    assert 0.0 < here["brier"] < 0.2
    assert 0.5 < here["calibration_slope"] < 1.6
    # The settings reach the model: a far stronger penalty gives another model.
    assert tuning.score(cfg, "m3", {"C": 1e-4}, data[:1])["auc"] != one["auc"]
    trees = tuning.score(cfg, "m4-design", {"num_leaves": 15}, data[:1])
    assert trees["by_origin"][ORIGIN.isoformat()]["rounds"] >= 1


def test_the_command_writes_the_chosen_settings_where_the_models_read_them(
    cfg: ProjectConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tracked_runs: list[Any],
) -> None:
    data = [_origin_data()]
    monkeypatch.setattr(tuning, "prepare", lambda *args, **kwargs: data)
    monkeypatch.setattr(tuning, "load_landmark_rows", lambda path: None)
    tuned, out = tmp_path / "tuned", tmp_path / "study" / "m2.json"
    args = ["--model", "m2", "--trials", "4", "--tuned-dir", str(tuned), "--out", str(out)]
    assert tuning.main(args) == 0
    record = json.loads((tuned / "m2.json").read_text(encoding="utf-8"))
    study = json.loads(out.read_text(encoding="utf-8"))
    assert record["model"] == "m2"
    assert record["trials"] == len(study["all_trials"]) == 4
    assert record["default_settings"] == {"C": 1.0} == study["all_trials"][0]["settings"]
    assert record["auc"] == max(t["auc"] for t in study["all_trials"]) >= record["default_auc"]
    assert record["settings"] == study["all_trials"][record["chosen_trial"]]["settings"]
    assert record["trial_sample_share"] == cfg.tuning.trial_sample_share
    assert record["seed"] == cfg.seeds.default
    assert record["search_space"] == {"C": [1e-3, 1e2, "log"]}
    assert "12 months" in record["objective"]
    assert "all_trials" not in record  # the committed file holds the choice, not the study
    # A model built afterwards runs with the chosen settings.
    monkeypatch.setattr(discrete_time, "TUNED_DIR", tuned)
    assert discrete_time.build("m2", cfg).settings == record["settings"]
    assert discrete_time.build("m3", cfg).settings == discrete_time.LOGISTIC_DEFAULTS
    printed = capsys.readouterr().out
    assert "m2: 4 trials" in printed
    assert "default settings" in printed
    (logged,) = tracked_runs
    assert logged["name"] == "tune-m2"
    assert logged["tags"] == {"step": "10", "kind": "tuning", "roles": "dev"}
    assert logged["artifacts"] == [str(out), str(tuned / "m2.json")]
    assert logged["params"]["best_C"] == record["settings"]["C"]
    assert logged["metrics"]["auc_mean"] == record["auc"]
    assert study["tracking"]["run_name"] == "tune-m2"
    # The same budget and seed choose the same settings again.
    assert tuning.main([*args, "--no-track"]) == 0
    assert json.loads((tuned / "m2.json").read_text(encoding="utf-8")) == record
    assert len(tracked_runs) == 1


def test_only_the_development_origins_are_prepared(cfg: ProjectConfig) -> None:
    asked: list[tuple[dt.date, str]] = []

    def features(origin: dt.date, role: str, only: Any) -> Any:
        asked.append((origin, role))
        raise ValueError("stop here")

    def training(origin: dt.date) -> Any:
        rows, _, _ = simulated_rows(50, seed=1, last=origin, end=origin)
        return rows

    final, _, _ = simulated_rows(50, seed=2)
    with pytest.raises(ValueError, match="stop here"):
        tuning.prepare(cfg, final, training, features, 0.5)
    dev = [o.date for o in cfg.walk_forward.origins if o.role == "dev"]
    assert asked == [(dev[0], "training")]  # the first development origin, and no locked one
    assert all(o.role == "dev" for o in cfg.walk_forward.origins if o.date in dev)
