"""M1, the static classifier at registration (CLAUDE.md Section 9), on simulated rows."""

import datetime as dt
from typing import Any

import numpy as np
import pytest

from trialpulse.config import ProjectConfig
from trialpulse.dates import add_months
from trialpulse.eval.ipcw import censoring_groups, horizon_labels_and_weights
from trialpulse.eval.metrics import ipcw_auc
from trialpulse.eval.rows import horizon_days
from trialpulse.features import registry
from trialpulse.models import static_clf
from trialpulse.models.static_clf import (
    CATEGORICAL,
    MIN_VALIDATION_ROWS,
    MODEL_FEATURES,
    StaticClassifier,
    category_levels,
    design_matrix,
)

from .conftest import ORIGIN, simulated_rows


@pytest.fixture(scope="module")
def fitted(cfg: ProjectConfig) -> StaticClassifier:
    rows, time, event = simulated_rows(12_000, seed=1, lapse_share=0.3)
    return StaticClassifier(cfg).fit_rows(rows, time, event, ORIGIN)


def test_the_model_reads_every_main_feature_of_the_registry() -> None:
    assert registry.model_columns() == MODEL_FEATURES
    assert len(MODEL_FEATURES) == 123
    assert set(CATEGORICAL) == {
        f.name for f in registry.FEATURES if f.main and f.kind == registry.CATEGORY
    }
    assert "phase_current_record" not in MODEL_FEATURES  # the sensitivity set stays out


def test_categories_become_codes_of_the_training_levels() -> None:
    """A level first seen after training is missing, like a value never given."""
    rows, _, _ = simulated_rows(300, seed=2)
    levels = category_levels(rows.features)
    assert levels["masking"] == ("DOUBLE", "NONE", "QUADRUPLE")
    later = dict(rows.features)
    masking = np.array(["NONE", "TRIPLE", None, "DOUBLE"], dtype=object)
    later = {name: values[:4] for name, values in later.items()}
    later["masking"] = masking
    x = design_matrix(later, levels)
    column = x[:, MODEL_FEATURES.index("masking")]
    assert column[0] == 1.0  # NONE is the second level
    assert np.isnan(column[1])  # TRIPLE was not in the training rows
    assert np.isnan(column[2])
    assert column[3] == 0.0
    assert x.shape == (4, len(MODEL_FEATURES))
    del later["masking"]
    with pytest.raises(ValueError, match="lack 1 model features"):
        design_matrix(later, levels)


def test_m1_separates_trials_that_stop_from_trials_that_do_not(
    cfg: ProjectConfig, fitted: StaticClassifier
) -> None:
    """Scored on trials posted after the origin, with every outcome known: the features that
    drive the simulation carry the ranking, and the mean predicted risk is the observed one.
    The second part is what fails if the latest registrations are trained on: among them
    only the trials that ended early have a label, and a model that sees the registration
    year learns that recent means risky (it overpredicted these rows by 40%). The censoring
    weights are not what this test checks (leaving them out moves the mean prediction by
    less than its tolerance): `test_each_fit_gets_the_rows_labels_and_weights_of_the_rule`
    reads them."""
    rows, time, event = simulated_rows(
        6_000, seed=3, first=ORIGIN, last=dt.date(2017, 1, 1), end=None
    )
    for months in cfg.horizons_months:
        score = fitted.predict_months(rows, months)
        assert score.shape == (len(rows),)
        assert ((score > 0) & (score < 1)).all()
        auc = ipcw_auc(time, event, score, horizon_days(rows.landmark_date, months))
        assert auc > 0.62, (months, auc)
        # The probability is an early-stop risk at the horizon, completions as controls.
        observed = float(((event == 1) & (time <= horizon_days(rows.landmark_date, months))).mean())
        assert score.mean() == pytest.approx(observed, abs=0.03)
        top = fitted.summary["horizons"][str(months)]["top_features_by_gain"]
        assert {"status", "enrollment_count"} & {f["feature"] for f in top[:6]}


def test_the_rounds_come_from_the_last_training_year(
    cfg: ProjectConfig, fitted: StaticClassifier
) -> None:
    """For a label at H months, the validation year is the latest 12 months of registrations
    whose horizon had passed by the origin."""
    day = np.array([np.datetime64(ORIGIN, "D")])
    for months in cfg.horizons_months:
        info = fitted.summary["horizons"][str(months)]
        assert info["rounds_chosen_by"] == "last training year"
        assert 1 <= info["rounds"] < static_clf.MAX_ROUNDS
        assert info["validation_before"] == str(add_months(day, -months)[0])
        assert info["validation_from"] == str(add_months(day, -(months + 12))[0])
        assert info["validation_rows"] > static_clf.MIN_VALIDATION_ROWS
        # Registrations of the last H months are left out, and some of the rest are censored.
        assert info["labeled_rows"] < info["training_rows"] < info["rows_at_landmark_0"]


def test_only_registrations_whose_horizon_has_passed_are_trained_on(cfg: ProjectConfig) -> None:
    rows, time, event = simulated_rows(4_000, seed=9)
    model = StaticClassifier(cfg).fit_rows(rows, time, event, ORIGIN)
    day = np.datetime64(ORIGIN, "D")
    for months in cfg.horizons_months:
        complete = add_months(rows.landmark_date, months) < day
        info = model.summary["horizons"][str(months)]
        assert info["training_rows"] == int(complete.sum()) < len(rows)
        # Without early censoring every one of those rows has a label.
        assert info["labeled_rows"] == info["training_rows"]


def test_a_row_censored_before_the_horizon_has_no_say(cfg: ProjectConfig) -> None:
    """The label and weight of each training row are those of the evaluation metrics, with
    censoring curves by sponsor class fitted on the training rows themselves (ADR 0017).
    Changing the features of the rows without a label changes nothing."""
    rows, time, event = simulated_rows(5_000, seed=4, lapse_share=0.3)
    months = max(cfg.horizons_months)
    complete = add_months(rows.landmark_date, months) < np.datetime64(ORIGIN, "D")
    groups = censoring_groups(rows.features["stratum"][complete], cfg.evaluation.censoring_min_rows)
    weight = np.zeros(len(rows))
    _, weight[complete] = horizon_labels_and_weights(
        time[complete], event[complete], horizon_days(rows.landmark_date[complete], months), groups
    )
    unlabeled = weight == 0
    assert (unlabeled & complete).sum() > 100  # censored early, as a lapsed record is
    assert weight[complete].max() > 1.05  # and the labeled rows of that class weigh more
    assert 0.1 < unlabeled.mean() < 0.9
    changed = dict(rows.features)
    scrambled = changed["enrollment_count"].copy()
    scrambled[unlabeled] = 1.0
    changed["enrollment_count"] = scrambled
    other = type(rows)(
        rows.trial_id, rows.landmark_index, rows.landmark_date, rows.event, rows.event_date, changed
    )
    a = StaticClassifier(cfg).fit_rows(rows, time, event, ORIGIN)
    b = StaticClassifier(cfg).fit_rows(other, time, event, ORIGIN)
    probe, _, _ = simulated_rows(500, seed=5)
    assert np.array_equal(a.predict_months(probe, months), b.predict_months(probe, months))
    # The shorter horizon labels other rows, so there the change is seen.
    short = min(cfg.horizons_months)
    assert not np.array_equal(a.predict_months(probe, short), b.predict_months(probe, short))


def test_two_fits_give_the_same_model(cfg: ProjectConfig, fitted: StaticClassifier) -> None:
    rows, time, event = simulated_rows(12_000, seed=1, lapse_share=0.3)
    again = StaticClassifier(cfg).fit_rows(rows, time, event, ORIGIN)
    probe, _, _ = simulated_rows(400, seed=6)
    for months in cfg.horizons_months:
        assert np.array_equal(
            fitted.predict_months(probe, months), again.predict_months(probe, months)
        )
    assert again.summary == fitted.summary


def test_m1_is_a_model_of_landmark_zero(cfg: ProjectConfig) -> None:
    rows, time, event = simulated_rows(400, seed=7)
    model = StaticClassifier(cfg)
    assert model.landmark_indices == (0,)
    assert model.feature_matrix
    later = type(rows)(
        rows.trial_id, rows.landmark_index + 1, rows.landmark_date, rows.event, rows.event_date,
        rows.features,
    )  # fmt: skip
    with pytest.raises(ValueError, match="landmark 0 only"):
        model.fit_rows(later, time, event, ORIGIN)
    with pytest.raises(RuntimeError, match="fit first"):
        model.predict_months(rows, 12)


def test_too_few_rows_for_a_validation_year_fall_back_to_fixed_rounds(cfg: ProjectConfig) -> None:
    rows, time, event = simulated_rows(600, seed=8)
    model = StaticClassifier(cfg).fit_rows(rows, time, event, ORIGIN)
    info = model.summary["horizons"][str(max(cfg.horizons_months))]
    assert info["rounds_chosen_by"] == "fallback"
    assert info["rounds"] == static_clf.FALLBACK_ROUNDS


def test_m1_runs_through_the_walk_forward_harness(cfg: ProjectConfig) -> None:
    """End to end on simulated trials: the harness cuts the training rows off at the origin,
    asks for the features of landmark 0, fits M1 and scores the registrations of the
    following year."""
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
        assert only == (0,)
        if role == "training":
            chosen = rows.subset(rows.landmark_date < day)
        else:
            chosen, _, _ = evaluation_rows(rows, day, cfg.walk_forward.eval_window_months)
        return FeatureRows(chosen.trial_id, chosen.landmark_index, dict(chosen.features))

    result = run(cfg, "m1", "2016", lambda: bare, training, n_resamples=20, load_features=features)[
        "origins"
    ][0]
    assert result["landmark_indices"] == [0]
    assert result["n_eval_rows"] == int(
        ((rows.landmark_date >= day) & (rows.landmark_date < np.datetime64("2017-01-01"))).sum()
    )
    summary = result["model_summary"]["horizons"]
    assert set(summary) == {str(m) for m in cfg.horizons_months}
    for months in cfg.horizons_months:
        pooled = result["horizons"][str(months)]["pooled"]
        assert pooled["auc"]["estimate"] > 0.62
        # A coarse bound on purpose. Other seeds of these simulated rows give slopes from
        # 1.0 to 1.7, so a narrow one would test the seed. What it must catch is a model
        # trained on the latest registrations: its slope is 0.2 to 0.3.
        assert 0.5 < pooled["calibration_slope"] < 2.5
        assert abs(pooled["calibration_intercept"]) < 1.0
        assert summary[str(months)]["training_rows"] < result["n_train_rows"]


def test_each_fit_gets_the_rows_labels_and_weights_of_the_rule(
    cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What LightGBM is handed, read at the call. Per horizon there are two fits. The first
    chooses the number of trees: fitted on the labeled registrations before the last
    training year, stopped early on that year. The second is the model: every labeled
    registration, for the number of trees the first found best. Labels and weights are
    those of the evaluation metrics, with one censoring curve per sponsor class fitted on
    the rows whose horizon had passed (ADR 0017, ADR 0023)."""
    rows, time, event = simulated_rows(12_000, seed=4, lapse_share=0.3)
    calls: list[dict[str, Any]] = []
    real = static_clf.lgb.train

    def recording(params: Any, train_set: Any, **kwargs: Any) -> Any:
        booster = real(params, train_set, **kwargs)
        valid = kwargs.get("valid_sets")
        calls.append(
            {
                "x": np.asarray(train_set.data),
                "y": np.asarray(train_set.get_label(), dtype=np.int64),
                "w": np.asarray(train_set.get_weight(), dtype=np.float64),
                "valid_y": None if not valid else np.asarray(valid[0].get_label(), dtype=np.int64),
                "valid_w": None
                if not valid
                else np.asarray(valid[0].get_weight(), dtype=np.float64),
                "rounds": kwargs["num_boost_round"],
                "best": booster.best_iteration,
                "ran": booster.current_iteration(),
            }
        )
        return booster

    monkeypatch.setattr(static_clf.lgb, "train", recording)
    model = StaticClassifier(cfg).fit_rows(rows, time, event, ORIGIN)
    assert len(calls) == 2 * len(cfg.horizons_months)
    day = np.datetime64(ORIGIN, "D")
    x = design_matrix(rows.features, model.levels)
    for i, months in enumerate(cfg.horizons_months):
        choosing, final = calls[2 * i], calls[2 * i + 1]
        complete = add_months(rows.landmark_date, months) < day
        horizon = horizon_days(rows.landmark_date[complete], months)
        classes = rows.features["stratum"][complete]
        groups = censoring_groups(classes, cfg.evaluation.censoring_min_rows)
        label, weight = np.zeros(len(rows), dtype=np.int64), np.zeros(len(rows))
        label[complete], weight[complete] = horizon_labels_and_weights(
            time[complete], event[complete], horizon, groups
        )
        labeled = weight > 0
        # The model: every labeled row whose horizon had passed, weighted by sponsor class.
        assert np.array_equal(final["y"], label[labeled])
        np.testing.assert_allclose(final["w"], weight[labeled], rtol=1e-6)  # stored as float32
        assert np.array_equal(final["x"], x[labeled], equal_nan=True)
        assert final["valid_y"] is None
        # Those weights are not the weights of one curve for all rows, nor all equal to 1:
        # a third of class OTHER is censored early, so its labeled rows weigh more.
        single = np.zeros(len(rows))
        _, single[complete] = horizon_labels_and_weights(time[complete], event[complete], horizon)
        assert np.abs(final["w"] - single[labeled]).max() > 0.02
        assert final["w"].max() > 1.05
        assert final["w"][rows.features["stratum"][labeled] != "OTHER"].max() < 1.01
        # The fit that chooses the trees: the registrations before the last training year,
        # stopped on that year, which is the latest 12 months whose horizon had passed.
        year_start = add_months(np.array([day]), -(months + 12))[0]
        year_end = add_months(np.array([day]), -months)[0]
        valid = labeled & (rows.landmark_date >= year_start)
        early = labeled & (rows.landmark_date < year_start)
        assert np.array_equal(choosing["y"], label[early])
        np.testing.assert_allclose(choosing["w"], weight[early], rtol=1e-6)
        assert np.array_equal(choosing["valid_y"], label[valid])
        np.testing.assert_allclose(choosing["valid_w"], weight[valid], rtol=1e-6)
        dates = rows.landmark_date[valid]
        assert year_start <= dates.min() < year_start + np.timedelta64(40, "D")
        assert year_end - np.timedelta64(40, "D") < dates.max() < year_end
        info = model.summary["horizons"][str(months)]
        assert info["validation_rows"] == int(valid.sum()) > MIN_VALIDATION_ROWS
        assert info["labeled_rows"] == int(labeled.sum())
        assert info["cases"] == int(label[labeled].sum())
        assert info["weighted_case_share"] == pytest.approx(
            float((weight * label).sum() / weight.sum())
        )
        # The trees of the model are the best round of the first fit. (LightGBM hands that
        # fit back cut to its best round, so its last round is the same number.)
        assert 1 <= choosing["best"] == choosing["ran"] < static_clf.MAX_ROUNDS
        assert final["rounds"] == choosing["best"] == info["rounds"]
        assert model.boosters[months].num_trees() == choosing["best"]
