"""docs/results_dev.md is written from result files only, and only from development ones."""

import json
from pathlib import Path
from typing import Any

import pytest

from trialpulse.cli import RefusedError
from trialpulse.config import load_project_config
from trialpulse.eval import results_dev


def _slice(auc: float, n: int = 1000) -> dict[str, Any]:
    def interval(value: float) -> dict[str, float]:
        return {"estimate": value, "ci_low": value - 0.01, "ci_high": value + 0.01}

    return {
        "n_rows": n,
        "auc": interval(auc),
        "brier": interval(0.03),
        "lift": interval(1.5),
        "calibration_slope": 1.1,
        "calibration_intercept": -0.05,
        "single_censoring_curve": {"auc": auc - 0.001},
        "calibration_by_group": [
            {"group": "OTHER", "n": 700, "mean_predicted": 0.03, "observed": 0.031,
             "censored_before_horizon": 0.04},
            {"group": "INDUSTRY", "n": 300, "mean_predicted": 0.045, "observed": 0.047,
             "censored_before_horizon": 0.01},
        ],
    }  # fmt: skip


def _result(model: str, auc: float, landmarks: Any = "all", role: str = "dev") -> dict[str, Any]:
    origins = []
    for year, shift in (("2016", 0.0), ("2017", 0.01)):
        pooled = _slice(auc + shift)
        by_index = {"0": _slice(auc + shift + 0.05, 300)}
        if landmarks == "all":
            by_index["1"] = _slice(auc + shift - 0.02, 700)
        summary = None
        if model == "m1":
            info = {
                "rows_at_landmark_0": 5000, "training_rows": 4000, "labeled_rows": 3900,
                "cases": 150, "validation_from": "2014-01-01", "validation_before": "2015-01-01",
                "validation_rows": 600, "rounds": 90,
                "top_features_by_gain": [{"feature": "sponsor_class", "share_of_gain": 0.05}],
            }  # fmt: skip
            summary = {"horizons": {"12": info, "24": info}}
        origins.append(
            {
                "origin": f"{year}-01-01",
                "role": role,
                "landmark_indices": landmarks,
                "model_summary": summary,
                "horizons": {
                    m: {"pooled": pooled, "by_landmark_index": by_index} for m in ("12", "24")
                },
            }
        )
    return {
        "model": model,
        "bootstrap_resamples": 1000,
        "unlock": None,
        "origins": origins,
        "tracking": {
            "store_description": "the MLflow server named by MLFLOW_TRACKING_URI",
            "experiment": "trialpulse-development", "run_name": f"{model}-dev",
            "run_id": "abc123", "git_commit": "0123456789abcdef", "git_dirty": "no",
        },
    }  # fmt: skip


def _write(root: Path, model: str, result: dict[str, Any]) -> None:
    path = root / "walkforward" / f"{model}_dev.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result), encoding="utf-8")


def test_the_document_compares_the_models_pooled_and_at_registration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "results"
    _write(root, "m0", _result("m0", 0.54))
    _write(root, "m1", _result("m1", 0.62, landmarks=[0]))
    doc = tmp_path / "results_dev.md"
    assert results_dev.main(["--results", str(root), "--doc", str(doc)]) == 0
    text = doc.read_text(encoding="utf-8")
    assert "Research demo. Not medical advice. Not for patient decision-making." in text
    assert "ClinicalTrials.gov" in text
    assert "Data as of" in text
    assert "—" not in text
    assert "\r" not in text
    # Pooled over all landmark indices: M0 only, because M1 scores landmark 0 alone.
    pooled = text.split("## All landmark indices, 12 months")[1].split("## ")[0]
    assert "| M0 | 2016 | 1,000 | 0.540 (0.530 to 0.550) |" in pooled
    assert "| M1 |" not in pooled
    assert "Mean AUC over the development origins: M0 0.5450." in pooled
    # At landmark 0 both models are scored on the same rows.
    first = text.split("## Landmark 0 (registration), 12 months")[1].split("## ")[0]
    assert "| M0 | 2016 | 300 | 0.590 (0.580 to 0.600) |" in first
    assert "| M1 | 2017 | 300 | 0.680 (0.670 to 0.690) |" in first
    assert "M0 0.5950; M1 0.6750" in first
    assert "| OTHER | 700 | 3.10% | 4.0% | 3.00% | 3.00% |" in text
    assert "## M1 in detail" in text
    assert "`sponsor_class` 5.0%" in text
    assert "| M1, development origins | the MLflow server named by MLFLOW_TRACKING_URI |" in text
    assert "`0123456`" in text
    assert "models: M0, M1" in capsys.readouterr().out
    assert results_dev.main(["--results", str(root), "--doc", str(doc)]) == 0
    assert "up to date" in capsys.readouterr().out


def test_the_diagnosis_and_the_cox_models_are_summarized_when_their_files_exist(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    _write(root, "m0", _result("m0", 0.54))
    (root / "diagnosis").mkdir()
    (root / "diagnosis" / "m0_calibration.json").write_text(
        json.dumps(
            [{"origin": "2016-01-01", "horizons": {"24": {
                "hindsight": {"calibration_slope": 1.25},
                "as of the origin": {"calibration_slope": 1.46}}}}]
        ),
        encoding="utf-8",
    )  # fmt: skip
    ratio = {"hazard_ratio": 1.8, "ci_low": 1.6, "ci_high": 2.0, "hazard_ratio_first_year": 2.5,
             "hazard_ratio_later": 1.2}  # fmt: skip
    weak = {**ratio, "hazard_ratio": 1.02, "ci_low": 0.95, "ci_high": 1.09}
    (root / "cox").mkdir()
    (root / "cox" / "cox_l0.json").write_text(
        json.dumps(
            {
                "origin": "2017-01-01",
                "rows": 1234,
                "terms": [
                    {"column": "a", "label": "not yet recruiting", "reference": "RECRUITING"},
                    {"column": "b", "label": "masked", "reference": "open label"},
                ],
                "causes": {
                    "early stop": {"ratios": {"a": ratio, "b": weak}},
                    "completion": {"ratios": {"a": {**ratio, "hazard_ratio": 0.7}, "b": weak}},
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = load_project_config()
    text = results_dev.document(cfg, results_dev.load_results(root), root)
    assert "| as of the origin | 1.460 |" in text
    assert "On the 1,234 landmark 0 rows of the cohort as of 2017-01-01" in text
    assert (
        "| not yet recruiting | RECRUITING | 1.80 (1.60 to 2.00) | 2.50 and 1.20 | 0.70 (" in text
    )
    assert "| masked |" not in text  # its interval holds 1
    assert "| Cox models at landmark 0 | not logged |" in text


def test_a_result_of_a_locked_origin_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "results"
    _write(root, "m0", _result("m0", 0.54, role="test"))
    with pytest.raises(RefusedError, match="development origins only"):
        results_dev.load_results(root)
    unlocked = _result("m0", 0.54)
    unlocked["unlock"] = {"tag": "prereg-v1"}
    _write(root, "m0", unlocked)
    with pytest.raises(RefusedError, match="development origins only"):
        results_dev.load_results(root)
    with pytest.raises(RefusedError, match="no development result"):
        results_dev.load_results(tmp_path / "empty")
