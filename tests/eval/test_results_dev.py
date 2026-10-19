"""docs/results_dev.md is written from result files only, and only from development ones.

The result files here are made up so that every cell of every table has a value of its own,
and so that the two horizons, the two origins and the two models differ everywhere: a number
printed in the wrong column, or taken from the wrong model, origin or horizon, changes a
line that a test reads in full."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from trialpulse.cli import RefusedError
from trialpulse.config import load_project_config
from trialpulse.eval import results_dev

# The mean predicted risk of class OTHER at origin 2016 and 12 months, per model. Origin 2017
# adds 0.01 and the 24-month horizon 0.02, so no two tables hold the same number.
PREDICTED = {"m0": 0.03, "m1": 0.02}
HORIZON_AUC = {"12": 0.0, "24": -0.1}  # the 24-month AUC is 0.1 lower
LOCAL = "a local store in mlruns/ (temporary: MLFLOW_TRACKING_URI is not set)"
REMOTE = "the MLflow server named by MLFLOW_TRACKING_URI"
NOT_EVERY_RUN = "**Not every run above is on the project's MLflow server (DagsHub).**"
NO_RUN = "**None of the runs above is on the project's MLflow server (DagsHub).**"


def _slice(auc: float, n: int, predicted: float) -> dict[str, Any]:
    def interval(value: float) -> dict[str, float]:
        return {"estimate": value, "ci_low": value - 0.01, "ci_high": value + 0.01}

    return {
        "n_rows": n,
        "auc": interval(auc),
        "brier": interval(0.03),
        "lift": interval(1.5),
        "calibration_slope": 1.1,
        "calibration_intercept": -0.05,
        "single_censoring_curve": {"auc": auc - 0.004},
        "calibration_by_group": [
            {"group": "OTHER", "n": 700, "mean_predicted": predicted, "observed": 0.031,
             "censored_before_horizon": 0.04},
            {"group": "INDUSTRY", "n": 300, "mean_predicted": predicted + 0.015,
             "observed": 0.047, "censored_before_horizon": 0.01},
        ],
        "calibration_table": [
            {"bin": 1, "n": n // 2, "mean_predicted": predicted / 2, "observed_cif": 0.011},
            {"bin": 2, "n": n - n // 2, "mean_predicted": predicted * 2, "observed_cif": 0.052},
        ],
    }  # fmt: skip


def _tracking(model: str, store: str = "remote", dirty: str = "no") -> dict[str, str]:
    return {
        "store": store, "store_description": REMOTE if store == "remote" else LOCAL,
        "experiment": "trialpulse-development", "run_name": f"{model}-dev", "run_id": "abc123",
        "git_commit": "0123456789abcdef", "git_dirty": dirty,
    }  # fmt: skip


def _result(
    model: str,
    auc: float,
    landmarks: Any = "all",
    role: str = "dev",
    tracking: dict[str, str] | None = None,
    years: tuple[str, ...] = ("2016", "2017"),
) -> dict[str, Any]:
    origins = []
    for year in years:
        shift = 0.0 if year == "2016" else 0.01
        horizons: dict[str, Any] = {}
        for months, drop in HORIZON_AUC.items():
            predicted = PREDICTED.get(model, 0.025) + shift + (0.02 if months == "24" else 0.0)
            by_index = {"0": _slice(auc + shift + drop + 0.05, 300, predicted)}
            if landmarks == "all":
                by_index["1"] = _slice(auc + shift + drop - 0.02, 700, predicted)
            horizons[months] = {
                "pooled": _slice(auc + shift + drop, 1000, predicted),
                "by_landmark_index": by_index,
            }
        summary = None
        if model == "m1":

            def info(passed: int) -> dict[str, Any]:
                return {
                    "rows_at_landmark_0": 5000, "training_rows": passed,
                    "labeled_rows": passed - 100, "cases": 150, "validation_from": "2014-01-01",
                    "validation_before": "2015-01-01", "validation_rows": 600, "rounds": 90,
                    "top_features_by_gain": [
                        {"feature": f"feature_{i}", "share_of_gain": 0.10 - i / 100}
                        for i in range(10)
                    ],
                }  # fmt: skip

            summary = {"horizons": {"12": info(4000), "24": info(3000)}}
        origins.append(
            {
                "origin": f"{year}-01-01",
                "role": role,
                "landmark_indices": landmarks,
                "model_summary": summary,
                "horizons": horizons,
            }
        )
    result: dict[str, Any] = {
        "model": model, "bootstrap_resamples": 1000, "unlock": None, "origins": origins,
    }  # fmt: skip
    if tracking is not None:
        result["tracking"] = tracking
    return result


def _write(root: Path, model: str, result: dict[str, Any]) -> None:
    path = root / "walkforward" / f"{model}_dev.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result), encoding="utf-8")


def _section(text: str, heading: str) -> str:
    return text.split(heading)[1].split("\n## ")[0]


def _block(section: str, title: str) -> str:
    """The table under one "Origin YYYY, H months:" line of a section."""
    return section.split(title)[1].split("\nOrigin ")[0]


def test_the_document_compares_the_models_pooled_and_at_registration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "results"
    _write(root, "m0", _result("m0", 0.54, tracking=_tracking("m0")))
    _write(root, "m1", _result("m1", 0.62, landmarks=[0], tracking=_tracking("m1", dirty="yes")))
    doc = tmp_path / "results_dev.md"
    args = ["--results", str(root), "--doc", str(doc), "--tuned-dir", str(tmp_path / "none")]
    assert results_dev.main(args) == 0
    text = doc.read_text(encoding="utf-8")
    assert "Research demo. Not medical advice. Not for patient decision-making." in text
    assert "ClinicalTrials.gov" in text
    assert "Data as of" in text
    assert "—" not in text
    assert "\r" not in text
    # Pooled over all landmark indices: M0 only, because M1 scores landmark 0 alone. Every
    # metric in its own column: AUC, Brier score, lift, slope, intercept, single-curve AUC.
    pooled = _section(text, "## All landmark indices, 12 months")
    assert (
        "| Model | Origin | Rows | AUC (95% interval) | Brier score | Lift at 10% | Calibration "
        "slope | Intercept | AUC, single censoring curve |"
    ) in pooled
    assert (
        "| M0 | 2016 | 1,000 | 0.540 (0.530 to 0.550) | 0.0300 (0.0200 to 0.0400) | 1.50 (1.49 "
        "to 1.51) | 1.100 | -0.050 | 0.536 |"
    ) in pooled
    assert "| M1 |" not in pooled
    assert "Mean AUC over the development origins: M0 0.5450." in pooled
    # The 24-month table holds the 24-month numbers.
    later = _section(text, "## All landmark indices, 24 months")
    assert "| M0 | 2016 | 1,000 | 0.440 (0.430 to 0.450) |" in later
    assert "| M0 | 2017 | 1,000 | 0.450 (0.440 to 0.460) |" in later
    assert "0.540" not in later
    assert "Mean AUC over the development origins: M0 0.4450." in later
    # Every landmark index of the models that score them all (Section 6).
    by_index = _section(text, "## By landmark index, 12 months")
    assert "| Model | Origin | Landmark index | Rows | AUC (95% interval) |" in by_index
    assert (
        "| M0 | 2016 | 1 | 700 | 0.520 (0.510 to 0.530) | 0.0300 (0.0200 to 0.0400) | 1.50 (1.49 "
        "to 1.51) | 1.100 | -0.050 |"
    ) in by_index
    assert "| M0 | 2017 | 0 | 300 | 0.600 (0.590 to 0.610) |" in by_index
    assert "| M1 |" not in by_index
    by_index_later = _section(text, "## By landmark index, 24 months")
    assert "| M0 | 2016 | 1 | 700 | 0.420 (0.410 to 0.430) |" in by_index_later
    assert "| M0 | 2017 | 0 | 300 | 0.500 (0.490 to 0.510) |" in by_index_later
    assert "0.520" not in by_index_later
    # At landmark 0 both models are scored on the same rows.
    first = _section(text, "## Landmark 0 (registration), 12 months")
    assert (
        "| M0 | 2016 | 300 | 0.590 (0.580 to 0.600) | 0.0300 (0.0200 to 0.0400) | 1.50 (1.49 to "
        "1.51) | 1.100 | -0.050 | 0.586 |"
    ) in first
    assert "| M1 | 2017 | 300 | 0.680 (0.670 to 0.690) |" in first
    assert "M0 0.5950; M1 0.6750" in first
    first_later = _section(text, "## Landmark 0 (registration), 24 months")
    assert "| M0 | 2016 | 300 | 0.490 (0.480 to 0.500) |" in first_later
    assert "| M1 | 2017 | 300 | 0.580 (0.570 to 0.590) |" in first_later
    assert "M0 0.4950; M1 0.5750" in first_later
    # By sponsor class: each model's own prediction beside the one observed rate, for the
    # origin and the horizon the line above the table names.
    by_class = _section(text, "## Predicted against observed risk by sponsor class, landmark 0")
    assert (
        "| Sponsor class | Rows | Observed (IPCW) | Censored before the horizon | Predicted, M0 "
        "| Predicted, M1 |"
    ) in by_class
    block = _block(by_class, "Origin 2016, 12 months:")
    assert "| OTHER | 700 | 3.10% | 4.0% | 3.00% | 2.00% |" in block
    assert "| INDUSTRY | 300 | 4.70% | 1.0% | 4.50% | 3.50% |" in block
    assert "| OTHER | 700 | 3.10% | 4.0% | 4.00% | 3.00% |" in _block(
        by_class, "Origin 2017, 12 months:"
    )
    assert "| OTHER | 700 | 3.10% | 4.0% | 5.00% | 4.00% |" in _block(
        by_class, "Origin 2016, 24 months:"
    )
    assert "| INDUSTRY | 300 | 4.70% | 1.0% | 7.50% | 6.50% |" in _block(
        by_class, "Origin 2017, 24 months:"
    )
    # By decile of each model's own predictions, again per origin and horizon.
    deciles = _section(text, "## Predicted against observed risk by decile, landmark 0")
    assert (
        "| Decile of predicted risk | Rows, M0 | Predicted, M0 | Observed, M0 | Rows, M1 | "
        "Predicted, M1 | Observed, M1 |"
    ) in deciles
    block = _block(deciles, "Origin 2016, 12 months:")
    assert "| 1 | 150 | 1.50% | 1.10% | 150 | 1.00% | 1.10% |" in block
    assert "| 2 | 150 | 6.00% | 5.20% | 150 | 4.00% | 5.20% |" in block
    assert "| 1 | 150 | 2.00% | 1.10% | 150 | 1.50% | 1.10% |" in _block(
        deciles, "Origin 2017, 12 months:"
    )
    assert "| 2 | 150 | 12.00% | 5.20% | 150 | 10.00% | 5.20% |" in _block(
        deciles, "Origin 2017, 24 months:"
    )
    assert deciles.count("Origin 2016, 12 months:") == deciles.count("Origin 2017, 24 months:") == 1
    # M1's rows: how many as of the origin, how many with the horizon passed, how many
    # labeled; and its strongest features, eight of them.
    detail = _section(text, "## M1 in detail")
    assert (
        "| 2016 | 12 | 5,000 | 4,000 | 3,900 | 150 | 2014-01-01 to 2015-01-01 | 600 | 90 |"
        in detail
    )
    assert (
        "| 2017 | 24 | 5,000 | 3,000 | 2,900 | 150 | 2014-01-01 to 2015-01-01 | 600 | 90 |"
        in detail
    )
    assert "`feature_0` 10.0%, `feature_1` 9.0%" in detail
    assert "`feature_7` 3.0%" in detail
    assert "feature_8" not in detail
    # The runs: where each went, from which commit, and whether the tree was clean.
    runs = _section(text, "## Runs")
    assert (
        f"| M0, development origins | {REMOTE} | `m0-dev` in `trialpulse-development`, id "
        "`abc123` | `0123456` | no |"
    ) in runs
    assert "id `abc123` | `0123456` | yes |" in runs.split("| M1, development origins |")[1]
    assert "on the project's MLflow server (DagsHub)" not in text  # both runs are on it
    assert "models: M0, M1" in capsys.readouterr().out
    assert results_dev.main(args) == 0
    assert "up to date" in capsys.readouterr().out
    # One model over all landmark indices is no ladder: no champion, no step-10 section.
    for heading in ("## The ladder", "## Differences between models", "## Ablation of M4",
                    "## Run time and size", "## Tuning"):  # fmt: skip
        assert heading not in text


def test_the_diagnosis_and_the_cox_models_are_summarized_when_their_files_exist(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    _write(root, "m0", _result("m0", 0.54, tracking=_tracking("m0")))
    (root / "diagnosis").mkdir()
    (root / "diagnosis" / "m0_calibration.json").write_text(
        json.dumps(
            {
                "origins": [{"origin": "2016-01-01", "horizons": {"24": {
                    "hindsight": {"calibration_slope": 1.25},
                    "as of the origin": {"calibration_slope": 1.46}}}}],
                "tracking": {**_tracking("m0", store="local"),
                             "run_name": "m0-calibration-diagnosis"},
            }
        ),
        encoding="utf-8",
    )  # fmt: skip

    def ratio(
        value: float, low: float, high: float, first: float, later: float
    ) -> dict[str, float]:
        return {"hazard_ratio": value, "ci_low": low, "ci_high": high,
                "hazard_ratio_first_year": first, "hazard_ratio_later": later}  # fmt: skip

    # Nine more characteristics whose interval excludes 1, all nearer to 1 than "a" and "c":
    # only the eight largest ratios are listed, so three of these are left out.
    extra = [f"e{i}" for i in range(1, 10)]
    stop = {
        "a": ratio(1.8, 1.6, 2.0, 2.5, 1.2),
        "b": ratio(1.02, 0.95, 1.09, 1.0, 1.0),  # its interval holds 1
        "c": ratio(0.5, 0.45, 0.56, 0.7, 0.4),
        **{name: ratio(1.2 - i / 100, 1.05, 1.3, 1.0, 1.0) for i, name in enumerate(extra)},
    }
    complete = {
        "a": ratio(0.7, 0.6, 0.8, 0.9, 0.6),
        "b": ratio(1.3, 1.2, 1.4, 1.0, 1.0),
        "c": ratio(1.1, 1.06, 1.13, 1.0, 1.0),
        **{name: ratio(1.0, 0.9, 1.1, 1.0, 1.0) for name in extra},
    }
    (root / "cox").mkdir()
    (root / "cox" / "cox_l0.json").write_text(
        json.dumps(
            {
                "origin": "2017-01-01",
                "rows": 1234,
                "terms": [
                    {"column": "a", "label": "not yet recruiting", "reference": "RECRUITING"},
                    {"column": "b", "label": "masked", "reference": "open label"},
                    {"column": "c", "label": "lists a behavioral intervention",
                     "reference": "does not"},
                    *({"column": name, "label": f"extra {name}", "reference": ""}
                      for name in extra),
                ],
                "causes": {"early stop": {"ratios": stop}, "completion": {"ratios": complete}},
            }
        ),
        encoding="utf-8",
    )  # fmt: skip
    cfg = load_project_config()
    text = results_dev.document(cfg, results_dev.load_results(root), root)
    assert "| as of the origin | 1.460 |" in text
    assert "| hindsight | 1.250 |" in text
    assert "On the 1,234 landmark 0 rows of the cohort as of 2017-01-01" in text
    cox = _section(text, "## Cause-specific Cox models (interpretation only)")
    below = (
        "| lists a behavioral intervention | does not | 0.50 (0.45 to 0.56) | 0.70 and 0.40 | "
        "1.10 (1.06 to 1.13) |"
    )
    above = (
        "| not yet recruiting | RECRUITING | 1.80 (1.60 to 2.00) | 2.50 and 1.20 | 0.70 (0.60 to "
        "0.80) |"
    )
    # Ratios on either side of 1 count, the one further from 1 first (0.50 before 1.80).
    assert below in cox
    assert above in cox
    assert cox.index(below) < cox.index(above)
    assert "| masked |" not in cox  # its interval holds 1
    # Eight rows in all: the two above and the six largest of the nine others.
    assert results_dev.TOP_RATIOS == 8
    assert [name for name in extra if f"| extra {name} |" in cox] == extra[:6]
    assert (
        "| extra e1 | (a number) | 1.20 (1.05 to 1.30) | 1.00 and 1.00 | 1.00 (0.90 to 1.10) |"
        in cox
    )
    # The runs table says plainly which runs are not on the server.
    runs = _section(text, "## Runs")
    assert "| Cox models at landmark 0 | not logged |" in runs
    assert f"| Calibration diagnosis of M0 | {LOCAL} | `m0-calibration-diagnosis` in " in runs
    assert NOT_EVERY_RUN in runs  # one of the three is on the server
    assert NO_RUN not in runs
    assert "exists only on the machine that produced it" in runs


def test_runs_that_are_nowhere_on_the_server_are_said_to_be(tmp_path: Path) -> None:
    cfg = load_project_config()
    root = tmp_path / "never"
    _write(root, "m0", _result("m0", 0.54))  # run with --no-track
    runs = _section(results_dev.document(cfg, results_dev.load_results(root), root), "## Runs")
    assert "| M0, development origins | not logged |" in runs
    assert NO_RUN in runs
    assert NOT_EVERY_RUN not in runs
    root = tmp_path / "local"
    _write(root, "m0", _result("m0", 0.54, tracking=_tracking("m0", store="local")))
    _write(root, "m1", _result("m1", 0.62, landmarks=[0], tracking=_tracking("m1", "local")))
    runs = _section(results_dev.document(cfg, results_dev.load_results(root), root), "## Runs")
    assert NO_RUN in runs
    assert NOT_EVERY_RUN not in runs


def test_result_files_that_do_not_match_give_a_document_or_a_message(tmp_path: Path) -> None:
    """A model with one origin fewer, results without decile tables, and a diagnosis file
    in the form an older version wrote."""
    cfg = load_project_config()
    root = tmp_path / "results"
    _write(root, "m0", _result("m0", 0.54))
    _write(root, "m1", _result("m1", 0.62, landmarks=[0], years=("2016",)))
    text = results_dev.document(cfg, results_dev.load_results(root), root)
    by_class = _section(text, "## Predicted against observed risk by sponsor class, landmark 0")
    assert "| Predicted, M0 | Predicted, M1 |" in _block(by_class, "Origin 2016, 12 months:")
    late = _block(by_class, "Origin 2017, 12 months:")
    assert "| Predicted, M0 |" in late
    assert "Predicted, M1" not in late
    deciles = _section(text, "## Predicted against observed risk by decile, landmark 0")
    assert "Rows, M1" not in _block(deciles, "Origin 2017, 24 months:")
    # No decile table in any result: no heading without a table under it.
    bare = {}
    for model, landmarks in (("m0", "all"), ("m1", [0])):
        bare[model] = _result(model, 0.54, landmarks=landmarks)
        for origin in bare[model]["origins"]:
            for by in origin["horizons"].values():
                for s in (by["pooled"], *by["by_landmark_index"].values()):
                    del s["calibration_table"]
    assert "by decile" not in results_dev.document(cfg, bare, tmp_path / "nothing")
    # A diagnosis file from before the run was logged with it (a bare list).
    (root / "diagnosis").mkdir()
    (root / "diagnosis" / "m0_calibration.json").write_text(
        json.dumps([{"origin": "2016-01-01", "horizons": {"24": {}}}]), encoding="utf-8"
    )
    with pytest.raises(RefusedError, match=r"older version of the diagnosis.*eval\.diagnosis"):
        results_dev.document(cfg, results_dev.load_results(root), root)


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


# Step 10: the ladder, the differences, the ablation, calibration, run time, tuning ---------------


def _ladder_result(
    model: str, auc: float, brier: float = 0.03, slope: float = 1.1
) -> dict[str, Any]:
    """A model that scores landmark indices 0 and 1, with numbers of its own everywhere."""
    result = _result(model, auc, tracking=_tracking(model))
    boosted = model.startswith("m4")
    for position, origin in enumerate(result["origins"]):
        for months, by in origin["horizons"].items():
            for s in (by["pooled"], *by["by_landmark_index"].values()):
                s["brier"]["estimate"] = brier
                s["calibration_slope"] = slope
                later = 0.02 if months == "24" else 0.0
                s["calibration_by_registration_year"] = [
                    {"group": str(2015 + position), "n": 600, "censored_before_horizon": 0.05,
                     "observed": 0.02 + later, "mean_predicted": auc / 20 + later},
                    {"group": str(2016 + position), "n": 400, "censored_before_horizon": 0.0,
                     "observed": 0.04 + later, "mean_predicted": auc / 10 + later},
                ]  # fmt: skip
        origin["model_summary"] = {
            "features": {"m2": 31, "m3": 47, "m4": 54, "m4-design": 31, "m4-amendments": 47,
                         "m4-text": 123}.get(model),
            "landmark_rows": 400_000 + 80_000 * position,
            "person_period_rows": 1_000_000 + 200_000 * position,
            "fit_seconds": 61.4 + position,
            "predict_seconds": 3.2,
            "model_bytes": 2_500_000 if boosted else 4_100,
            **({"rounds": 250 + position} if boosted else {"iterations": 45}),
        } if model != "m0" else None  # fmt: skip
    return result


def _pair(model: str, baseline: str, delta: float, indices: tuple[int, ...] = (0, 1),
          by_index: bool = False) -> dict[str, Any]:  # fmt: skip
    def interval(value: float) -> dict[str, float]:
        return {"estimate": value, "ci_low": value - 0.004, "ci_high": value + 0.004}

    def part(shift: float) -> dict[str, Any]:
        return {"delta_auc": interval(delta + shift), "delta_brier": interval(-delta / 100 - shift)}

    horizons = {}
    for months, later in (("12", 0.0), ("24", 0.001)):
        slices = {"0": part(later + 0.01)}
        if len(indices) > 1:
            slices["all"] = part(later)
        if by_index:
            slices["1"] = part(later + 0.02)
        horizons[months] = slices
    return {"model": model, "baseline": baseline, "what": f"what {model} adds",
            "landmark_indices": list(indices), "horizons": horizons}  # fmt: skip


def _numbers_per_origin(pair: dict[str, Any], made: dict[str, dict[str, Any]]) -> None:
    """What the comparison writes beside each difference: both models' AUC and Brier score at
    each origin. Here they are those of the made-up result files, as they must be."""
    for months, slices in pair["horizons"].items():
        for part, values in slices.items():
            values["by_origin"] = {}
            for position, day in enumerate(("2016-01-01", "2017-01-01")):
                cells: dict[str, float] = {}
                for role in ("model", "baseline"):
                    by = made[pair[role]]["origins"][position]["horizons"][months]
                    s = by["pooled"] if part == "all" else by["by_landmark_index"][part]
                    cells[f"auc_{role}"] = s["auc"]["estimate"]
                    cells[f"brier_{role}"] = s["brier"]["estimate"]
                values["by_origin"][day] = cells


@pytest.fixture
def step10(tmp_path: Path) -> tuple[Path, Path]:
    root, tuned = tmp_path / "results", tmp_path / "tuned"
    aucs = {"m0": 0.54, "m2": 0.60, "m3": 0.66, "m4": 0.70, "m4-design": 0.61,
            "m4-amendments": 0.67, "m4-text": 0.69}  # fmt: skip
    made = {model: _ladder_result(model, auc) for model, auc in aucs.items()}
    made["m1"] = _result("m1", 0.62, landmarks=[0], tracking=_tracking("m1"))
    for model, result in made.items():
        _write(root, model, result)
    pairs = [
        _pair("m2", "m0", 0.06, by_index=True),
        _pair("m3", "m2", 0.05, by_index=True),
        _pair("m4", "m1", 0.03, indices=(0,)),
        _pair("m4-amendments", "m4-design", 0.055),
        _pair("m4-text", "m4", 0.0005),
    ]
    for pair in pairs:
        _numbers_per_origin(pair, made)
    (root / "compare").mkdir()
    (root / "compare" / "dev.json").write_text(
        json.dumps(
            {
                "bootstrap_resamples": 1000,
                "pairs": pairs,
                "tracking": {**_tracking("m4"), "run_name": "compare-dev"},
            }
        ),
        encoding="utf-8",
    )
    tuned.mkdir()
    (tuned / "m4.json").write_text(
        json.dumps({"trials": 40, "trial_sample_share": 0.25, "default_auc": 0.6812,
                    "auc": 0.6904, "settings": {"learning_rate": 0.03125, "num_leaves": 63}}),
        encoding="utf-8",
    )  # fmt: skip
    (tuned / "m2.json").write_text(
        json.dumps({"trials": 40, "trial_sample_share": 0.25, "default_auc": 0.5911,
                    "auc": 0.5913, "settings": {"C": 0.0123456}}),
        encoding="utf-8",
    )  # fmt: skip
    (root / "tuning").mkdir()
    (root / "tuning" / "m4.json").write_text(
        json.dumps({"tracking": {**_tracking("m4", store="local"), "run_name": "tune-m4"}}),
        encoding="utf-8",
    )
    return root, tuned


def test_the_ladder_names_the_champion_by_the_declared_rule(step10: tuple[Path, Path]) -> None:
    root, tuned = step10
    cfg = load_project_config()
    results = results_dev.load_results(root)
    text = results_dev.document(cfg, results, root, tuned)
    ladder = _section(text, "## The ladder and the champion")
    assert (
        "| Model | AUC, 12 months, all landmark indices | AUC, 24 months, all landmark indices "
        "| AUC, 12 months, landmark 0 | AUC, 24 months, landmark 0 | Brier score, 12 months, all "
        "| Calibration slope, 12 months, all |"
    ) in ladder
    # Means over the two origins: 0.70 and 0.71 at 12 months, 0.1 lower at 24, 0.05 higher
    # at landmark 0.
    assert "| M4 | 0.7050 | 0.6050 | 0.7550 | 0.6550 | 0.03000 | 1.100 |" in ladder
    assert "| M0 | 0.5450 | 0.4450 | 0.5950 | 0.4950 | 0.03000 | 1.100 |" in ladder
    assert "| M1 | n/a | n/a | 0.6750 | 0.5750 | n/a | n/a |" in ladder  # landmark 0 only
    assert "M4-DESIGN" not in ladder  # the ablation rungs are not rungs of the ladder
    assert "the champion is **M4**" in ladder
    assert "among the rungs of the ladder that score every landmark index (M0, M2, M3 and M4)" in (
        ladder
    )
    assert "not rungs of the ladder and not candidates. The paired differences" in ladder
    assert results_dev.champion(results) == "m4"
    # The rule reads the AUC at 12 months over all landmark indices and no other AUC: a
    # model far ahead at 24 months and at landmark 0 is not the champion for that.
    contest = {
        "m2": _ladder_result("m2", 0.66),
        "m3": _ladder_result("m3", 0.68),
        "m4": _ladder_result("m4", 0.67),
    }
    for origin in contest["m4"]["origins"]:
        origin["horizons"]["24"]["pooled"]["auc"]["estimate"] = 0.90
        for months in ("12", "24"):
            origin["horizons"][months]["by_landmark_index"]["0"]["auc"]["estimate"] = 0.95
    assert results_dev.champion(contest) == "m3"
    # The mean over the origins, not the first origin: M3 leads in 2016, M4 on the mean.
    split = {"m3": _ladder_result("m3", 0.66), "m4": _ladder_result("m4", 0.66)}
    for model, by_year in (("m3", (0.70, 0.64)), ("m4", (0.69, 0.67))):
        for origin, value in zip(split[model]["origins"], by_year, strict=True):
            origin["horizons"]["12"]["pooled"]["auc"]["estimate"] = value
    assert results_dev.champion(split) == "m4"
    # Ties in AUC go to the lower Brier score, then to the slope closest to 1 (1.05 against
    # 0.80, which is smaller but further away); a model of landmark 0 only is no candidate,
    # whatever its AUC.
    tied = {
        "m2": _ladder_result("m2", 0.70, brier=0.031, slope=1.0),
        "m3": _ladder_result("m3", 0.70, brier=0.030, slope=0.80),
        "m4": _ladder_result("m4", 0.70, brier=0.030, slope=1.05),
        "m1": _result("m1", 0.99, landmarks=[0]),
    }
    assert results_dev.champion(tied) == "m4"
    del tied["m4"]
    assert results_dev.champion(tied) == "m3"  # the lower Brier score, against a slope of 1
    assert results_dev.champion({"m0": tied["m2"]}) is None  # nothing to choose between
    # An ablation rung that scores higher on the rule's own number is named beside the
    # champion: it is no candidate, and the reader must see it. (The made-up comparison is
    # removed first: it was computed from the other scores of that rung.)
    (root / "compare" / "dev.json").unlink()
    higher = dict(results)
    higher["m4-text"] = _ladder_result("m4-text", 0.71)
    said = _section(
        results_dev.document(cfg, higher, root, tuned), "## The ladder and the champion"
    )
    assert "the champion is **M4**" in said
    assert "M4-TEXT has a higher mean AUC at 12 months (0.7150 against 0.7050)" in said
    assert "M4-DESIGN has a higher" not in said


def test_a_comparison_made_from_other_scores_is_refused(step10: tuple[Path, Path]) -> None:
    """The comparison file holds each model's AUC and Brier score at each origin. They must
    be the numbers of the result files: if a model was run again after the comparison, the
    differences are between new and old scores, and the document is not written. A cell that
    cannot be checked refuses the document too."""
    root, tuned = step10
    cfg = load_project_config()
    results = results_dev.load_results(root)
    path = root / "compare" / "dev.json"
    compared = json.loads(path.read_text(encoding="utf-8"))

    def written(change: tuple[int, str, str, str, str, float] | None = None) -> str:
        changed = json.loads(json.dumps(compared))
        if change is not None:
            pair, months, part, day, cell, by = change
            changed["pairs"][pair]["horizons"][months][part]["by_origin"][day][cell] += by
        path.write_text(json.dumps(changed), encoding="utf-8")
        return results_dev.document(cfg, results, root, tuned)

    assert "## Differences between models, with paired intervals" in written()
    # The last digits of two computations may differ; nothing else may.
    assert written((1, "24", "0", "2017-01-01", "brier_baseline", 1e-12))
    nan = float("nan")
    cases = [
        ((1, "24", "0", "2017-01-01", "brier_baseline", 1e-6),
         "result file of M2 (origin 2017-01-01, 24 months, landmark 0: brier"),
        ((2, "12", "0", "2016-01-01", "auc_model", -1e-6),
         "result file of M4 (origin 2016-01-01, 12 months, landmark 0: auc"),
        ((2, "12", "0", "2016-01-01", "auc_baseline", 3e-8),  # small, and still shown
         "result file of M1 (origin 2016-01-01, 12 months, landmark 0: auc"),
        ((0, "12", "all", "2017-01-01", "auc_baseline", 1e-6),
         "result file of M0 (origin 2017-01-01, 12 months, all landmark indices: auc"),
        ((4, "24", "all", "2016-01-01", "brier_model", -1e-6),
         "result file of M4-TEXT (origin 2016-01-01, 24 months, all landmark indices: brier"),
        # A landmark index other than 0, and an ablation rung: every slice of every pair.
        ((1, "12", "1", "2016-01-01", "auc_model", 1e-6),
         "result file of M3 (origin 2016-01-01, 12 months, landmark 1: auc"),
        ((3, "12", "all", "2017-01-01", "auc_baseline", 1e-6),
         "result file of M4-DESIGN (origin 2017-01-01, 12 months, all landmark indices: auc"),
        # A cell that is not a number is not "within the tolerance".
        ((0, "24", "all", "2016-01-01", "auc_model", nan),
         "result file of M2 (origin 2016-01-01, 24 months, all landmark indices: auc nan in"),
    ]  # fmt: skip
    for change, message in cases:
        with pytest.raises(RefusedError) as refused:
            written(change)
        said = str(refused.value)
        assert message in said
        assert "uv run python -m trialpulse.eval.compare" in said
        shown = re.search(r": \w+ (\S+) in the comparison, (\S+) in the result file", said)
        assert shown is not None
        assert shown.group(1) != shown.group(2)  # the message shows the difference it found
    assert written()  # and the comparison as it was is accepted again
    # A model of the comparison without a result file, and a slice no result file holds.
    without = {name: result for name, result in results.items() if name != "m3"}
    with pytest.raises(RefusedError) as refused:
        results_dev.document(cfg, without, root, tuned)
    assert "compares M3 at origin 2016-01-01 (12 months, landmark 0)" in str(refused.value)
    assert "no result file holds that slice" in str(refused.value)
    shorter = json.loads(json.dumps(results))
    del shorter["m2"]["origins"][1]["horizons"]["24"]["by_landmark_index"]["1"]
    with pytest.raises(RefusedError) as refused:
        results_dev.document(cfg, shorter, root, tuned)
    assert "compares M2 at origin 2017-01-01 (24 months, landmark 1)" in str(refused.value)
    # A comparison that holds no numbers per origin cannot be checked at all.
    bare = json.loads(json.dumps(compared))
    del bare["pairs"][4]["horizons"]["24"]["all"]["by_origin"]
    path.write_text(json.dumps(bare), encoding="utf-8")
    with pytest.raises(RefusedError) as refused:
        results_dev.document(cfg, results, root, tuned)
    assert "no numbers per origin for M4-TEXT minus M4 (24 months, all landmark indices)" in (
        str(refused.value)
    )


def test_differences_ablation_and_calibration_tables_read_their_cells(
    step10: tuple[Path, Path],
) -> None:
    root, tuned = step10
    cfg = load_project_config()
    text = results_dev.document(cfg, results_dev.load_results(root), root, tuned)
    differences = _section(text, "## Differences between models, with paired intervals")
    assert "1,000 resamples" in differences
    assert (
        "| M3 minus M2 | the amendment signals | +0.0500 (+0.0460 to +0.0540) | +0.0600 "
        "(+0.0560 to +0.0640) | +0.0510 (+0.0470 to +0.0550) | +0.0610 (+0.0570 to +0.0650) | "
        "-0.00050 (-0.00450 to +0.00350) |"
    ) in differences
    # M4 against M1 exists at landmark 0 only: the other cells stay empty.
    assert (
        "| M4 minus M1 | the dynamic model against the static one, at registration |  | "
        "+0.0400 (+0.0360 to +0.0440) |  | +0.0410 (" in differences
    )
    by_index = differences.split("Difference in AUC at 12 months, by landmark index:")[1]
    assert "| Landmark index | M2 minus M0 | M3 minus M2 |" in by_index
    assert "| 1 | +0.0800 (+0.0760 to +0.0840) | +0.0700 (+0.0660 to +0.0740) |" in by_index
    assert "| 0 | +0.0700 (+0.0660 to +0.0740) | +0.0600 (+0.0560 to +0.0640) |" in by_index
    assert "M4 minus M1" not in by_index
    later = differences.split("Difference in AUC at 24 months, by landmark index:")[1]
    assert "| 1 | +0.0810 (+0.0770 to +0.0850) | +0.0710 (+0.0670 to +0.0750) |" in later
    ablation = _section(text, "## Ablation of M4")
    rows = [line for line in ablation.splitlines() if line.startswith("| M4")]
    assert [row.split(" | ")[0] for row in rows] == [
        "| M4-DESIGN", "| M4-AMENDMENTS", "| M4", "| M4-TEXT",
    ]  # fmt: skip
    assert rows[0].endswith("| 31 | 0.6150 | 0.5150 | 0.03000 |  |")
    assert rows[1].endswith("| 47 | 0.6750 | 0.5750 | 0.03000 | +0.0550 (+0.0510 to +0.0590) |")
    assert rows[2].endswith("| 54 | 0.7050 | 0.6050 | 0.03000 |  |")  # that pair was not compared
    assert rows[3].endswith("| 123 | 0.6950 | 0.5950 | 0.03000 | +0.0005 (-0.0035 to +0.0045) |")
    # Calibration over all landmark indices, by sponsor class and by registration year: the
    # models of every landmark index only, each with its own prediction.
    pooled = _section(
        text, "## Predicted against observed risk by sponsor class, all landmark indices"
    )
    assert (
        "| Sponsor class | Rows | Observed (IPCW) | Censored before the horizon | Predicted, M0 "
        "| Predicted, M2 | Predicted, M3 | Predicted, M4 |"
    ) in pooled
    assert "M1" not in pooled
    by_year = _section(
        text, "## Predicted against observed risk by registration year, all landmark indices"
    )
    block = _block(by_year, "Origin 2016, 12 months:")
    assert (
        "| Registration year | Rows | Observed (IPCW) | Censored before the horizon | "
        "Predicted, M0 | Predicted, M2 | Predicted, M3 | Predicted, M4 |"
    ) in block
    assert "| 2015 | 600 | 2.00% | 5.0% | 2.70% | 3.00% | 3.30% | 3.50% |" in block
    assert "| 2016 | 400 | 4.00% | 0.0% | 5.40% | 6.00% | 6.60% | 7.00% |" in block
    assert "| 2017 | 400 | 6.00% | 0.0% | 7.40% | 8.00% | 8.60% | 9.00% |" in _block(
        by_year, "Origin 2017, 24 months:"
    )
    assert "Origin 2016, 24 months:" in by_year


def test_run_time_tuning_and_runs_are_listed(step10: tuple[Path, Path]) -> None:
    root, tuned = step10
    cfg = load_project_config()
    text = results_dev.document(cfg, results_dev.load_results(root), root, tuned)
    runtime = _section(text, "## Run time and size")
    assert "| M4 | 2017 | 54 | 480,000 | 1,200,000 | 62 | 3 | 2.5 MB | 251 |" in runtime
    assert "| M2 | 2016 | 31 | 400,000 | 1,000,000 | 61 | 3 | 4 kB | 45 |" in runtime
    assert "| M0 |" not in runtime  # M0 and M1 report no fit of this kind
    assert "| M1 |" not in runtime
    # A linear fit that stopped at its iteration limit says so in its row, and only there.
    stopped = json.loads((root / "walkforward" / "m3_dev.json").read_text(encoding="utf-8"))
    stopped["origins"][0]["model_summary"]["converged"] = False
    stopped["origins"][1]["model_summary"]["converged"] = True
    _write(root, "m3", stopped)
    again = results_dev.document(cfg, results_dev.load_results(root), root, tuned)
    rows = [line for line in _section(again, "## Run time and size").splitlines()
            if line.startswith("| M3 |")]  # fmt: skip
    assert rows[0].endswith("| 4 kB | 45 (the limit: not converged) |")
    assert rows[1].endswith("| 4 kB | 45 |")
    assert again.count("not converged") == 1
    tuning = _section(text, "## Tuning")
    assert (
        "| M4 | 40 | 25% | 0.6812 | 0.6904 | `learning_rate` 0.03125, `num_leaves` 63 |" in tuning
    )
    assert "| M2 | 40 | 25% | 0.5911 | 0.5913 | `C` 0.01235 |" in tuning
    assert "| M3 |" not in tuning  # not tuned yet: no file
    # Without a folder handed in, the document reads the folder the models read, which in
    # the tests is an empty one (tests/conftest.py), never the repository's config/tuned.
    assert "## Tuning" not in results_dev.document(cfg, results_dev.load_results(root), root)
    runs = _section(text, "## Runs")
    assert f"| Paired differences between models | {REMOTE} | `compare-dev` in " in runs
    assert f"| Tuning of M4 | {LOCAL} | `tune-m4` in " in runs
    assert "| Tuning of M2 |" not in runs  # no study file for it
    assert "| M4-TEXT, development origins |" in runs
    assert NOT_EVERY_RUN in runs
    # Without the ladder there is nothing to write.
    with pytest.raises(RefusedError, match="no development result of a model of the ladder"):
        results_dev.document(cfg, {"m4-design": _ladder_result("m4-design", 0.6)}, root, tuned)
