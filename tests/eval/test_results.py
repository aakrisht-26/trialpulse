"""docs/results.md from the result files of the locked run (Step 11), on made-up result files.
No locked origin is evaluated: the files are written here as an unlocked run would write
them."""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from trialpulse.cli import RefusedError
from trialpulse.config import load_project_config
from trialpulse.eval import results

COMMIT, TAG_OBJECT = "c" * 40, "t" * 40
UNLOCK = {
    "tag": "prereg-v1",
    "tag_object": TAG_OBJECT,
    "commit": COMMIT,
    "registered": "2026-11-01",
    "remote": "origin",
}


def _tracking(name: str, commit: str = COMMIT, dirty: str = "no") -> dict[str, str]:
    return {
        "run_name": name,
        "run_id": f"{abs(hash(name)):032x}"[:32],
        "git_commit": commit,
        "git_dirty": dirty,
        "store": "mlflow",
        "store_description": "the server",
        "experiment": "x",
    }


MAIN_INPUTS = {
    "landmarks": results.relative(results.LANDMARKS_PATH),
    "training_dir": results.relative(results.TRAINING_DIR),
    "features_dir": results.relative(results.FEATURES_DIR),
    "training_features_dir": results.relative(results.FEATURES_DIR),
    "predictions_dir": results.relative(results.PREDICTIONS_DIR),
    "cohort_variant": None,
}
VARIANT_INPUTS = {
    **MAIN_INPUTS,
    "landmarks": results.relative(results.VARIANT_COHORT_DIR / results.LANDMARKS_PATH.name),
    "features_dir": "data/features_unknown_as_stop",
    "predictions_dir": results.relative(results.PREDICTIONS_DIR / "unknown-as-stop"),
    "cohort_variant": "unknown-as-stop",
}


def _sha(model: str, year: str) -> str:
    return hashlib.sha256(f"{model} {year}".encode()).hexdigest()


def _scores(model: str, year: str) -> dict[str, str]:
    """A scores file as the walk-forward run of a model and origin records it."""
    return {"path": f"{model}_{year}/origin_{year}-01-01.parquet", "sha256": _sha(model, year)}


def _read_by(models: tuple[str, ...], years: tuple[str, ...]) -> list[dict[str, str]]:
    return [{"model": m, "origin": f"{y}-01-01", **_scores(m, y)} for m in models for y in years]


def _interval(value: float, half: float = 0.01) -> dict[str, float]:
    return {"estimate": value, "ci_low": value - half, "ci_high": value + half}


def _pooled(auc: float, slope: float, n: int = 50_000) -> dict[str, Any]:
    return {
        "n_rows": n,
        "auc": _interval(auc),
        "brier": _interval(0.03, 0.001),
        "lift": _interval(3.1, 0.2),
        "calibration_slope": slope,
        "calibration_intercept": 0.05,
        "single_censoring_curve": {
            "auc": auc - 0.005,
            "brier": 0.0302,
            "lift": 3.05,
            "calibration_slope": slope + 0.01,
            "calibration_intercept": 0.04,
        },
        "calibration_table": [
            {"bin": b, "n": n // 10, "mean_predicted": 0.004 * b, "observed_cif": 0.0038 * b}
            for b in range(1, 11)
        ],
    }


def _walkforward(model: str, year: str, auc: float, slope: float, **extra: Any) -> dict[str, Any]:
    by_index = {str(k): _pooled(auc - 0.01 * k, slope, 5000) for k in range(7)}
    horizons = {
        m: {
            "pooled": _pooled(
                auc - (0.015 if m == "24" else 0), slope - (0.04 if m == "24" else 0)
            ),
            "by_landmark_index": by_index,
        }
        for m in ("12", "24")
    }
    variant = extra.get("variant") == "unknown-as-stop"
    return {
        "model": model,
        "origins_spec": year,
        "bootstrap_resamples": 1000,
        "unlock": UNLOCK,
        "origins": [
            {
                "origin": f"{year}-01-01",
                "role": "stress" if year == "2020" else "test",
                "predictions": _scores(model, f"{year} variant" if variant else year),
                "horizons": horizons,
            }
        ],
        "inputs": VARIANT_INPUTS if variant else MAIN_INPUTS,
        "tracking": _tracking(f"{model}-{year}"),
        **extra,
    }


def _write(path: Path, content: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content), encoding="utf-8")


PREDICTIONS = [
    {
        "id": "h1_delta_auc_12",
        "statement": "M4 minus M1, landmark 0, 12 months",
        "predicted": 0.007,
        "low": -0.02,
        "high": 0.02,
    },
    {
        "id": "h1_delta_auc_24",
        "statement": "M4 minus M1, landmark 0, 24 months",
        "predicted": 0.009,
        "low": 0.0,
        "high": 0.03,
        "low_exclusive": True,
    },
    {
        "id": "h2_slope_12",
        "statement": "slope of M3 minus M2 per landmark index",
        "predicted": 0.005,
        "low": 0.0,
        "high": 0.012,
        "low_exclusive": True,
    },
    {
        "id": "h3_difference_12",
        "statement": "operational minus scientific AUC",
        "predicted": 0.07,
        "low": 0.02,
        "high": 0.13,
    },
    {
        "id": "h5_difference",
        "statement": "2020 minus 2018 and 2019",
        "predicted": -0.025,
        "low": -0.08,
        "high": -0.005,
    },
    {
        "id": "auc_12_2018",
        "statement": "champion AUC, 12 months, 2018",
        "predicted": 0.725,
        "low": 0.7,
        "high": 0.75,
    },
    {
        "id": "auc_12_2019",
        "statement": "champion AUC, 12 months, 2019",
        "predicted": 0.725,
        "low": 0.7,
        "high": 0.75,
    },
    {
        "id": "brier_12_2019",
        "statement": "champion Brier, 12 months, 2019",
        "predicted": 0.031,
        "low": 0.024,
        "high": 0.038,
    },
]


def _registration(predictions: list[dict[str, Any]] | None = None) -> str:
    block = json.dumps({"predictions": PREDICTIONS if predictions is None else predictions})
    return f"# Pre-registration\n\nRegistered: 2026-11-01\n\n```json predictions\n{block}\n```\n"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A complete locked run. The 2019 AUC (0.755) is outside its registered range, and the
    2018 calibration slope at 24 months (0.78) breaks H4."""
    base = tmp_path / "results"
    aucs = {"m0": 0.55, "m1": 0.70, "m2": 0.66, "m3": 0.70, "m4": 0.73}
    for model, auc in aucs.items():
        for year in ("2018", "2019"):
            value = 0.755 if (model, year) == ("m4", "2019") else auc
            _write(
                base / "walkforward" / f"{model}_{year}.json",
                _walkforward(model, year, value, 0.82 if year == "2018" else 0.9),
            )
    _write(base / "walkforward" / "m4_2020.json", _walkforward("m4", "2020", 0.70, 0.85))
    for year in ("2018", "2019"):
        _write(
            base / "walkforward" / "unknown-as-stop" / f"m4_{year}.json",
            _walkforward("m4", year, 0.71, 0.8, variant="unknown-as-stop"),
        )
    pair = {
        "model": "m4",
        "baseline": "m1",
        "horizons": {
            m: {"0": {"delta_auc": _interval(d)}} for m, d in (("12", 0.004), ("24", 0.012))
        },
    }
    pair["inputs"] = _read_by(("m4", "m1"), ("2018", "2019"))
    common = {
        "unlock": UNLOCK,
        "bootstrap_resamples": 1000,
        "predictions_dir": results.relative(results.PREDICTIONS_DIR),
    }
    _write(
        base / "compare" / "test" / "m4__m1.json",
        {"pairs": [pair], **common, "tracking": _tracking("compare-m4-vs-m1-test")},
    )
    trend = {
        m: {"slope": _interval(0.006, 0.003), "late_minus_early": _interval(0.02)}
        for m in ("12", "24")
    }
    pair = {
        "model": "m3",
        "baseline": "m2",
        "trend": trend,
        "horizons": {
            m: {"all": {"delta_auc": _interval(0.04)}, "0": {"delta_auc": _interval(0.02)}}
            for m in ("12", "24")
        },
    }
    pair["inputs"] = _read_by(("m3", "m2"), ("2018", "2019"))
    _write(
        base / "compare" / "test" / "m3__m2.json",
        {"pairs": [pair], **common, "tracking": _tracking("compare-m3-vs-m2-test")},
    )
    by_origin = {
        f"{y}-01-01": {
            "cases": {"operational": 1800, "scientific": 210, "other": 400, "none": 300},
            "operational": 0.74,
            "scientific": 0.67,
            "intervals": {
                "operational": _interval(0.74),
                "scientific": _interval(0.67),
                "difference": _interval(0.07, 0.05),
            },
        }
        for y in ("2018", "2019")
    }
    _write(
        base / "hypotheses" / "h3-test.json",
        {
            "horizons": {
                m: {
                    "operational": _interval(0.74),
                    "scientific": _interval(0.67),
                    "difference": _interval(0.07 - (0.01 if m == "24" else 0), 0.04),
                    "by_origin": by_origin,
                }
                for m in ("12", "24")
            },
            "model": "m4",
            "origins_spec": "test",
            "inputs": _read_by(("m4",), ("2018", "2019")),
            **common,
            "tracking": _tracking("h3-test"),
        },
    )
    _write(
        base / "hypotheses" / "h5.json",
        {
            "difference": _interval(-0.03),
            "model": "m4",
            "inputs": _read_by(("m4",), ("2018", "2019", "2020")),
            **common,
            "tracking": _tracking("h5"),
        },
    )
    return base


def test_the_document_holds_every_registered_prediction_against_its_value(root: Path) -> None:
    run = results.load_run(root)
    text = results.document(run, _registration(), load_project_config())
    assert text.startswith("# Results on the locked origins\n\nResearch demo. Not medical advice.")
    assert "registered on 2026-11-01, tag `prereg-v1` (tag object `tttttttttttt`)" in text
    table = text.split("## The registered predictions against the observed values")[1]
    table = table.split("## Every model on the test origins")[0]
    # Primary claims in bold; the 2019 AUC of 0.755 is outside 0.7 to 0.75.
    assert "| **auc_12_2018** |" in table
    assert "| **h2_slope_12** |" in table
    row = next(line for line in table.splitlines() if line.startswith("| **auc_12_2019**"))
    assert row.endswith("| **falsified**: outside the range |")
    row = next(line for line in table.splitlines() if line.startswith("| h1_delta_auc_24"))
    assert "| above 0 to +0.03 |" in row
    assert row.endswith("| within the range |")
    assert "Falsified predictions: auc_12_2019." in table
    # H4: the 2018 slope at 24 months is 0.82 - 0.04 = 0.78, below 0.8.
    assert "| H4 | **no** | slopes 0.820, 0.900, 0.780, 0.860 against [0.8, 1.2] |" in table
    assert "| H1 | yes | absolute difference 0.0040 < 0.02 at 12 months, and +0.0120 > 0" in table
    assert "Hypotheses whose condition failed: H4." in table
    # The other sections read their files.
    assert "## Sensitivity: UNKNOWN as an early stop" in text
    assert (
        "| 2018 | 12 | 1,800 | 0.740 (0.730 to 0.750) | 210 | 0.670 (0.660 to 0.680) | "
        "+0.070 (+0.020 to +0.120) |" in text
    )
    # H3 over both origins at each horizon, with its interval (ADR 0028).
    assert (
        "| 2018 and 2019 (mean) | 24 |  | 0.740 (0.730 to 0.750) |  | 0.670 (0.660 to 0.680) "
        "| +0.060 (+0.020 to +0.100) |" in text
    )
    # The UNKNOWN analysis has the lift (ADR 0029); the single-curve check and H2's
    # secondary statistics are reported (ADR 0017, section H2 of the registration).
    unknown = text.split("## Sensitivity: UNKNOWN as an early stop")[1].split("\n## ")[0]
    assert "| Lift, main | Lift, variant |" in unknown
    assert "ADRs 0014 and 0029" in unknown
    single = text.split("## Sensitivity: one censoring curve for all rows")[1].split("\n## ")[0]
    assert "| 2018 | 12 | 0.730 (0.720 to 0.740) | 0.725 |" in single
    trend = text.split("## The trend over landmark indices (H2)")[1].split("\n## ")[0]
    assert (
        "| M3 minus M2 | 24 | +0.0060 (+0.0030 to +0.0090) | +0.0200 (+0.0100 to +0.0300) |"
        in trend
    )
    assert "H5: the AUC at 12 months minus the mean of 2018 and 2019 is -0.0300" in text
    assert "| M3 minus M2 | +0.0400 (+0.0300 to +0.0500) |" in text
    runs = text.split("## Runs")[1]
    assert "walkforward/unknown-as-stop/m4_2018.json" in runs
    assert "hypotheses/h5.json" in runs


def test_a_run_of_more_than_one_clean_commit_is_refused(root: Path) -> None:
    def changed(relative: str, **tracking: str) -> None:
        path = root / relative
        content = json.loads(path.read_text(encoding="utf-8"))
        content["tracking"].update(tracking)
        _write(path, content)

    run = results.load_run(root)
    assert results.check_one_commit(run.files)["commit"] == COMMIT
    changed("compare/test/m4__m1.json", git_dirty="yes")
    with pytest.raises(RefusedError, match=r"m4__m1\.json: run with uncommitted changes"):
        results.check_one_commit(results.load_run(root).files)
    changed("compare/test/m4__m1.json", git_dirty="no", git_commit="d" * 40)
    with pytest.raises(RefusedError) as refused:
        results.check_one_commit(results.load_run(root).files)
    assert "not from the tagged commit" in str(refused.value)
    assert "the files name 2 commits" in str(refused.value)


def test_files_unlocked_by_different_tags_or_not_logged_are_refused(root: Path) -> None:
    path = root / "hypotheses" / "h5.json"
    content = json.loads(path.read_text(encoding="utf-8"))
    _write(path, {**content, "unlock": {**UNLOCK, "tag_object": "u" * 40}})
    with pytest.raises(RefusedError, match="the files name 2 unlocks"):
        results.check_one_commit(results.load_run(root).files)
    _write(path, {k: v for k, v in content.items() if k != "tracking"})
    with pytest.raises(RefusedError, match=r"h5\.json: not logged"):
        results.check_one_commit(results.load_run(root).files)


def test_an_incomplete_run_names_the_commands_that_complete_it(root: Path) -> None:
    (root / "walkforward" / "m4_2020.json").unlink()
    (root / "hypotheses" / "h3-test.json").unlink()
    with pytest.raises(RefusedError) as refused:
        results.load_run(root)
    said = str(refused.value)
    assert "walkforward --model m4 --origins 2020 --unlock-test" in said
    assert "hypotheses h3 --origins test --unlock-test" in said


def test_the_predictions_come_from_the_registered_block_only(root: Path) -> None:
    run = results.load_run(root)
    cfg = load_project_config()
    with pytest.raises(RefusedError, match="holds no ```json predictions block"):
        results.document(run, "# Pre-registration\n\nRegistered: 2026-11-01\n", cfg)
    with pytest.raises(RefusedError, match="registered twice"):
        results.document(run, _registration([*PREDICTIONS, PREDICTIONS[0]]), cfg)
    unknown = [{"id": "auc_36_2018", "statement": "x", "predicted": 0.7, "low": 0.6, "high": 0.8}]
    with pytest.raises(RefusedError, match="cannot compute"):
        results.document(run, _registration(unknown), cfg)


def test_a_range_with_an_open_end_holds_what_it_says() -> None:
    above = results.Prediction("x", "x", 0.009, 0.0, 0.03, low_exclusive=True)
    assert not above.holds(0.0)
    assert above.holds(1e-9)
    assert above.holds(0.03)
    assert not above.holds(0.0301)
    closed = results.Prediction("y", "y", 0.725, 0.7, 0.75)
    assert closed.holds(0.7)
    assert closed.holds(0.75)
    assert closed.range_text() == "0.7 to 0.75"


@pytest.mark.slow  # over half a second; CI runs every test
def test_the_command_writes_the_document_and_its_calibration_figures(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def registration(commit: str, repo: Path = results.REPO_ROOT) -> str:
        seen.append(commit)
        return _registration()

    monkeypatch.setattr(results, "registration_at", registration)
    doc, figures = tmp_path / "results.md", tmp_path / "figures"
    args = ["--results", str(root), "--doc", str(doc), "--figures", str(figures)]
    assert results.main(args) == 0
    assert seen == [COMMIT]  # the registration as committed with the tag, not the working tree
    text = doc.read_text(encoding="utf-8")
    assert "![results_calibration_12m](figures/results_calibration_12m.png)" in text
    assert sorted(p.name for p in figures.iterdir()) == [
        "results_calibration_12m.png",
        "results_calibration_24m.png",
    ]
    assert results.main(args) == 0  # a second run finds the same document


def test_the_statistics_a_prediction_can_name_are_those_computed(root: Path) -> None:
    assert set(results.observed(results.load_run(root))) == results.statistic_ids()


def test_the_draft_registration_names_computable_predictions_and_passes_the_text_check() -> None:
    """docs/preregistration.md as committed: its block parses, every prediction names a
    statistic the generator computes, every primary claim is registered, and once the
    registration commit replaces the status line with a Registered line, the lock finds no
    placeholder text (ADR 0004)."""
    from trialpulse.eval.lock import find_placeholders

    text = (results.REPO_ROOT / "docs" / "preregistration.md").read_text(encoding="utf-8")
    predictions = results.registered_predictions(text)
    assert set(predictions) <= results.statistic_ids()
    assert set(results.PRIMARY) <= set(predictions)
    for prediction in predictions.values():
        assert prediction.low is None or prediction.high is None or prediction.low < prediction.high
        assert prediction.holds(prediction.predicted), prediction.id
    if "Status: draft, not yet registered." in text:
        # What the registration commit does (section 4, item 4): the status line becomes a
        # Registered line and the draft note goes. Forgetting the note fails the lock.
        registered = text.replace("Status: draft, not yet registered.", "Registered: 2026-11-01")
        assert find_placeholders(registered) != []
        note = re.search(r"^\*\*Draft for Aakrisht's review.*\n\n", registered, re.MULTILINE)
        assert note is not None
        assert find_placeholders(registered.replace(note.group(0), "")) == []


def _edit(path: Path, change: Any) -> None:
    content = json.loads(path.read_text(encoding="utf-8"))
    change(content)
    _write(path, content)


def test_a_prediction_also_falls_with_the_condition_of_its_hypothesis(root: Path) -> None:
    """Section 1 of the registration: falsified outside its range, or when the condition of
    its hypothesis fails. H3's difference inside its range with an interval reaching below 0."""

    def weak(content: dict[str, Any]) -> None:
        content["horizons"]["12"]["difference"] = {
            "estimate": 0.05,
            "ci_low": -0.01,
            "ci_high": 0.11,
        }

    _edit(root / "hypotheses" / "h3-test.json", weak)
    text = results.document(results.load_run(root), _registration(), load_project_config())
    row = next(line for line in text.splitlines() if line.startswith("| h3_difference_12"))
    assert row.endswith("| **falsified**: the condition of H3 failed |")
    assert "Falsified predictions: h3_difference_12, auc_12_2019." in text


@pytest.mark.parametrize(
    ("relative_path", "change", "message"),
    [
        ("compare/test/m3__m2.json",
         lambda c: c["pairs"][0]["inputs"][0].update(sha256="0" * 64),
         "not the scores of the walk-forward result of m3 at 2018-01-01"),
        ("hypotheses/h3-test.json",
         lambda c: c.update(predictions_dir="data/results/walkforward/predictions/unknown-as-stop"),
         "not the main folder"),
        ("hypotheses/h3-test.json", lambda c: c.update(model="m3"), "not of the champion"),
        ("hypotheses/h5.json", lambda c: c.update(model="m2"), "not of the champion"),
        ("compare/test/m3__m2.json", lambda c: c.update(bootstrap_resamples=50),
         "50 bootstrap resamples, not the registered 1000"),
        ("compare/test/m3__m2.json",
         lambda c: c["pairs"][0]["trend"]["12"]["slope"].update(resamples=50),
         "intervals from ['50'] resamples"),
        ("walkforward/m4_2018.json",
         lambda c: c["inputs"].update(cohort_variant="unknown-as-stop"),
         "did not read the main inputs"),
        ("walkforward/unknown-as-stop/m4_2019.json",
         lambda c: c.update(inputs=MAIN_INPUTS),
         "did not read the UNKNOWN variant's inputs"),
    ],
)  # fmt: skip
def test_files_that_did_not_read_what_the_run_wrote_are_refused(
    root: Path, relative_path: str, change: Any, message: str
) -> None:
    _edit(root / relative_path, change)
    with pytest.raises(RefusedError, match="do not hold together") as refused:
        results.document(results.load_run(root), _registration(), load_project_config())
    assert message in str(refused.value)


def test_a_refused_document_leaves_no_figure_behind(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(results, "registration_at", lambda commit, repo=None: "# no block\n")
    figures = tmp_path / "figures"
    args = ["--results", str(root), "--doc", str(tmp_path / "r.md"), "--figures", str(figures)]
    with pytest.raises(RefusedError, match="no ```json predictions block"):
        results.main(args)
    assert not figures.exists()
    assert not (tmp_path / "r.md").exists()
