"""Sponsor identity without people's names (CLAUDE.md Sections 2 and 8, Step 3).

A sponsor is an individual when its class is INDIV, when its name carries a personal
degree title (PhD, MD, MPH and similar) and no organization word (ADR 0012), or when its
name opens with a personal title (Dr, Prof, Mr and similar) and holds no organization word
(ADR 0022). An individual sponsor's name is never stored: its name and key are empty, and
Step 7's sponsor track record falls back to the class-level rate for it.

Other sponsors keep a display name (HTML unescaped, emails scrubbed) and a key: the name in
lowercase with punctuation removed and whitespace collapsed (CLAUDE.md Section 8: no fuzzy
entity resolution in v1).

**Identity for the track record (Step 7, ADR 0018).** A stored key is not always a sponsor
identity. `has_identity` says whether a key may carry a track record of its own: not when
the sponsor is an individual, not when the key is a registry placeholder (a redacted name
stands for many companies), and not when the key opens with a personal title and holds no
organization word. Since ADR 0022 no key of that last kind is stored at all, and the
canonical contract refuses one; the clause stays in `has_identity` as a second guard.
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


# Keys that stand for "no name given", not for one sponsor.
PLACEHOLDER_KEYS: tuple[str, ...] = ("redacted", "no sponsor")
# A personal title at the start of a key (keys are lowercase, without punctuation).
PERSONAL_TITLE_KEY = r"^(dr|dra|drs|prof|professor|doctor|mr|mrs|ms|miss|sir) "
# ORGANIZATION_WORD for keys. A key is words separated by single spaces, so a word starts
# at the start of the key or after a space; "\b" would mean different things to Python and
# to DuckDB (RE2) next to a letter outside ASCII.
ORGANIZATION_WORD_KEY = (
    r"(^| )(universit|hospital|institut|cent(er|re)|clinic|college|foundation|inc|ltd|llc|"
    r"gmbh|corp|pharma|group|health|medical|research|network|association|society|school|"
    r"ministry|department|council|trust|agency|laborator|company)"
)


@dataclass(frozen=True)
class Sponsor:
    name: str | None
    key: str | None
    is_individual: bool


def opens_with_personal_title(key: str | None) -> bool:
    """Whether a sponsor key is a person named by title: it opens with a personal title and
    holds no organization word (ADR 0022). "dr a example" is; "dr example memorial hospital"
    is an organization named after a person."""
    if not key:
        return False
    titled = re.search(PERSONAL_TITLE_KEY, key) is not None
    return titled and re.search(ORGANIZATION_WORD_KEY, key) is None


def is_individual(name: str | None, sponsor_class: str | None) -> bool:
    if (sponsor_class or "").upper() == INDIVIDUAL_CLASS:
        return True
    if not name:
        return False
    if DEGREE_TITLE.search(name) and not ORGANIZATION_WORD.search(name):
        return True
    return opens_with_personal_title(sponsor_key(name))


def sponsor_key(name: str) -> str | None:
    text = unicodedata.normalize("NFKC", name).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = " ".join(text.split())
    return text or None


def has_identity(key: str | None, individual: bool | None) -> bool:
    """Whether a sponsor key may carry a track record of its own (ADR 0018)."""
    if individual or not key or key in PLACEHOLDER_KEYS:
        return False
    return not opens_with_personal_title(key)


def has_identity_sql(key: str, individual: str) -> str:
    """SQL form of `has_identity` for a key column and an is-individual column."""
    placeholders = ", ".join(f"'{k}'" for k in PLACEHOLDER_KEYS)
    return (
        f"({key} IS NOT NULL AND {key} <> '' AND NOT coalesce({individual}, false) "
        f"AND {key} NOT IN ({placeholders}) "
        f"AND NOT (regexp_matches({key}, '{PERSONAL_TITLE_KEY}') "
        f"AND NOT regexp_matches({key}, '{ORGANIZATION_WORD_KEY}')))"
    )


def sponsor(name: str | None, sponsor_class: str | None) -> Sponsor:
    """The stored sponsor fields: nothing that names a person."""
    clean = normalize_text(name)
    if is_individual(clean, sponsor_class):
        return Sponsor(None, None, True)
    return Sponsor(clean, sponsor_key(clean) if clean else None, False)
