"""Generated Markdown documents made of independent parts.

docs/data_audit.md has part 1 (written by the warehouse build) and part 2 (written by the
cohort build). Each command replaces only its own part, the text from its heading to the
next part's heading, so rebuilding one part never erases the other. Only headings that
start with the family prefix (for the audit, "# Data audit, part ") separate parts, so a
line starting with "# " inside a part (a comment in a code block, for example) is ordinary
text.
"""

from pathlib import Path


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
    path.write_text("\n".join(out), encoding="utf-8")
