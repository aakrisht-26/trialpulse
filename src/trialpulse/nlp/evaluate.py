"""Scoring reason labels against the gold reference labels (CLAUDE.md Section 11, ADR 0010).

The gold labels are reference labels from an adjudicated model panel, so every score here
measures agreement with that panel, not with human judgment.

- Accuracy, macro-F1 with a percentile bootstrap interval (resampling texts), Cohen's kappa,
  per-label precision, recall and F1, and the confusion matrix (rows: reference, columns:
  prediction). Macro-F1 averages the per-label F1 over the labels that occur in the
  reference or the predictions, as scikit-learn does by default.
- Test items are scored once per model. Test results are saved in docs/results/ (tracked in
  git), and a second test scoring is refused when the result file exists or ever existed in
  git history. Test results are aggregate only; dev items may be listed one by one for
  prompt work.

    uv run python -m trialpulse.nlp.evaluate --dev reason_v1
    uv run python -m trialpulse.nlp.evaluate --test llm
    uv run python -m trialpulse.nlp.evaluate --test distilled
"""

import argparse
import csv
import datetime as dt
import json
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from trialpulse.config import REPO_ROOT, load_project_config
from trialpulse.nlp.gold import LABELS_PATH, NLP_DIR, SAMPLE_PATH, load_sample
from trialpulse.nlp.taxonomy import LABELS, validate_label

RESULTS_DIR = NLP_DIR / "results"  # dev results (gitignored)
TEST_RESULTS_DIR = REPO_ROOT / "docs" / "results"  # test results, tracked in git
PREDICTION_COLUMNS = ("nct_id", "text_sha256", "label")
Key = tuple[str, str]


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    if not a or len(a) != len(b):
        raise ValueError("kappa needs two equal-length, non-empty label sequences")
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in ca) / n**2
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def _codes(labels: Sequence[str]) -> np.ndarray:
    index = {label: n for n, label in enumerate(LABELS)}
    return np.array([index[validate_label(label)] for label in labels], dtype=np.int64)


def _confusion(reference: np.ndarray, predicted: np.ndarray) -> np.ndarray:
    k = len(LABELS)
    return np.bincount(reference * k + predicted, minlength=k * k).reshape(k, k)


def _macro_f1(matrix: np.ndarray) -> float:
    tp = np.diag(matrix).astype(float)
    denominator = matrix.sum(axis=0) + matrix.sum(axis=1)  # 2tp + fp + fn
    present = denominator > 0
    f1 = np.divide(2 * tp, denominator, out=np.zeros_like(tp), where=present)
    return float(f1[present].mean())


def score(
    reference: Sequence[str],
    predicted: Sequence[str],
    resamples: int,
    confidence: float,
    seed: int,
) -> dict[str, Any]:
    """Every metric for one set of predictions, with a bootstrap interval for macro-F1."""
    if not reference or len(reference) != len(predicted):
        raise ValueError("reference and predictions must be non-empty and of equal length")
    ref, pred = _codes(reference), _codes(predicted)
    matrix = _confusion(ref, pred)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(ref), size=(resamples, len(ref)))
    boot = np.array([_macro_f1(_confusion(ref[d], pred[d])) for d in draws])
    alpha = (1 - confidence) / 2
    per_label: dict[str, dict[str, float | int]] = {}
    for n, label in enumerate(LABELS):
        tp, fp, fn = matrix[n, n], matrix[:, n].sum() - matrix[n, n], matrix[n].sum() - matrix[n, n]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
        per_label[label] = {
            "precision": round(float(precision), 4),
            "recall": round(float(recall), 4),
            "f1": round(float(f1), 4),
            "support": int(matrix[n].sum()),
        }
    return {
        "n": len(ref),
        "accuracy": round(float((ref == pred).mean()), 4),
        "macro_f1": round(_macro_f1(matrix), 4),
        "macro_f1_ci": [
            round(float(np.quantile(boot, alpha)), 4),
            round(float(np.quantile(boot, 1 - alpha)), 4),
        ],
        "kappa": round(cohen_kappa(list(reference), list(predicted)), 4),
        "per_label": per_label,
        "confusion": {"labels": list(LABELS), "matrix": matrix.tolist()},
        "bootstrap": {"resamples": resamples, "confidence": confidence, "seed": seed},
    }


def read_reference(path: Path, split: str) -> dict[Key, str]:
    with path.open(newline="", encoding="utf-8") as fh:
        return {
            (r["nct_id"], r["text_sha256"]): validate_label(r["label"])
            for r in csv.DictReader(fh)
            if r["split"] == split
        }


def write_predictions(path: Path, predictions: Mapping[Key, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(PREDICTION_COLUMNS)
        for (nct_id, digest), label in sorted(predictions.items()):
            writer.writerow([nct_id, digest, validate_label(label)])
    tmp.replace(path)


def read_predictions(path: Path) -> dict[Key, str]:
    with path.open(newline="", encoding="utf-8") as fh:
        return {
            (r["nct_id"], r["text_sha256"]): validate_label(r["label"]) for r in csv.DictReader(fh)
        }


def aligned(
    reference: Mapping[Key, str], predictions: Mapping[Key, str]
) -> tuple[list[str], list[str]]:
    """Reference and predicted labels in key order. Every reference item must be predicted."""
    missing = set(reference) - set(predictions)
    if missing:
        raise ValueError(f"{len(missing)} reference items have no prediction")
    keys = sorted(reference)
    return [reference[k] for k in keys], [predictions[k] for k in keys]


def score_test_once(
    name: str,
    reference: Mapping[Key, str],
    predictions: Mapping[Key, str],
    results_dir: Path,
    metadata: Mapping[str, Any],
    resamples: int,
    confidence: float,
    seed: int,
) -> dict[str, Any]:
    """Score one model on the test split and save the result. A model already scored on the
    test split is refused, so each model gets exactly one test scoring."""
    path = results_dir / f"test_{name}.json"
    if path.exists() or _in_git_history(path):
        raise FileExistsError(f"{name} was already scored on the test split ({path})")
    ref, pred = aligned(reference, predictions)
    result = {
        "model": name,
        "split": "test",
        "scored_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        **metadata,
        **score(ref, pred, resamples, confidence, seed),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def _in_git_history(path: Path) -> bool:
    """Whether a file was ever committed, so deleting a test result cannot reopen scoring."""
    done = subprocess.run(
        ["git", "-C", str(path.parent), "log", "--all", "--format=%H", "--", path.name],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode == 0 and bool(done.stdout.strip())


def dev_errors(
    reference: Mapping[Key, str], predictions: Mapping[Key, str], texts: Mapping[Key, str]
) -> list[dict[str, str]]:
    """The dev items the predictions got wrong, with their texts, for prompt work."""
    return [
        {"nct_id": k[0], "reference": reference[k], "predicted": predictions[k], "text": texts[k]}
        for k in sorted(reference)
        if predictions.get(k) != reference[k]
    ]


def summary_lines(result: Mapping[str, Any]) -> list[str]:
    lo, hi = result["macro_f1_ci"]
    level = round(100 * result["bootstrap"]["confidence"])
    lines = [
        f"n={result['n']}  accuracy={result['accuracy']:.3f}  macro-F1={result['macro_f1']:.3f} "
        f"({level}% CI {lo:.3f} to {hi:.3f})  kappa={result['kappa']:.3f}",
        "per-label F1: "
        + ", ".join(
            f"{k} {v['f1']:.2f} (n={v['support']})" for k, v in result["per_label"].items()
        ),
        "confusion (rows reference, columns prediction): " + " ".join(LABELS),
    ]
    lines += [
        f"  {label:>14} {row}"
        for label, row in zip(LABELS, result["confusion"]["matrix"], strict=True)
    ]
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    from trialpulse.nlp.llm_labeler import FINAL_PROMPT, LABELS_DIR, committed_prompt, prompt_stem

    parser = argparse.ArgumentParser(description="Score reason labels against the gold set")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dev", metavar="PROMPT", help="score an LLM prompt version on dev")
    group.add_argument("--test", choices=("llm", "distilled"), help="score a model on test, once")
    args = parser.parse_args(argv)
    cfg = load_project_config()
    boot = (cfg.evaluation.bootstrap_resamples, cfg.evaluation.confidence_level, cfg.seeds.default)

    if args.dev:
        stem = prompt_stem(args.dev)
        reference = read_reference(LABELS_PATH, "dev")
        predictions = read_predictions(LABELS_DIR / f"dev_{stem}.csv")
        ref, pred = aligned(reference, predictions)
        result = score(ref, pred, *boot)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        (RESULTS_DIR / f"dev_{stem}.json").write_text(json.dumps(result, indent=2) + "\n", "utf-8")
        print("\n".join(summary_lines(result)))
        texts = {i.key: i.why_stopped for i in load_sample(SAMPLE_PATH) if i.split == "dev"}
        for e in dev_errors(reference, predictions, texts):
            print(f"- reference {e['reference']}, predicted {e['predicted']}: {e['text']}")
        return 0

    reference = read_reference(LABELS_PATH, "test")
    if args.test == "llm":
        stem = prompt_stem(FINAL_PROMPT)
        predictions = read_predictions(LABELS_DIR / f"test_{stem}.csv")
        metadata: dict[str, Any] = {
            "prompt": FINAL_PROMPT,
            "prompt_commit": committed_prompt(FINAL_PROMPT),
        }
    else:
        from trialpulse.nlp.distill import (
            MODEL_PATH,
            TEST_PREDICTIONS_MODEL,
            TEST_PREDICTIONS_PATH,
            check_final,
            load_metadata,
            model_identity,
        )

        identity = model_identity(MODEL_PATH)
        check_final(identity)
        predicted_by = json.loads(TEST_PREDICTIONS_MODEL.read_text(encoding="utf-8"))
        if predicted_by != identity:
            raise ValueError("the test predictions come from another model; rerun --predict-test")
        predictions = read_predictions(TEST_PREDICTIONS_PATH)
        metadata = {"model_identity": identity, "model_metadata": load_metadata(MODEL_PATH)}
    result = score_test_once(args.test, reference, predictions, TEST_RESULTS_DIR, metadata, *boot)
    print("\n".join(summary_lines(result)))  # aggregate only: no test item is printed
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
