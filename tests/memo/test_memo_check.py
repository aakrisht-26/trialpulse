"""The memo's fabrication check (ADR 0084, memo_v2): faithful memos pass, and deliberately
fabricated memos are refused, each for its own reason. The model writes only the driver
sentences of paragraph 2; paragraphs 1 and 3 are the template's."""

import datetime as dt
from typing import Any

import pytest

from trialpulse.memo import check as c
from trialpulse.memo import payload as p
from trialpulse.memo import template as t

PAYLOAD: dict[str, Any] = {
    "payload_version": 2, "nct_id": "NCT01234567", "score_date": "2026-11-16",
    "horizons_months": [12, 24], "risk_percent": {"12": 14, "24": 26}, "chosen_as": [p.TOP],
    "status": "RECRUITING", "phase_in_title": "PHASE2", "sponsor_class": "INDUSTRY",
    "drivers_per_horizon": 5, "top_n": 100,
    "drivers": {"12": [
        {"feature": "enrollment_count", "label": "enrollment count", "value": "1704",
         "display": "1,704", "direction": "lowers"},
        {"feature": "sponsor_stop_rate", "label": "sponsor early-stop rate", "value": "0.446",
         "display": "44.6%", "direction": "raises"},
        {"feature": "months_since_t0", "label": "time since registration", "value": "36",
         "display": "36 months", "direction": "raises"}],
        "24": [{"feature": "enrollment_count", "label": "enrollment count", "value": "1704",
                "display": "1,704", "direction": "lowers"}]},
    "record_changes": [{"post_date": "2026-11-10", "changed": ["dates", "enrollment"]}],
    "record_changes_total": 1,
    "field_changes": [{"post_date": "2026-11-10", "field": "enrollment_count",
                       "label": "enrollment count", "before": "120", "after": "80"}],
    "values_held_from": "2026-11-08",
}  # fmt: skip
NAMES = ("Acme Pharma Inc.", "Northwind University")
SENTENCES = [
    "The enrollment count is 1,704, which lowers the risk.",
    "The sponsor early-stop rate is 44.6%, which raises the risk.",
    "The time since registration is 36 months, which raises the risk.",
]
GOOD = t.memo(PAYLOAD, SENTENCES)


def with_sentences(*replace: tuple[int, str], payload: dict[str, Any] = PAYLOAD) -> list[str]:
    """The good memo with some of its driver sentences replaced (index, sentence)."""
    found = list(SENTENCES)
    for i, sentence in replace:
        found[i] = sentence
    return t.memo(payload, found)


def test_a_faithful_memo_passes() -> None:
    result = c.check(GOOD, PAYLOAD, NAMES)
    assert result.passed, result.details
    assert result.paragraphs == GOOD


THIRD = "The time since registration is 36 months, which raises the risk"


@pytest.mark.parametrize(
    ("sentence", "reason"),
    [
        (f"{THIRD} by 7 points.", "number"),  # an invented number
        ("The time since registration is 37 months, which raises the risk.", "number"),
        (f"{THIRD} since 2026-11-12.", "date"),  # an invented date
        (f"{THIRD} since November 10, 2026.", "date_format"),
        (f"{THIRD} since 10/11/2026.", "date_format"),
        (f"{THIRD} in Q4 2026.", "date_format"),
        (f"{THIRD}, as for NCT07654321.", "registry_id"),
        (f"{THIRD} for Acme Pharma Inc.", "name"),
        (f"{THIRD}, says Dr. Smith.", "name"),
        (f"{THIRD}, so the drug is unlikely to work.", "banned_language"),
        (f"{THIRD}, so the trial will fail.", "banned_language"),
        (f"{THIRD}, so patients should avoid it.", "banned_language"),
        (f"{THIRD} because of its safety record.", "banned_language"),
        (f"{THIRD}, and it will certainly stop.", "banned_language"),
        (f"{THIRD}; we recommend a review.", "banned_language"),
        (f"{THIRD} (see [the record](https://clinicaltrials.gov)).", "link"),
        (f"{THIRD} (www.example.org).", "link"),
    ],
)
def test_a_fabricated_fact_or_claim_is_refused(sentence: str, reason: str) -> None:
    result = c.check(with_sentences((2, sentence)), PAYLOAD, NAMES)
    assert not result.passed
    assert reason in result.reasons, result.details


def test_a_risk_off_by_one_point_is_refused() -> None:
    first = GOOD[0].replace("14%", "15%")
    result = c.check([first, GOOD[1], GOOD[2]], PAYLOAD, NAMES)
    assert result.reasons == ["template", "number"]


@pytest.mark.parametrize(
    "sentence",
    [
        f"{THIRD}, about three years.",
        f"{THIRD}, twelve of them.",
        f"{THIRD}, a dozen in all.",
        f"{THIRD}, half of the plan.",
        f"{THIRD}, one of several.",
        f"{THIRD}, a couple of them.",
    ],
)
def test_a_number_in_words_is_refused(sentence: str) -> None:
    """Change A: every number must be in digits, so the number rule sees it."""
    result = c.check(with_sentences((2, sentence)), PAYLOAD, NAMES)
    assert "number_word" in result.reasons, result.details


@pytest.mark.parametrize(
    "sentence",
    [
        f"{THIRD}, and it doubled.",
        f"{THIRD}, and it tripled.",
        f"{THIRD}, and it halved.",
        f"{THIRD}, twice the average.",
        f"{THIRD}, 3 times the average.",
        f"{THIRD}, x times the average.",
        f"{THIRD} twofold.",
        f"{THIRD} 2-fold.",
        f"{THIRD} by 2x.",
    ],
)
def test_an_invented_comparison_is_refused(sentence: str) -> None:
    """Change A: no multiplier or ratio words."""
    result = c.check(with_sentences((2, sentence)), PAYLOAD, NAMES)
    assert "comparison" in result.reasons, result.details


def test_the_structure_is_three_paragraphs_and_a_short_second_one() -> None:
    assert c.check(GOOD[:2], PAYLOAD).reasons == ["structure"]
    assert "structure" in c.check([GOOD[0], "x" * 1001, GOOD[2]], PAYLOAD).reasons
    assert "structure" in c.check([GOOD[0], "", GOOD[2]], PAYLOAD).reasons


# memo_v2: the per-sentence rule ---------------------------------------------------------


def test_a_swapped_direction_is_refused() -> None:
    swapped = "The enrollment count is 1,704, which raises the risk."
    result = c.check(with_sentences((0, swapped)), PAYLOAD, NAMES)
    assert result.reasons == ["driver_direction"]
    both = "The enrollment count is 1,704, which lowers the risk and raises the risk."
    assert c.check(with_sentences((0, both)), PAYLOAD, NAMES).reasons == ["driver_direction"]


def test_a_swapped_driver_order_is_refused() -> None:
    result = c.check(with_sentences((0, SENTENCES[1]), (1, SENTENCES[0])), PAYLOAD, NAMES)
    assert "driver_label" in result.reasons
    assert "driver_number" in result.reasons
    assert not result.passed


def test_another_drivers_label_in_a_sentence_is_refused() -> None:
    extra = "The time since registration is 36 months, beside the enrollment count, which raises the risk."  # noqa: E501
    result = c.check(with_sentences((2, extra)), PAYLOAD, NAMES)
    assert result.reasons == ["other_label"]


def test_another_drivers_number_in_a_sentence_is_refused() -> None:
    extra = "The time since registration is 36 months against 1,704, which raises the risk."
    assert c.check(with_sentences((2, extra)), PAYLOAD, NAMES).reasons == ["driver_number"]


def test_a_sentence_per_driver_exactly() -> None:
    fewer = t.memo(PAYLOAD, SENTENCES[:2])
    assert c.check(fewer, PAYLOAD, NAMES).reasons == ["sentence_count"]
    more = t.memo(PAYLOAD, [*SENTENCES, "A driver is not a reason."])
    assert c.check(more, PAYLOAD, NAMES).reasons == ["sentence_count"]


def test_raw_feature_names_are_refused() -> None:
    """The review's finding: memos restated column names a reader cannot use."""
    raw = "enrollment_count = 1704 (lowers)."
    result = c.check(with_sentences((0, raw)), PAYLOAD, NAMES)
    assert "driver_label" in result.reasons
    assert "driver_direction" in result.reasons


def test_the_label_matches_case_aside_and_a_decimal_point_never_splits_a_sentence() -> None:
    capital = "Sponsor early-stop rate: 44.6%, which raises the risk."
    assert c.check(with_sentences((1, capital)), PAYLOAD, NAMES).passed
    assert c.sentences("A 44.6% rate. Then 3 more.") == ["A 44.6% rate.", "Then 3 more."]


def test_a_payload_without_12_month_drivers_has_a_fixed_second_paragraph() -> None:
    bare = {**PAYLOAD, "drivers": {"12": [], "24": []}}
    assert c.check(t.memo(bare, None), bare, NAMES).passed
    wrong = [t.paragraph_one(bare), "The score has no drivers.", t.paragraph_three(bare)]
    assert c.check(wrong, bare, NAMES).reasons == ["sentence_count"]


# memo_v2: the review's failures, made impossible by the template rule --------------------


def test_no_record_change_when_the_payload_holds_some_is_refused() -> None:
    """Memos 1, 16 and 18 of the review said that no record change was held."""
    third = "No record change is held since the trial's first live version."
    result = c.check([GOOD[0], GOOD[1], third], PAYLOAD, NAMES)
    assert result.reasons == ["template"]


def test_a_contradiction_about_record_changes_is_refused() -> None:
    """Memo 17 named a record change, then said that none was held."""
    third = ("The latest record change occurred on 2026-11-10 and changed dates. No record "
             "change is held since the trial's first live version.")  # fmt: skip
    assert c.check([GOOD[0], GOOD[1], third], PAYLOAD, NAMES).reasons == ["template"]


MEMO_6 = {**PAYLOAD, "record_changes": [
    {"post_date": "2026-08-18", "changed": ["interventions or phase in the title"]},
    {"post_date": "2026-08-12", "changed": ["dates"]},
    {"post_date": "2026-05-20", "changed": ["status", "dates", "enrollment"]},
    {"post_date": "2026-02-23", "changed": ["status", "dates"]},
    {"post_date": "2025-12-16", "changed": ["dates"]},
    {"post_date": "2025-09-04", "changed": ["status"]}],
    "record_changes_total": 6, "field_changes": []}  # fmt: skip


def test_wrong_change_kinds_on_a_date_are_refused() -> None:
    """Memo 6 said 2025-09-04 changed status and dates, and lost the enrollment change of
    2026-05-20."""
    third = ("The latest record changes are: status, dates on 2025-09-04; dates on 2025-12-16; "
             "status, dates on 2026-02-23; status, dates on 2026-05-20; dates on 2026-08-12; "
             "interventions or phase in the title on 2026-08-18.")  # fmt: skip
    good = t.memo(MEMO_6, SENTENCES)
    assert c.check(good, MEMO_6, NAMES).passed
    assert c.check([good[0], good[1], third], MEMO_6, NAMES).reasons == ["template"]


MEMO_19 = {**PAYLOAD, "field_changes": [
    {"post_date": "2026-11-16", "field": "start_date", "label": "start date",
     "before": "2026-11-18", "after": "2026-11-19"},
    {"post_date": "2026-11-13", "field": "start_date", "label": "start date",
     "before": "2026-11-17", "after": "2026-11-18"}]}  # fmt: skip


def test_a_garbled_before_and_after_is_refused() -> None:
    """Memo 19 wrote "start_date after 2026-11-19 before 2026-11-18 post_date 2026-11-16"."""
    third = ("Record changes include start_date after 2026-11-19 before 2026-11-18 "
             "post_date 2026-11-16; start_date after 2026-11-18 before 2026-11-17 "
             "post_date 2026-11-13.")  # fmt: skip
    good = t.memo(MEMO_19, SENTENCES)
    assert c.check(good, MEMO_19, NAMES).passed
    assert c.check([good[0], good[1], third], MEMO_19, NAMES).reasons == ["template"]


# Normalization (memo_v2) ----------------------------------------------------------------

HYPHENS = (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFE58, 0xFE63, 0xFF0D)
SPACES = (0x00A0, 0x202F, 0x2009, 0x2007)


@pytest.mark.parametrize("code", HYPHENS, ids=hex)
def test_every_unicode_hyphen_dash_and_minus_is_a_hyphen(code: int) -> None:
    """The first run refused 12 memos that wrote "early-stop" with U+2011."""
    sentence = f"The sponsor early{chr(code)}stop rate is 44.6%, which raises the risk."
    result = c.check(with_sentences((1, sentence)), PAYLOAD, NAMES)
    assert result.passed, result.details
    assert result.paragraphs[1] == GOOD[1]
    assert c.normalize(f"early{chr(code)}stop") == "early-stop"


@pytest.mark.parametrize("code", SPACES, ids=hex)
def test_special_spaces_are_plain_spaces(code: int) -> None:
    sentence = f"The enrollment{chr(code)}count is 1,704, which lowers{chr(code)}the risk."
    result = c.check(with_sentences((0, sentence)), PAYLOAD, NAMES)
    assert result.passed, result.details
    assert result.paragraphs[1] == GOOD[1]


def test_the_checked_text_holds_no_special_character() -> None:
    weird = "".join(chr(x) for x in (*HYPHENS, *SPACES))
    assert c.normalize(weird) == "-" * len(HYPHENS) + " " * len(SPACES)


def test_feature_names_are_not_numbers() -> None:
    sentence = "The time since registration (months_since_t0) is 36 months, which raises the risk."
    assert c.check(with_sentences((2, sentence)), PAYLOAD, NAMES).passed


# Change B: the required wording depends on why the trial was chosen --------------------


def moved(direction: str, top: bool = False) -> dict[str, Any]:
    chosen = sorted({p.MOVED, *([p.TOP] if top else [])})
    return {**PAYLOAD, "chosen_as": chosen,
            "move": {"previous_risk_percent_12": 9 if direction == "rose" else 19,
                     "previous_score_date": "2026-11-09", "direction": direction,
                     "change_points": 5}}  # fmt: skip


@pytest.mark.parametrize(("direction", "top"), [("rose", False), ("fell", False),
                                                ("rose", True), ("fell", True)])  # fmt: skip
def test_a_movers_template_passes_and_says_elevated_only_in_the_top_100(
    direction: str, top: bool
) -> None:
    payload = moved(direction, top)
    good = t.memo(payload, SENTENCES)
    assert c.check(good, payload, NAMES).passed
    assert (c.REQUIRED in good[0]) is top
    assert direction in good[0]
    assert "elevated" not in " ".join(good[1:])


def test_the_wording_rules_still_hold_beside_the_template() -> None:
    first = GOOD[0].replace("has elevated early-stop risk", "has a risk")
    assert c.check([first, GOOD[1], GOOD[2]], PAYLOAD, NAMES).reasons == [
        "template", "required_phrase"]  # fmt: skip
    payload = moved("fell")
    elevated = "The time since registration is 36 months, an elevated value, which raises the risk."
    result = c.check(with_sentences((2, elevated), payload=payload), payload, NAMES)
    assert result.reasons == ["elevated_after_fall"]
    rose = "The time since registration is 36 months and rose, which raises the risk."
    assert "direction" in c.check(with_sentences((2, rose), payload=payload), payload,
                                  NAMES).reasons  # fmt: skip


def test_the_allowed_numbers_include_renderings_of_shares() -> None:
    numbers, dates = c.allowed(PAYLOAD)
    for token in ("0.446", "0.45", "0.4", "45", "44.6", "1704", "100", "5", "12", "24"):
        assert c._decimal(token) in numbers, token
    assert c._decimal("2026") not in numbers  # a date's year is no number of its own
    assert dates == {"2026-11-16", "2026-11-10", "2026-11-08"}
    assert dt.date.fromisoformat(max(dates)) == dt.date(2026, 11, 16)
