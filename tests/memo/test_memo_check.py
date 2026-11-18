"""The memo's fabrication check (ADR 0084, memo_v3): faithful memos pass, and deliberately
fabricated memos are refused, each for its own reason. The model writes only the driver
sentences of paragraph 2; paragraphs 1 and 3 are the template's."""

import datetime as dt
from typing import Any

import pytest

from trialpulse.memo import check as c
from trialpulse.memo import labels as lab
from trialpulse.memo import payload as p
from trialpulse.memo import template as t

RATE = "early-stop rate of the sponsor, smoothed toward its class rate"
TIME = "time since the first posting, as the score counts it"
PAYLOAD: dict[str, Any] = {
    "payload_version": 3, "nct_id": "NCT01234567", "score_date": "2026-11-16",
    "horizons_months": [12, 24], "risk_percent": {"12": 14, "24": 26}, "chosen_as": [p.TOP],
    "status": "RECRUITING", "phase_in_title": "PHASE2", "sponsor_class": "INDUSTRY",
    "status_verified_month": "2026-10", "drivers_per_horizon": 5, "top_n": 100,
    "drivers": {"12": [
        {"feature": "enrollment_count", "label": "enrollment count", "value": "1704",
         "display": "1,704, the estimated target", "direction": "lowers"},
        {"feature": "sponsor_stop_rate", "label": RATE, "value": "0.446", "display": "44.6%",
         "direction": "raises"},
        {"feature": "months_since_t0", "label": TIME, "value": "36", "display": "36 months",
         "direction": "raises"}],
        "24": [{"feature": "enrollment_count", "label": "enrollment count", "value": "1704",
                "display": "1,704, the estimated target", "direction": "lowers"}]},
    "first_posted_on": "2024-03-04", "stored_markers": True,
    "record_changes": [{"post_date": "2026-11-10", "changed": ["dates", "enrollment"]}],
    "record_changes_total": 1,
    "since_go_live": [{"post_date": "2026-11-10", "held": p.COMPARED, "values": [
        {"field": "primary_completion_date", "label": "primary completion date",
         "before": "2027-06-30", "after": "2028-01-31"},
        {"field": "enrollment_count", "label": "enrollment count", "before": "120",
         "after": "80"}]}],
    "values_held_from": "2026-11-08",
}  # fmt: skip
NAMES = ("Acme Pharma Inc.", "Northwind University")
SENTENCES = [
    "The enrollment count is 1,704, the estimated target, which lowers the risk.",
    f"The {RATE} is 44.6%, which raises the risk.",
    f"The {TIME} is 36 months, which raises the risk.",
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


THIRD = f"The {TIME} is 36 months, which raises the risk"


@pytest.mark.parametrize(
    ("sentence", "reason"),
    [
        (f"{THIRD} by 7 points.", "number"),  # an invented number
        (f"The {TIME} is 37 months, which raises the risk.", "number"),
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


# The per-sentence rule (memo_v2; the display value word for word since memo_v3) ----------


def test_a_swapped_direction_is_refused() -> None:
    swapped = "The enrollment count is 1,704, the estimated target, which raises the risk."
    result = c.check(with_sentences((0, swapped)), PAYLOAD, NAMES)
    assert result.reasons == ["driver_direction"]
    both = ("The enrollment count is 1,704, the estimated target, which lowers the risk and "
            "raises the risk.")  # fmt: skip
    assert c.check(with_sentences((0, both)), PAYLOAD, NAMES).reasons == ["driver_direction"]


def test_a_swapped_driver_order_is_refused() -> None:
    result = c.check(with_sentences((0, SENTENCES[1]), (1, SENTENCES[0])), PAYLOAD, NAMES)
    assert "driver_label" in result.reasons
    assert "driver_value" in result.reasons
    assert "driver_number" in result.reasons
    assert not result.passed


def test_another_drivers_label_in_a_sentence_is_refused() -> None:
    extra = f"The {TIME} is 36 months, beside the enrollment count, which raises the risk."
    result = c.check(with_sentences((2, extra)), PAYLOAD, NAMES)
    assert result.reasons == ["other_label", "driver_words"]


def test_another_drivers_number_in_a_sentence_is_refused() -> None:
    extra = f"The {TIME} is 36 months against 1,704, which raises the risk."
    reasons = c.check(with_sentences((2, extra)), PAYLOAD, NAMES).reasons
    assert reasons == ["driver_words", "driver_number"]


def test_a_sentence_per_driver_exactly() -> None:
    fewer = t.memo(PAYLOAD, SENTENCES[:2])
    assert c.check(fewer, PAYLOAD, NAMES).reasons == ["sentence_count"]
    more = t.memo(PAYLOAD, [*SENTENCES, "A driver is not a reason."])
    assert c.check(more, PAYLOAD, NAMES).reasons == ["sentence_count"]


def test_raw_feature_names_are_refused() -> None:
    """The v1 review's finding: memos restated column names a reader cannot use."""
    raw = "enrollment_count = 1704 (lowers)."
    result = c.check(with_sentences((0, raw)), PAYLOAD, NAMES)
    assert {"driver_label", "driver_value", "driver_direction"} <= set(result.reasons)


def test_the_label_matches_case_aside_and_a_decimal_point_never_splits_a_sentence() -> None:
    capital = "Early-stop rate of the sponsor, smoothed toward its class rate: 44.6%, which raises the risk."  # noqa: E501
    assert c.check(with_sentences((1, capital)), PAYLOAD, NAMES).passed
    assert c.sentences("A 44.6% rate. Then 3 more.") == ["A 44.6% rate.", "Then 3 more."]


def test_a_payload_without_12_month_drivers_has_a_fixed_second_paragraph() -> None:
    bare = {**PAYLOAD, "drivers": {"12": [], "24": []}}
    assert c.check(t.memo(bare, None), bare, NAMES).passed
    wrong = [t.paragraph_one(bare), "The score has no drivers.", t.paragraph_three(bare)]
    assert c.check(wrong, bare, NAMES).reasons == ["sentence_count"]


def one_driver(name: str, value: str, direction: str = "raises") -> dict[str, Any]:
    driver = {"feature": name, "label": lab.feature_label(name), "value": value,
              "display": lab.feature_display(name, value), "direction": direction}  # fmt: skip
    return {**PAYLOAD, "drivers": {"12": [driver], "24": []}}


@pytest.mark.parametrize(
    ("name", "value", "flipped"),
    [
        ("completion_slip_months", "-60", ("earlier", "later")),
        ("start_slip_months", "39", ("later", "earlier")),
        ("sponsor_class", "INDUSTRY", ("industry", "research network")),
        ("currently_suspended", "no", ("not suspended now", "suspended now")),
        ("currently_suspended", "yes", ("suspended now", "not suspended now")),  # holds it
        ("status", "RECRUITING", ("recruiting", "active and not recruiting")),  # holds it
        ("status", "RECRUITING", ("recruiting", "not yet recruiting")),  # holds it
        ("registered_after_start", "yes", ("after the recorded", "not after the recorded")),
    ],
)  # fmt: skip
def test_a_sentence_must_hold_its_display_value_word_for_word(
    name: str, value: str, flipped: tuple[str, str]
) -> None:
    """Decision D (memo_v3): the check held only a display's numbers, so "later" where the
    display says "earlier" passed; a sentence must now hold the display word for word."""
    payload = one_driver(name, value)
    d = payload["drivers"]["12"][0]
    good = f"The {d['label']} is {d['display']}, which raises the risk."
    assert c.check(t.memo(payload, [good]), payload, NAMES).passed
    wrong = good.replace(flipped[0], flipped[1], 1)
    assert wrong != good
    result = c.check(t.memo(payload, [wrong]), payload, NAMES)
    assert "driver_value" in result.reasons, result.details
    assert set(result.reasons) <= {"driver_value", "driver_words"}, result.details


def test_a_dropped_part_of_a_display_is_refused() -> None:
    """A display with its own clause ("the estimated target") must be kept whole."""
    short = "The enrollment count is 1,704, which lowers the risk."
    assert "driver_value" in c.check(with_sentences((0, short)), PAYLOAD, NAMES).reasons


@pytest.mark.parametrize(
    "sentence",
    [
        "The recruitment status is no longer recruiting, which raises the risk.",
        "The recruitment status is recruiting, which no longer raises the risk.",
        "The recruitment status is recruiting, which never raises the risk.",
        "The recruitment status is not recruiting, which raises the risk.",
        "The recruitment status isn't recruiting, which raises the risk.",
    ],
)
def test_a_negated_value_or_effect_is_refused(sentence: str) -> None:
    """The memo_v3 review: a sentence held the display and still negated it."""
    payload = one_driver("status", "RECRUITING")
    result = c.check(t.memo(payload, [sentence]), payload, NAMES)
    assert "driver_words" in result.reasons, result.details


@pytest.mark.parametrize(
    ("tail", "reason"),
    [
        (", and the sponsor class is industry", "other_label"),  # a feature that is no driver
        (", although the trial was terminated", "banned_language"),
        (", and the trial has been withdrawn", "banned_language"),
        (", so the trial is likely to end early", "banned_language"),
        (", so the_trial_will_fail", "banned_language"),
        (", a doubled_rate", "comparison"),
    ],
)
def test_an_invented_fact_beside_a_driver_is_refused(tail: str, reason: str) -> None:
    """The memo_v3 review: anything beyond a driver's label, value and effect is invented,
    since the model is sent nothing else."""
    sentence = f"The enrollment count is 1,704, the estimated target{tail}, which lowers the risk."
    result = c.check(with_sentences((0, sentence)), PAYLOAD, NAMES)
    assert "driver_words" in result.reasons, result.details
    assert reason in result.reasons, result.details


def test_a_plural_verb_is_refused() -> None:
    """The v2 audit's grammar finding: labels took "is" in some memos and "are" in others."""
    plural = f"The {TIME} are 36 months, which raises the risk."
    assert c.check(with_sentences((2, plural)), PAYLOAD, NAMES).reasons == ["driver_words"]


def test_connective_variants_of_the_form_pass() -> None:
    for sentence in (f"{TIME[:1].upper()}{TIME[1:]} is 36 months, which raises the risk.",
                     f"The {TIME}, at 36 months, raises the risk.",
                     f"With the {TIME} at 36 months, this raises the risk."):  # fmt: skip
        result = c.check(with_sentences((2, sentence)), PAYLOAD, NAMES)
        assert result.passed, (sentence, result.details)


# The template rule: the v1 review's failures stay impossible ------------------------------


def test_no_record_change_when_the_payload_holds_some_is_refused() -> None:
    """Memos 1, 16 and 18 of the v1 review said that no record change was held."""
    third = "No record change is held since the trial's first live version."
    result = c.check([GOOD[0], GOOD[1], third], PAYLOAD, NAMES)
    assert result.reasons == ["template"]


def test_a_contradiction_about_record_changes_is_refused() -> None:
    """Memo 17 of the v1 review named a record change, then said that none was held."""
    third = ("The latest record change occurred on 2026-11-10 and changed dates. No record "
             "change is held since the trial's first live version.")  # fmt: skip
    assert c.check([GOOD[0], GOOD[1], third], PAYLOAD, NAMES).reasons == ["template"]


DATES = "dates or status verification"
# NCT07153796's markers (memo 6 of the v1 review, memo 7 of the v2 review): 2025-09-04 is its
# first posting, which v1 and v2 called a status change.
MEMO_6 = {**PAYLOAD, "first_posted_on": "2025-09-04", "status_verified_month": "none",
          "record_changes": [
              {"post_date": "2026-08-18", "changed": ["intervention types or title phase"]},
              {"post_date": "2026-08-12", "changed": [DATES]},
              {"post_date": "2026-05-20", "changed": ["status", DATES, "enrollment"]},
              {"post_date": "2026-02-23", "changed": ["status", DATES]},
              {"post_date": "2025-12-16", "changed": [DATES]}],
          "record_changes_total": 5, "since_go_live": []}  # fmt: skip


def test_wrong_change_kinds_on_a_date_are_refused() -> None:
    """Memo 6 of the v1 review said 2025-09-04 changed status and dates (it was the first
    posting), and lost the enrollment change of 2026-05-20."""
    third = ("The latest record changes are: status, dates on 2025-09-04; dates on 2025-12-16; "
             "status, dates on 2026-02-23; status, dates on 2026-05-20; dates on 2026-08-12; "
             "interventions or phase in the title on 2026-08-18.")  # fmt: skip
    good = t.memo(MEMO_6, SENTENCES)
    assert c.check(good, MEMO_6, NAMES).passed
    assert c.check([good[0], good[1], third], MEMO_6, NAMES).reasons == ["template"]


MEMO_19 = {**PAYLOAD,
           "record_changes": [{"post_date": "2026-11-16", "changed": ["dates"]},
                              {"post_date": "2026-11-13", "changed": ["dates"]}],
           "record_changes_total": 2,
           "since_go_live": [
               {"post_date": "2026-11-16", "held": p.COMPARED, "values": [
                   {"field": "start_date", "label": "start date", "before": "2026-11-18",
                    "after": "2026-11-19"}]},
               {"post_date": "2026-11-13", "held": p.COMPARED, "values": [
                   {"field": "start_date", "label": "start date", "before": "2026-11-17",
                    "after": "2026-11-18"}]}]}  # fmt: skip


def test_a_garbled_before_and_after_is_refused() -> None:
    """Memo 19 of the v1 review wrote "start_date after 2026-11-19 before 2026-11-18 post_date
    2026-11-16"."""
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
    sentence = f"The {RATE.replace('-', chr(code))} is 44.6%, which raises the risk."
    result = c.check(with_sentences((1, sentence)), PAYLOAD, NAMES)
    assert result.passed, result.details
    assert result.paragraphs[1] == GOOD[1]
    assert c.normalize(f"early{chr(code)}stop") == "early-stop"


@pytest.mark.parametrize("code", SPACES, ids=hex)
def test_special_spaces_are_plain_spaces(code: int) -> None:
    sentence = (f"The enrollment{chr(code)}count is 1,704, the estimated{chr(code)}target, which "
                f"lowers{chr(code)}the risk.")  # fmt: skip
    result = c.check(with_sentences((0, sentence)), PAYLOAD, NAMES)
    assert result.passed, result.details
    assert result.paragraphs[1] == GOOD[1]


def test_the_checked_text_holds_no_special_character() -> None:
    weird = "".join(chr(x) for x in (*HYPHENS, *SPACES))
    assert c.normalize(weird) == "-" * len(HYPHENS) + " " * len(SPACES)


def test_feature_names_are_not_numbers() -> None:
    """The number rule skips a feature name; the words rule refuses it as an extra word."""
    sentence = f"The {TIME} (months_since_t0) is 36 months, which raises the risk."
    reasons = c.check(with_sentences((2, sentence)), PAYLOAD, NAMES).reasons
    assert reasons == ["driver_words"]


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
    elevated = f"The {TIME} is 36 months, an elevated value, which raises the risk."
    result = c.check(with_sentences((2, elevated), payload=payload), payload, NAMES)
    assert result.reasons == ["driver_words", "elevated_after_fall"]
    rose = f"The {TIME} is 36 months and rose, which raises the risk."
    assert "direction" in c.check(with_sentences((2, rose), payload=payload), payload,
                                  NAMES).reasons  # fmt: skip


def test_the_allowed_numbers_include_renderings_of_shares() -> None:
    numbers, dates = c.allowed(PAYLOAD)
    for token in ("0.446", "0.45", "0.4", "45", "44.6", "1704", "100", "5", "12", "24", "10"):
        assert c._decimal(token) in numbers, token
    assert dates == {"2026-11-16", "2026-11-10", "2026-11-08", "2024-03-04", "2027-06-30",
                     "2028-01-31"}  # fmt: skip
    assert dt.date.fromisoformat(max(dates)) == dt.date(2028, 1, 31)
