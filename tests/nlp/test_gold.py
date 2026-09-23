"""Taxonomy, gold sampling and label storage on synthetic data (no real texts)."""

import csv
import datetime as dt
from collections import Counter
from pathlib import Path

import duckdb
import pytest

from trialpulse.config import ProjectConfig, load_project_config
from trialpulse.nlp.gold import (
    GoldItem,
    allocate,
    early_stop_texts,
    next_unlabeled,
    read_labels,
    save_label,
    split_dev_test,
    stratified_sample,
)
from trialpulse.nlp.taxonomy import LABELS, group_of, validate_label


@pytest.fixture
def cfg() -> ProjectConfig:
    return load_project_config()


def test_taxonomy_matches_the_config_groups(cfg: ProjectConfig) -> None:
    assert set(cfg.stop_reasons.operational) | set(cfg.stop_reasons.scientific) == set(LABELS) - {
        "other"
    }
    assert group_of("accrual", cfg) == "operational"
    assert group_of("efficacy", cfg) == "scientific"
    assert group_of("other", cfg) == "other"
    with pytest.raises(ValueError, match="unknown label"):
        validate_label("unclear")


def test_allocate_is_proportional_with_minimums() -> None:
    sizes = {("TERMINATED", 2015): 800, ("TERMINATED", 2016): 150, ("WITHDRAWN", 2016): 50,
             ("WITHDRAWN", 2017): 2}  # fmt: skip
    alloc = allocate(sizes, 100)
    assert sum(alloc.values()) == 100
    assert alloc[("WITHDRAWN", 2017)] == 1  # tiny strata still appear
    assert alloc[("TERMINATED", 2015)] > alloc[("TERMINATED", 2016)] > alloc[("WITHDRAWN", 2016)]
    assert all(alloc[k] <= sizes[k] for k in sizes)
    assert allocate({("A", 1): 3}, 10) == {("A", 1): 3}


def _pool(n: int) -> list[GoldItem]:
    return [
        GoldItem(f"NCT{i:08d}", 1, "TERMINATED" if i % 3 else "WITHDRAWN", 2010 + i % 12,
                 f"synthetic reason {i}")
        for i in range(n)
    ]  # fmt: skip


def test_stratified_sample_is_seeded_and_stratified() -> None:
    pool = _pool(5000)
    sample = stratified_sample(pool, 400, seed=42)

    assert len(sample) == 400
    assert sample == stratified_sample(list(reversed(pool)), 400, seed=42)
    assert len({i.key for i in sample}) == 400
    share = Counter(i.status for i in sample)["WITHDRAWN"] / 400
    assert share == pytest.approx(1 / 3, abs=0.02)


def test_split_is_100_dev_and_300_test() -> None:
    split = split_dev_test(stratified_sample(_pool(5000), 400, seed=42), 100, seed=42)
    assert Counter(i.split for i in split) == {"dev": 100, "test": 300}
    assert split == split_dev_test(stratified_sample(_pool(5000), 400, 42), 100, 42)


def test_early_stop_texts_use_the_event_version(cfg: ProjectConfig, tmp_path: Path) -> None:
    path = tmp_path / "h.parquet"
    rows = [
        ("NCT1", 0, "RECRUITING", "INTERVENTIONAL", "2012-01-01", "2012-01-01", None),
        ("NCT1", 1, "TERMINATED", "INTERVENTIONAL", "2012-01-01", "2014-05-01", "Slow accrual"),
        ("NCT1", 2, "TERMINATED", "INTERVENTIONAL", "2012-01-01", "2015-01-01", "Edited later"),
        ("NCT2", 0, "WITHDRAWN", "INTERVENTIONAL", "2019-01-01", "2019-06-01", " "),
        ("NCT3", 0, "WITHDRAWN", "OBSERVATIONAL", "2019-01-01", "2019-06-01", "Funding"),
        ("NCT4", 0, "WITHDRAWN", "INTERVENTIONAL", "2006-01-01", "2007-06-01", "Funding"),
    ]
    with duckdb.connect() as con:
        con.execute(
            """CREATE TABLE h (nct_id VARCHAR, nct_version INTEGER, overall_status VARCHAR,
               study_type VARCHAR, study_first_post_date DATE, last_update_post_date DATE,
               why_stopped VARCHAR)"""
        )
        con.executemany("INSERT INTO h VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        con.execute(f"COPY h TO '{path.as_posix()}' (FORMAT parquet)")
        items = early_stop_texts(con, path.as_posix(), cfg)

    # NCT2 has a blank reason, NCT3 is observational, NCT4 was first posted before 2008.
    assert items == [GoldItem("NCT1", 1, "TERMINATED", 2014, "Slow accrual")]


def test_labels_are_saved_without_text_and_can_be_revised(tmp_path: Path) -> None:
    path = tmp_path / "labels" / "gold_labels.csv"
    items = split_dev_test(_pool(3), 1, seed=1)
    when = dt.datetime(2026, 9, 23, tzinfo=dt.UTC)

    save_label(path, items[1], "accrual", now=when)
    save_label(path, items[0], "funding", now=when)
    save_label(path, items[1], "business", now=when)

    labels = read_labels(path)
    assert {k: v["label"] for k, v in labels.items()} == {
        items[0].key: "funding",
        items[1].key: "business",
    }
    text = path.read_text(encoding="utf-8")
    assert "synthetic reason" not in text
    with path.open(newline="", encoding="utf-8") as fh:
        assert next(csv.reader(fh)) == ["nct_id", "nct_version", "split", "label", "labeled_at"]
    assert next_unlabeled(items, labels) == items[2]
    with pytest.raises(ValueError, match="unknown label"):
        save_label(path, items[2], "vague")
