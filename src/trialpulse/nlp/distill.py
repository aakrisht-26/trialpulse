"""The distilled reason classifier (CLAUDE.md Section 11): TF-IDF plus logistic regression,
trained on the LLM labels of the 10,000-text sample. It is the only reason model used in
production, and it labels every early stop in the cohort.

- Features: word 1- and 2-grams and character 3- to 5-grams (within words), TF-IDF with
  sublinear term frequency, on the normalized text.
- Model selection: 5-fold stratified cross-validation on the LLM-labeled training texts
  only, by macro-F1 against the LLM labels, over a small grid of C and class weighting. The
  gold sets play no part in fitting or selection.
- No gold text is ever in the training set: a training row whose text hash is a gold hash
  is refused.

    uv run python -m trialpulse.nlp.distill            (the same as --train)
    uv run python -m trialpulse.nlp.distill --train
    uv run python -m trialpulse.nlp.distill --dev
    uv run python -m trialpulse.nlp.distill --predict-test
    uv run python -m trialpulse.nlp.distill --label-all

Expected refusals (a provisional model sent to test prediction, a missing model or input, a
gold text in the training set) print one "refused:" line and exit with code 2.
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from itertools import product
from pathlib import Path
from typing import Any

import duckdb
import joblib
import numpy as np
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import FeatureUnion, Pipeline

from trialpulse.cli import RefusedError, run
from trialpulse.config import ProjectConfig, load_project_config
from trialpulse.nlp.evaluate import (
    read_predictions,
    read_reference,
    score,
    summary_lines,
    write_predictions,
)
from trialpulse.nlp.gold import (
    LABELS_PATH,
    NLP_DIR,
    SAMPLE_PATH,
    EarlyStop,
    GoldItem,
    load_sample,
    normalize_text,
    text_sha256,
)
from trialpulse.nlp.taxonomy import group_of, validate_label

MODEL_DIR = NLP_DIR / "models"
MODEL_PATH = MODEL_DIR / "distilled.joblib"
TEST_PREDICTIONS_PATH = NLP_DIR / "results" / "test_distilled_predictions.csv"
TEST_PREDICTIONS_MODEL = TEST_PREDICTIONS_PATH.with_suffix(".json")  # which model predicted
REASONS_DIR = NLP_DIR / "reasons"
REASON_COLUMNS = (
    "nct_id",
    "status",
    "text_sha256",
    "label",
    "reason_group",
    "confidence",
    "model",
)
GRID: dict[str, tuple[Any, ...]] = {"C": (0.5, 2.0, 8.0, 32.0), "class_weight": (None, "balanced")}
FOLDS = 5
Key = tuple[str, str]


class ProvisionalModelError(RefusedError, ValueError):
    pass


class GoldInTrainingError(RefusedError, ValueError):
    pass


class TooFewLabelsError(RefusedError, ValueError):
    pass


def build_pipeline(c: float, class_weight: str | None, seed: int) -> Pipeline:
    features = FeatureUnion(
        [
            ("words", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
            (
                "chars",
                TfidfVectorizer(
                    analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True
                ),
            ),
        ]
    )
    model = LogisticRegression(C=c, class_weight=class_weight, max_iter=5000, random_state=seed)
    return Pipeline([("features", features), ("model", model)])


def training_set(
    items: Sequence[GoldItem], labels: Mapping[Key, str], gold: set[str]
) -> tuple[list[str], list[str]]:
    """Normalized texts and their LLM labels. A gold text in the training set is refused."""
    rows = [(i, labels[i.key]) for i in items if i.key in labels]
    leaked = [i for i, _ in rows if i.text_sha256 in gold]
    if leaked:
        raise GoldInTrainingError(f"{len(leaked)} gold texts are in the distillation training set")
    return [normalize_text(i.why_stopped) for i, _ in rows], [validate_label(y) for _, y in rows]


def select_and_fit(
    texts: Sequence[str], labels: Sequence[str], seed: int, folds: int = FOLDS
) -> tuple[Pipeline, dict[str, Any]]:
    """Pick C and class weighting by cross-validated macro-F1, then refit on all texts."""
    if not labels:
        raise TooFewLabelsError("no LLM labels yet; run trialpulse.nlp.llm_labeler --sample first")
    smallest = min(Counter(labels).values())
    k = min(folds, smallest)
    if k < 2:
        raise TooFewLabelsError(
            "every label needs at least 2 training texts for cross-validation; label more texts"
        )
    splitter = StratifiedKFold(n_splits=k, shuffle=True, random_state=seed)
    results = []
    for c, weight in product(GRID["C"], GRID["class_weight"]):
        predicted = cross_val_predict(
            build_pipeline(c, weight, seed), list(texts), list(labels), cv=splitter
        )
        macro = float(f1_score(labels, predicted, average="macro"))
        results.append({"C": c, "class_weight": weight, "cv_macro_f1": round(macro, 4)})
    best = max(results, key=lambda r: (r["cv_macro_f1"], -GRID["C"].index(r["C"])))
    pipeline = build_pipeline(best["C"], best["class_weight"], seed).fit(list(texts), list(labels))
    return pipeline, {"folds": k, "grid": results, "best": best}


def predict(pipeline: Pipeline, texts: Sequence[str]) -> tuple[list[str], list[float]]:
    """Labels and the probability of each chosen label."""
    probabilities = pipeline.predict_proba([normalize_text(t) for t in texts])
    classes = pipeline.classes_
    best = np.argmax(probabilities, axis=1)
    return [str(classes[b]) for b in best], [
        round(float(p[b]), 4) for p, b in zip(probabilities, best, strict=True)
    ]


def save_model(path: Path, pipeline: Pipeline, metadata: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, path)
    path.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def load_model(path: Path) -> Pipeline:
    pipeline: Pipeline = joblib.load(path)
    return pipeline


def load_metadata(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    return data


def model_identity(path: Path) -> dict[str, Any]:
    """What identifies a saved model: its file hash, training time and training size."""
    meta = load_metadata(path)
    return {
        "model_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "trained_at": meta["trained_at"],
        "training_texts": meta["training_texts"],
        "sample_size": meta["sample_size"],
    }


def check_final(identity: Mapping[str, Any]) -> None:
    """The one test scoring is kept for the model trained on the whole LLM sample. A
    provisional model, trained on part of it, is refused."""
    if identity["training_texts"] < identity["sample_size"]:
        raise ProvisionalModelError(
            f"the model is provisional: trained on {identity['training_texts']} of "
            f"{identity['sample_size']} sample texts; finish the LLM sample and retrain first"
        )


def reason_rows(
    records: Iterable[EarlyStop], pipeline: Pipeline, model_name: str, cfg: ProjectConfig
) -> list[dict[str, str]]:
    """One row per early stop with a non-blank reason: its predicted label, the label's
    group and the model's probability for it."""
    kept = [
        r for r in records
        if r.status.upper() in cfg.statuses.early_stop and r.why_stopped
        and normalize_text(r.why_stopped)
    ]  # fmt: skip
    labels, confidence = predict(pipeline, [r.why_stopped or "" for r in kept])
    return [
        {
            "nct_id": r.nct_id,
            "status": r.status.upper(),
            "text_sha256": text_sha256(normalize_text(r.why_stopped or "")),
            "label": label,
            "reason_group": group_of(label, cfg),
            "confidence": f"{p:.4f}",
            "model": model_name,
        }
        for r, label, p in zip(kept, labels, confidence, strict=True)
    ]


def write_reasons(rows: Sequence[Mapping[str, str]], folder: Path) -> tuple[Path, Path]:
    """The labels as CSV and as Parquet (through DuckDB)."""
    folder.mkdir(parents=True, exist_ok=True)
    csv_path, parquet_path = (
        folder / "early_stop_reasons.csv",
        folder / "early_stop_reasons.parquet",
    )
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REASON_COLUMNS)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: r["nct_id"]))
    source = csv_path.as_posix().replace("'", "''")
    target = parquet_path.as_posix().replace("'", "''")
    duckdb.sql(
        "COPY (SELECT * REPLACE (CAST(confidence AS DOUBLE) AS confidence) "
        f"FROM read_csv('{source}', header=true, all_varchar=true)) "
        f"TO '{target}' (FORMAT parquet)"
    )
    return csv_path, parquet_path


def main(argv: Sequence[str] | None = None) -> int:
    from trialpulse.nlp.llm_labeler import (
        FINAL_PROMPT,
        LABELS_DIR,
        LLM_SAMPLE_PATH,
        cached_early_stops,
        gold_hashes,
        prompt_stem,
    )

    parser = argparse.ArgumentParser(description="The distilled reason classifier")
    group = parser.add_mutually_exclusive_group()
    for flag in ("train", "dev", "predict-test", "label-all"):
        group.add_argument(f"--{flag}", action="store_true")
    args = parser.parse_args(argv)
    cfg = load_project_config()
    seed = cfg.seeds.default

    if args.train or not (args.dev or args.predict_test or args.label_all):
        labels_path = LABELS_DIR / f"sample_{prompt_stem(FINAL_PROMPT)}.csv"
        if not LLM_SAMPLE_PATH.is_file() or not labels_path.is_file():
            raise RefusedError(
                "no LLM sample labels; run trialpulse.nlp.llm_labeler --sample 10000 first"
            )
        sample = load_sample(LLM_SAMPLE_PATH)
        texts, labels = training_set(sample, read_predictions(labels_path), gold_hashes())
        pipeline, selection = select_and_fit(texts, labels, seed)
        metadata = {
            "model": "tfidf-logreg",
            "trained_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "training_texts": len(texts),
            "sample_size": len(sample),
            "teacher": {"prompt": FINAL_PROMPT, "labels_file": labels_path.name},
            "label_counts": dict(Counter(labels).most_common()),
            "selection": selection,
            "scikit_learn": sklearn.__version__,
        }
        save_model(MODEL_PATH, pipeline, metadata)
        print(json.dumps({k: metadata[k] for k in ("training_texts", "label_counts")}, indent=2))
        print(f"best: {selection['best']}; saved to {MODEL_PATH}")
        return 0

    if not MODEL_PATH.is_file():
        raise RefusedError("no trained model; run trialpulse.nlp.distill --train first")
    if (args.dev or args.predict_test) and not SAMPLE_PATH.is_file():
        raise RefusedError(
            f"no gold sample at {SAMPLE_PATH}; build it with trialpulse.nlp.gold --build"
        )
    pipeline = load_model(MODEL_PATH)
    meta = load_metadata(MODEL_PATH)
    name = f"tfidf-logreg n={meta['training_texts']} trained {meta['trained_at']}"
    if args.dev:
        items = [i for i in load_sample(SAMPLE_PATH) if i.split == "dev"]
        predicted, _ = predict(pipeline, [i.why_stopped for i in items])
        reference = read_reference(LABELS_PATH, "dev")
        keys = [i.key for i in items]
        result = score(
            [reference[k] for k in keys], predicted, cfg.evaluation.bootstrap_resamples,
            cfg.evaluation.confidence_level, seed,
        )  # fmt: skip
        print("\n".join(summary_lines(result)))
        return 0
    if args.predict_test:
        identity = model_identity(MODEL_PATH)
        check_final(identity)
        items = [i for i in load_sample(SAMPLE_PATH) if i.split == "test"]
        predicted, _ = predict(pipeline, [i.why_stopped for i in items])
        write_predictions(
            TEST_PREDICTIONS_PATH, dict(zip([i.key for i in items], predicted, strict=True))
        )
        TEST_PREDICTIONS_MODEL.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {len(items)} test predictions to {TEST_PREDICTIONS_PATH} (not scored)")
        return 0
    rows = reason_rows(cached_early_stops(cfg), pipeline, name, cfg)
    csv_path, parquet_path = write_reasons(rows, REASONS_DIR)
    print(
        f"labeled {len(rows)} early stops: {dict(Counter(r['label'] for r in rows).most_common())}"
    )
    print(f"wrote {csv_path} and {parquet_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
