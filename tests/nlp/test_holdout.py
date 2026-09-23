"""No gold-test text may appear in the LLM labeling prompt files (ADR 0010)."""

import json
from pathlib import Path

import pytest

from trialpulse.nlp.gold import LABELS_PATH, SAMPLE_PATH, load_sample, normalize_text, text_sha256
from trialpulse.nlp.holdout import (
    MAX_REASON_CHARS,
    PROMPTS_DIR,
    hash_leaks,
    heldout_hashes,
    prompt_files,
    prompt_leaks,
    span_hashes,
    text_leaks,
)

SECRET = "Synthetic: enrollment was far  slower than planned."
SECRET_HASH = text_sha256(normalize_text(SECRET))
S = "Synthetic: enrollment was far slower than planned"

EMBEDDINGS = {
    "quoted": f'Example: "{SECRET}" -> accrual',
    "wrapped list item": "Examples:\n- Synthetic: ENROLLMENT was far\n  slower than planned!\n- x",
    "parentheses": f"({SECRET})",
    "inside a longer sentence": f"{S} by the sponsors",
    "inline code": f"- `{S}` -> accrual",
    "compact table": f"|{S}|accrual|",
    "link": f"- [{S}](https://clinicaltrials.gov/study/NCT00000000)",
    "arrow": f"{S}->accrual",
    "unicode arrow": f"{S}→accrual",
    "key and value": f'text="{S}"',
    "numbered without space": f"1.{S}",
    "compact json": json.dumps([{"text": SECRET, "label": "accrual"}], separators=(",", ":")),
    "json with newline escapes": json.dumps({"content": f"Examples:\n{S}\n-> accrual"}),
    "python literal": f'PROMPT = "Examples:\\n{S}\\n"',
    "blockquote across lines": "> Synthetic: enrollment was far slower\n> than planned.",
    "byte order mark": f"﻿{S}",
}


@pytest.mark.parametrize("document", EMBEDDINGS.values(), ids=EMBEDDINGS.keys())
def test_an_embedded_test_text_is_found_by_hash_alone(document: str) -> None:
    """The hash check is all CI has, so it must catch every embedding on its own."""
    assert set(hash_leaks(document, {SECRET_HASH})) == {SECRET_HASH}
    assert set(text_leaks(document, [SECRET])) == {SECRET_HASH}


@pytest.mark.parametrize(
    "document",
    [
        "Synthetic: enrollment was far slower",  # only part of the text
        "Enrollment was far slower than planned.",
        "Synthetic: enrollment was far slower than plannedness",  # the last word differs
        "None.",
    ],
)
def test_part_of_a_test_text_is_not_a_leak(document: str) -> None:
    assert hash_leaks(document, {SECRET_HASH}) == {}
    assert text_leaks(document, [SECRET]) == {}


def test_a_leak_is_reported_with_the_line_where_it_starts() -> None:
    document = (
        "# Prompt\n\nRules first.\n\nExamples:\n"
        "- Synthetic: enrollment was far\n  slower than planned\n"
    )
    assert hash_leaks(document, {SECRET_HASH}) == {SECRET_HASH: 6}
    assert text_leaks(document, [SECRET]) == {SECRET_HASH: 6}
    assert prompt_leaks(document, {SECRET_HASH}, [SECRET]) == {SECRET_HASH: 6}
    assert prompt_leaks("clean", {SECRET_HASH}, [SECRET]) == {}


def test_span_hashes_are_bounded_by_the_reason_length() -> None:
    words = " ".join(f"w{n}" for n in range(10))
    assert text_sha256("w0 w1 w2") in span_hashes(words, max_chars=8)
    assert text_sha256("w0 w1 w2 w3") not in span_hashes(words, max_chars=8)
    long_text = " ".join(["word"] * 60)  # 299 characters, longer than any why_stopped
    assert text_sha256(long_text) not in span_hashes(long_text)
    assert len(long_text) > MAX_REASON_CHARS


def test_heldout_hashes_read_the_test_rows(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    path.write_text("nct_id,text_sha256,split\nNCT1,aaa,test\nNCT2,bbb,dev\n", encoding="utf-8")
    assert heldout_hashes(path) == {"aaa"}


def test_prompt_files_skip_caches(tmp_path: Path) -> None:
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"")
    (tmp_path / "v1.md").write_text("prompt", encoding="utf-8")
    assert prompt_files(tmp_path) == [tmp_path / "v1.md"]
    assert prompt_files(tmp_path / "missing") == []


def test_no_test_text_appears_in_the_prompt_files() -> None:
    """The real check. It runs on the committed test hashes in CI, and also on the texts
    when the gitignored gold sample is present."""
    hashes = heldout_hashes(LABELS_PATH)
    assert len(hashes) == 300  # the check is never vacuous
    texts: list[str] = []
    if SAMPLE_PATH.is_file():
        test_items = [i for i in load_sample(SAMPLE_PATH) if i.split == "test"]
        assert {i.text_sha256 for i in test_items} == hashes  # the local sample matches
        assert all(len(normalize_text(i.why_stopped)) <= MAX_REASON_CHARS for i in test_items)
        texts = [i.why_stopped for i in test_items]
    files = prompt_files(PROMPTS_DIR)
    assert files, "the prompts folder and its README must exist"
    for path in files:
        leaks = prompt_leaks(path.read_text(encoding="utf-8"), hashes, texts)
        assert not leaks, (
            f"{path.name} contains {len(leaks)} gold-test text(s) at lines "
            f"{sorted(set(leaks.values()))} (hashes {sorted(h[:12] for h in leaks)}); "
            "use dev items only"
        )
