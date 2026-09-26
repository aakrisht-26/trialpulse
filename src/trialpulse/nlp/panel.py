"""Reference labels from an adjudicated model panel (ADR 0010).

The gold set is not labeled by hand. Three independent labelers label every text, seeing
only docs/labeling_guide.md and the text under an opaque panel id. For a split, a fourth
labeler that did not label the text adjudicates. A fresh labeler then relabels a seeded 10%
as a consistency check. The labelers are model agents run outside this module; this module
prepares their inputs, checks and reads their outputs, and writes labels/gold_labels.csv.

Files in data/nlp/panel/ (gitignored):

- key.csv: panel id to (nct_id, text_sha256, split). Never shown to a labeler.
- inputs/batch{b}_labeler{r}.json: [{"id", "text"}] in a seeded order per labeler.
- votes.csv: one row per (panel id, labeler) with the label, the deciding words and the
  justification.
- adjudication_input.json and adjudications.csv: the splits and the adjudicator's decisions.
- consistency_input.json and consistency.csv: the seeded 10% and the fresh labeler's labels.
- run.json: the run record (panel model, guide hash, labeled_at).

    uv run python -m trialpulse.nlp.panel --prepare
    uv run python -m trialpulse.nlp.panel --adjudication-input
    uv run python -m trialpulse.nlp.panel --consistency-input
    uv run python -m trialpulse.nlp.panel --finalize
    uv run python -m trialpulse.nlp.panel --report
"""

import argparse
import csv
import json
import random
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

from trialpulse.config import load_project_config
from trialpulse.nlp.evaluate import cohen_kappa
from trialpulse.nlp.gold import (
    LABELS_PATH,
    NLP_DIR,
    PANEL_METHOD,
    PANEL_OUTCOMES,
    SAMPLE_PATH,
    GoldItem,
    load_sample,
    normalize_text,
    write_labels,
)
from trialpulse.nlp.taxonomy import LABELS, validate_label

PANEL_DIR = NLP_DIR / "panel"
N_LABELERS = 3
BATCH_SIZE = 100
CONSISTENCY_FRACTION = 0.10
PANEL_FAMILY = "claude"
PANEL_FAMILY_MARKERS = ("claude", "anthropic")
KEY_COLUMNS = ("panel_id", "nct_id", "text_sha256", "split")
VOTE_COLUMNS = ("panel_id", "labeler", "label", "deciding_words", "justification")
ADJUDICATION_COLUMNS = ("panel_id", "adjudicator", "label", "rationale")
FRAGMENT_SEPARATOR = " | "  # between deciding-word fragments quoted from one text


@dataclass(frozen=True)
class KeyEntry:
    panel_id: str
    nct_id: str
    text_sha256: str
    split: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.nct_id, self.text_sha256)


@dataclass(frozen=True)
class Vote:
    panel_id: str
    labeler: str
    label: str
    deciding_words: str
    justification: str


@dataclass(frozen=True)
class Adjudication:
    panel_id: str
    adjudicator: str
    label: str
    rationale: str


def check_graded_model(model_id: str) -> str:
    """Refuse a model from the panel's family: a model graded against the reference labels
    must come from a different family (ADR 0010)."""
    if any(marker in model_id.lower() for marker in PANEL_FAMILY_MARKERS):
        raise ValueError(
            f"{model_id!r} is from the panel's model family ({PANEL_FAMILY}); a model graded "
            "against the reference labels must come from a different family"
        )
    return model_id


def assign_panel_ids(items: Sequence[GoldItem], seed: int) -> list[tuple[str, GoldItem]]:
    """Opaque ids p001, p002, ... in a seeded order, so an id reveals neither the trial nor
    the split."""
    ordered = sorted(items, key=lambda i: i.key)
    random.Random(f"{seed}:panel-ids").shuffle(ordered)
    width = max(3, len(str(len(ordered))))
    return [(f"p{n:0{width}d}", item) for n, item in enumerate(ordered, start=1)]


def labeler_inputs(
    assigned: Sequence[tuple[str, GoldItem]], batch_size: int, n_labelers: int, seed: int
) -> dict[str, list[dict[str, str]]]:
    """Input file name to its items ({"id", "text"} only). Each batch goes to n_labelers
    labelers, and each labeler gets its own seeded order."""
    files: dict[str, list[dict[str, str]]] = {}
    for b, start in enumerate(range(0, len(assigned), batch_size), start=1):
        batch = [
            {"id": pid, "text": item.why_stopped}
            for pid, item in assigned[start : start + batch_size]
        ]
        for r in range(1, n_labelers + 1):
            order = list(batch)
            random.Random(f"{seed}:batch{b}:labeler{r}").shuffle(order)
            files[f"batch{b}_labeler{r}.json"] = order
    return files


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row[c] for c in columns})
    tmp.replace(path)


def _read_csv(path: Path, columns: Sequence[str]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != tuple(columns):
            raise ValueError(f"{path.name}: expected columns {columns}, got {reader.fieldnames}")
        return list(reader)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def prepare(items: Sequence[GoldItem], panel_dir: Path, seed: int) -> dict[str, int]:
    """Write the key and the labeler inputs. Deterministic for a given sample and seed. An
    existing key that differs is refused, since votes may already refer to it."""
    assigned = assign_panel_ids(items, seed)
    key_rows = [
        {"panel_id": pid, "nct_id": i.nct_id, "text_sha256": i.text_sha256, "split": i.split}
        for pid, i in assigned
    ]
    key_path = panel_dir / "key.csv"
    if key_path.is_file() and _read_csv(key_path, KEY_COLUMNS) != key_rows:
        raise ValueError(f"{key_path} exists and differs from this sample; votes may refer to it")
    _write_csv(key_path, KEY_COLUMNS, key_rows)
    files = labeler_inputs(assigned, BATCH_SIZE, N_LABELERS, seed)
    for name, rows in files.items():
        _write_json(panel_dir / "inputs" / name, rows)
    return {"items": len(assigned), "input_files": len(files), "labelers_per_text": N_LABELERS}


def read_key(path: Path) -> dict[str, KeyEntry]:
    return {r["panel_id"]: KeyEntry(**r) for r in _read_csv(path, KEY_COLUMNS)}


def write_votes(path: Path, votes: Iterable[Vote]) -> None:
    rows = sorted(votes, key=lambda v: (v.panel_id, v.labeler))
    _write_csv(path, VOTE_COLUMNS, (v.__dict__ for v in rows))


def read_votes(path: Path) -> dict[str, list[Vote]]:
    votes: dict[str, list[Vote]] = defaultdict(list)
    for row in _read_csv(path, VOTE_COLUMNS):
        votes[row["panel_id"]].append(Vote(**row))
    return dict(votes)


def write_adjudications(path: Path, decisions: Iterable[Adjudication]) -> None:
    rows = sorted(decisions, key=lambda d: d.panel_id)
    _write_csv(path, ADJUDICATION_COLUMNS, (d.__dict__ for d in rows))


def read_adjudications(path: Path) -> dict[str, Adjudication]:
    decisions: dict[str, Adjudication] = {}
    for row in _read_csv(path, ADJUDICATION_COLUMNS):
        if row["panel_id"] in decisions:
            raise ValueError(f"two adjudications for {row['panel_id']}")
        decisions[row["panel_id"]] = Adjudication(**row)
    return decisions


def check_votes(votes: Mapping[str, Sequence[Vote]], ids: Iterable[str], n_labelers: int) -> None:
    """Every id has n_labelers valid votes from distinct labelers, and no vote is for an
    unknown id."""
    expected = set(ids)
    unknown = set(votes) - expected
    if unknown:
        raise ValueError(f"votes for unknown panel ids: {sorted(unknown)[:5]}")
    for pid in sorted(expected):
        cast = votes.get(pid, [])
        if len(cast) != n_labelers or len({v.labeler for v in cast}) != n_labelers:
            raise ValueError(f"{pid} needs {n_labelers} votes from distinct labelers")
        for v in cast:
            validate_label(v.label)


def resolve(labels: Sequence[str], adjudicated: str | None) -> tuple[str, str]:
    """The reference label and the panel outcome. A unanimous label stands; a split takes
    the adjudicator's label, as a majority outcome when two labelers gave it and as an
    adjudicated outcome otherwise."""
    for label in labels:
        validate_label(label)
    counts = Counter(labels)
    top, n = counts.most_common(1)[0]
    if n == len(labels):
        if adjudicated is not None:
            raise ValueError("a unanimous text is not adjudicated")
        return top, "unanimous"
    if adjudicated is None:
        raise ValueError("a split needs an adjudication")
    validate_label(adjudicated)
    return adjudicated, "majority" if 2 * counts[adjudicated] > len(labels) else "adjudicated"


def split_ids(votes: Mapping[str, Sequence[Vote]]) -> list[str]:
    return sorted(pid for pid, cast in votes.items() if len({v.label for v in cast}) > 1)


def adjudication_input(
    votes: Mapping[str, Sequence[Vote]], texts: Mapping[str, str]
) -> list[dict[str, Any]]:
    """For each split: the text and the three labels with their justifications, and nothing
    else about the trial."""
    return [
        {
            "id": pid,
            "text": texts[pid],
            "labelers": [
                {
                    "label": v.label,
                    "deciding_words": v.deciding_words,
                    "justification": v.justification,
                }
                for v in sorted(votes[pid], key=lambda v: v.labeler)
            ],
        }
        for pid in split_ids(votes)
    ]


def consistency_ids(ids: Iterable[str], fraction: float, seed: int) -> list[str]:
    """A seeded random sample of the panel ids, in the drawn order."""
    pool = sorted(ids)
    return random.Random(f"{seed}:consistency").sample(pool, round(fraction * len(pool)))


def reference_rows(
    key: Mapping[str, KeyEntry],
    items: Mapping[tuple[str, str], GoldItem],
    votes: Mapping[str, Sequence[Vote]],
    decisions: Mapping[str, Adjudication],
    labeled_at: str,
) -> dict[tuple[str, str], dict[str, str]]:
    """One labels-file row per gold item. Every split must have exactly one adjudication,
    by an adjudicator that did not label the text, and a unanimous text none."""
    check_votes(votes, key, N_LABELERS)
    unknown = set(decisions) - set(key)
    if unknown:
        raise ValueError(f"adjudications for unknown panel ids: {sorted(unknown)[:5]}")
    rows: dict[tuple[str, str], dict[str, str]] = {}
    for pid, entry in key.items():
        decision = decisions.get(pid)
        if decision is not None and decision.adjudicator in {v.labeler for v in votes[pid]}:
            raise ValueError(f"{pid} was adjudicated by one of its labelers")
        label, outcome = resolve(
            [v.label for v in votes[pid]], decision.label if decision else None
        )
        item = items[entry.key]
        rows[entry.key] = {
            "nct_id": entry.nct_id,
            "text_sha256": entry.text_sha256,
            "split": entry.split,
            "label": label,
            "source": item.source,
            "labeled_at": labeled_at,
            "assisted": "false",
            "method": PANEL_METHOD,
            "panel_outcome": outcome,
        }
    return rows


def quoted_verbatim(deciding_words: str, text: str) -> bool:
    """Whether every quoted fragment appears in the text as whole words, after
    normalization."""
    target = normalize_text(text)
    fragments = [normalize_text(f) for f in deciding_words.split(FRAGMENT_SEPARATOR.strip())]
    return bool(fragments) and all(
        f and re.search(rf"(?<!\w){re.escape(f)}(?!\w)", target) for f in fragments
    )


def check_consistency(
    consistency: Mapping[str, Sequence[Vote]],
    expected_ids: Iterable[str],
    votes: Mapping[str, Sequence[Vote]],
    decisions: Mapping[str, Adjudication],
) -> None:
    """The consistency relabels cover exactly the seeded sample, one valid vote per text,
    each by a fresh labeler: not one of the text's labelers and not its adjudicator."""
    if set(consistency) != set(expected_ids):
        raise ValueError("consistency relabels do not match the seeded consistency sample")
    for pid, cast in consistency.items():
        if len(cast) != 1:
            raise ValueError(f"{pid} needs exactly one consistency relabel")
        validate_label(cast[0].label)
        earlier = {v.labeler for v in votes[pid]}
        if pid in decisions:
            earlier.add(decisions[pid].adjudicator)
        if cast[0].labeler in earlier:
            raise ValueError(f"{pid} was relabeled by one of its panel members, not a fresh one")


def _share(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def panel_report(
    key: Mapping[str, KeyEntry],
    texts: Mapping[str, str],
    votes: Mapping[str, Sequence[Vote]],
    rows: Mapping[tuple[str, str], Mapping[str, str]],
    consistency: Mapping[str, Sequence[Vote]],
) -> dict[str, Any]:
    """Counts and agreement only; no text and no per-item label."""
    report: dict[str, Any] = {"items": len(key)}
    for split in ("all", "dev", "test"):
        chosen = [r for r in rows.values() if split == "all" or r["split"] == split]
        outcomes = Counter(r["panel_outcome"] for r in chosen)
        report[split] = {
            "n": len(chosen),
            "labels": {label: sum(r["label"] == label for r in chosen) for label in LABELS},
            "outcomes": {o: outcomes[o] for o in PANEL_OUTCOMES},
            "outcome_shares": {o: _share(outcomes[o], len(chosen)) for o in PANEL_OUTCOMES},
        }
    pairs = [
        (a.label == b.label)
        for cast in votes.values()
        for a, b in combinations(sorted(cast, key=lambda v: v.labeler), 2)
    ]
    report["pairwise_agreement"] = _share(sum(pairs), len(pairs))
    all_votes = [v for cast in votes.values() for v in cast]
    report["deciding_words_verbatim"] = _share(
        sum(bool(quoted_verbatim(v.deciding_words, texts[v.panel_id])) for v in all_votes),
        len(all_votes),
    )
    reference = {pid: rows[entry.key]["label"] for pid, entry in key.items()}
    fresh = sorted((pid, cast[0].label) for pid, cast in consistency.items())
    if fresh:
        ref = [reference[pid] for pid, _ in fresh]
        new = [label for _, label in fresh]
        agree = sum(x == y for x, y in zip(ref, new, strict=True))
        report["consistency"] = {
            "n": len(fresh),
            "agree": agree,
            "agreement": _share(agree, len(fresh)),
            "kappa": round(cohen_kappa(ref, new), 4),
        }
    return report


def _draw(pool: Sequence[str], k: int, seed: str) -> list[str]:
    return sorted(random.Random(seed).sample(sorted(pool), min(k, len(pool))))


def decision_examples(
    key: Mapping[str, KeyEntry],
    texts: Mapping[str, str],
    votes: Mapping[str, Sequence[Vote]],
    decisions: Mapping[str, Adjudication],
    k: int,
    seed: int,
) -> list[dict[str, Any]]:
    """Up to k split texts decided by the adjudicator, for review: the dev ones first, then
    the test ones it overturned, then a seeded draw of the test ones where it kept the
    majority. Each carries the three votes and the adjudicator's rationale."""
    outcome = {p: resolve([v.label for v in votes[p]], d.label)[1] for p, d in decisions.items()}
    dev = _draw([p for p in decisions if key[p].split == "dev"], k, f"{seed}:examples:dev")
    test = [p for p in decisions if key[p].split == "test"]
    overturned = _draw(
        [p for p in test if outcome[p] == "adjudicated"], k - len(dev),
        f"{seed}:examples:test-adjudicated",
    )  # fmt: skip
    kept = _draw(
        [p for p in test if outcome[p] == "majority"], k - len(dev) - len(overturned),
        f"{seed}:examples:test-majority",
    )  # fmt: skip
    return [
        {
            "id": pid,
            "split": key[pid].split,
            "text": texts[pid],
            "votes": [
                {
                    "label": v.label,
                    "deciding_words": v.deciding_words,
                    "justification": v.justification,
                }
                for v in sorted(votes[pid], key=lambda v: v.labeler)
            ],
            "decision": decisions[pid].label,
            "outcome": outcome[pid],
            "rationale": decisions[pid].rationale,
        }
        for pid in [*dev, *overturned, *kept]
    ]


def examples_markdown(examples: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# Adjudicator decisions: examples (reference panel, ADR 0010)",
        "",
        "Gitignored. May contain test texts: never use this file for prompt work.",
        "",
    ]
    for e in examples:
        lines += [
            f"## {e['id']} ({e['split']}): {e['decision']} ({e['outcome']})",
            "",
            f"Text: {e['text']}",
            "",
            "Labelers:",
            *(f'- {v["label"]}: "{v["deciding_words"]}" {v["justification"]}' for v in e["votes"]),
            "",
            f"Adjudicator rationale: {e['rationale']}",
            "",
        ]
    return "\n".join(lines)


def _load(panel_dir: Path, sample_path: Path) -> tuple[
    dict[str, KeyEntry], dict[tuple[str, str], GoldItem], dict[str, str]
]:  # fmt: skip
    key = read_key(panel_dir / "key.csv")
    items = {i.key: i for i in load_sample(sample_path)}
    texts = {pid: items[entry.key].why_stopped for pid, entry in key.items()}
    return key, items, texts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reference labels from a model panel")
    group = parser.add_mutually_exclusive_group(required=True)
    for flag in ("prepare", "adjudication-input", "consistency-input", "finalize", "report"):
        group.add_argument(f"--{flag}", action="store_true")
    parser.add_argument(
        "--examples", type=int, default=0,
        help="with --report: write up to k adjudicator decisions for review (counts printed)",
    )  # fmt: skip
    parser.add_argument("--panel-dir", type=Path, default=PANEL_DIR)
    parser.add_argument("--sample", type=Path, default=SAMPLE_PATH)
    parser.add_argument("--labels", type=Path, default=LABELS_PATH)
    args = parser.parse_args(argv)
    seed = load_project_config().seeds.default
    panel_dir: Path = args.panel_dir

    if args.prepare:
        print(json.dumps(prepare(load_sample(args.sample), panel_dir, seed), indent=2))
        return 0
    key, items, texts = _load(panel_dir, args.sample)
    votes = read_votes(panel_dir / "votes.csv")
    check_votes(votes, key, N_LABELERS)
    if args.adjudication_input:
        data = adjudication_input(votes, texts)
        _write_json(panel_dir / "adjudication_input.json", data)
        print(f"splits to adjudicate: {len(data)} of {len(key)}")
        return 0
    consistency_sample = consistency_ids(key, CONSISTENCY_FRACTION, seed)
    if args.consistency_input:
        _write_json(
            panel_dir / "consistency_input.json",
            [{"id": p, "text": texts[p]} for p in consistency_sample],
        )
        print(f"consistency sample: {len(consistency_sample)} of {len(key)}")
        return 0
    adjudications = panel_dir / "adjudications.csv"
    decisions = read_adjudications(adjudications) if adjudications.is_file() else {}
    run = json.loads((panel_dir / "run.json").read_text(encoding="utf-8"))
    rows = reference_rows(key, items, votes, decisions, run["labeled_at"])
    if args.finalize:
        write_labels(args.labels, rows)
        print(f"wrote {len(rows)} reference labels to {args.labels}")
        return 0
    consistency = read_votes(panel_dir / "consistency.csv")
    check_consistency(consistency, consistency_sample, votes, decisions)
    report = panel_report(key, texts, votes, rows, consistency)
    _write_json(panel_dir / "report.json", report)
    print(json.dumps(report, indent=2))
    if args.examples:
        examples = decision_examples(key, texts, votes, decisions, args.examples, seed)
        path = panel_dir / "adjudicated_examples.md"
        path.write_text(examples_markdown(examples), encoding="utf-8")
        dev = [e for e in examples if e["split"] == "dev"]
        _write_json(panel_dir / "dev_examples.json", dev)
        print(f"wrote {len(examples)} adjudicator decisions to {path} ({len(dev)} dev)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
