"""Writing generated documents and replacing their sections (trialpulse.reports)."""

import os
from pathlib import Path

import pytest

from trialpulse.reports import (
    markdown_table,
    replace_section,
    write_bytes_if_changed,
    write_text_if_changed,
)

LONG_AGO = 1_577_836_800_000_000_000  # 2020-01-01, in nanoseconds

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


def test_markdown_table_has_a_header_a_rule_and_one_line_per_row() -> None:
    assert markdown_table(["Year", "Trials"], [[2016, "1,200"], [2017, "n/a"]]) == [
        "| Year | Trials |",
        "| --- | --- |",
        "| 2016 | 1,200 |",
        "| 2017 | n/a |",
    ]
    assert markdown_table(["Only"], []) == ["| Only |", "| --- |"]


def _aged(path: Path, content: bytes) -> Path:
    """A file with this content and an old modification time, to see whether it is touched."""
    path.write_bytes(content)
    os.utime(path, ns=(LONG_AGO, LONG_AGO))
    return path


def test_an_unchanged_file_is_left_untouched_whatever_its_line_endings(tmp_path: Path) -> None:
    """Git for Windows checks a text file out with CRLF (core.autocrlf). Regenerating it must
    not turn it back into LF: the content is the same, yet git would show it as modified."""
    text = "# Title\n\nFirst line.\nSecond line.\n"
    for name, on_disk in (("lf.md", text), ("crlf.md", text.replace("\n", "\r\n"))):
        path = _aged(tmp_path / name, on_disk.encode("utf-8"))
        assert write_text_if_changed(path, text) is False
        assert path.read_bytes() == on_disk.encode("utf-8"), name
        assert path.stat().st_mtime_ns == LONG_AGO, name


def test_a_changed_file_keeps_the_line_endings_it_had(tmp_path: Path) -> None:
    crlf = _aged(tmp_path / "crlf.md", b"old\r\ntext\r\n")
    lf = _aged(tmp_path / "lf.md", b"old\ntext\n")
    new = tmp_path / "made" / "new.md"
    assert all(write_text_if_changed(path, "new\ntext\n") for path in (crlf, lf, new))
    assert crlf.read_bytes() == b"new\r\ntext\r\n"
    assert lf.read_bytes() == b"new\ntext\n"
    assert new.read_bytes() == b"new\ntext\n"  # a new file gets LF on every platform
    assert crlf.stat().st_mtime_ns != LONG_AGO


def test_generated_text_must_come_with_lf_line_endings(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="LF line endings"):
        write_text_if_changed(tmp_path / "doc.md", "a\r\nb\r\n")
    assert not (tmp_path / "doc.md").exists()


def test_bytes_are_written_only_when_they_differ(tmp_path: Path) -> None:
    path = _aged(tmp_path / "figure.png", b"\x89PNG same")
    assert write_bytes_if_changed(path, b"\x89PNG same") is False
    assert path.stat().st_mtime_ns == LONG_AGO
    assert write_bytes_if_changed(path, b"\x89PNG other") is True
    assert path.read_bytes() == b"\x89PNG other"
    assert write_bytes_if_changed(tmp_path / "made" / "new.png", b"new") is True


def test_replacing_a_section_with_itself_leaves_a_crlf_file_untouched(tmp_path: Path) -> None:
    path = _aged(tmp_path / "audit.md", (ONE + "\n" + TWO).replace("\n", "\r\n").encode("utf-8"))
    replace_section(path, "# Data audit, part 2", TWO, FAMILY)
    assert path.stat().st_mtime_ns == LONG_AGO
    replace_section(path, "# Data audit, part 2", TWO.replace("Second", "Changed"), FAMILY)
    assert path.read_bytes() == (ONE + "\n" + TWO).replace("Second", "Changed").replace(
        "\n", "\r\n"
    ).encode("utf-8")
