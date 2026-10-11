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
from trialpulse.dates import add_months
from trialpulse.eval.ipcw import censoring_groups
from trialpulse.eval.lock import TestLockError
from trialpulse.eval.metrics import calibration_slope_intercept, ipcw_auc, ipcw_brier
from trialpulse.eval.rows import LandmarkRows, horizon_days
from trialpulse.eval.walkforward import FeatureRows, evaluation_rows
from trialpulse.models import discrete_time, tuning

from .conftest import ORIGIN, simulated_rows


def _origin_data(
    n_train: int = 5_000,
    n_eval: int = 2_500,
    seed: int = 0,
    origin: dt.date = ORIGIN,
    lapse_share: float = 0.0,
) -> tuning.OriginData:
    """One origin: training rows as of it, and the registrations of the following year with
    every outcome known, except that `lapse_share` of the trials of class OTHER are censored
    early (so that censoring depends on the sponsor class, as it does in the registry)."""
    train, time, event = simulated_rows(n_train, seed=20 + seed, last=origin, end=origin)
    ev, ev_time, ev_event = simulated_rows(
        n_eval, seed=30 + seed, first=origin, last=origin.replace(year=origin.year + 1),
        end=None, lapse_share=lapse_share,
    )  # fmt: skip
    groups = censoring_groups(ev.features["stratum"], 200)
    return tuning.OriginData(
        origin, train, time, event, ev, ev_time, ev_event, groups, n_train, n_train
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
        # The lower AUC loses, whatever else it has.
        {"number": 0, "auc": 0.70, "brier": 0.030, "calibration_slope": 1.00},
        # Same AUC as 2 and 3, a worse Brier score: it loses though its slope is exactly 1.
        {"number": 1, "auc": 0.71, "brier": 0.040, "calibration_slope": 1.00},
        # Same AUC and Brier score as 3: 0.80 is further from 1 than 1.05, though smaller.
        {"number": 2, "auc": 0.71, "brier": 0.035, "calibration_slope": 0.80},
        {"number": 3, "auc": 0.71, "brier": 0.035, "calibration_slope": 1.05},
    ]
    assert [t["number"] for t in sorted(trials, key=tuning.rank)] == [3, 2, 1, 0]


def test_the_study_starts_with_the_default_setting_and_keeps_its_budget(
    cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []
    created: list[dict[str, Any]] = []
    real = optuna.create_study

    studies: list[Any] = []

    def recording(**kwargs: Any) -> Any:
        created.append(kwargs)
        studies.append(real(**kwargs))
        return studies[-1]

    monkeypatch.setattr(tuning.optuna, "create_study", recording)

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
    # The search looks for a higher AUC, with the project's seed.
    assert created[0]["direction"] == "maximize"
    assert isinstance(created[0]["sampler"], optuna.samplers.TPESampler)
    # What the search is told about each trial is its AUC, and nothing else.
    assert [trial.value for trial in studies[0].trials] == [t["auc"] for t in study["trials"]]
    again = tuning.tune(cfg, "m4", [], 12, scorer)
    assert [t["settings"] for t in again["trials"]] == [t["settings"] for t in study["trials"]]
    other_seed = cfg.model_copy(update={"seeds": cfg.seeds.model_copy(update={"default": 7})})
    other = tuning.tune(other_seed, "m4", [], 12, scorer)
    assert other["trials"][0]["settings"] == study["trials"][0]["settings"]  # the default
    assert [t["settings"] for t in other["trials"]] != [t["settings"] for t in study["trials"]]
    for name in ("m1", "m4-design", "m0"):
        with pytest.raises(RefusedError, match="no settings of its own"):
            tuning.tune(cfg, name, [], 2, scorer)


def test_the_chosen_trial_breaks_ties_by_the_declared_rule(cfg: ProjectConfig) -> None:
    """Every trial has the same AUC here. The lower Brier score wins; with equal Brier scores
    too, the slope closest to 1. The default setting comes first and is not the best."""

    def by_brier(cfg: Any, model: str, settings: Any, data: Any) -> dict[str, Any]:
        off = abs(float(np.log(settings["num_leaves"] / 60.0)))
        return {"auc": 0.7, "brier": 0.03 + off / 1000.0, "calibration_slope": 1.0, "by_origin": {}}

    study = tuning.tune(cfg, "m4", [], 12, by_brier)
    assert study["best"]["brier"] == min(t["brier"] for t in study["trials"])
    assert study["best"]["brier"] < study["default"]["brier"]
    assert study["best"]["number"] != 0

    def by_slope(cfg: Any, model: str, settings: Any, data: Any) -> dict[str, Any]:
        slope = 1.0 + float(np.log(settings["num_leaves"] / 60.0))
        return {"auc": 0.7, "brier": 0.03, "calibration_slope": slope, "by_origin": {}}

    study = tuning.tune(cfg, "m4", [], 12, by_slope)
    distances = [abs(t["calibration_slope"] - 1.0) for t in study["trials"]]
    assert abs(study["best"]["calibration_slope"] - 1.0) == min(distances) < distances[0]
    # Some slopes are below 1 and closer to it than the best above 1 is not required: both
    # sides occur, so "closest" is not "smallest".
    assert min(t["calibration_slope"] for t in study["trials"]) < study["best"]["calibration_slope"]


def test_the_objective_is_the_mean_over_the_origins_of_the_auc_at_12_months(
    cfg: ProjectConfig,
) -> None:
    """What a trial is scored on (ADR 0025, decision 4): at each origin the IPCW AUC at 12
    months of the model fitted on that origin's training rows, with the censoring groups of
    the harness; then the mean over the origins. Recomputed here without the tuning code,
    on rows that can tell it from its neighbors."""
    later = dt.date(2017, 1, 1)
    data = [
        _origin_data(seed=0, lapse_share=0.6),
        # Fewer evaluation rows than the first origin: a mean over the origins is not a
        # mean over their rows.
        _origin_data(seed=1, origin=later, lapse_share=0.6, n_eval=1_500),
    ]
    settings = {"C": 0.5}
    result = tuning.score(cfg, "m3", settings, data)
    assert list(result["by_origin"]) == [ORIGIN.isoformat(), later.isoformat()]
    auc, brier, slope = [], [], []
    for d in data:
        model = discrete_time.build("m3", cfg, settings).fit_rows(
            d.train, d.train_time, d.train_event, d.origin
        )
        twelve = model.predict_months(d.evaluation, 12).copy()
        h = horizon_days(d.evaluation.landmark_date, 12)
        auc.append(ipcw_auc(d.time, d.event, twelve, h, d.groups))
        brier.append(ipcw_brier(d.time, d.event, twelve, h, d.groups))
        slope.append(calibration_slope_intercept(d.time, d.event, twelve, h, d.groups)[0])
        here = result["by_origin"][d.origin.isoformat()]
        assert here["auc"] == pytest.approx(auc[-1], abs=1e-12)
        assert here["brier"] == pytest.approx(brier[-1], abs=1e-12)
        assert here["calibration_slope"] == pytest.approx(slope[-1], abs=1e-9)
        # Not the 24-month horizon, and not one censoring curve for all rows.
        far = model.predict_months(d.evaluation, 24)
        h24 = horizon_days(d.evaluation.landmark_date, 24)
        assert abs(auc[-1] - ipcw_auc(d.time, d.event, far, h24, d.groups)) > 1e-4
        assert abs(auc[-1] - ipcw_auc(d.time, d.event, twelve, h)) > 1e-6
    assert abs(auc[0] - auc[1]) > 1e-3  # so the mean is neither origin's own number
    assert result["auc"] == pytest.approx(float(np.mean(auc)), abs=1e-12)
    assert result["brier"] == pytest.approx(float(np.mean(brier)), abs=1e-12)
    assert result["calibration_slope"] == pytest.approx(float(np.mean(slope)), abs=1e-9)


def test_a_trial_fits_and_scores_the_model_with_its_settings(cfg: ProjectConfig) -> None:
    data = [_origin_data(seed=0), _origin_data(seed=1)]
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


def _two_landmarks() -> LandmarkRows:
    """Made-up trials first posted from 2010 to 2018, each with a landmark row at
    registration and, if it is still open six months later, a second one: a trial is more
    than one row, as in the cohort."""
    base, _, _ = simulated_rows(
        6_000, seed=40, first=dt.date(2010, 1, 1), last=dt.date(2018, 1, 1), end=None
    )
    later = add_months(base.landmark_date, 6)
    still = base.event_date > later
    n = len(base)

    def stack(first: Any, second: Any) -> Any:
        return np.concatenate([first, second[still]])

    features = {name: stack(values, values) for name, values in base.features.items()}
    features["landmark_index"] = stack(np.zeros(n), np.ones(n))
    features["months_since_t0"] = stack(np.zeros(n), np.full(n, 6.0))
    return LandmarkRows(
        stack(base.trial_id, base.trial_id),
        stack(np.zeros(n, dtype=np.int64), np.ones(n, dtype=np.int64)),
        stack(base.landmark_date, later),
        stack(base.event, base.event),
        stack(base.event_date, base.event_date),
        features,
    )


def test_only_the_development_origins_are_prepared_with_a_share_of_their_trials(
    cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loaders of the harness on made-up trials: the tuning asks them for the
    development origins and for nothing else, keeps a seeded share of each origin's training
    trials with all their rows, and every evaluation row with the harness's censoring groups."""
    rows = _two_landmarks()
    bare = LandmarkRows(
        rows.trial_id, rows.landmark_index, rows.landmark_date, rows.event, rows.event_date,
        {"stratum": rows.features["stratum"]},
    )  # fmt: skip
    asked: list[tuple[str, dt.date | None]] = []

    def as_of(origin: dt.date) -> LandmarkRows:
        day = np.datetime64(origin, "D")
        before = bare.subset(bare.landmark_date < day)
        late = before.event_date >= day
        return LandmarkRows(
            before.trial_id, before.landmark_index, before.landmark_date,
            np.where(late, 0, before.event).astype(np.int64),
            np.where(late, day, before.event_date).astype("datetime64[D]"), before.features,
        )  # fmt: skip

    def final() -> LandmarkRows:
        asked.append(("final rows", None))
        return bare

    def training(origin: dt.date) -> LandmarkRows:
        asked.append(("training rows", origin))
        return as_of(origin)

    def features(origin: dt.date, role: str, only: Any) -> FeatureRows:
        asked.append((f"{role} features", origin))
        day = np.datetime64(origin, "D")
        if role == "training":
            chosen = rows.subset(rows.landmark_date < day)
        else:
            chosen, _, _ = evaluation_rows(rows, day, cfg.walk_forward.eval_window_months)
        return FeatureRows(chosen.trial_id, chosen.landmark_index, dict(chosen.features))

    data = tuning.prepare(cfg, final, training, features, 0.5)
    dev = [o.date for o in cfg.walk_forward.origins if o.role == "dev"]
    assert dev == [dt.date(2016, 1, 1), dt.date(2017, 1, 1)]
    assert [d.origin for d in data] == dev
    assert asked == [("final rows", None)] + [
        (what, origin)
        for origin in dev
        for what in ("training rows", "training features", "evaluation features")
    ]
    for d in data:
        day = np.datetime64(d.origin, "D")
        every = as_of(d.origin)
        keep = tuning.sample_trials(every.trial_id, 0.5, cfg.seeds.default)
        # Trials are counted and drawn, not rows, and a drawn trial keeps all its rows.
        assert d.training_trials == len(set(every.trial_id.tolist())) < len(every)
        assert d.sampled_trials == len(set(every.trial_id[keep].tolist())) < len(d.train)
        assert 0.4 * d.training_trials < d.sampled_trials < 0.6 * d.training_trials
        assert d.train.trial_id.tolist() == every.trial_id[keep].tolist()
        assert d.train.landmark_index.tolist() == every.landmark_index[keep].tolist()
        drawn = set(d.train.trial_id.tolist())
        assert sum(1 for trial in every.trial_id.tolist() if trial in drawn) == len(d.train)
        # The follow-up and the events are those of the drawn rows.
        follow_up = (every.event_date - every.landmark_date).astype(np.int64).astype(float)
        assert d.train_time.tolist() == follow_up[keep].tolist()
        assert d.train_event.tolist() == every.event[keep].tolist()
        assert len(set(d.train_time.tolist())) > 100
        year_end = np.datetime64(d.origin.replace(year=d.origin.year + 1), "D")
        window = (bare.landmark_date >= day) & (bare.landmark_date < year_end)
        assert d.evaluation.trial_id.tolist() == bare.trial_id[window].tolist()  # every row
        assert len(d.time) == len(d.event) == len(d.evaluation)
        groups = censoring_groups(
            bare.features["stratum"][window], cfg.evaluation.censoring_min_rows
        )
        assert d.groups.tolist() == groups.tolist()
        assert len(set(d.groups.tolist())) >= 2  # a curve per sponsor class, as in the harness
        assert "enrollment_count" in d.train.features
        assert "enrollment_count" in d.evaluation.features
    everything = tuning.prepare(cfg, final, training, features, 1.0)
    assert all(d.sampled_trials == d.training_trials for d in everything)
    assert all(len(d.train) == len(as_of(d.origin)) for d in everything)
    # The origins read are the ones that passed the lock, whatever the configuration lists.
    second = [o for o in cfg.walk_forward.origins if o.date == dev[1]]
    monkeypatch.setattr(tuning, "select_origins", lambda cfg, spec: second)
    asked.clear()
    assert [d.origin for d in tuning.prepare(cfg, final, training, features, 0.5)] == [dev[1]]
    assert {origin for _, origin in asked} == {None, dev[1]}


def test_a_locked_origin_never_reaches_the_tuning(
    cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tuning has no unlock flag. Its origins pass the test lock like the harness's, so
    if they ever held a locked origin it would be refused before a row is read: not the
    final landmark rows either, which hold the evaluation rows of every origin."""
    asked: list[Any] = []

    def loader(*args: Any) -> Any:
        asked.append(args)
        raise AssertionError("no data may be read")

    monkeypatch.setattr(tuning, "select_origins", lambda cfg, spec: list(cfg.walk_forward.origins))
    with pytest.raises(TestLockError, match="--unlock-test"):
        tuning.prepare(cfg, loader, loader, loader, 0.5)
    assert asked == []
    # What the lock is asked: the roles of every origin, and never "unlocked".
    handed: list[tuple[set[str], bool]] = []

    def recording(roles: set[str], repo: Any, unlock_flag: bool) -> None:
        handed.append((set(roles), unlock_flag))
        raise TestLockError("refused by the test")

    monkeypatch.setattr(tuning, "require_unlock", recording)
    with pytest.raises(TestLockError, match="refused by the test"):
        tuning.prepare(cfg, loader, loader, loader, 0.5)
    assert handed == [({"dev", "test", "stress"}, False)]
    assert asked == []
