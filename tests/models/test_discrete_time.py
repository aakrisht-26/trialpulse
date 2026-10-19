"""The discrete-time models M2, M3 and M4 (CLAUDE.md Sections 6 and 9), on hand-built and
simulated rows. One slow test reads the real cohort where it is built."""

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pytest

from trialpulse.cohort.person_period import expand
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import ProjectConfig
from trialpulse.dates import add_months
from trialpulse.eval.metrics import ipcw_auc
from trialpulse.eval.rows import (
    TRAINING_DIR,
    LandmarkRows,
    horizon_days,
    load_landmark_rows,
    training_path,
)
from trialpulse.eval.walkforward import training_rows
from trialpulse.features import registry
from trialpulse.models import discrete_time as dtm
from trialpulse.models.aalen_johansen import aalen_johansen
from trialpulse.models.discrete_time import (
    DiscreteTimeBoosting,
    DiscreteTimeLogistic,
    LinearDesign,
    build,
    cumulative_incidence,
    feature_names,
    person_period,
    tree_levels,
    tree_matrix,
)

from .conftest import ORIGIN, simulated_rows

D = np.datetime64


def _rows(
    landmarks: list[str], events: list[int], ends: list[str]
) -> tuple[LandmarkRows, Any, Any]:
    landmark = np.array(landmarks, dtype="datetime64[D]")
    end = np.array(ends, dtype="datetime64[D]")
    event = np.array(events, dtype=np.int64)
    rows = LandmarkRows(
        np.array([f"NCT{i:08d}" for i in range(len(event))]),
        np.zeros(len(event), dtype=np.int64),
        landmark,
        event,
        end,
        {"stratum": np.array(["OTHER"] * len(event))},
    )
    return rows, (end - landmark).astype(np.int64).astype(np.float64), event


# The cumulative incidence ------------------------------------------------------------------------


def test_the_cumulative_incidence_follows_from_the_two_hazards() -> None:
    """One trial, two intervals. It stops in the first with probability 0.1 and completes
    with 0.3, so it enters the second with 0.6 and stops there with 0.6 * 0.2."""
    stop = np.array([[0.1, 0.2, 0.5]])
    complete = np.array([[0.3, 0.1, 0.5]])
    cif = cumulative_incidence(stop, complete)
    assert cif == pytest.approx(np.array([[0.1, 0.1 + 0.6 * 0.2, 0.22 + 0.6 * 0.7 * 0.5]]))
    # Completion competes: with a higher hazard of completion, fewer trials are left to stop.
    faster = cumulative_incidence(stop, complete + 0.2)
    assert (faster[:, 1:] < cif[:, 1:]).all()
    assert faster[0, 0] == cif[0, 0]
    # A trial that surely ends in the first interval adds nothing later.
    ended = cumulative_incidence(np.array([[0.4, 0.9]]), np.array([[0.6, 0.1]]))
    assert ended == pytest.approx(np.array([[0.4, 0.4]]))
    assert cumulative_incidence(np.zeros((3, 4)), np.full((3, 4), 0.2)).max() == 0.0


def test_a_horizon_is_a_whole_number_of_intervals(cfg: ProjectConfig) -> None:
    rows, time, event = simulated_rows(600, seed=2)
    model = build("m2", cfg).fit_rows(rows, time, event, ORIGIN)
    six, twelve = model.predict_months(rows, 6), model.predict_months(rows, 12)
    assert (twelve >= six).all()
    assert (model.predict_months(rows, 24) >= twelve).all()
    for months in (0, 9, 30):
        with pytest.raises(ValueError, match="whole number of intervals"):
            model.predict_months(rows, months)
    stop, complete = model.hazards(rows)
    assert stop.shape == complete.shape == (len(rows), cfg.discrete_time.n_intervals)
    assert ((stop > 0) & (complete > 0) & (stop + complete < 1)).all()
    assert twelve == pytest.approx(cumulative_incidence(stop, complete)[:, 1])


def test_a_horizon_costs_only_the_intervals_it_needs(
    cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Twelve months are two intervals: scoring them does not score the other two. A second
    horizon on the same rows adds the missing intervals and gives what a model asked for
    all four at once gives."""
    rows, time, event = simulated_rows(900, seed=2)
    model = build("m2", cfg).fit_rows(rows, time, event, ORIGIN)
    asked: list[int] = []
    real = model._probabilities

    def counting(landmark: Any, interval: int) -> Any:
        asked.append(interval)
        return real(landmark, interval)

    monkeypatch.setattr(model, "_probabilities", counting)
    # A clock that moves one second per reading: each scoring pass takes "one second".
    ticks = iter(range(10_000))
    monkeypatch.setattr(dtm, "clock", SimpleNamespace(perf_counter=lambda: float(next(ticks))))
    twelve = model.predict_months(rows, 12)
    assert asked == [1, 2]
    assert model.summary["predict_seconds"] == 1.0
    assert model.predict_months(rows, 6) == pytest.approx(model.hazards(rows, 1)[0][:, 0])
    assert asked == [1, 2]  # already known
    twenty_four = model.predict_months(rows, 24)
    assert asked == [1, 2, 3, 4]
    assert model.summary["predicted_rows"] == len(rows)
    # The scoring time of the rows is that of both passes, not of the last one.
    assert model.summary["predict_seconds"] == 2.0
    fresh = build("m2", cfg).fit_rows(rows, time, event, ORIGIN)
    stop, complete = fresh.hazards(rows)
    both = cumulative_incidence(stop, complete)
    assert np.array_equal(twelve, both[:, 1])
    assert np.array_equal(twenty_four, both[:, 3])
    # Other rows are scored afresh.
    other = rows.subset(np.arange(len(rows)) < 10)
    model.predict_months(other, 12)
    assert asked == [1, 2, 3, 4, 1, 2]
    assert model.summary["predicted_rows"] == 10
    assert model.summary["predict_seconds"] == 1.0  # other rows: counted from nothing


# Person-period rows ------------------------------------------------------------------------------


def test_the_intervals_of_a_training_row_end_within_the_observation_window(
    cfg: ProjectConfig,
) -> None:
    """Origin 2016-01-01 (ADR 0015). A: stopped in its second interval. B: open on the
    origin, two full intervals behind it (the second ends on the origin itself). C: its
    record lapsed in the first interval, so no interval was followed to its end. D: posted
    three months before the origin, no interval has ended. E: completed in its first. F:
    stopped on the last day of its first interval, which belongs to that interval, as an
    event on exactly L + 12 months counts within 12 months. G: censored on that day, so it
    was followed through the first interval and no further."""
    rows, time, event = _rows(
        ["2015-01-01", "2015-01-01", "2015-01-01", "2015-10-01", "2014-01-01", "2015-01-01",
         "2015-01-01"],
        [1, 0, 0, 0, 2, 1, 0],
        ["2015-09-01", "2016-01-01", "2015-05-01", "2016-01-01", "2014-03-01", "2015-07-01",
         "2015-07-01"],
    )  # fmt: skip
    periods = person_period(rows, time, event, dt.date(2016, 1, 1), cfg)
    got = list(zip(periods.row.tolist(), periods.interval.tolist(), periods.outcome.tolist(),
                   strict=True))  # fmt: skip
    assert got == [
        (0, 1, 0), (0, 2, 1), (1, 1, 0), (1, 2, 0), (4, 1, 2), (5, 1, 1), (6, 1, 0),
    ]  # fmt: skip
    assert periods.interval_end.astype(str).tolist() == [
        "2015-07-01", "2016-01-01", "2015-07-01", "2016-01-01", "2014-07-01", "2015-07-01",
        "2015-07-01",
    ]  # fmt: skip
    # It is the expansion of the cohort build, row for row.
    rules = CohortRules.from_config(cfg, cutoff=dt.date(2016, 1, 1))
    built = expand(
        {"trial_id": rows.trial_id, "landmark_index": rows.landmark_index,
         "landmark_date": rows.landmark_date, "event": rows.event, "event_date": rows.event_date,
         "stratum": rows.features["stratum"]},
        rules,
    )  # fmt: skip
    assert built["trial_id"].tolist() == rows.trial_id[periods.row].tolist()
    assert built["outcome"].tolist() == periods.outcome.tolist()
    assert built["interval"].tolist() == periods.interval.tolist()


@pytest.mark.slow  # reads the real training rows of two origins, where the cohort is built
@pytest.mark.parametrize("origin", [dt.date(2016, 1, 1), dt.date(2017, 1, 1)])
def test_on_the_real_cohort_the_models_train_on_the_rows_the_build_stored(
    cfg: ProjectConfig, origin: dt.date
) -> None:
    """A model does not read the person-period file of the cohort build: it expands the
    landmark rows itself, from follow-up days and with row positions in place of trial ids.
    On the real training rows of a development origin the two must be the same rows."""
    landmarks = training_path(TRAINING_DIR, origin)
    stored = landmarks.with_name("person_period.parquet")
    if not (landmarks.is_file() and stored.is_file()):
        pytest.skip("the cohort is not built here")
    rows, time, event = training_rows(load_landmark_rows(landmarks), np.datetime64(origin))
    periods = person_period(rows, time, event, origin, cfg)
    key = ["trial_id", "landmark_index", "interval"]
    mine = pd.DataFrame(
        {"trial_id": rows.trial_id[periods.row],
         "landmark_index": rows.landmark_index[periods.row],
         "interval": periods.interval, "outcome": periods.outcome,
         "interval_end": periods.interval_end.astype("datetime64[s]")}
    ).sort_values(key, ignore_index=True)  # fmt: skip
    with duckdb.connect() as con:
        built = con.execute(
            f"""SELECT trial_id, landmark_index, "interval", outcome, interval_end
            FROM read_parquet('{stored.as_posix()}')"""
        ).df().sort_values(key, ignore_index=True)  # fmt: skip
    assert len(mine) == len(built) > 1_000_000
    for column in (*key, "outcome"):
        assert mine[column].tolist() == built[column].tolist(), column
    assert (
        mine["interval_end"].to_numpy().astype("datetime64[D]").tolist()
        == built["interval_end"].to_numpy().astype("datetime64[D]").tolist()
    )


def test_a_model_cannot_be_fitted_without_a_finished_interval(cfg: ProjectConfig) -> None:
    rows, time, event = _rows(["2015-10-01"], [0], ["2016-01-01"])
    with pytest.raises(ValueError, match="no interval of the training rows ended"):
        build("m2", cfg).fit_rows(rows, time, event, dt.date(2016, 1, 1))


# The required test of Section 9 -------------------------------------------------------------------


def test_without_covariates_the_discrete_cif_matches_aalen_johansen(
    cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Section 9: with no covariates, the discrete-time CIF matches Aalen-Johansen at the
    interval boundaries. The model here knows the interval and nothing else, and is not
    regularized. Staggered entry and one end of observation, as on a walk-forward origin."""
    monkeypatch.setattr(dtm, "feature_names", lambda families: ())
    rows, time, event = simulated_rows(40_000, seed=7)
    model = DiscreteTimeLogistic(cfg, name="m2", settings={"C": 1e6})
    model.fit_rows(rows, time, event, ORIGIN)
    assert model.summary["features"] == 0
    assert model.summary["columns"] == cfg.discrete_time.n_intervals
    one = rows.subset(np.arange(len(rows)) < 1)
    curve = aalen_johansen(time, event)
    for months in (6, 12, 18, 24):
        day = float(horizon_days(np.array(["2014-01-01"], dtype="datetime64[D]"), months)[0])
        discrete = float(model.predict_months(one, months)[0])
        assert discrete == pytest.approx(float(curve.at(np.array([day]))[0]), abs=0.004), months
    # The hazards are the plain shares of each interval's rows: nothing else is fitted.
    periods = person_period(rows, time, event, ORIGIN, cfg)
    stop, complete = model.hazards(one)
    for j in range(1, cfg.discrete_time.n_intervals + 1):
        mine = periods.outcome[periods.interval == j]
        assert stop[0, j - 1] == pytest.approx((mine == 1).mean(), abs=1e-3)
        assert complete[0, j - 1] == pytest.approx((mine == 2).mean(), abs=1e-3)


# The ladder --------------------------------------------------------------------------------------


def test_each_rung_of_the_ladder_holds_the_one_below() -> None:
    """ADRs 0024 and 0026: M0 knows the sponsor class; M2 adds the design family, M3 the
    amendment signals, M4 the sponsor record and competition, and no text. The ablation
    rungs add the same families to M4's learner, and `m4-text` adds the text families."""
    m2, m3, m4 = (set(feature_names(dtm.FAMILY_SETS[m])) for m in ("m2", "m3", "m4"))
    assert {"sponsor_class", "landmark_index", "months_since_t0"} < m2 < m3 < m4
    text = {f.name for f in registry.FEATURES if f.main and f.family == registry.TEXT}
    assert feature_names(dtm.FAMILY_SETS["m4-text"]) == registry.model_columns()
    assert set(registry.model_columns()) - m4 == text
    assert len(text) == 69
    assert not m4 & text
    design = {f.name for f in registry.FEATURES if f.main and f.family == registry.DESIGN}
    amendments = {f.name for f in registry.FEATURES if f.main and f.family == registry.AMENDMENTS}
    assert m2 == design | {"sponsor_class", "landmark_index", "months_since_t0"}
    assert m3 - m2 == amendments
    assert "status" in m3 - m2  # the recruitment status is an amendment signal
    assert "sponsor_stop_rate" in m4 - m3
    assert "open_interventional_trials" in m4 - m3
    assert "eligibility_svd_01" not in m4
    rungs = [
        set(feature_names(dtm.FAMILY_SETS[m])) for m in ("m4-design", "m4-amendments", "m4-text")
    ]
    assert rungs[0] == m2
    assert rungs[1] == m3
    assert rungs[0] < rungs[1] < m4 < rungs[2]
    assert rungs[2] - m4 == text
    assert len(m4) == 54


def test_which_learner_a_name_gets(cfg: ProjectConfig) -> None:
    assert isinstance(build("m2", cfg), DiscreteTimeLogistic)
    assert isinstance(build("m3", cfg), DiscreteTimeLogistic)
    for name in ("m4", "m4-design", "m4-amendments", "m4-text"):
        model = build(name, cfg)
        assert isinstance(model, DiscreteTimeBoosting)
        assert model.landmark_indices is None  # every landmark index
        assert model.feature_matrix
    with pytest.raises(ValueError, match="unknown discrete-time model"):
        build("m5", cfg)


@pytest.fixture(scope="module")
def fitted(cfg: ProjectConfig) -> dict[str, dtm.DiscreteTimeModel]:
    rows, time, event = simulated_rows(12_000, seed=1, lapse_share=0.3)
    return {
        name: build(name, cfg).fit_rows(rows, time, event, ORIGIN) for name in ("m2", "m3", "m4")
    }


def test_the_models_rank_and_calibrate_on_later_trials(
    cfg: ProjectConfig, fitted: dict[str, dtm.DiscreteTimeModel]
) -> None:
    """Scored on trials posted after the origin, with every outcome known. In the simulation
    the recruitment status drives the early stops, and it is an amendment signal: M2 does
    not see it and M3 does. The mean predicted risk is the observed share at each horizon,
    with completion as a competing event."""
    rows, time, event = simulated_rows(6_000, seed=3, first=ORIGIN, last=dt.date(2017, 1, 1),
                                       end=None)  # fmt: skip
    auc: dict[str, float] = {}
    for name, model in fitted.items():
        for months in cfg.horizons_months:
            h = horizon_days(rows.landmark_date, months)
            score = model.predict_months(rows, months)
            assert ((score > 0) & (score < 1)).all()
            observed = float(((event == 1) & (time <= h)).mean())
            assert score.mean() == pytest.approx(observed, abs=0.02), (name, months)
            auc[f"{name} {months}"] = ipcw_auc(time, event, score, h)
    assert auc["m2 12"] > 0.55  # the enrollment target and the sponsor class
    assert auc["m3 12"] > auc["m2 12"] + 0.04
    assert auc["m4 12"] > auc["m2 12"] + 0.04
    assert auc["m4 24"] > 0.63
    summary = fitted["m4"].summary
    assert summary["person_period_rows"] == sum(summary["outcomes"].values())
    assert summary["features"] == 123
    assert summary["model_bytes"] > 10_000
    assert summary["fit_seconds"] >= 0
    assert summary["predicted_rows"] == len(rows)
    assert {"status", "enrollment_count"} & {
        f["feature"] for f in summary["top_features_by_gain"][:6]
    }
    assert fitted["m3"].summary["converged"]
    assert fitted["m3"].summary["columns"] > fitted["m2"].summary["columns"]


def test_two_fits_give_the_same_predictions(
    cfg: ProjectConfig, fitted: dict[str, dtm.DiscreteTimeModel]
) -> None:
    rows, time, event = simulated_rows(12_000, seed=1, lapse_share=0.3)
    probe, _, _ = simulated_rows(300, seed=6)
    for name in ("m2", "m4"):
        again = build(name, cfg).fit_rows(rows, time, event, ORIGIN)
        assert np.array_equal(
            again.predict_months(probe, 24), fitted[name].predict_months(probe, 24)
        )


# Scoring -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["m2", "m4"])
def test_scoring_uses_what_was_fitted_on_the_training_rows(cfg: ProjectConfig, name: str) -> None:
    """Section 6: the centers, scales, fill values and category levels are those of the
    training rows. So a row scored alone gets the prediction it gets among other rows, also
    a row with a missing number and a sponsor class the training rows never showed. And the
    hazards a model keeps belong to the rows object they were computed for and to the fit
    that computed them."""
    rows, time, event = simulated_rows(4_000, seed=11)
    model = build(name, cfg).fit_rows(rows, time, event, ORIGIN)
    later, _, _ = simulated_rows(400, seed=12, first=ORIGIN, last=dt.date(2017, 1, 1), end=None)
    features = {key: values.copy() for key, values in later.features.items()}
    features["sponsor_class"][:3] = "NETWORK"  # first seen after training
    features["enrollment_count"][1:5] = np.nan
    features["masking"][2] = None
    later = later.with_features(features)
    together = model.predict_months(later, 24).copy()
    position = np.arange(len(later))
    for i in range(8):
        alone = model.predict_months(later.subset(position == i), 24)
        # (the linear models multiply in single precision, hence the tolerance)
        assert alone[0] == pytest.approx(together[i], rel=1e-6), i
    assert len(set(np.round(together[:8], 5))) == 8  # eight rows, eight predictions
    # Two sets of rows of the same size are two sets of rows.
    first, second = later.subset(position < 200), later.subset(position >= 200)
    assert model.predict_months(first, 12) == pytest.approx(model.predict_months(later, 12)[:200])
    assert model.predict_months(second, 12) == pytest.approx(model.predict_months(later, 12)[200:])
    # A second fit of the same object forgets the hazards of the first.
    before = model.predict_months(later, 12).copy()
    others = simulated_rows(4_000, seed=13)
    model.fit_rows(*others, ORIGIN)
    fresh = build(name, cfg).fit_rows(*others, ORIGIN)
    after = model.predict_months(later, 12)
    assert np.array_equal(after, fresh.predict_months(later, 12))
    assert not np.array_equal(after, before)


def test_the_trees_are_asked_about_each_interval_by_its_number(cfg: ProjectConfig) -> None:
    """In these rows no trial stops early in its first six months (those stops are turned
    into completions), so the hazard depends on the interval and the trees must be asked
    with the right one."""
    rows, time, event = simulated_rows(9_000, seed=15)
    event = np.where((event == 1) & (time <= 185), 2, event).astype(np.int64)
    model = build("m4-design", cfg).fit_rows(rows, time, event, ORIGIN)
    assert isinstance(model, DiscreteTimeBoosting)
    assert model.booster is not None
    probe, _, _ = simulated_rows(300, seed=16)
    stop, complete = model.hazards(probe)
    landmark = tree_matrix(probe.features, model.features, model.levels)
    for j in range(1, cfg.discrete_time.n_intervals + 1):
        asked = np.column_stack([landmark, np.full(len(probe), float(j), dtype=np.float32)])
        p = np.asarray(model.booster.predict(asked))
        assert np.array_equal(stop[:, j - 1], p[:, 1]), j
        assert np.array_equal(complete[:, j - 1], p[:, 2]), j
    assert np.median(stop[:, 0]) < 0.3 * np.median(stop[:, 1])  # the trees learned it
    assert np.median(stop[:, 0]) < 0.3 * np.median(stop[:, 3])


def test_an_outcome_the_training_rows_never_show_gets_probability_zero(
    cfg: ProjectConfig,
) -> None:
    """A fit on rows where no trial stops early has two classes, continue and completion.
    Their probabilities must go to their own columns, and an early stop gets none."""
    rows, time, event = simulated_rows(1_500, seed=14)
    no_stop = np.where(event == 1, 2, event).astype(np.int64)
    model = build("m2", cfg).fit_rows(rows, time, no_stop, ORIGIN)
    stop, complete = model.hazards(rows)
    assert (stop == 0.0).all()
    assert ((complete > 0.0) & (complete < 1.0)).all()
    assert (model.predict_months(rows, 24) == 0.0).all()
    assert model.summary["outcomes"]["early stop"] == 0


# What the learners are handed --------------------------------------------------------------------


def test_the_trees_of_m4_are_chosen_on_the_last_training_year(
    cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two fits. The first is fitted on the intervals that ended more than 12 months before
    the origin and stopped early on those that ended in the last 12 months; the second, the
    model, runs the best number of rounds on every person-period row. Each row carries the
    features of its landmark row and the number of its interval, and no weight."""
    rows, time, event = simulated_rows(8_000, seed=4)
    calls: list[dict[str, Any]] = []
    real = dtm.lgb.train

    def recording(params: Any, train_set: Any, **kwargs: Any) -> Any:
        booster = real(params, train_set, **kwargs)
        valid = kwargs.get("valid_sets")
        calls.append(
            {
                "x": np.asarray(train_set.data),
                "y": np.asarray(train_set.get_label(), dtype=np.int64),
                "w": train_set.get_weight(),
                "valid_x": None if not valid else np.asarray(valid[0].data),
                "valid_y": None if not valid else np.asarray(valid[0].get_label(), dtype=np.int64),
                "rounds": kwargs["num_boost_round"],
                "best": booster.best_iteration,
                "params": dict(params),
                "patience": [c.stopping_rounds for c in kwargs.get("callbacks") or []],
            }
        )
        return booster

    monkeypatch.setattr(dtm.lgb, "train", recording)
    model = build("m4", cfg).fit_rows(rows, time, event, ORIGIN)
    assert isinstance(model, DiscreteTimeBoosting)
    choosing, final = calls
    periods = person_period(rows, time, event, ORIGIN, cfg)
    landmark = tree_matrix(rows.features, model.features, model.levels)
    x = np.column_stack([landmark[periods.row], periods.interval])
    year_start = add_months(np.array([D(ORIGIN, "D")]), -12)[0]
    valid = periods.interval_end > year_start
    assert 500 < valid.sum() < len(periods) - 500
    assert np.array_equal(final["x"], x, equal_nan=True)
    assert np.array_equal(final["y"], periods.outcome)
    assert np.array_equal(choosing["x"], x[~valid], equal_nan=True)
    assert np.array_equal(choosing["y"], periods.outcome[~valid])
    assert np.array_equal(choosing["valid_x"], x[valid], equal_nan=True)
    assert np.array_equal(choosing["valid_y"], periods.outcome[valid])
    assert final["valid_y"] is None
    assert final["w"] is None  # person-period rows carry no weight
    assert choosing["w"] is None
    assert final["x"][:, -1].tolist() == periods.interval.astype(float).tolist()
    assert model.columns[-1] == "interval"
    # The interval ends of the validation rows lie in the last 12 months, the origin included.
    assert periods.interval_end[valid].min() > year_start
    assert periods.interval_end[valid].max() == D(ORIGIN, "D")
    assert periods.interval_end[~valid].max() <= year_start
    info = model.summary
    assert final["rounds"] == choosing["best"] == info["rounds"]
    assert info["rounds_chosen_by"] == "last training year"
    assert info["validation_rows"] == int(valid.sum())
    assert model.booster is not None
    assert model.booster.num_trees() == 3 * choosing["best"]  # one tree per class and round
    expected = {**dtm.BOOSTING_FIXED, **dtm.BOOSTING_DEFAULTS, "seed": cfg.seeds.default}
    assert choosing["params"] == final["params"] == expected
    assert (expected["objective"], expected["num_class"]) == ("multiclass", 3)
    assert choosing["patience"] == [dtm.EARLY_STOPPING_ROUNDS] == [50]
    assert final["patience"] == []


def test_too_few_rows_for_a_validation_year_fall_back_to_fixed_rounds(cfg: ProjectConfig) -> None:
    rows, time, event = simulated_rows(700, seed=8)
    model = build("m4-design", cfg).fit_rows(rows, time, event, ORIGIN)
    assert model.summary["rounds_chosen_by"] == "fallback"
    assert model.summary["rounds"] == dtm.FALLBACK_ROUNDS


def test_the_linear_design_follows_its_rules_value_by_value() -> None:
    features: dict[str, Any] = {
        "enrollment_count": np.array([99.0, np.nan, 9.0, 999.0]),
        "start_slip_months": np.array([-3.0, 0.0, 3.0, 0.0]),
        "registration_year": np.array([2010.0, 2012.0, 2014.0, 2012.0]),
        "masking": np.array(["NONE", None, "DOUBLE", "NONE"], dtype=object),
        "healthy_volunteers": np.array([1.0, 0.0, np.nan, 0.0]),
        "ever_suspended": np.array([0.0, 1.0, 0.0, 0.0]),
        "landmark_index": np.array([0.0, 0.0, 2.0, 2.0]),
        "months_since_t0": np.array([0.0, 0.0, 12.0, 12.0]),
    }
    design = LinearDesign.fit(features, tuple(features))
    assert design.columns == [
        "enrollment_count", "enrollment_count not given", "start_slip_months",
        "registration_year", "masking=DOUBLE", "masking=NONE", "masking not given",
        "healthy_volunteers", "healthy_volunteers not given", "ever_suspended",
        "landmark_index=0.0", "landmark_index=2.0",
    ]  # fmt: skip
    x = design.matrix(features)
    assert x.shape == (4, 12)
    column = dict(zip(design.columns, x.T, strict=True))
    # A count goes on a log scale: 9, 99 and 999 are evenly spaced. The missing one takes
    # the median (99), and its flag is set.
    given = column["enrollment_count"][[2, 0, 3]]
    assert given[1] - given[0] == pytest.approx(given[2] - given[1], rel=1e-4)
    assert column["enrollment_count"][1] == pytest.approx(column["enrollment_count"][0])
    assert column["enrollment_count not given"].tolist() == [0.0, 1.0, 0.0, 0.0]
    # A slip keeps its sign and is centered and scaled.
    slip = column["start_slip_months"]
    assert slip[0] == pytest.approx(-slip[2])
    assert slip[1] == slip[3] == 0.0
    assert column["start_slip_months"].std() == pytest.approx(1.0, rel=1e-5)
    # The registration year is not put on a log scale.
    year = column["registration_year"]
    assert year[2] - year[1] == pytest.approx(year[1] - year[0])
    assert column["masking=NONE"].tolist() == [1.0, 0.0, 0.0, 1.0]
    assert column["masking not given"].tolist() == [0.0, 1.0, 0.0, 0.0]
    assert column["healthy_volunteers"].tolist() == [1.0, 0.0, 0.0, 0.0]
    assert column["healthy_volunteers not given"].tolist() == [0.0, 0.0, 1.0, 0.0]
    assert "ever_suspended not given" not in design.columns  # never missing here
    assert column["landmark_index=2.0"].tolist() == [0.0, 0.0, 1.0, 1.0]
    assert not any(name.startswith("months_since_t0") for name in design.columns)
    # Later rows: a level first seen later sets no column, and the fitted center, scale and
    # fill are used, not those of the later rows.
    later = {name: values[:2].copy() for name, values in features.items()}
    later["masking"] = np.array(["TRIPLE", "NONE"], dtype=object)
    later["enrollment_count"] = np.array([np.nan, 99.0])
    again = dict(zip(design.columns, design.matrix(later).T, strict=True))
    assert again["masking=DOUBLE"].tolist() == again["masking not given"].tolist() == [0.0, 0.0]
    assert again["masking=NONE"].tolist() == [0.0, 1.0]
    assert again["enrollment_count"][0] == pytest.approx(column["enrollment_count"][0])
    assert again["enrollment_count"][1] == pytest.approx(column["enrollment_count"][0])
    del later["masking"]
    with pytest.raises(ValueError, match="lack 1 model features"):
        design.matrix(later)


def test_a_missing_number_takes_the_median_of_the_training_rows() -> None:
    """Two small trials and one of 99,999 participants: the median is 9, far from the mean
    on any scale."""
    features: dict[str, Any] = {"enrollment_count": np.array([9.0, 9.0, 99_999.0, np.nan])}
    design = LinearDesign.fit(features, ("enrollment_count",))
    assert design.columns == ["enrollment_count", "enrollment_count not given"]
    x = design.matrix(features)
    assert x[3, 0] == pytest.approx(x[0, 0])
    assert x[2, 0] - x[3, 0] > 1.0
    assert x[:, 1].tolist() == [0.0, 0.0, 0.0, 1.0]


def test_the_trees_get_codes_and_missing_values() -> None:
    features: dict[str, Any] = {
        "masking": np.array(["NONE", None, "DOUBLE"], dtype=object),
        "enrollment_count": np.array([10.0, np.nan, 30.0]),
    }
    names = ("masking", "enrollment_count")
    levels = tree_levels(features, names)
    assert levels == {"masking": ("DOUBLE", "NONE")}
    x = tree_matrix(features, names, levels)
    assert (x[0, 0], x[2, 0]) == (1.0, 0.0)  # the codes of NONE and DOUBLE
    assert np.isnan(x[1, 0])
    assert x[0, 1] == 10.0
    assert np.isnan(x[1, 1])
    later = {"masking": np.array(["TRIPLE"], dtype=object), "enrollment_count": np.array([5.0])}
    assert np.isnan(tree_matrix(later, names, levels)[0, 0])  # a level first seen later
    with pytest.raises(ValueError, match="lack 1 model features"):
        tree_matrix({"masking": features["masking"]}, names, levels)


def test_the_logistic_models_are_fitted_on_person_period_rows_with_the_interval(
    cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows, time, event = simulated_rows(3_000, seed=9)
    seen: dict[str, Any] = {}
    real = dtm.LogisticRegression.fit

    def recording(self: Any, x: Any, y: Any, *args: Any, **kwargs: Any) -> Any:
        seen.update(x=np.asarray(x), y=np.asarray(y), c=self.C, weight=kwargs.get("sample_weight"))
        return real(self, x, y, *args, **kwargs)

    monkeypatch.setattr(dtm.LogisticRegression, "fit", recording)
    model = build("m3", cfg, {"C": 0.25}).fit_rows(rows, time, event, ORIGIN)
    assert isinstance(model, DiscreteTimeLogistic)
    assert model.design is not None
    periods = person_period(rows, time, event, ORIGIN, cfg)
    landmark = model.design.matrix(rows.features)
    assert np.array_equal(seen["x"][:, : landmark.shape[1]], landmark[periods.row])
    one_hot = seen["x"][:, landmark.shape[1] :]
    assert one_hot.shape[1] == cfg.discrete_time.n_intervals
    assert (one_hot.sum(axis=1) == 1).all()
    assert (one_hot.argmax(axis=1) + 1).tolist() == periods.interval.tolist()
    assert np.array_equal(seen["y"], periods.outcome)
    assert seen["c"] == 0.25 == model.summary["settings"]["C"]
    assert seen["weight"] is None


# Settings from the tuning ------------------------------------------------------------------------


def test_a_model_runs_with_the_settings_the_tuning_chose(
    cfg: ProjectConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dtm, "TUNED_DIR", tmp_path)
    assert build("m4", cfg).settings == dtm.BOOSTING_DEFAULTS  # nothing tuned yet
    assert build("m2", cfg).settings == dtm.LOGISTIC_DEFAULTS
    (tmp_path / "m4.json").write_text(
        json.dumps({"settings": {"num_leaves": 63, "learning_rate": 0.03}}), encoding="utf-8"
    )
    (tmp_path / "m3.json").write_text(json.dumps({"settings": {"C": 0.1}}), encoding="utf-8")
    assert dtm.tuned_settings("m4", tmp_path) == {"num_leaves": 63, "learning_rate": 0.03}
    tuned = build("m4", cfg).settings
    assert tuned == {**dtm.BOOSTING_DEFAULTS, "num_leaves": 63, "learning_rate": 0.03}
    # The ablation rungs run with M4's settings, so that only the features differ.
    for rung in ("m4-design", "m4-amendments", "m4-text"):
        assert build(rung, cfg).settings == tuned
    assert build("m3", cfg).settings == {"C": 0.1}
    assert build("m2", cfg).settings == dtm.LOGISTIC_DEFAULTS  # M2 has no file of its own
    # Settings handed in replace both.
    assert build("m4", cfg, {"num_leaves": 15}).settings["num_leaves"] == 15


# Through the harness ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["m3", "m4-design"])
def test_a_discrete_time_model_runs_through_the_walk_forward_harness(
    cfg: ProjectConfig, name: str, tmp_path: Path
) -> None:
    """End to end on simulated trials: the harness hands over the training rows as of the
    origin with their features, the model expands them into intervals, and the registrations
    of the following year are scored at both horizons."""
    from trialpulse.eval.walkforward import FeatureRows, evaluation_rows, run

    rows, _, _ = simulated_rows(
        14_000, seed=10, first=dt.date(2010, 1, 1), last=dt.date(2017, 1, 1), end=None
    )
    day = np.datetime64(ORIGIN, "D")
    bare = type(rows)(
        rows.trial_id, rows.landmark_index, rows.landmark_date, rows.event, rows.event_date,
        {"stratum": rows.features["stratum"]},
    )  # fmt: skip

    def training(origin: dt.date) -> Any:
        before = bare.subset(bare.landmark_date < day)
        late = before.event_date >= day
        return type(rows)(
            before.trial_id, before.landmark_index, before.landmark_date,
            np.where(late, 0, before.event).astype(np.int64),
            np.where(late, day, before.event_date).astype("datetime64[D]"), before.features,
        )  # fmt: skip

    def features(origin: dt.date, role: str, only: Any) -> FeatureRows:
        assert only is None  # every landmark index
        if role == "training":
            chosen = rows.subset(rows.landmark_date < day)
        else:
            chosen, _, _ = evaluation_rows(rows, day, cfg.walk_forward.eval_window_months)
        return FeatureRows(chosen.trial_id, chosen.landmark_index, dict(chosen.features))

    result = run(cfg, name, "2016", lambda: bare, training, n_resamples=20,
                 load_features=features, predictions_dir=tmp_path)["origins"][0]  # fmt: skip
    assert result["landmark_indices"] == "all"
    summary = result["model_summary"]
    assert summary["landmark_rows"] == result["n_train_rows"]
    assert summary["person_period_rows"] > summary["landmark_rows"]
    assert summary["predicted_rows"] == result["n_eval_rows"]
    assert summary["model_bytes"] > 0
    for months in cfg.horizons_months:
        pooled = result["horizons"][str(months)]["pooled"]
        assert pooled["auc"]["estimate"] > (0.62 if name == "m3" else 0.55)
        assert 0.5 < pooled["calibration_slope"] < 2.0
        assert abs(pooled["calibration_intercept"]) < 0.6
    assert (tmp_path / f"{name}_2016" / "origin_2016-01-01.parquet").is_file()
