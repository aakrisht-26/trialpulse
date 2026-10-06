"""Section replacement in generated documents (trialpulse.reports)."""

from pathlib import Path

import pytest

from trialpulse.reports import replace_section

FAMILY = "# Data audit, part "
ONE = "# Data audit, part 1: warehouse\n\nFirst.\n"
TWO = "# Data audit, part 2: cohort\n\nSecond.\n"


def test_append_replace_and_repeat(tmp_path: Path) -> None:
    path = tmp_path / "audit.md"
    replace_section(path, FAMILY + "1", ONE, FAMILY)
    assert path.read_text(encoding="utf-8") == ONE
    replace_section(path, FAMILY + "2", TWO, FAMILY)
    assert path.read_text(encoding="utf-8") == ONE + "\n" + TWO
    replace_section(path, FAMILY + "1", ONE.replace("First", "First, rebuilt"), FAMILY)
    text = path.read_text(encoding="utf-8")
    assert text == ONE.replace("First", "First, rebuilt") + "\n" + TWO
    replace_section(path, FAMILY + "2", TWO, FAMILY)  # writing the same part again
    assert path.read_text(encoding="utf-8") == text


def test_a_hash_line_inside_a_part_is_ordinary_text(tmp_path: Path) -> None:
    """Only part headings separate parts: a comment line in a code block must not."""
    path = tmp_path / "audit.md"
    with_comment = ONE + "\n```sql\n# a comment in a code block\nSELECT 1\n```\n\nTail of part 1.\n"
    replace_section(path, FAMILY + "1", with_comment, FAMILY)
    replace_section(path, FAMILY + "2", TWO, FAMILY)
    replace_section(path, FAMILY + "1", ONE, FAMILY)
    assert path.read_text(encoding="utf-8") == ONE + "\n" + TWO


def test_a_part_outside_the_family_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must start with"):
        replace_section(tmp_path / "a.md", "# Other", "# Other\n", FAMILY)
    with pytest.raises(ValueError, match="must start with"):
        replace_section(tmp_path / "a.md", FAMILY + "1", "Wrong start\n", FAMILY)
