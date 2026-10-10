"""docs/results_dev.md is written from result files only, and only from development ones.

The result files here are made up so that every cell of every table has a value of its own,
and so that the two horizons, the two origins and the two models differ everywhere: a number
printed in the wrong column, or taken from the wrong model, origin or horizon, changes a
line that a test reads in full."""

import json
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
            predicted = PREDICTED[model] + shift + (0.02 if months == "24" else 0.0)
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
    assert results_dev.main(["--results", str(root), "--doc", str(doc)]) == 0
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
    assert results_dev.main(["--results", str(root), "--doc", str(doc)]) == 0
    assert "up to date" in capsys.readouterr().out


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
