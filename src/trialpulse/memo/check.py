"""The fabrication check of a risk memo (ADR 0084): deterministic, and a memo that fails is
never stored.

A memo passes only if all of these hold (the reason codes in brackets):

- [structure] exactly three non-empty paragraphs, the second of at most 1,000 characters;
- [template] the first and third paragraphs are exactly the template's (`memo.template`):
  the risks and why the trial was chosen, and its record changes;
- [sentence_count] the second paragraph has exactly one sentence per 12-month driver (or is
  the fixed sentence of a payload without one); in sentence i, [driver_label] driver i's
  label appears (case aside), [driver_value] driver i's display value appears word for word
  (case aside) and no other level or flag phrase of its feature that the display does not
  hold (memo_v3), [driver_direction] its effect appears as "raises the risk" or
  "lowers the risk" and the other effect does not, [other_label] no label of another served
  feature appears, [driver_words] beyond its label, display value and effect the sentence
  holds only connective words ("the", "is", "which" and the like; memo_v3), since the model
  is sent nothing else, so a negation, another fact or an outcome would be invented, and
  [driver_number] every number is driver i's display value in an allowed rendering;
- [registry_id] no registry id but the trial's own;
- [date] every ISO date (YYYY-MM-DD) is a date of the payload; [date_format] no date in any
  other form (a month's name, a slashed or dotted date, a quarter);
- [number] every number written in digits is a number of the payload, in an allowed
  rendering (as shown; a decimal rounded to 0 to 2 places; a share between 0 and 1 as a
  percent with 0 or 1 decimals); feature names (snake_case) are not read as numbers;
- [number_word] no number written in words ("twelve", "a dozen", "half", "a couple"), so every
  number goes through the number rule (change A);
- [comparison] no multiplier or ratio words ("doubled", "twice", "3 times", "x times", "2x",
  "twofold"), so no comparison the payload does not hold (change A);
- [banned_language] none of CLAUDE.md Section 2's forbidden claims: outcome claims ("fail",
  "will stop", and since memo_v3 "was terminated", "has stopped", "likely to end"), efficacy
  or safety claims, advice to patients, certainty words; the word rules read a snake_case
  token as words ("the_trial_will_fail"), while the number rule skips feature names;
- [name] no sponsor display name the API can show and no personal title;
- [link] no web address and no Markdown link, since the page renders the text as Markdown;
- [required_phrase] a trial in the top 100 says "elevated early-stop risk"; [risk_stated]
  a trial chosen for a move states its 12-month risk as a percent; [direction] it states the
  direction of the move ("rose" or "increased"; "fell" or "decreased") and never the
  opposite; [elevated_after_fall] a trial chosen for a fall alone never calls its risk
  elevated (change B).

Before the check, every Unicode hyphen, dash and minus sign becomes "-" and every
non-breaking, narrow or thin space a plain space (memo_v2); the stored memo is exactly the
normalized text that was checked. The result lists every failed rule with details for the
review; the details quote only the memo and the payload.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from trialpulse.memo import labels, template
from trialpulse.memo.payload import MOVED, TOP

NCT = re.compile(r"\bNCT\d{8}\b")
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
MONTHS = ("January|February|March|April|June|July|August|September|October|November|December|"
          "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec")  # fmt: skip
OTHER_DATES = (
    re.compile(rf"\b(?:{MONTHS})\b\.?"),
    re.compile(r"\bMay\b(?=\s*\d)|(?<=\d)\s*May\b"),  # "May" as a month, not the verb
    re.compile(r"\b\d{1,4}[/.]\d{1,2}[/.]\d{1,4}\b"),
    re.compile(r"\bQ[1-4]\s*\d{2,4}\b"),
)
IDENTIFIER = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b")
NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])|(?<![\w.])\d+(?:\.\d+)?")
NUMBER_WORDS = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|"
    r"seventy|eighty|ninety|hundreds?|thousands?|millions?|billions?|dozens?|half|couple)\b",
    re.IGNORECASE,
)
COMPARISONS = re.compile(
    r"\b(?:doubl\w*|tripl\w*|quadrupl\w*|halv\w*|twice|thrice)\b|\b\w*fold\b"
    r"|(?:\d+(?:\.\d+)?|\bx)\s*(?:-\s*)?times\b|\b\d+(?:\.\d+)?\s*x\b",
    re.IGNORECASE,
)
BANNED = re.compile(
    r"\bfail(?:s|ed|ing|ure|ures)?\b|\bwill\s+(?:stop|end|be\s+stopped|be\s+terminated"
    r"|terminate|be\s+withdrawn|close)\b|\bgoing\s+to\s+stop\b"
    r"|\befficac\w*|\b(?:in)?effective(?:ness)?\b|\bworks?\b|\bworked\b|\bcures?\b"
    r"|\bbenefit\w*|\b(?:un)?safe(?:ty|ly)?\b|\btoxic\w*|\bharm(?:s|ful)?\b"
    r"|\bpatients?\s+should\b|\byou\s+should\b|\bshould\s+(?:not\s+)?(?:enroll|join|avoid)\b"
    r"|\benrol(?:l|ling|ls)?\s+in\b|\bconsider\s+enrolling\b|\byour\s+doctor\b|\btalk\s+to\b"
    r"|\brecommend\w*|\badvi[cs]\w*"
    r"|\bcertain(?:ly)?\b|\bguarantee\w*|\bdefinitely\b|\binevitabl\w*|\bsurely\b"
    # memo_v3: outcome claims in the past tense or as a likelihood
    r"|\b(?:was|were|has|have|had|is|are|been|being|got|gets|get)\s+(?:been\s+)?"
    r"(?:terminated|withdrawn|stopped|halted|ended|discontinued|cancell?ed)\b"
    r"|\b(?:likely|unlikely|probably|expected|set|about|bound)\s+to\s+(?:be\s+)?"
    r"(?:stop|end|terminat\w*|withdraw\w*|fail|close|halt)\w*\b",
    re.IGNORECASE,
)
TITLES = re.compile(r"\b(?:Dr|Prof|Mr|Mrs|Ms|MD|PhD)\b\.?")
LINKS = re.compile(r"https?://|\bwww\.|\]\(|<a\b|\.(?:com|org|gov|net)\b", re.IGNORECASE)
REQUIRED = "elevated early-stop risk"
RISE = re.compile(r"\b(?:rose|risen|rising|increased|increase)\b", re.IGNORECASE)
FALL = re.compile(r"\b(?:fell|fallen|falling|decreased|decrease|dropped|declined)\b",
                  re.IGNORECASE)  # fmt: skip
ELEVATED = re.compile(r"\belevated\b", re.IGNORECASE)
MAX_PARAGRAPH = 1000  # the model's paragraph; the template's are not limited
# memo_v2: every Unicode hyphen, dash and minus to "-", and special spaces to a plain space.
HYPHENS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"
SPACES = "\u00a0\u202f\u2009\u2007"
NORMAL = str.maketrans({**dict.fromkeys(HYPHENS, "-"), **dict.fromkeys(SPACES, " ")})
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
EFFECTS = {"raises": "raises the risk", "lowers": "lowers the risk"}
# memo_v3: besides its driver's label, value and effect, a sentence holds only these words,
# which carry no fact of their own; the model is sent nothing else, so any other word (a
# negation, another feature, an outcome) would be invented.
CONNECTIVES = frozenset({"the", "a", "an", "is", "which", "and", "this", "that", "it", "its",
                         "of", "at", "with", "being", "here"})  # fmt: skip
WORD = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass
class CheckResult:
    """The outcome of the check: the normalized text, and every failed rule with details."""

    paragraphs: list[str]
    reasons: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.reasons

    def fail(self, reason: str, detail: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)
        self.details.append(f"{reason}: {detail}")


def normalize(text: str) -> str:
    """Every Unicode hyphen, dash and minus as "-", and special spaces as a plain space."""
    return text.translate(NORMAL)


def sentences(paragraph: str) -> list[str]:
    """A paragraph's sentences: split after a full stop, question or exclamation mark that a
    space follows, so a decimal point never splits one."""
    return [s for s in SENTENCE_END.split(paragraph.strip()) if s]


def _phrase(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-]){re.escape(phrase)}(?![\w-])", re.IGNORECASE)


def _has(phrase: str, text: str) -> bool:
    """The phrase appears in the text as whole words, case aside."""
    return _phrase(phrase).search(text) is not None


def _extra_words(sentence: str, driver: Mapping[str, Any]) -> list[str]:
    """The words of a driver's sentence beyond its display value, its label, an effect phrase
    (each taken out once, case aside) and the connective words."""
    rest = sentence
    for phrase in (str(driver["display"]), str(driver["label"]), *EFFECTS.values()):
        rest = _phrase(phrase).sub(" ", rest, count=1)
    return [w for w in WORD.findall(rest) if w.lower() not in CONNECTIVES]


def _alternatives(driver: Mapping[str, Any]) -> list[str]:
    """The other words a category or flag of the driver's feature can be shown with, those the
    displayed value does not itself hold: "not suspended now" holds "suspended now", so a
    sentence holding the display can still say the opposite unless these are refused."""
    entry = labels.FEATURES.get(str(driver.get("feature")))
    if entry is None:
        return []
    phrases = [*entry.levels.values(), entry.yes, entry.no]
    display = str(driver["display"])
    return [x for x in phrases if x and x != display and not _has(x, display)]


def _drivers(paragraph: str, payload: Mapping[str, Any], result: CheckResult) -> None:
    """The per-sentence rule of the second paragraph (memo_v2; the display value word for word
    since memo_v3)."""
    drivers = template.drivers_12(payload)
    if not drivers:
        if paragraph != template.NO_DRIVERS:
            result.fail("sentence_count", "no driver is stored, but the paragraph is not fixed")
        return
    found = sentences(paragraph)
    if len(found) != len(drivers):
        result.fail("sentence_count", f"{len(found)} sentences for {len(drivers)} drivers")
        return
    for i, (sentence, driver) in enumerate(zip(found, drivers, strict=True), start=1):
        if not _has(driver["label"], sentence):
            result.fail("driver_label", f"sentence {i} lacks '{driver['label']}'")
        if not _has(driver["display"], sentence):
            result.fail("driver_value", f"sentence {i} lacks '{driver['display']}'")
        for phrase in _alternatives(driver):
            if _has(phrase, sentence):
                result.fail("driver_value", f"sentence {i} says '{phrase}'")
        effect = EFFECTS[driver["direction"]]
        other = next(e for e in EFFECTS.values() if e != effect)
        if not _has(effect, sentence):
            result.fail("driver_direction", f"sentence {i} lacks '{effect}'")
        if _has(other, sentence):
            result.fail("driver_direction", f"sentence {i} says '{other}'")
        others = {d["label"] for d in drivers} | {e.label for e in labels.FEATURES.values()}
        for label in sorted(others - {driver["label"]}):
            if _has(label, sentence) and not _has(label, str(driver["display"])):
                result.fail("other_label", f"sentence {i} names '{label}'")
        extra = _extra_words(sentence, driver)
        if extra:
            result.fail("driver_words", f"sentence {i} holds '{' '.join(extra)}'")
        numbers, _ = allowed({"display": driver["display"]})
        for token in NUMBER.findall(IDENTIFIER.sub(" ", sentence)):
            d = _decimal(token)
            if d is None or not any(d == n for n in numbers):
                result.fail("driver_number", f"sentence {i} has {token}")


def _scalars(value: Any) -> Iterable[Any]:
    if isinstance(value, Mapping):
        for v in value.values():
            yield from _scalars(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _scalars(v)
    else:
        yield value


def _decimal(token: str) -> Decimal | None:
    try:
        return Decimal(token.replace(",", ""))
    except InvalidOperation:
        return None


def _round(d: Decimal, places: int) -> Decimal:
    return d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def allowed(payload: Mapping[str, Any]) -> tuple[set[Decimal], set[str]]:
    """The numbers (with their allowed renderings) and the ISO dates of a payload."""
    numbers: set[Decimal] = set()
    dates: set[str] = set()
    for value in _scalars(payload):
        if isinstance(value, bool) or value is None:
            continue
        text = str(value)
        dates.update(ISO_DATE.findall(text))
        text = NCT.sub(" ", ISO_DATE.sub(" ", text))
        text = IDENTIFIER.sub(" ", text)
        for token in NUMBER.findall(text):
            d = _decimal(token)
            if d is None:
                continue
            numbers.add(d)
            if d != d.to_integral_value():
                numbers.update(_round(d, p) for p in (0, 1, 2))
                if Decimal(0) <= d <= Decimal(1):
                    numbers.update(_round(d * 100, p) for p in (0, 1))
    return numbers, dates


def check(
    paragraphs: Sequence[str], payload: Mapping[str, Any], names: Iterable[str] = ()
) -> CheckResult:
    """Every rule of the module text, on the memo's paragraphs against its payload."""
    texts = [normalize(str(p)).strip() for p in paragraphs]
    result = CheckResult(texts)
    if len(texts) != 3 or any(not t for t in texts) or len(texts[1]) > MAX_PARAGRAPH:
        result.fail("structure", f"{len(texts)} paragraphs of lengths {[len(t) for t in texts]}")
    else:
        if texts[0] != template.paragraph_one(payload):
            result.fail("template", "the first paragraph is not the template's")
        if texts[2] != template.paragraph_three(payload):
            result.fail("template", "the third paragraph is not the template's")
        _drivers(texts[1], payload, result)
    text = "\n\n".join(texts)
    own = str(payload["nct_id"])
    numbers, dates = allowed(payload)

    for found in NCT.findall(text):
        if found != own:
            result.fail("registry_id", found)
    rest = NCT.sub(" ", text)
    for found in ISO_DATE.findall(rest):
        if found not in dates:
            result.fail("date", found)
    rest = ISO_DATE.sub(" ", rest)
    for pattern in OTHER_DATES:
        for m in pattern.finditer(rest):
            result.fail("date_format", m.group(0).strip())
    words = IDENTIFIER.sub(" ", rest)  # for the number rule: a feature name is no number
    spaced = rest.replace("_", " ")  # for the word rules: a snake_case claim reads as words
    for m in NUMBER_WORDS.finditer(spaced):
        result.fail("number_word", m.group(0))
    for m in COMPARISONS.finditer(spaced):
        result.fail("comparison", m.group(0).strip())
    for token in NUMBER.findall(words):
        d = _decimal(token)
        if d is None or not any(d == n for n in numbers):
            result.fail("number", token)
    for m in BANNED.finditer(spaced):
        result.fail("banned_language", m.group(0))
    lowered = text.lower()
    for name in names:
        name = name.strip()
        if len(name) >= 3 and re.search(rf"(?<!\w){re.escape(name.lower())}(?!\w)", lowered):
            result.fail("name", name)
    for m in TITLES.finditer(text):
        result.fail("name", m.group(0))
    for m in LINKS.finditer(text):
        result.fail("link", m.group(0))

    chosen = set(payload.get("chosen_as") or ())
    if TOP in chosen and REQUIRED not in lowered:
        result.fail("required_phrase", f"no '{REQUIRED}'")
    move = payload.get("move")
    if MOVED in chosen and move:
        risk = int(payload["risk_percent"]["12"])
        if not re.search(rf"(?<![\d.]){risk}\s*(?:%|percent\b)", text):
            result.fail("risk_stated", f"the 12-month risk {risk}% is not stated")
        same, other = (RISE, FALL) if move["direction"] == "rose" else (FALL, RISE)
        if not same.search(spaced):
            result.fail("direction", f"the direction '{move['direction']}' is not stated")
        for m in other.finditer(spaced):
            result.fail("direction", f"the opposite direction '{m.group(0)}'")
        if move["direction"] == "fell" and TOP not in chosen and ELEVATED.search(spaced):
            result.fail("elevated_after_fall", "a fallen risk called elevated")
    return result
