"""The recalibration of ADR 0027 and the measurement of its rule, on made-up rows."""

import datetime as dt
from typing import Any

import numpy as np
import pytest

from trialpulse.config import ProjectConfig
from trialpulse.eval.lock import TestLockError
from trialpulse.eval.rows import LandmarkRows
from trialpulse.eval.walkforward import FeatureRows, evaluation_rows
from trialpulse.models import recalibration as rc

from .conftest import simulated_rows

N_INDICES = 7


def _draw(probabilities: np.ndarray[Any, Any], rng: np.random.Generator) -> np.ndarray[Any, Any]:
    u = rng.random(len(probabilities))
    return np.where(
        u < probabilities[:, 1], 1, np.where(u < probabilities[:, 1] + probabilities[:, 2], 2, 0)
    ).astype(np.int64)


def _softmax(stop: np.ndarray[Any, Any], complete: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    weights = np.column_stack([np.ones(len(stop)), np.exp(stop), np.exp(complete)])
    out: np.ndarray[Any, Any] = weights / weights.sum(axis=1, keepdims=True)
    return out


@pytest.mark.slow  # over half a second; CI runs every test
def test_well_calibrated_predictions_are_left_almost_alone() -> None:
    rng = np.random.default_rng(1)
    n = 80_000
    index = rng.integers(0, N_INDICES, n)
    truth = _softmax(rng.normal(-3.5, 0.8, n), rng.normal(-1.5, 0.6, n))
    fitted = rc.fit(truth, index, _draw(truth, rng), N_INDICES)
    assert fitted.stop_slope == pytest.approx(1.0, abs=0.06)
    assert fitted.complete_slope == pytest.approx(1.0, abs=0.04)
    assert max(abs(a) for a in fitted.stop_intercepts) < 0.25
    assert max(abs(a) for a in fitted.complete_intercepts) < 0.1


@pytest.mark.slow  # over half a second; CI runs every test
def test_a_known_distortion_is_undone() -> None:
    """The model's log odds are too spread out (by 1/0.7 for an early stop) and shifted by
    landmark index; outcomes follow the truth. The fit finds the slope and the shifts."""
    rng = np.random.default_rng(2)
    n = 150_000
    index = rng.integers(0, N_INDICES, n)
    shift = np.linspace(0.4, -0.4, N_INDICES)
    true_stop, true_complete = rng.normal(-3.2, 0.7, n), rng.normal(-1.6, 0.5, n)
    model = _softmax((true_stop - shift[index]) / 0.7, (true_complete + 0.2) / 0.9)
    fitted = rc.fit(model, index, _draw(_softmax(true_stop, true_complete), rng), N_INDICES)
    assert fitted.stop_slope == pytest.approx(0.7, abs=0.05)
    assert fitted.complete_slope == pytest.approx(0.9, abs=0.03)
    assert np.allclose(fitted.stop_intercepts, shift, atol=0.15)
    assert np.allclose(fitted.complete_intercepts, -0.2, atol=0.06)
    # Applied, the recalibrated probabilities are close to the truth, and closer than before.
    fixed = rc.apply(fitted, model, index)
    truth = _softmax(true_stop, true_complete)
    assert np.abs(fixed - truth).mean() < 0.3 * np.abs(model - truth).mean()


def test_applying_keeps_probabilities_and_the_identity_changes_nothing() -> None:
    rng = np.random.default_rng(3)
    p = _softmax(rng.normal(-3, 1, 500), rng.normal(-1, 1, 500))
    index = rng.integers(0, N_INDICES, 500)
    same = rc.apply(rc.Recalibration.identity(N_INDICES), p, index)
    assert np.allclose(same, p, atol=1e-12)
    other = rc.Recalibration(tuple(np.linspace(-1, 1, N_INDICES)), 0.5, (0.3,) * N_INDICES, 1.4)
    moved = rc.apply(other, p, index)
    assert np.allclose(moved.sum(axis=1), 1.0)
    assert ((moved > 0) & (moved < 1)).all()
    assert not np.allclose(moved, p)


def test_a_landmark_index_without_rows_keeps_intercept_0() -> None:
    rng = np.random.default_rng(4)
    p = _softmax(rng.normal(-3, 0.8, 20_000), rng.normal(-1.5, 0.6, 20_000))
    index = rng.integers(0, 6, 20_000)  # no row at index 6
    fitted = rc.fit(p, index, _draw(p, rng), N_INDICES)
    assert fitted.stop_intercepts[6] == 0.0
    assert fitted.complete_intercepts[6] == 0.0


def test_the_rule_reads_each_of_its_four_conditions() -> None:
    def numbers(slope: float, i0: float, i6: float, brier: float, auc: float) -> dict[str, Any]:
        return {
            "24": {"calibration_slope": slope, "intercept_by_landmark_index": {"0": i0, "6": i6},
                   "brier": 0.05, "auc": 0.70},
            "12": {"calibration_slope": 1.0, "intercept_by_landmark_index": {"0": 0.0, "6": 0.0},
                   "brier": brier, "auc": auc},
        }  # fmt: skip

    raw = numbers(0.82, 0.10, -0.15, 0.0300, 0.7200)
    good = numbers(0.95, -0.05, 0.08, 0.0299, 0.7195)
    assert rc.judge(raw, good) == {
        "slope_closer_to_1": True, "intercepts_closer_to_0": True,
        "brier_not_larger": True, "auc_not_lower": True,
    }  # fmt: skip
    assert not rc.judge(raw, numbers(1.20, -0.05, 0.08, 0.0299, 0.72))["slope_closer_to_1"]
    assert not rc.judge(raw, numbers(0.95, -0.12, 0.08, 0.0299, 0.72))["intercepts_closer_to_0"]
    assert not rc.judge(raw, numbers(0.95, -0.05, 0.16, 0.0299, 0.72))["intercepts_closer_to_0"]
    assert not rc.judge(raw, numbers(0.95, -0.05, 0.08, 0.0301, 0.72))["brier_not_larger"]
    assert rc.judge(raw, numbers(0.95, -0.05, 0.08, 0.0300, 0.72))["brier_not_larger"]  # equal
    assert not rc.judge(raw, numbers(0.95, -0.05, 0.08, 0.0299, 0.7185))["auc_not_lower"]
    assert rc.judge(raw, numbers(0.95, -0.05, 0.08, 0.0299, 0.7191))["auc_not_lower"]


def _loaders(cfg: ProjectConfig) -> tuple[Any, Any, Any]:
    rows, _, _ = simulated_rows(
        12_000, seed=50, first=dt.date(2010, 1, 1), last=dt.date(2018, 1, 1), end=None
    )
    bare = LandmarkRows(
        rows.trial_id, rows.landmark_index, rows.landmark_date, rows.event, rows.event_date,
        {"stratum": rows.features["stratum"]},
    )  # fmt: skip

    def training(origin: dt.date) -> LandmarkRows:
        day = np.datetime64(origin, "D")
        before = bare.subset(bare.landmark_date < day)
        late = before.event_date >= day
        return LandmarkRows(
            before.trial_id, before.landmark_index, before.landmark_date,
            np.where(late, 0, before.event).astype(np.int64),
            np.where(late, day, before.event_date).astype("datetime64[D]"), before.features,
        )  # fmt: skip

    def features(origin: dt.date, role: str, only: Any) -> FeatureRows:
        day = np.datetime64(origin, "D")
        if role == "training":
            chosen = rows.subset(rows.landmark_date < day)
        else:
            chosen, _, _ = evaluation_rows(rows, day, cfg.walk_forward.eval_window_months)
        return FeatureRows(chosen.trial_id, chosen.landmark_index, dict(chosen.features))

    return (lambda: bare), training, features


@pytest.mark.slow  # fits M4 twice; CI runs every test
def test_the_measurement_reports_both_sides_and_the_four_conditions(cfg: ProjectConfig) -> None:
    final, training, features = _loaders(cfg)
    result = rc.measure(cfg, final, training, features)
    assert [o["origin"] for o in result["origins"]] == ["2016-01-01", "2017-01-01"]
    for o in result["origins"]:
        assert o["validation_rows"] > 500
        assert set(o["conditions"]) == {
            "slope_closer_to_1", "intercepts_closer_to_0", "brier_not_larger", "auc_not_lower",
        }  # fmt: skip
        assert o["passes"] == all(o["conditions"].values())
        for side in ("without", "with"):
            assert set(o[side]) == {"12", "24"}
            assert 0.5 < o[side]["12"]["auc"] < 1.0
        assert len(o["recalibration"]["stop_intercepts"]) == cfg.landmarks.max_index + 1
        assert o["conditions"] == rc.judge(o["without"], o["with"])
    assert result["adopted"] == all(o["passes"] for o in result["origins"])


def test_a_locked_origin_is_refused_before_any_row_is_read(
    cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[Any] = []

    def loader(*args: Any) -> Any:
        asked.append(args)
        raise AssertionError("no data may be read")

    monkeypatch.setattr(rc, "select_origins", lambda cfg, spec: list(cfg.walk_forward.origins))
    with pytest.raises(TestLockError, match="--unlock-test"):
        rc.measure(cfg, loader, loader, loader)
    assert asked == []
