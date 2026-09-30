"""Text normalization shared by the offline and live paths (CLAUDE.md Section 2, Step 3).

The two sources store the same registry text in different markup. The history dataset holds
most long texts as HTML (`<p>`, `<ul><li>`, entities such as `&gt;` and `&#x27;`), some as
plain text; API v2 returns Markdown (`* item`, `1. item`, backslash escapes such as `\\>`).
Both are reduced to one canonical plain text:

- one line per block (a paragraph or a list item), in order, without list markers;
- inside a block, every run of whitespace (a soft line break included) is one space, which is
  how the registry itself joins a paragraph's lines;
- HTML entities are unescaped, repeatedly, so double-escaped text is also recovered;
- Markdown backslash escapes are removed;
- email addresses are replaced with a fixed marker, because free text may hold contact
  details, which are never stored.

    normalize_text("<p>Inclusion:</p><ul><li>Age &gt;\\n18</li></ul>")  -> "Inclusion:\\nAge > 18"
    normalize_text("Inclusion:\\n\\n* Age \\\\>\\n18")                -> "Inclusion:\\nAge > 18"
"""

import hashlib
import html
import re

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_MARKER = "[email removed]"
_HTML_BLOCK_HINT = re.compile(r"(?i)<(p|ul|ol|li|br|div|h[1-6])\b[^>]*>")
_HTML_BLOCK_TAG = re.compile(r"(?i)</?(p|div|ul|ol|li|br|h[1-6]|table|tr|blockquote)\b[^>]*>")
_HTML_ANY_TAG = re.compile(r"(?i)</?[a-z][a-z0-9]*\b[^<>]*>")
_BLANK_LINE = re.compile(r"\n[ \t]*\n")
_LIST_MARKER = re.compile(r"^\s*(?:[*+-]|(\d{1,9})[.)])\s+")
# CommonMark: any ASCII punctuation character can be escaped with a backslash.
_MARKDOWN_ESCAPE = re.compile(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~])")


def unescape_html(text: str) -> str:
    for _ in range(3):  # double-escaped text needs more than one pass
        once = html.unescape(text)
        if once == text:
            return text
        text = once
    return text


def scrub_emails(text: str) -> tuple[str, int]:
    return EMAIL_PATTERN.subn(EMAIL_MARKER, text)


def is_html(text: str) -> bool:
    return _HTML_BLOCK_HINT.search(text) is not None


def _html_blocks(text: str) -> list[str]:
    text = _HTML_BLOCK_TAG.sub("\n\n", text)
    text = _HTML_ANY_TAG.sub("", text)  # inline tags (strong, em, a, sup) keep their text
    return _BLANK_LINE.split(unescape_html(text))


def _markdown_blocks(text: str) -> list[str]:
    """Paragraphs and list items, following the CommonMark rules the registry's rendering
    agrees with:

    - a list item may interrupt a paragraph only if it is a bullet or an ordered item
      numbered 1, so "2) ..." right after a plain paragraph line continues that paragraph;
    - inside a list any item number starts the next item, and a paragraph indented under an
      item after a blank line (API v2 writes `\\n\\n   OR\\n4. ...`) keeps the list open;
    - a paragraph that is not indented after a blank line ends the list.
    """
    blocks: list[list[str]] = []
    current: list[str] | None = None
    in_list = False
    for line in unescape_html(text).split("\n"):
        if not line.strip():
            current = None
            continue
        marker = _LIST_MARKER.match(line)
        number = marker.group(1) if marker else None
        starts_item = marker is not None and (
            current is None or in_list or number is None or int(number) == 1
        )
        if starts_item and marker is not None:
            current, in_list = [line[marker.end() :]], True
            blocks.append(current)
        elif current is None:
            current = [line]
            in_list = in_list and line[:1].isspace()
            blocks.append(current)
        else:
            current.append(line)
    return [" ".join(lines) for lines in blocks]


def normalize_text(value: str | None) -> str | None:
    """The stored form of a free-text field, from either source, or None when nothing is left."""
    if value is None:
        return None
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    blocks = _html_blocks(text) if is_html(text) else _markdown_blocks(text)
    lines = (" ".join(_MARKDOWN_ESCAPE.sub(r"\1", block).split()) for block in blocks)
    clean, _ = scrub_emails("\n".join(line for line in lines if line))
    return clean or None


def text_hash(text: str) -> str:
    """The key of a normalized text in the warehouse's texts table."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
