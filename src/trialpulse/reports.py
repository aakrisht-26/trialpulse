"""Generated documents: writing them reproducibly, and documents made of independent parts.

**Writing.** A generated file that is committed must come back byte for byte when it is
regenerated, on any machine. The content is the generator's job. The bytes also depend on
line endings, which git may change on checkout (with core.autocrlf, as Git for Windows
sets it, a text file is checked out with CRLF). `write_text_if_changed` therefore leaves a
file alone when it already says the same thing, and keeps the line endings a file already
has when it does change. Without that, a regenerated file can differ from its checkout by
nothing but line endings, and `git status` reports it as modified.

**Parts.** docs/data_audit.md has part 1 (written by the warehouse build) and part 2 (written by the
cohort build). Each command replaces only its own part, the text from its heading to the
next part's heading, so rebuilding one part never erases the other. Only headings that
start with the family prefix (for the audit, "# Data audit, part ") separate parts, so a
line starting with "# " inside a part (a comment in a code block, for example) is ordinary
text.
"""

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any


def write_text_if_changed(path: Path, text: str) -> bool:
    """Write a generated text file and say whether anything was written.

    `text` uses LF line endings. If the file already holds the same text, whatever its
    line endings, it is not touched. If it is rewritten, it keeps the line endings it had;
    a new file gets LF."""
    if "\r" in text:
        raise ValueError("generated text must use LF line endings")
    newline = "\n"
    if path.is_file():
        existing = path.read_bytes()
        if existing.replace(b"\r\n", b"\n") == text.encode("utf-8"):
            return False
        if b"\r\n" in existing:
            newline = "\r\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline=newline)
    return True


def write_bytes_if_changed(path: Path, data: bytes) -> bool:
    """Write a generated binary file, unless it already holds exactly these bytes."""
    if path.is_file() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def _parts(text: str, family: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith(family) and current:
            parts.append("".join(current))
            current = []
        current.append(line)
    if current:
        parts.append("".join(current))
    return parts


def replace_section(path: Path, heading: str, body: str, family: str) -> None:
    """Write `body` (which starts with a line beginning `heading`) in place of the existing
    part whose first line begins with `heading`, or append it. `family` is the prefix every
    part heading shares; `heading` must start with it."""
    if not heading.startswith(family) or not body.startswith(heading):
        raise ValueError(f"the part must start with {heading!r}, inside the family {family!r}")
    block = body.rstrip("\n") + "\n"
    existing = _parts(path.read_text(encoding="utf-8"), family) if path.is_file() else []
    replaced = False
    out: list[str] = []
    for part in existing:
        if part.startswith(heading):
            out.append(block)
            replaced = True
        else:
            out.append(part.rstrip("\n") + "\n")
    if not replaced:
        out.append(block)
    write_text_if_changed(path, "\n".join(out))


def markdown_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    """The lines of a Markdown table: a header row, a rule, and one line per row."""
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return lines
