"""Keeps the gold-test texts out of the LLM labeling prompt (ADR 0010).

Prompt work uses the 100 dev items only. The prompt lives in src/trialpulse/nlp/prompts/,
and tests/nlp/test_holdout.py checks every file there against the test texts. The Step 6
labeler must also run prompt_leaks on the prompt it actually sends.

Two checks, each run on the document as written, with backslash escapes decoded (a prompt
stored as JSON or a Python literal), and with Markdown line prefixes removed (blockquotes,
list bullets):

- by hash, using the test rows of the committed labels file, so it runs in CI without the
  texts. Every run of words is normalized and hashed like a gold text, and characters fused
  to its first or last word (quotes, backticks, table pipes, JSON keys, links, arrows) are
  also cut away at each non-word character;
- by text, when the gitignored gold sample is present: each normalized test text is looked
  for in the normalized document, bounded by non-word characters.

Both catch verbatim copies after normalization (case, whitespace, surrounding punctuation).
Neither catches a paraphrase or an edit inside a text. A leak is reported by the first
characters of its hash and a line number, never by its text.
"""

import csv
import re
from collections.abc import Iterable
from pathlib import Path

from trialpulse.nlp.gold import normalize_text, text_sha256

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
MAX_REASON_CHARS = 250  # ClinicalTrials.gov limits why_stopped to 250 characters

_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_WHITESPACE_ESCAPE = re.compile(r"\\[nrt]")
_OTHER_ESCAPE = re.compile(r"\\(.)")
_MARKDOWN_PREFIX = re.compile(r"^[ \t]*(?:>[ \t]*|[-*+][ \t]+|\d+[.)][ \t]+)+", re.MULTILINE)


def heldout_hashes(labels_path: Path) -> set[str]:
    """The text hashes of the test split in the labels file."""
    with labels_path.open(newline="", encoding="utf-8") as fh:
        return {r["text_sha256"] for r in csv.DictReader(fh) if r["split"] == "test"}


def _unescape(text: str) -> str:
    text = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)
    text = _WHITESPACE_ESCAPE.sub(" ", text)
    return _OTHER_ESCAPE.sub(r"\1", text)


def document_variants(document: str) -> list[str]:
    """The document as written, with escapes decoded, and then without Markdown line
    prefixes. Line breaks are kept, so line numbers stay valid."""
    base = document.replace("\ufeff", "")
    unescaped = _unescape(base)
    return list(dict.fromkeys([base, unescaped, _MARKDOWN_PREFIX.sub("", unescaped)]))


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _heads(token: str) -> set[str]:
    """Where a text could start inside a token: the whole token, or after any non-word
    character."""
    cuts = {token} | {token[n + 1 :] for n, c in enumerate(token) if not _is_word_char(c)}
    return {c for c in cuts if c}


def _tails(token: str) -> set[str]:
    """Where a text could end inside a token: the whole token, or before any non-word
    character."""
    cuts = {token} | {token[:n] for n, c in enumerate(token) if not _is_word_char(c)}
    return {c for c in cuts if c}


def span_hashes(document: str, max_chars: int = MAX_REASON_CHARS) -> dict[str, int]:
    """The hash of every normalized run of words that could be a gold text, mapped to the
    line where it starts."""
    found: dict[str, int] = {}
    for variant in document_variants(document):
        words = [
            (word.lower(), number)
            for number, line in enumerate(variant.split("\n"), start=1)
            for word in line.split()
        ]
        for start, (first, line) in enumerate(words):
            heads = _heads(first)
            middle = ""  # the words strictly between the first and the last
            for end in range(start, len(words)):
                if end > start + 1:
                    inner = words[end - 1][0]
                    middle = f"{middle} {inner}" if middle else inner
                if len(middle) > max_chars:
                    break
                if end == start:
                    candidates = {tail for head in heads for tail in _tails(head)}
                else:
                    join = f" {middle} " if middle else " "
                    tails = _tails(words[end][0])
                    candidates = {f"{head}{join}{tail}" for head in heads for tail in tails}
                for candidate in candidates:
                    normalized = normalize_text(candidate)
                    if normalized and len(normalized) <= max_chars:
                        found.setdefault(text_sha256(normalized), line)
    return found


def hash_leaks(document: str, hashes: set[str]) -> dict[str, int]:
    """Test hashes found in the document, with the line where each starts."""
    return {h: line for h, line in span_hashes(document).items() if h in hashes}


def text_leaks(document: str, texts: Iterable[str]) -> dict[str, int]:
    """The hashes of the texts whose normalized form appears in the normalized document,
    with the line where each starts."""
    patterns = [
        (text_sha256(n), re.compile(rf"(?<!\w){re.escape(n)}(?!\w)"))
        for n in {normalize_text(t) for t in texts}
        if n
    ]
    leaks: dict[str, int] = {}
    for variant in document_variants(document):
        normalized = normalize_text(variant)
        lines = variant.split("\n")
        for digest, pattern in patterns:
            if digest not in leaks and pattern.search(normalized):
                leaks[digest] = _start_line(lines, pattern)
    return leaks


def _start_line(lines: list[str], pattern: re.Pattern[str]) -> int:
    """The line where the first match starts: the first line that completes a match, then
    the last line from which the match still holds. Only runs for a found leak."""
    for end in range(len(lines)):
        if pattern.search(normalize_text(" ".join(lines[: end + 1]))):
            for start in range(end, -1, -1):
                if pattern.search(normalize_text(" ".join(lines[start : end + 1]))):
                    return start + 1
    return 0


def prompt_leaks(prompt: str, hashes: set[str], texts: Iterable[str] = ()) -> dict[str, int]:
    """Every test text found in a prompt by either check, with its line. Run this on the
    rendered prompt a labeler sends, not only on the files it is built from."""
    return {**text_leaks(prompt, texts), **hash_leaks(prompt, hashes)}


def prompt_files(prompt_dir: Path = PROMPTS_DIR) -> list[Path]:
    if not prompt_dir.is_dir():
        return []
    return sorted(p for p in prompt_dir.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
