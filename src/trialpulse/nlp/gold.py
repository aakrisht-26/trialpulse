"""The gold set for reason labeling (CLAUDE.md Section 11).

400 distinct why_stopped texts from the current ClinicalTrials.gov records (API v2) of the
cohort's early stops (TERMINATED or WITHDRAWN, non-blank why_stopped). Labeling does not
wait for the version-history dataset.

- Texts are normalized (lowercase, whitespace collapsed, surrounding punctuation trimmed)
  and deduplicated before sampling: each normalized text appears once, in one split only.
- The sample is stratified by status and stop year. The stop year is the year of the
  actual completion date when present, otherwise the year of the last update post date.
- The split is 100 dev and 300 test, stratified by status, with a fixed seed.
- Labels are keyed by (nct_id, text_sha256), where text_sha256 is the SHA-256 of the
  normalized text, plus a source column with the pull date, so they survive a later
  switch of history source. The texts stay under data/ (gitignored); labels/ holds ids,
  hashes and labels only.
- The labels are reference labels from an adjudicated model panel (ADR 0010), written by
  trialpulse.nlp.panel with method model-panel-v1 and a panel outcome.
- The labeling app remains as an optional review tool for dev texts only: test texts are
  never opened once the reference labels are committed. It writes to
  data/nlp/review_labels.csv with method manual, and save_label never overwrites a panel
  label. A dev text may show a suggested label from data/nlp/dev_suggestions.csv
  (gitignored), and each saved label records whether it was assisted.

    uv run python -m trialpulse.nlp.gold --build
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import random
import unicodedata
from collections import defaultdict
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx

from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.ingest.ctgov_api import (
    MAX_PAGE_SIZE,
    VERSION_URL,
    ApiClient,
    get_path,
    iter_study_pages,
    query_dir,
)
from trialpulse.nlp.taxonomy import validate_label

GOLD_SIZE = 400
DEV_SIZE = 100
NLP_DIR = REPO_ROOT / "data" / "nlp"
PULL_CACHE = NLP_DIR / "api_pull"
SAMPLE_PATH = NLP_DIR / "gold_sample.csv"
LABELS_PATH = REPO_ROOT / "labels" / "gold_labels.csv"
REVIEW_LABELS_PATH = NLP_DIR / "review_labels.csv"
SUGGESTIONS_PATH = NLP_DIR / "dev_suggestions.csv"
LABEL_COLUMNS = (
    "nct_id",
    "text_sha256",
    "split",
    "label",
    "source",
    "labeled_at",
    "assisted",
    "method",
    "panel_outcome",
)
PANEL_METHOD = "model-panel-v1"  # reference labels from an adjudicated model panel
MANUAL_METHOD = "manual"  # a label saved in the labeling app
PANEL_OUTCOMES = ("unanimous", "majority", "adjudicated")
SUGGESTION_COLUMNS = ("nct_id", "text_sha256", "suggestion")
SAMPLE_COLUMNS = ("nct_id", "text_sha256", "status", "stop_year", "split", "source", "why_stopped")

_S = "protocolSection.statusModule"
PULL_FIELDS = (
    "protocolSection.identificationModule.nctId",
    f"{_S}.overallStatus",
    f"{_S}.whyStopped",
    f"{_S}.completionDateStruct",
    f"{_S}.lastUpdatePostDateStruct",
)


@dataclass(frozen=True)
class EarlyStop:
    """The current record of one early-stopped trial, as pulled."""

    nct_id: str
    status: str
    why_stopped: str | None
    completion_date: str | None
    completion_date_type: str | None
    last_update_post_date: str | None


@dataclass(frozen=True)
class GoldItem:
    nct_id: str
    text_sha256: str
    status: str
    stop_year: int
    why_stopped: str
    split: str = ""
    source: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.nct_id, self.text_sha256)


def normalize_text(text: str) -> str:
    """Lowercase, collapse whitespace, and trim punctuation around the text. Punctuation
    inside the text is kept."""
    collapsed = " ".join(text.split()).lower()
    start, end = 0, len(collapsed)
    while start < end and _is_trimmable(collapsed[start]):
        start += 1
    while end > start and _is_trimmable(collapsed[end - 1]):
        end -= 1
    return collapsed[start:end]


def _is_trimmable(char: str) -> bool:
    return char.isspace() or unicodedata.category(char).startswith("P")


def text_sha256(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def stop_year(
    completion_date: str | None, completion_type: str | None, last_update_post_date: str | None
) -> int | None:
    """The year of the actual completion date when present, otherwise the year of the last
    update post date."""
    if completion_date and (completion_type or "").upper() == "ACTUAL":
        return int(completion_date[:4])
    if last_update_post_date:
        return int(last_update_post_date[:4])
    return None


def pull_params(cfg: ProjectConfig) -> dict[str, str | int]:
    """The cohort's early stops, current records, only the fields the gold set needs."""
    return {
        "filter.advanced": (
            f"AREA[StudyType]{cfg.population.study_type} AND AREA[StudyFirstPostDate]"
            f"RANGE[{cfg.population.min_first_post_date.isoformat()},MAX]"
        ),
        "filter.overallStatus": "|".join(cfg.statuses.early_stop),
        "fields": ",".join(PULL_FIELDS),
        "pageSize": MAX_PAGE_SIZE,
    }


def _to_early_stop(study: dict[str, Any]) -> EarlyStop:
    return EarlyStop(
        nct_id=str(get_path(study, PULL_FIELDS[0])),
        status=str(get_path(study, f"{_S}.overallStatus")),
        why_stopped=get_path(study, f"{_S}.whyStopped"),
        completion_date=get_path(study, f"{_S}.completionDateStruct.date"),
        completion_date_type=get_path(study, f"{_S}.completionDateStruct.type"),
        last_update_post_date=get_path(study, f"{_S}.lastUpdatePostDateStruct.date"),
    )


def pull_early_stops(
    api: ApiClient, cfg: ProjectConfig, cache_root: Path, today: dt.date
) -> tuple[list[EarlyStop], dict[str, Any]]:
    """Pull (or read from the cache) the current records, and the pull's metadata: the pull
    date and the API data timestamp, both fixed when the pull starts. They are saved before
    the first page is requested, so an interrupted pull that resumes on a later day keeps
    its original date."""
    params = pull_params(cfg)
    meta_path = query_dir(cache_root, params) / "pull.json"
    if meta_path.is_file():
        meta: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
    else:
        version = api.get_json(VERSION_URL)
        meta = {"pull_date": today.isoformat(), "data_timestamp": version.get("dataTimestamp")}
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = meta_path.with_name(meta_path.name + ".tmp")
        tmp.write_text(json.dumps(meta), encoding="utf-8")
        tmp.replace(meta_path)
    records = [
        _to_early_stop(s) for page in iter_study_pages(api, cache_root, params) for s in page
    ]
    return records, {**meta, "records": len(records)}


def distinct_items(
    records: Iterable[EarlyStop], early_stop: Sequence[str], source: str
) -> tuple[list[GoldItem], dict[str, int]]:
    """One item per distinct normalized text, from early stops with a non-blank reason and
    a known stop year. For repeated texts the trial with the lowest NCT ID is kept."""
    kept: dict[str, GoldItem] = {}
    stats = {"records": 0, "non_blank": 0, "no_stop_year": 0}
    for record in sorted(records, key=lambda r: r.nct_id):
        stats["records"] += 1
        if record.status.upper() not in early_stop or not record.why_stopped:
            continue
        normalized = normalize_text(record.why_stopped)
        if not normalized:
            continue
        stats["non_blank"] += 1
        year = stop_year(
            record.completion_date, record.completion_date_type, record.last_update_post_date
        )
        if year is None:
            stats["no_stop_year"] += 1
            continue
        digest = text_sha256(normalized)
        if digest not in kept:
            kept[digest] = GoldItem(
                record.nct_id, digest, record.status.upper(), year, record.why_stopped.strip(),
                source=source,
            )  # fmt: skip
    stats["distinct_texts"] = len(kept)
    return sorted(kept.values(), key=lambda i: i.key), stats


def allocate[K: Hashable](sizes: dict[K, int], total: int) -> dict[K, int]:
    """Proportional allocation with largest remainders, at least one per non-empty stratum
    when the total allows, and never more than a stratum holds."""
    strata = sorted((k for k, n in sizes.items() if n > 0), key=repr)
    population = sum(sizes[k] for k in strata)
    if population <= total:
        return {k: sizes[k] for k in strata}
    quotas = {k: total * sizes[k] / population for k in strata}
    alloc = {k: min(sizes[k], math.floor(quotas[k])) for k in strata}
    if total >= len(strata):
        for k in strata:
            alloc[k] = max(alloc[k], 1)
    by_remainder = sorted(strata, key=lambda k: (-(quotas[k] - math.floor(quotas[k])), repr(k)))
    while sum(alloc.values()) < total:
        progressed = False
        for k in by_remainder:
            if sum(alloc.values()) == total:
                break
            if alloc[k] < sizes[k]:
                alloc[k] += 1
                progressed = True
        if not progressed:
            break
    while sum(alloc.values()) > total:  # minimums can overshoot: trim the largest strata
        k = max(strata, key=lambda s: (alloc[s], repr(s)))
        alloc[k] -= 1
    return alloc


def stratified_sample(items: Sequence[GoldItem], total: int, seed: int) -> list[GoldItem]:
    """A seeded sample stratified by (status, stop year)."""
    by_stratum: dict[tuple[str, int], list[GoldItem]] = defaultdict(list)
    for item in sorted(items, key=lambda i: i.key):
        by_stratum[(item.status, item.stop_year)].append(item)
    alloc = allocate({k: len(v) for k, v in by_stratum.items()}, total)
    rng = random.Random(seed)
    chosen: list[GoldItem] = []
    for stratum in sorted(alloc):
        chosen += rng.sample(by_stratum[stratum], alloc[stratum])
    return sorted(chosen, key=lambda i: i.key)


def split_dev_test(items: Sequence[GoldItem], dev_size: int, seed: int) -> list[GoldItem]:
    """Seeded split, stratified by status: dev_size items for prompt development (shared
    across statuses in proportion), the rest held out as test."""
    by_status: dict[str, list[GoldItem]] = defaultdict(list)
    for item in sorted(items, key=lambda i: i.key):
        by_status[item.status].append(item)
    dev_alloc = allocate({s: len(v) for s, v in by_status.items()}, dev_size)
    rng = random.Random(seed)
    dev_keys: set[tuple[str, str]] = set()
    for status in sorted(by_status):
        members = list(by_status[status])
        rng.shuffle(members)
        dev_keys |= {m.key for m in members[: dev_alloc.get(status, 0)]}
    return [replace(i, split="dev" if i.key in dev_keys else "test") for i in items]


def write_sample(items: Sequence[GoldItem], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(SAMPLE_COLUMNS)
        for i in items:
            writer.writerow(
                [i.nct_id, i.text_sha256, i.status, i.stop_year, i.split, i.source, i.why_stopped]
            )


def load_sample(path: Path) -> list[GoldItem]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [
            GoldItem(r["nct_id"], r["text_sha256"], r["status"], int(r["stop_year"]),
                     r["why_stopped"], r["split"], r["source"])
            for r in csv.DictReader(fh)
        ]  # fmt: skip


def read_labels(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {(r["nct_id"], r["text_sha256"]): r for r in csv.DictReader(fh)}


def write_labels(path: Path, labels: dict[tuple[str, str], dict[str, str]]) -> None:
    """Write a labels file atomically, sorted by key. Columns missing from a row (a file
    written before a column existed) are left blank."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        for key in sorted(labels):
            writer.writerow({c: labels[key].get(c, "") for c in LABEL_COLUMNS})
    tmp.replace(path)


def save_label(
    path: Path,
    item: GoldItem,
    label: str,
    now: dt.datetime | None = None,
    assisted: bool = False,
) -> None:
    """Record or replace one label saved in the labeling app (method manual).
    assisted records whether a suggestion was shown when the label was given; it can only be
    true for a dev item. A reference label from the model panel is never overwritten."""
    validate_label(label)
    if assisted and item.split != "dev":
        raise ValueError(
            f"{item.split or 'unsplit'} items are labeled blind; assisted must be false"
        )
    labels = read_labels(path)
    if labels.get(item.key, {}).get("method") == PANEL_METHOD:
        raise ValueError(f"{path} holds panel reference labels; save review labels elsewhere")
    stamp = (now or dt.datetime.now(dt.UTC)).isoformat(timespec="seconds")
    labels[item.key] = {
        "nct_id": item.nct_id,
        "text_sha256": item.text_sha256,
        "split": item.split,
        "label": label,
        "source": item.source,
        "labeled_at": stamp,
        "assisted": "true" if assisted else "false",
        "method": MANUAL_METHOD,
        "panel_outcome": "",
    }
    write_labels(path, labels)


def next_unlabeled(
    items: Sequence[GoldItem], labels: dict[tuple[str, str], dict[str, str]]
) -> GoldItem | None:
    return next((i for i in items if i.key not in labels), None)


def review_items(items: Iterable[GoldItem]) -> list[GoldItem]:
    """The items the review app may show: dev items only, in key order. Test texts are never
    opened once the reference labels are committed (ADR 0010)."""
    return sorted((i for i in items if i.split == "dev"), key=lambda i: i.key)


def write_dev_suggestions(path: Path, suggestions: Sequence[tuple[GoldItem, str]]) -> None:
    """Save suggested labels for dev items. Test items are labeled blind, so a suggestion
    for any item outside the dev split is refused."""
    for item, label in suggestions:
        validate_label(label)
        if item.split != "dev":
            raise ValueError(f"suggestions are for dev items only, not {item.key}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(SUGGESTION_COLUMNS)
        for item, label in sorted(suggestions, key=lambda s: s[0].key):
            writer.writerow([item.nct_id, item.text_sha256, label])


def suggestion_for(item: GoldItem, suggestions: dict[tuple[str, str], str]) -> str | None:
    """The suggestion to show for an item, if any. Test items never get one."""
    return suggestions.get(item.key) if item.split == "dev" else None


def load_dev_suggestions(path: Path, items: Iterable[GoldItem]) -> dict[tuple[str, str], str]:
    """Suggested labels keyed by item, for dev items only. A row for any other item is
    ignored, so no suggestion can reach a test item even if the file contains one."""
    if not path.is_file():
        return {}
    dev_keys = {i.key for i in items if i.split == "dev"}
    suggestions: dict[tuple[str, str], str] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = (row["nct_id"], row["text_sha256"])
            if key in dev_keys:
                suggestions[key] = validate_label(row["suggestion"])
    return suggestions


def source_label(meta: dict[str, Any]) -> str:
    return f"ctgov-api-v2 pulled {meta['pull_date']}"


def build_gold_sample(
    api: ApiClient, cfg: ProjectConfig, out: Path, today: dt.date, cache_root: Path = PULL_CACHE
) -> dict[str, Any]:
    records, meta = pull_early_stops(api, cfg, cache_root, today)
    items, stats = distinct_items(records, cfg.statuses.early_stop, source_label(meta))
    seed = cfg.seeds.default
    sample = split_dev_test(stratified_sample(items, GOLD_SIZE, seed), DEV_SIZE, seed)
    write_sample(sample, out)
    return {**meta, **stats, "sampled": len(sample), "requests_this_run": api.requests_made}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the gold sample for reason labeling")
    parser.add_argument("--build", action="store_true", required=True)
    parser.parse_args(argv)
    cfg = load_project_config()
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        summary = build_gold_sample(
            ApiClient(client), cfg, SAMPLE_PATH, dt.datetime.now(dt.UTC).date()
        )
    print(json.dumps(summary, indent=2))
    print(f"wrote {summary['sampled']} items to {SAMPLE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
