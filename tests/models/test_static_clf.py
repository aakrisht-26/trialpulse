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
    year learns that recent means risky (it overpredicted these rows by 40%). It also fails
    if the censoring weights are left out, because a third of one sponsor class is censored
    early in the training rows."""
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
        assert 0.6 < pooled["calibration_slope"] < 1.6
        assert summary[str(months)]["training_rows"] < result["n_train_rows"]
