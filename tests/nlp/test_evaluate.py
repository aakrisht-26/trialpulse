"""Gold scoring on synthetic labels: every metric against scikit-learn, the bootstrap
interval, alignment, and the once-only rule for test scoring."""

import random
from pathlib import Path

import pytest
from sklearn.metrics import cohen_kappa_score, confusion_matrix, f1_score

from trialpulse.nlp.evaluate import (
    aligned,
    cohen_kappa,
    dev_errors,
    read_predictions,
    score,
    score_test_once,
    summary_lines,
    write_predictions,
)
from trialpulse.nlp.taxonomy import LABELS


def _labels(n: int, seed: int, noise: float) -> tuple[list[str], list[str]]:
    rng = random.Random(seed)
    reference = [rng.choice(LABELS) for _ in range(n)]
    predicted = [rng.choice(LABELS) if rng.random() < noise else r for r in reference]
    return reference, predicted


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_metrics_match_scikit_learn(seed: int) -> None:
    reference, predicted = _labels(300, seed, noise=0.3)
    result = score(reference, predicted, resamples=200, confidence=0.95, seed=seed)
    assert result["accuracy"] == pytest.approx(
        sum(r == p for r, p in zip(reference, predicted, strict=True)) / 300, abs=1e-4
    )
    assert result["macro_f1"] == pytest.approx(
        f1_score(reference, predicted, average="macro"), abs=1e-4
    )
    assert result["kappa"] == pytest.approx(cohen_kappa_score(reference, predicted), abs=1e-4)
    expected = f1_score(reference, predicted, average=None, labels=list(LABELS), zero_division=0)
    assert [result["per_label"][k]["f1"] for k in LABELS] == pytest.approx(list(expected), abs=1e-4)
    assert (
        result["confusion"]["matrix"]
        == confusion_matrix(reference, predicted, labels=list(LABELS)).tolist()
    )


def test_macro_f1_averages_over_the_labels_that_occur() -> None:
    reference = ["accrual", "accrual", "funding", "funding"]
    predicted = ["accrual", "funding", "funding", "funding"]
    result = score(reference, predicted, resamples=50, confidence=0.95, seed=0)
    assert result["macro_f1"] == pytest.approx(
        f1_score(reference, predicted, average="macro"), abs=1e-4
    )


def test_perfect_predictions() -> None:
    reference, _ = _labels(100, 7, noise=0)
    result = score(reference, reference, resamples=100, confidence=0.95, seed=0)
    assert result["accuracy"] == result["macro_f1"] == result["kappa"] == 1.0
    assert result["macro_f1_ci"] == [1.0, 1.0]


def test_the_bootstrap_interval_is_seeded_and_brackets_the_estimate() -> None:
    reference, predicted = _labels(300, 5, noise=0.4)
    first = score(reference, predicted, resamples=300, confidence=0.95, seed=42)
    again = score(reference, predicted, resamples=300, confidence=0.95, seed=42)
    lo, hi = first["macro_f1_ci"]
    assert first["macro_f1_ci"] == again["macro_f1_ci"]
    assert lo < first["macro_f1"] < hi
    assert score(reference, predicted, 300, 0.95, seed=7)["macro_f1_ci"] != first["macro_f1_ci"]


def test_inputs_are_checked() -> None:
    with pytest.raises(ValueError, match="equal length"):
        score(["accrual"], [], resamples=10, confidence=0.95, seed=0)
    with pytest.raises(ValueError, match="unknown label"):
        score(["accrual"], ["harm"], resamples=10, confidence=0.95, seed=0)
    assert cohen_kappa(["a", "b"], ["a", "b"]) == 1.0


def test_every_reference_item_needs_a_prediction() -> None:
    reference = {("NCT1", "a"): "accrual", ("NCT2", "b"): "other"}
    assert aligned(reference, {**reference, ("NCT3", "c"): "safety"}) == (
        ["accrual", "other"],
        ["accrual", "other"],
    )
    with pytest.raises(ValueError, match="no prediction"):
        aligned(reference, {("NCT1", "a"): "accrual"})


def test_predictions_round_trip(tmp_path: Path) -> None:
    predictions = {("NCT2", "b"): "funding", ("NCT1", "a"): "covid19"}
    write_predictions(tmp_path / "p.csv", predictions)
    assert read_predictions(tmp_path / "p.csv") == predictions


def test_each_model_is_scored_on_test_only_once(tmp_path: Path) -> None:
    reference, predicted = _labels(40, 3, noise=0.2)
    keys = [(f"NCT{n}", f"h{n}") for n in range(40)]
    ref = dict(zip(keys, reference, strict=True))
    pred = dict(zip(keys, predicted, strict=True))
    result = score_test_once("llm", ref, pred, tmp_path, {"prompt": "v1"}, 100, 0.95, 0)
    assert result["prompt"] == "v1"
    assert (tmp_path / "test_llm.json").is_file()
    with pytest.raises(FileExistsError, match="already scored"):
        score_test_once("llm", ref, pred, tmp_path, {}, 100, 0.95, 0)
    assert any("macro-F1" in line for line in summary_lines(result))


def test_dev_errors_list_only_the_wrong_items() -> None:
    reference = {("NCT1", "a"): "accrual", ("NCT2", "b"): "other"}
    predictions = {("NCT1", "a"): "accrual", ("NCT2", "b"): "business"}
    texts = {("NCT1", "a"): "Slow accrual", ("NCT2", "b"): "Stopped"}
    assert dev_errors(reference, predictions, texts) == [
        {"nct_id": "NCT2", "reference": "other", "predicted": "business", "text": "Stopped"}
    ]


def test_a_test_result_ever_committed_blocks_a_second_scoring(tmp_path: Path) -> None:
    import subprocess

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    reference, predicted = _labels(20, 1, noise=0.1)
    keys = [(f"NCT{n}", f"h{n}") for n in range(20)]
    ref, pred = dict(zip(keys, reference, strict=True)), dict(zip(keys, predicted, strict=True))
    score_test_once("llm", ref, pred, tmp_path, {}, 50, 0.95, 0)
    git("add", "test_llm.json")
    git("commit", "-q", "-m", "result")
    (tmp_path / "test_llm.json").unlink()  # deleting the file does not reopen scoring
    with pytest.raises(FileExistsError, match="already scored"):
        score_test_once("llm", ref, pred, tmp_path, {}, 50, 0.95, 0)


def test_the_summary_names_the_configured_interval_level() -> None:
    reference, predicted = _labels(50, 2, noise=0.2)
    result = score(reference, predicted, resamples=50, confidence=0.9, seed=0)
    assert "(90% CI" in summary_lines(result)[0]
