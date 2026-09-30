"""Sponsor identity without people's names (CLAUDE.md Sections 2 and 8, Step 3).

A sponsor is an individual when its class is INDIV, or when its name carries a personal
degree title (PhD, MD, MPH and similar) and no organization word. An individual sponsor's
name is never stored: its name and key are empty, and Step 7's sponsor track record falls
back to the class-level rate for it.

Other sponsors keep a display name (HTML unescaped, emails scrubbed) and a key: the name in
lowercase with punctuation removed and whitespace collapsed (CLAUDE.md Section 8: no fuzzy
entity resolution in v1).
"""

import re
import unicodedata
from dataclasses import dataclass

from trialpulse.contracts.text import normalize_text

INDIVIDUAL_CLASS = "INDIV"
DEGREE_TITLE = re.compile(r"(,\s*|\s)(PhD|Ph\.D\.|MD|M\.D\.|DrPH|MPH)([\s,.]|$)")
ORGANIZATION_WORD = re.compile(
    r"(?i)\b(universit|hospital|institut|cent(er|re)|clinic|college|foundation|inc|ltd|llc|"
    r"gmbh|corp|pharma|group|health|medical|research|network|association|society|school|"
    r"ministry|department|council|trust|agency|laborator|company|s\.a\.|ag\b|bv\b|sa\b)"
)


@dataclass(frozen=True)
class Sponsor:
    name: str | None
    key: str | None
    is_individual: bool


def is_individual(name: str | None, sponsor_class: str | None) -> bool:
    if (sponsor_class or "").upper() == INDIVIDUAL_CLASS:
        return True
    return bool(name and DEGREE_TITLE.search(name) and not ORGANIZATION_WORD.search(name))


def sponsor_key(name: str) -> str | None:
    text = unicodedata.normalize("NFKC", name).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = " ".join(text.split())
    return text or None


def sponsor(name: str | None, sponsor_class: str | None) -> Sponsor:
    """The stored sponsor fields: nothing that names a person."""
    clean = normalize_text(name)
    if is_individual(clean, sponsor_class):
        return Sponsor(None, None, True)
    return Sponsor(clean, sponsor_key(clean) if clean else None, False)
