"""Generated Markdown documents made of independent top-level sections.

docs/data_audit.md has part 1 (written by the warehouse build) and part 2 (written by the
cohort build). Each command replaces only its own section, the text from its "# " heading
to the next "# " heading, so rebuilding one part never erases the other.
"""

from pathlib import Path


def _sections(text: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith("# ") and current:
            sections.append("".join(current))
            current = []
        current.append(line)
    if current:
        sections.append("".join(current))
    return sections


def replace_section(path: Path, heading: str, body: str) -> None:
    """Write `body` (which starts with a line beginning `heading`) in place of the existing
    section whose first line begins with `heading`, or append it."""
    if not body.startswith(heading):
        raise ValueError(f"the section must start with {heading!r}")
    block = body.rstrip("\n") + "\n"
    existing = _sections(path.read_text(encoding="utf-8")) if path.is_file() else []
    replaced = False
    out: list[str] = []
    for section in existing:
        if section.startswith(heading):
            out.append(block)
            replaced = True
        else:
            out.append(section.rstrip("\n") + "\n")
    if not replaced:
        out.append(block)
    path.write_text("\n".join(out), encoding="utf-8")
