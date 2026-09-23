"""The gold set for reason labeling (CLAUDE.md Section 11).

400 why_stopped texts, stratified by status (TERMINATED or WITHDRAWN) and stop year, split
100 dev and 300 test with a fixed seed. The text of each early stop is the why_stopped of
its event version: the first version whose status is an early stop (Section 6).

The sample with texts lives under data/ (gitignored). Labels are written to
labels/gold_labels.csv, keyed by nct_id and nct_version, without any text.

    uv run python -m trialpulse.nlp.gold --build
"""

import argparse
import csv
import datetime as dt
import math
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb

from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.nlp.taxonomy import validate_label

GOLD_SIZE = 400
DEV_SIZE = 100
SAMPLE_PATH = REPO_ROOT / "data" / "nlp" / "gold_sample.csv"
LABELS_PATH = REPO_ROOT / "labels" / "gold_labels.csv"
LABEL_COLUMNS = ("nct_id", "nct_version", "split", "label", "labeled_at")


@dataclass(frozen=True)
class GoldItem:
    nct_id: str
    nct_version: int
    status: str
    stop_year: int
    why_stopped: str
    split: str = ""

    @property
    def key(self) -> tuple[str, int]:
        return (self.nct_id, self.nct_version)


def early_stop_texts(
    con: duckdb.DuckDBPyConnection, glob: str, cfg: ProjectConfig
) -> list[GoldItem]:
    """The event version of every early stop in the population, with a non-empty reason."""
    early = ", ".join(f"'{s}'" for s in cfg.statuses.early_stop)
    study_type = cfg.population.study_type.replace("'", "''")
    min_date = cfg.population.min_first_post_date.isoformat()
    rows = con.execute(
        f"""SELECT nct_id, nct_version, upper(overall_status), year(last_update_post_date),
                   trim(why_stopped)
        FROM read_parquet('{glob}')
        WHERE upper(overall_status) IN ({early})
          AND upper(study_type) = '{study_type}'
          AND study_first_post_date >= DATE '{min_date}'
        QUALIFY row_number() OVER (PARTITION BY nct_id ORDER BY nct_version) = 1"""
    ).fetchall()
    return [
        GoldItem(str(r[0]), int(r[1]), str(r[2]), int(r[3]), str(r[4]))
        for r in rows
        if r[3] is not None and r[4]
    ]


def allocate(sizes: dict[tuple[str, int], int], total: int) -> dict[tuple[str, int], int]:
    """Proportional allocation with largest remainders, at least one per non-empty stratum
    when the total allows, and never more than a stratum holds."""
    strata = sorted(k for k, n in sizes.items() if n > 0)
    population = sum(sizes[k] for k in strata)
    if population <= total:
        return {k: sizes[k] for k in strata}
    quotas = {k: total * sizes[k] / population for k in strata}
    alloc = {k: min(sizes[k], math.floor(quotas[k])) for k in strata}
    if total >= len(strata):
        for k in strata:
            alloc[k] = max(alloc[k], 1)
    by_remainder = sorted(strata, key=lambda k: (-(quotas[k] - math.floor(quotas[k])), k))
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
        k = max(strata, key=lambda s: (alloc[s], s))
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
    """Seeded split: dev_size items for prompt development, the rest held out as test."""
    order = list(range(len(items)))
    random.Random(seed).shuffle(order)
    dev = set(order[:dev_size])
    return [
        GoldItem(i.nct_id, i.nct_version, i.status, i.stop_year, i.why_stopped,
                 "dev" if n in dev else "test")
        for n, i in enumerate(items)
    ]  # fmt: skip


def write_sample(items: Sequence[GoldItem], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["nct_id", "nct_version", "status", "stop_year", "split", "why_stopped"])
        for i in items:
            writer.writerow(
                [i.nct_id, i.nct_version, i.status, i.stop_year, i.split, i.why_stopped]
            )


def load_sample(path: Path) -> list[GoldItem]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [
            GoldItem(r["nct_id"], int(r["nct_version"]), r["status"], int(r["stop_year"]),
                     r["why_stopped"], r["split"])
            for r in csv.DictReader(fh)
        ]  # fmt: skip


def read_labels(path: Path) -> dict[tuple[str, int], dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {(r["nct_id"], int(r["nct_version"])): r for r in csv.DictReader(fh)}


def save_label(path: Path, item: GoldItem, label: str, now: dt.datetime | None = None) -> None:
    """Record or replace one label. The file is rewritten atomically, sorted by key."""
    validate_label(label)
    labels = read_labels(path)
    stamp = (now or dt.datetime.now(dt.UTC)).isoformat(timespec="seconds")
    labels[item.key] = {
        "nct_id": item.nct_id,
        "nct_version": str(item.nct_version),
        "split": item.split,
        "label": label,
        "labeled_at": stamp,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        for key in sorted(labels):
            writer.writerow({c: labels[key][c] for c in LABEL_COLUMNS})
    tmp.replace(path)


def next_unlabeled(
    items: Sequence[GoldItem], labels: dict[tuple[str, int], dict[str, str]]
) -> GoldItem | None:
    return next((i for i in items if i.key not in labels), None)


def build_gold_sample(glob: str, cfg: ProjectConfig, out: Path) -> list[GoldItem]:
    with duckdb.connect() as con:
        pool = early_stop_texts(con, glob, cfg)
    sample = split_dev_test(stratified_sample(pool, GOLD_SIZE, cfg.seeds.default), DEV_SIZE,
                            cfg.seeds.default)  # fmt: skip
    write_sample(sample, out)
    return sample


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the gold sample for reason labeling")
    parser.add_argument("--build", action="store_true", required=True)
    parser.parse_args(argv)
    cfg = load_project_config()
    if not cfg.dataset.revision:
        print("dataset.revision is not pinned; run Step 2 part a first")
        return 2
    glob = (
        REPO_ROOT / "data" / "raw" / "history" / cfg.dataset.revision / cfg.dataset.config_name
    ).as_posix() + "/*.parquet"
    sample = build_gold_sample(glob, cfg, SAMPLE_PATH)
    print(f"wrote {len(sample)} items to {SAMPLE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
