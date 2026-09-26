"""The distilled classifier on synthetic texts: selection and fit, the gold-exclusion rule,
predictions with confidence, and the labels for every early stop."""

from pathlib import Path

import duckdb
import pytest

from trialpulse.config import load_project_config
from trialpulse.nlp.distill import (
    GRID,
    load_metadata,
    load_model,
    predict,
    reason_rows,
    save_model,
    select_and_fit,
    training_set,
    write_reasons,
)
from trialpulse.nlp.gold import EarlyStop, GoldItem, normalize_text, text_sha256

TEMPLATES = {
    "accrual": "slow enrollment at site {n}, too few eligible patients",
    "funding": "grant money ran out for program {n}",
    "safety": "serious adverse events in cohort {n} led the monitoring board to stop",
    "covid19": "covid-19 pandemic restrictions halted visits in wave {n}",
}


def _corpus(per_label: int = 12) -> tuple[list[str], list[str]]:
    texts, labels = [], []
    for label, template in TEMPLATES.items():
        for n in range(per_label):
            texts.append(template.format(n=n))
            labels.append(label)
    return texts, labels


def test_selection_covers_the_grid_and_the_model_learns() -> None:
    texts, labels = _corpus()
    pipeline, selection = select_and_fit(texts, labels, seed=42, folds=3)
    assert len(selection["grid"]) == len(GRID["C"]) * len(GRID["class_weight"])
    assert selection["folds"] == 3
    assert selection["best"]["cv_macro_f1"] > 0.9
    predicted, confidence = predict(
        pipeline, ["Slow enrollment at site 99, too few eligible patients"]
    )
    assert predicted == ["accrual"]
    assert 0 < confidence[0] <= 1


def test_selection_needs_two_texts_per_label() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        select_and_fit(
            ["slow enrollment", "grant ended", "grant stopped"],
            ["accrual", "funding", "funding"],
            seed=0,
        )


def test_a_gold_text_is_refused_in_the_training_set() -> None:
    items = [
        GoldItem("NCT1", text_sha256("slow enrollment"), "TERMINATED", 2015, "Slow enrollment"),
        GoldItem("NCT2", text_sha256("grant ended"), "WITHDRAWN", 2016, "Grant ended."),
    ]
    labels = {items[0].key: "accrual", items[1].key: "funding"}
    texts, ys = training_set(items, labels, gold=set())
    assert texts == ["slow enrollment", "grant ended"]
    assert ys == ["accrual", "funding"]
    with pytest.raises(ValueError, match="gold texts"):
        training_set(items, labels, gold={items[1].text_sha256})
    assert training_set(items, {items[0].key: "accrual"}, gold=set())[1] == ["accrual"]


def test_every_early_stop_with_a_reason_gets_a_label(tmp_path: Path) -> None:
    texts, labels = _corpus()
    pipeline, _ = select_and_fit(texts, labels, seed=42, folds=3)
    records = [
        EarlyStop("NCT3", "TERMINATED", "Grant money ran out for program 77", None, None, None),
        EarlyStop(
            "NCT1", "WITHDRAWN", "COVID-19 pandemic restrictions halted visits", None, None, None
        ),
        EarlyStop("NCT2", "TERMINATED", "   ", None, None, None),  # blank reason
        EarlyStop("NCT4", "COMPLETED", "Slow enrollment", None, None, None),  # not an early stop
    ]
    cfg = load_project_config()
    rows = reason_rows(records, pipeline, "test-model", cfg)
    assert [r["nct_id"] for r in rows] == ["NCT3", "NCT1"]
    assert [r["label"] for r in rows] == ["funding", "covid19"]
    assert [r["reason_group"] for r in rows] == ["operational", "operational"]
    assert rows[0]["text_sha256"] == text_sha256(
        normalize_text("Grant money ran out for program 77")
    )

    csv_path, parquet_path = write_reasons(rows, tmp_path / "reasons")
    assert csv_path.read_text("utf-8").splitlines()[1].startswith("NCT1,")  # sorted by trial
    count = duckdb.sql(f"SELECT count(*) FROM read_parquet('{parquet_path.as_posix()}')").fetchone()
    assert count == (2,)


def test_the_model_and_its_metadata_round_trip(tmp_path: Path) -> None:
    texts, labels = _corpus(per_label=6)
    pipeline, selection = select_and_fit(texts, labels, seed=1, folds=2)
    save_model(
        tmp_path / "m.joblib", pipeline, {"selection": selection, "training_texts": len(texts)}
    )
    loaded = load_model(tmp_path / "m.joblib")
    assert predict(loaded, texts[:3]) == predict(pipeline, texts[:3])
    assert load_metadata(tmp_path / "m.joblib")["training_texts"] == len(texts)
