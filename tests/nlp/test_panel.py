"""The model-panel reference labels (ADR 0010) on synthetic data: opaque ids, labeler
inputs with the text only, panel outcomes, adjudication checks, the consistency sample and
the labels file."""

import csv
import json
from collections import Counter
from pathlib import Path

import pytest

from trialpulse.nlp.gold import (
    LABEL_COLUMNS,
    PANEL_METHOD,
    GoldItem,
    read_labels,
    write_labels,
    write_sample,
)
from trialpulse.nlp.panel import (
    Adjudication,
    KeyEntry,
    Vote,
    adjudication_input,
    assign_panel_ids,
    check_consistency,
    check_graded_model,
    check_votes,
    cohen_kappa,
    consistency_ids,
    decision_examples,
    examples_markdown,
    labeler_inputs,
    main,
    panel_report,
    prepare,
    quoted_verbatim,
    read_adjudications,
    read_key,
    read_votes,
    reference_rows,
    resolve,
    split_ids,
    write_adjudications,
    write_votes,
)

SOURCE = "ctgov-api-v2 pulled 2026-09-23"


def _items(n: int) -> list[GoldItem]:
    return [
        GoldItem(f"NCT{i:08d}", f"{i:064x}", "TERMINATED" if i % 3 else "WITHDRAWN", 2010 + i % 9,
                 f"Synthetic reason {i}", "dev" if i % 4 == 0 else "test", SOURCE)
        for i in range(n)
    ]  # fmt: skip


def _votes(pid: str, *labels: str) -> list[Vote]:
    return [Vote(pid, f"b1-l{n}", lab, "reason", f"note {n}") for n, lab in enumerate(labels, 1)]


def test_resolve_gives_the_label_and_the_panel_outcome() -> None:
    assert resolve(["accrual"] * 3, None) == ("accrual", "unanimous")
    assert resolve(["accrual", "accrual", "other"], "accrual") == ("accrual", "majority")
    assert resolve(["accrual", "accrual", "other"], "other") == ("other", "adjudicated")
    assert resolve(["accrual", "accrual", "other"], "business") == ("business", "adjudicated")
    assert resolve(["accrual", "funding", "other"], "funding") == ("funding", "adjudicated")
    with pytest.raises(ValueError, match="not adjudicated"):
        resolve(["safety"] * 3, "safety")
    with pytest.raises(ValueError, match="needs an adjudication"):
        resolve(["safety", "safety", "efficacy"], None)
    with pytest.raises(ValueError, match="unknown label"):
        resolve(["safety", "safety", "harm"], "safety")
    with pytest.raises(ValueError, match="unknown label"):
        resolve(["safety", "safety", "efficacy"], "harm")


def test_panel_ids_are_opaque_seeded_and_complete() -> None:
    items = _items(40)
    assigned = assign_panel_ids(items, seed=42)
    assert [pid for pid, _ in assigned] == [f"p{n:03d}" for n in range(1, 41)]
    assert sorted(i.key for _, i in assigned) == sorted(i.key for i in items)
    assert [i.key for _, i in assigned] != sorted(i.key for i in items)  # not in key order
    assert assigned == assign_panel_ids(list(reversed(items)), seed=42)
    assert assigned != assign_panel_ids(items, seed=7)


def test_labeler_inputs_hold_the_text_only_in_a_seeded_order_per_labeler() -> None:
    assigned = assign_panel_ids(_items(250), seed=42)
    files = labeler_inputs(assigned, batch_size=100, n_labelers=3, seed=42)
    assert sorted(files) == [f"batch{b}_labeler{r}.json" for b in (1, 2, 3) for r in (1, 2, 3)]
    for b, size in ((1, 100), (2, 100), (3, 50)):
        orders = [files[f"batch{b}_labeler{r}.json"] for r in (1, 2, 3)]
        assert all(len(o) == size for o in orders)
        assert len({tuple(r["id"] for r in o) for o in orders}) == 3  # three different orders
        assert all(sorted(o, key=lambda r: r["id"]) == sorted(orders[0], key=lambda r: r["id"])
                   for o in orders)  # fmt: skip
    rows = [row for rows in files.values() for row in rows]
    assert all(set(row) == {"id", "text"} for row in rows)
    assert Counter(row["id"] for row in rows) == {pid: 3 for pid, _ in assigned}
    text = json.dumps(files)
    for hint in ("NCT", "TERMINATED", "WITHDRAWN", '"dev"', '"test"', "2015"):
        assert hint not in text


def test_prepare_is_deterministic_and_refuses_a_different_key(tmp_path: Path) -> None:
    items = _items(30)
    summary = prepare(items, tmp_path, seed=42)
    assert summary == {"items": 30, "input_files": 3, "labelers_per_text": 3}
    files = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    first = {p: p.read_bytes() for p in files}
    prepare(items, tmp_path, seed=42)
    assert {p: p.read_bytes() for p in files} == first
    key = read_key(tmp_path / "key.csv")
    assert {e.key for e in key.values()} == {i.key for i in items}
    with pytest.raises(ValueError, match="differs"):
        prepare(items[:29], tmp_path, seed=42)


def test_votes_round_trip_and_are_checked(tmp_path: Path) -> None:
    votes = _votes("p001", "accrual", "accrual", "other") + _votes("p002", *["funding"] * 3)
    path = tmp_path / "votes.csv"
    write_votes(path, votes)
    read = read_votes(path)
    order = lambda v: (v.panel_id, v.labeler)  # noqa: E731
    assert sorted((v for cast in read.values() for v in cast), key=order) == sorted(
        votes, key=order
    )
    check_votes(read, ["p001", "p002"], 3)
    assert split_ids(read) == ["p001"]
    with pytest.raises(ValueError, match="3 votes"):
        check_votes(read, ["p001", "p002", "p003"], 3)
    with pytest.raises(ValueError, match="unknown panel ids"):
        check_votes(read, ["p001"], 3)
    duplicated = {"p001": [*read["p001"][:2], read["p001"][0]]}
    with pytest.raises(ValueError, match="distinct labelers"):
        check_votes(duplicated, ["p001"], 3)
    with path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(["panel_id", "label"])
    with pytest.raises(ValueError, match="expected columns"):
        read_votes(path)


def test_adjudication_input_holds_the_splits_with_text_and_justifications() -> None:
    votes = {
        "p001": _votes("p001", "accrual", "accrual", "other"),
        "p002": _votes("p002", *["funding"] * 3),
    }
    data = adjudication_input(votes, {"p001": "Text one", "p002": "Text two"})
    assert [d["id"] for d in data] == ["p001"]
    assert data[0]["text"] == "Text one"
    assert [v["label"] for v in data[0]["labelers"]] == ["accrual", "accrual", "other"]
    assert set(data[0]) == {"id", "text", "labelers"}
    assert all(set(v) == {"label", "deciding_words", "justification"} for v in data[0]["labelers"])


def test_consistency_sample_is_a_seeded_ten_percent() -> None:
    ids = [f"p{n:03d}" for n in range(1, 401)]
    drawn = consistency_ids(ids, 0.10, seed=42)
    assert len(set(drawn)) == len(drawn) == 40
    assert set(drawn) <= set(ids)
    assert drawn == consistency_ids(reversed(ids), 0.10, seed=42)
    assert drawn != consistency_ids(ids, 0.10, seed=7)


Panel = tuple[
    dict[str, KeyEntry], dict[tuple[str, str], GoldItem], dict[str, list[Vote]],
    dict[str, Adjudication],
]  # fmt: skip


def _panel(tmp_path: Path) -> Panel:
    items = _items(8)
    prepare(items, tmp_path, seed=42)
    key = read_key(tmp_path / "key.csv")
    pids = sorted(key)
    votes = {pid: _votes(pid, *["accrual"] * 3) for pid in pids}
    votes[pids[0]] = _votes(pids[0], "accrual", "accrual", "other")
    votes[pids[1]] = _votes(pids[1], "safety", "efficacy", "other")
    decisions = {
        pids[0]: Adjudication(pids[0], "adjudicator-1", "accrual", "slow enrollment decides"),
        pids[1]: Adjudication(pids[1], "adjudicator-1", "safety", "harm is stated"),
    }
    return key, {i.key: i for i in items}, votes, decisions


def test_reference_rows_follow_the_panel_and_the_labels_schema(tmp_path: Path) -> None:
    key, items, votes, decisions = _panel(tmp_path)
    rows = reference_rows(key, items, votes, decisions, "2026-09-23T12:00:00+00:00")
    assert len(rows) == 8
    assert Counter(r["panel_outcome"] for r in rows.values()) == {
        "unanimous": 6, "majority": 1, "adjudicated": 1,
    }  # fmt: skip
    assert all(r["assisted"] == "false" and r["method"] == PANEL_METHOD for r in rows.values())
    assert all(r["source"] == SOURCE for r in rows.values())
    path = tmp_path / "labels" / "gold_labels.csv"
    write_labels(path, rows)
    with path.open(newline="", encoding="utf-8") as fh:
        assert tuple(next(csv.reader(fh))) == LABEL_COLUMNS
    assert read_labels(path) == rows
    assert "Synthetic" not in path.read_text(encoding="utf-8")


def test_reference_rows_refuse_a_broken_panel(tmp_path: Path) -> None:
    key, items, votes, decisions = _panel(tmp_path)
    pids = sorted(key)
    stamp = "2026-09-23T12:00:00+00:00"
    missing = {pid: d for pid, d in decisions.items() if pid != pids[1]}
    with pytest.raises(ValueError, match="needs an adjudication"):
        reference_rows(key, items, votes, missing, stamp)
    extra = {**decisions, pids[2]: Adjudication(pids[2], "adjudicator-1", "accrual", "x")}
    with pytest.raises(ValueError, match="not adjudicated"):
        reference_rows(key, items, votes, extra, stamp)
    own = {**decisions, pids[0]: Adjudication(pids[0], "b1-l3", "accrual", "x")}
    with pytest.raises(ValueError, match="by one of its labelers"):
        reference_rows(key, items, votes, own, stamp)
    with pytest.raises(ValueError, match="3 votes"):
        reference_rows(key, items, {pid: votes[pid] for pid in pids[1:]}, decisions, stamp)
    stray = {**decisions, "p999": Adjudication("p999", "adjudicator-1", "accrual", "x")}
    with pytest.raises(ValueError, match="unknown panel ids"):
        reference_rows(key, items, votes, stray, stamp)


def test_adjudications_round_trip_and_refuse_duplicates(tmp_path: Path) -> None:
    decisions = [
        Adjudication("p002", "adj", "safety", "r2"),
        Adjudication("p001", "adj", "other", "r1"),
    ]
    path = tmp_path / "adjudications.csv"
    write_adjudications(path, decisions)
    assert read_adjudications(path) == {d.panel_id: d for d in decisions}
    with path.open("a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(["p001", "adj", "other", "again"])
    with pytest.raises(ValueError, match="two adjudications"):
        read_adjudications(path)


def test_report_counts_outcomes_agreement_and_consistency(tmp_path: Path) -> None:
    key, items, votes, decisions = _panel(tmp_path)
    rows = reference_rows(key, items, votes, decisions, "t")
    texts = {pid: f"Slow accrual in case {pid}" for pid in key}
    pids = sorted(key)
    fresh = ("accrual", "safety", "accrual", "other")  # reference: accrual, safety, accrual x2
    consistency = {
        p: [Vote(p, "consistency-1", lab, "x", "y")] for p, lab in zip(pids[:4], fresh, strict=True)
    }
    report = panel_report(key, texts, votes, rows, consistency)
    assert report["items"] == 8
    assert report["all"]["outcomes"] == {"unanimous": 6, "majority": 1, "adjudicated": 1}
    assert report["all"]["outcome_shares"]["unanimous"] == 0.75
    assert sum(report["all"]["labels"].values()) == 8
    assert report["dev"]["n"] + report["test"]["n"] == 8
    # 6 unanimous texts agree on 3 pairs each, the 2-1 split on 1 pair, the 3-way split on 0.
    assert report["pairwise_agreement"] == round(19 / 24, 4)
    assert report["deciding_words_verbatim"] == 0.0  # "reason" is not in the texts
    assert report["consistency"]["n"] == 4
    assert report["consistency"]["agree"] == 3
    assert report["consistency"]["kappa"] == round(5 / 9, 4)  # (12/16 - 7/16) / (9/16)
    dev_n = report["dev"]["n"]
    assert sum(report["dev"]["outcomes"].values()) == dev_n
    assert sum(report["dev"]["outcome_shares"].values()) == pytest.approx(1.0, abs=1e-3)
    assert "Slow accrual" not in json.dumps(report)
    quoted = dict.fromkeys(key, "Synthetic reason stated here")
    assert panel_report(key, quoted, votes, rows, {})["deciding_words_verbatim"] == 1.0


def test_consistency_relabels_must_be_fresh_single_valid_and_complete(tmp_path: Path) -> None:
    key, _, votes, decisions = _panel(tmp_path)
    pids = sorted(key)
    sample = pids[:2]
    good = {p: [Vote(p, "consistency-1", "accrual", "x", "y")] for p in sample}
    check_consistency(good, sample, votes, decisions)
    with pytest.raises(ValueError, match="seeded consistency sample"):
        check_consistency(good, pids[:3], votes, decisions)
    with pytest.raises(ValueError, match="exactly one"):
        check_consistency({**good, pids[0]: good[pids[0]] * 2}, sample, votes, decisions)
    bad = [Vote(pids[0], "consistency-1", "Accrual", "x", "y")]
    with pytest.raises(ValueError, match="unknown label"):
        check_consistency({**good, pids[0]: bad}, sample, votes, decisions)
    own = [Vote(pids[0], "b1-l2", "accrual", "x", "y")]
    with pytest.raises(ValueError, match="not a fresh one"):
        check_consistency({**good, pids[0]: own}, sample, votes, decisions)
    by_adjudicator = [Vote(pids[0], "adjudicator-1", "accrual", "x", "y")]
    with pytest.raises(ValueError, match="not a fresh one"):
        check_consistency({**good, pids[0]: by_adjudicator}, sample, votes, decisions)


def test_decision_examples_take_dev_then_overturned_then_kept_test_splits() -> None:
    splits = {"p1": "dev", "p2": "dev", "p3": "test", "p4": "test", "p5": "test", "p6": "test"}
    key = {p: KeyEntry(p, f"NCT{p}", p, s) for p, s in splits.items()}
    votes = {p: _votes(p, "accrual", "accrual", "other") for p in splits}
    decisions = {
        p: Adjudication(p, "adjudicator-1", "other" if p in {"p3", "p4"} else "accrual", f"r {p}")
        for p in splits
    }
    texts = {p: f"text {p}" for p in splits}

    examples = decision_examples(key, texts, votes, decisions, 5, seed=42)
    ids = [e["id"] for e in examples]
    assert ids[:4] == ["p1", "p2", "p3", "p4"]  # all dev, then all overturned test texts
    assert ids[4] in {"p5", "p6"}  # a seeded draw of the kept test decisions
    assert ids == [e["id"] for e in decision_examples(key, texts, votes, decisions, 5, seed=42)]
    assert [e["outcome"] for e in examples] == ["majority"] * 2 + ["adjudicated"] * 2 + ["majority"]
    assert all(len(e["votes"]) == 3 and e["rationale"] for e in examples)
    assert [e["id"] for e in decision_examples(key, texts, votes, decisions, 1, seed=42)] in (
        ["p1"], ["p2"],
    )  # fmt: skip
    markdown = examples_markdown(examples)
    assert "never use this file for prompt work" in markdown
    assert all(f"## {i} " in markdown for i in ids)


def test_quoted_verbatim_checks_every_fragment() -> None:
    text = "Terminated due to  SLOW enrollment; sponsor decision."
    assert quoted_verbatim("slow enrollment", text)
    assert quoted_verbatim('"Slow enrollment" | sponsor decision', text)
    assert not quoted_verbatim("slow recruitment", text)
    assert not quoted_verbatim("slow enrollment | funding", text)
    assert not quoted_verbatim("", text)
    assert not quoted_verbatim("harm", "Sponsor (pharmaceutical company) decision")
    assert not quoted_verbatim("ment", "Slow enrollment")


def test_cohen_kappa() -> None:
    assert cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0
    assert cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == 0.0
    assert cohen_kappa(["a", "a"], ["a", "a"]) == 1.0
    with pytest.raises(ValueError, match="non-empty"):
        cohen_kappa([], [])


def test_graded_models_must_come_from_another_family() -> None:
    assert check_graded_model("openai/gpt-oss-120b") == "openai/gpt-oss-120b"
    for model in ("claude-opus-5-5", "anthropic/claude-sonnet-5", "Claude-Haiku"):
        with pytest.raises(ValueError, match="different family"):
            check_graded_model(model)


def test_main_runs_the_panel_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sample, panel, labels = tmp_path / "sample.csv", tmp_path / "panel", tmp_path / "labels.csv"
    items = _items(8)
    write_sample(items, sample)
    paths = ["--panel-dir", str(panel), "--sample", str(sample), "--labels", str(labels)]

    assert main(["--prepare", *paths]) == 0
    key = read_key(panel / "key.csv")
    pids = sorted(key)
    votes = [v for p in pids for v in _votes(p, *["funding"] * 3)]
    write_votes(panel / "votes.csv", votes)
    (panel / "run.json").write_text('{"labeled_at": "2026-09-23T00:00:00+00:00"}', "utf-8")
    assert main(["--finalize", *paths]) == 0  # no splits, so no adjudications file is needed
    assert {r["panel_outcome"] for r in read_labels(labels).values()} == {"unanimous"}

    split = pids[3]
    votes = [v for v in votes if v.panel_id != split] + _votes(split, "safety", "safety", "other")
    write_votes(panel / "votes.csv", votes)
    assert main(["--adjudication-input", *paths]) == 0
    assert [
        d["id"] for d in json.loads((panel / "adjudication_input.json").read_text("utf-8"))
    ] == [split]
    write_adjudications(panel / "adjudications.csv", [Adjudication(split, "adj-1", "safety", "r")])
    assert main(["--consistency-input", *paths]) == 0
    sample_ids = [
        d["id"] for d in json.loads((panel / "consistency_input.json").read_text("utf-8"))
    ]
    assert len(sample_ids) == 1  # 10% of 8, rounded
    write_votes(panel / "consistency.csv", [Vote(sample_ids[0], "fresh-1", "funding", "x", "y")])

    assert main(["--finalize", *paths]) == 0
    rows = read_labels(labels)
    assert Counter(r["panel_outcome"] for r in rows.values()) == {"unanimous": 7, "majority": 1}
    capsys.readouterr()
    assert main(["--report", "--examples", "10", *paths]) == 0
    printed = capsys.readouterr().out
    assert "Synthetic reason" not in printed  # counts only, never a text
    report = json.loads((panel / "report.json").read_text("utf-8"))
    assert report["all"]["outcomes"] == {"unanimous": 7, "majority": 1, "adjudicated": 0}
    assert "Synthetic reason" in (panel / "adjudicated_examples.md").read_text("utf-8")
