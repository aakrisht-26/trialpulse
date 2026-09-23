"""Gold sampling and label storage on synthetic data (no real texts, no network)."""

import csv
import datetime as dt
import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from tenacity import wait_none

from trialpulse.config import ProjectConfig, load_project_config
from trialpulse.ingest.ctgov_api import STUDIES_URL, VERSION_URL, ApiClient
from trialpulse.nlp.gold import (
    EarlyStop,
    GoldItem,
    allocate,
    build_gold_sample,
    distinct_items,
    load_sample,
    next_unlabeled,
    normalize_text,
    read_labels,
    save_label,
    split_dev_test,
    stop_year,
    stratified_sample,
    text_sha256,
)
from trialpulse.nlp.taxonomy import LABELS, group_of, validate_label

EARLY = ("TERMINATED", "WITHDRAWN")


@pytest.fixture
def cfg() -> ProjectConfig:
    return load_project_config()


def test_taxonomy_matches_the_config_groups(cfg: ProjectConfig) -> None:
    grouped = set(cfg.stop_reasons.operational) | set(cfg.stop_reasons.scientific)
    assert grouped == set(LABELS) - {"other"}
    assert group_of("accrual", cfg) == "operational"
    assert group_of("efficacy", cfg) == "scientific"
    assert group_of("other", cfg) == "other"
    with pytest.raises(ValueError, match="unknown label"):
        validate_label("unclear")


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ('  "Slow   Accrual."  ', "slow accrual"),
        ("Sponsor decision;\n\tfunding ended!", "sponsor decision; funding ended"),
        ("(COVID-19)", "covid-19"),
        ("...", ""),
        ("Low enrollment (n=3).", "low enrollment (n=3"),
    ],
)
def test_normalize_text(raw: str, normalized: str) -> None:
    assert normalize_text(raw) == normalized


def test_text_sha256_is_the_hash_of_the_normalized_text() -> None:
    assert text_sha256("slow accrual") == hashlib.sha256(b"slow accrual").hexdigest()


@pytest.mark.parametrize(
    ("completion", "ctype", "updated", "year"),
    [
        ("2019-05-31", "ACTUAL", "2021-02-01", 2019),
        ("2019-05", "ACTUAL", "2021-02-01", 2019),
        ("2023-12-31", "ESTIMATED", "2021-02-01", 2021),
        (None, None, "2021-02-01", 2021),
        (None, None, None, None),
    ],
)
def test_stop_year_rule(
    completion: str | None, ctype: str | None, updated: str | None, year: int | None
) -> None:
    assert stop_year(completion, ctype, updated) == year


def _stop(nct: str, text: str | None, status: str = "TERMINATED", year: int = 2015) -> EarlyStop:
    return EarlyStop(nct, status, text, f"{year}-06-30", "ACTUAL", f"{year + 1}-01-10")


def test_distinct_items_dedupe_and_filter() -> None:
    records = [
        _stop("NCT00000009", "Slow accrual."),
        _stop("NCT00000001", "  slow   ACCRUAL "),  # same normalized text, lower NCT ID wins
        _stop("NCT00000002", "   "),  # blank reason
        _stop("NCT00000003", None),  # no reason
        _stop("NCT00000004", "Funding ended", status="COMPLETED"),  # not an early stop
        _stop("NCT00000005", "Funding ended", status="WITHDRAWN", year=2020),
    ]

    items, stats = distinct_items(records, EARLY, "ctgov-api-v2 pulled 2026-09-23")

    assert [(i.nct_id, i.status, i.stop_year) for i in items] == [
        ("NCT00000001", "TERMINATED", 2015),
        ("NCT00000005", "WITHDRAWN", 2020),
    ]
    assert items[0].text_sha256 == text_sha256("slow accrual")
    assert items[0].why_stopped == "slow   ACCRUAL"  # the kept trial's own text, trimmed
    assert all(i.source == "ctgov-api-v2 pulled 2026-09-23" for i in items)
    assert stats == {"records": 6, "non_blank": 3, "no_stop_year": 0, "distinct_texts": 2}


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
        GoldItem(f"NCT{i:08d}", text_sha256(f"reason {i}"),
                 "TERMINATED" if i % 3 else "WITHDRAWN", 2010 + i % 12, f"Synthetic reason {i}")
        for i in range(n)
    ]  # fmt: skip


def test_stratified_sample_is_seeded_and_stratified() -> None:
    pool = _pool(5000)
    sample = stratified_sample(pool, 400, seed=42)

    assert len(sample) == 400
    assert sample == stratified_sample(list(reversed(pool)), 400, seed=42)
    assert len({i.text_sha256 for i in sample}) == 400
    share = Counter(i.status for i in sample)["WITHDRAWN"] / 400
    assert share == pytest.approx(1 / 3, abs=0.02)


def test_split_is_100_dev_and_300_test_stratified_by_status() -> None:
    sample = stratified_sample(_pool(5000), 400, seed=42)
    split = split_dev_test(sample, 100, seed=42)

    assert Counter(i.split for i in split) == {"dev": 100, "test": 300}
    by_status = Counter((i.status, i.split) for i in split)
    for status in ("TERMINATED", "WITHDRAWN"):
        total = by_status[(status, "dev")] + by_status[(status, "test")]
        assert by_status[(status, "dev")] == pytest.approx(total / 4, abs=1)
    assert len({i.text_sha256 for i in split}) == len(split)  # each text in one split only
    assert split == split_dev_test(sample, 100, seed=42)


def test_labels_are_keyed_by_trial_and_text_hash(tmp_path: Path) -> None:
    path = tmp_path / "labels" / "gold_labels.csv"
    items = [
        GoldItem("NCT1", text_sha256(f"r{i}"), "TERMINATED", 2015, f"Synthetic {i}", "test",
                 "ctgov-api-v2 pulled 2026-09-23")
        for i in range(3)
    ]  # fmt: skip
    when = dt.datetime(2026, 9, 23, tzinfo=dt.UTC)

    save_label(path, items[1], "accrual", now=when)
    save_label(path, items[0], "funding", now=when)
    save_label(path, items[1], "business", now=when)

    labels = read_labels(path)
    assert {k: v["label"] for k, v in labels.items()} == {
        items[0].key: "funding",
        items[1].key: "business",
    }
    assert labels[items[0].key]["source"] == "ctgov-api-v2 pulled 2026-09-23"
    assert "Synthetic" not in path.read_text(encoding="utf-8")
    with path.open(newline="", encoding="utf-8") as fh:
        assert next(csv.reader(fh)) == [
            "nct_id",
            "text_sha256",
            "split",
            "label",
            "source",
            "labeled_at",
        ]
    assert next_unlabeled(items, labels) == items[2]
    with pytest.raises(ValueError, match="unknown label"):
        save_label(path, items[2], "vague")


def _study(nct: str, status: str, why: str | None, completion: str, ctype: str) -> dict[str, Any]:
    status_module: dict[str, Any] = {
        "overallStatus": status,
        "completionDateStruct": {"date": completion, "type": ctype},
        "lastUpdatePostDateStruct": {"date": "2024-03-01", "type": "ACTUAL"},
    }
    if why is not None:
        status_module["whyStopped"] = why
    return {
        "protocolSection": {"identificationModule": {"nctId": nct}, "statusModule": status_module}
    }


@respx.mock
def test_build_gold_sample_end_to_end(cfg: ProjectConfig, tmp_path: Path) -> None:
    respx.get(VERSION_URL).mock(
        return_value=httpx.Response(200, json={"dataTimestamp": "2026-09-23T09:00:00"})
    )
    page1 = [_study(f"NCT{i:08d}", "TERMINATED", f"Reason {i}.", "2018-05-01", "ACTUAL")
             for i in range(300)]  # fmt: skip
    page2 = [_study(f"NCT{i:08d}", "WITHDRAWN", f"Other reason {i}", "2030-01-01", "ESTIMATED")
             for i in range(300, 500)]  # fmt: skip
    page2.append(_study("NCT99999999", "WITHDRAWN", "reason 7", "2020-01-01", "ACTUAL"))
    route = respx.get(STUDIES_URL).mock(
        side_effect=[
            httpx.Response(200, json={"studies": page1, "nextPageToken": "t1", "totalCount": 501}),
            httpx.Response(200, json={"studies": page2}),
        ]
    )
    api = ApiClient(httpx.Client(), per_minute=60_000, wait=wait_none(), sleep=lambda _: None)
    out = tmp_path / "gold_sample.csv"

    summary = build_gold_sample(api, cfg, out, dt.date(2026, 9, 23), tmp_path / "cache")

    first_call = route.calls[0].request.url.params
    assert first_call["filter.overallStatus"] == "TERMINATED|WITHDRAWN"
    assert "AREA[StudyType]INTERVENTIONAL" in first_call["filter.advanced"]
    assert summary["distinct_texts"] == 500  # "reason 7" duplicates "Reason 7."
    assert summary["pull_date"] == "2026-09-23"
    sample = load_sample(out)
    assert len(sample) == 400
    assert Counter(i.split for i in sample) == {"dev": 100, "test": 300}
    assert {i.source for i in sample} == {"ctgov-api-v2 pulled 2026-09-23"}
    assert {i.stop_year for i in sample if i.status == "WITHDRAWN"} == {2024}  # not ACTUAL
    assert {i.stop_year for i in sample if i.status == "TERMINATED"} == {2018}

    # A rerun on a later day reads the cache: no requests, same source and sample.
    again_api = ApiClient(httpx.Client(), per_minute=60_000)
    again = build_gold_sample(again_api, cfg, out, dt.date(2026, 10, 1), tmp_path / "cache")
    assert again["requests_this_run"] == 0
    assert load_sample(out) == sample
