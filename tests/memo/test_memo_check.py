"""The memo's fabrication check (ADR 0084): faithful memos pass, and deliberately fabricated
memos are refused, each for its own reason."""

import datetime as dt
from typing import Any

import pytest

from trialpulse.memo import check as c
from trialpulse.memo import payload as p

PAYLOAD: dict[str, Any] = {
    "payload_version": 1, "nct_id": "NCT01234567", "score_date": "2026-11-16",
    "horizons_months": [12, 24], "risk_percent": {"12": 14, "24": 26}, "chosen_as": [p.TOP],
    "status": "RECRUITING", "phase_in_title": "PHASE2", "sponsor_class": "INDUSTRY",
    "drivers_per_horizon": 5, "top_n": 100,
    "drivers": {"12": [{"feature": "enrollment_count", "value": "1704", "direction": "lowers"},
                       {"feature": "sponsor_stop_rate", "value": "0.446", "direction": "raises"},
                       {"feature": "months_since_t0", "value": "36", "direction": "raises"}],
                "24": [{"feature": "enrollment_count", "value": "1704", "direction": "lowers"}]},
    "record_changes": [{"post_date": "2026-11-10", "changed": ["dates", "enrollment"]}],
    "field_changes": [{"post_date": "2026-11-10", "field": "enrollment_count",
                       "before": "120", "after": "80"}],
}  # fmt: skip
NAMES = ("Acme Pharma Inc.", "Northwind University")

GOOD = [
    "NCT01234567 has elevated early-stop risk: 14% within 12 months and 26% within 24 months, "
    "scored on 2026-11-16. It is among the 100 unflagged trials with the highest 12-month risk.",
    "The 5 drivers at 12 months: `enrollment_count` (1704) lowers the risk, "
    "`sponsor_stop_rate` (0.446, about 45%) raises it, and `months_since_t0` (36) raises it. "
    "A driver describes what moved the model's score, not why a trial stops.",
    "On 2026-11-10 the record changed its dates and enrollment: `enrollment_count` went from "
    "120 to 80.",
]


def memo(first: str | None = None, second: str | None = None,
         third: str | None = None) -> list[str]:  # fmt: skip
    return [first or GOOD[0], second or GOOD[1], third or GOOD[2]]


def test_a_faithful_memo_passes() -> None:
    result = c.check(GOOD, PAYLOAD, NAMES)
    assert result.passed, result.details


@pytest.mark.parametrize(
    ("third", "reason"),
    [
        ("On 2026-11-10 enrollment went from 120 to 75.", "number"),  # an invented number
        ("On 2026-11-10 the target fell by 40 participants.", "number"),  # a computed one
        ("On 2026-11-12 enrollment went from 120 to 80.", "date"),  # an invented date
        ("On November 10, 2026 enrollment went from 120 to 80.", "date_format"),
        ("On 10/11/2026 enrollment went from 120 to 80.", "date_format"),
        ("In Q4 2026 enrollment went from 120 to 80.", "date_format"),
        ("Like NCT07654321, its enrollment went from 120 to 80 on 2026-11-10.", "registry_id"),
        ("Acme Pharma Inc. cut enrollment from 120 to 80 on 2026-11-10.", "name"),
        ("Dr. Smith cut enrollment from 120 to 80 on 2026-11-10.", "name"),
        ("The drug is unlikely to work, so enrollment went from 120 to 80.", "banned_language"),
        ("The trial will fail: enrollment went from 120 to 80 on 2026-11-10.", "banned_language"),
        ("Patients should avoid it: enrollment went from 120 to 80.", "banned_language"),
        ("Its safety record is poor; enrollment went from 120 to 80.", "banned_language"),
        ("It will certainly stop; enrollment went from 120 to 80.", "banned_language"),
        ("We recommend a review: enrollment went from 120 to 80.", "banned_language"),
    ],
)
def test_a_fabricated_fact_or_claim_is_refused(third: str, reason: str) -> None:
    result = c.check(memo(third=third), PAYLOAD, NAMES)
    assert not result.passed
    assert reason in result.reasons, result.details


def test_a_risk_off_by_one_point_is_refused() -> None:
    first = GOOD[0].replace("14%", "15%")
    result = c.check(memo(first=first), PAYLOAD, NAMES)
    assert result.reasons == ["number"]


@pytest.mark.parametrize(
    "third",
    [
        "Over twelve months enrollment went from 120 to 80 on 2026-11-10.",
        "The record changed three versions ago: enrollment went from 120 to 80.",
        "A dozen changes; enrollment went from 120 to 80 on 2026-11-10.",
        "Enrollment fell by half, from 120 to 80, on 2026-11-10.",
        "One change on 2026-11-10: enrollment went from 120 to 80.",
        "A couple of changes on 2026-11-10: enrollment went from 120 to 80.",
    ],
)
def test_a_number_in_words_is_refused(third: str) -> None:
    """Change A: every number must be in digits, so the number rule sees it."""
    result = c.check(memo(third=third), PAYLOAD, NAMES)
    assert "number_word" in result.reasons, result.details


@pytest.mark.parametrize(
    "second",
    [
        "Its risk doubled since registration; `enrollment_count` (1704) lowers it.",
        "Its risk tripled; `enrollment_count` (1704) lowers it.",
        "The risk halved; `enrollment_count` (1704) lowers it.",
        "Its risk is twice the average; `enrollment_count` (1704) lowers it.",
        "Its risk is 3 times the average; `enrollment_count` (1704) lowers it.",
        "Its risk is x times the average; `enrollment_count` (1704) lowers it.",
        "A twofold rise; `enrollment_count` (1704) lowers it.",
        "A 2-fold rise; `enrollment_count` (1704) lowers it.",
        "A 2x rise; `enrollment_count` (1704) lowers it.",
    ],
)
def test_an_invented_comparison_is_refused(second: str) -> None:
    """Change A: no multiplier or ratio words."""
    result = c.check(memo(second=second), PAYLOAD, NAMES)
    assert "comparison" in result.reasons, result.details


def test_the_structure_is_three_short_paragraphs() -> None:
    assert c.check(GOOD[:2], PAYLOAD).reasons == ["structure"]
    assert "structure" in c.check([*GOOD[:2], "x" * 701], PAYLOAD).reasons
    assert "structure" in c.check([GOOD[0], "", GOOD[2]], PAYLOAD).reasons


def test_dashes_are_replaced_before_the_check() -> None:
    en, em = chr(0x2013), chr(0x2014)  # an en dash and an em dash
    dashed = GOOD[2].replace("went from 120 to 80", f"went from 120{en}80") + f" Done {em} no more."
    result = c.check(memo(third=dashed), PAYLOAD, NAMES)
    assert result.passed, result.details
    assert "120 to 80" in result.paragraphs[2]
    assert em not in result.paragraphs[2]
    assert "Done, no more." in result.paragraphs[2]


def test_feature_names_and_horizon_words_are_not_numbers() -> None:
    second = "The 12-month and 24-month drivers include `months_since_t0` (36), which raises it."
    assert c.check(memo(second=second), PAYLOAD, NAMES).passed


# Change B: the required wording depends on why the trial was chosen --------------------


def moved(direction: str, top: bool = False) -> dict[str, Any]:
    chosen = sorted({p.MOVED, *([p.TOP] if top else [])})
    return {**PAYLOAD, "chosen_as": chosen,
            "move": {"previous_risk_percent_12": 9 if direction == "rose" else 19,
                     "previous_score_date": "2026-11-09", "direction": direction,
                     "change_points": 5}}  # fmt: skip


ROSE = ("NCT01234567's 12-month risk rose to 14% on 2026-11-16, from 9% on 2026-11-09; at 24 "
        "months it is 26%.")  # fmt: skip
FELL = ("NCT01234567's 12-month risk fell to 14% on 2026-11-16, from 19% on 2026-11-09; at 24 "
        "months it is 26%.")  # fmt: skip


def test_elevated_is_required_in_the_top_100_only() -> None:
    first = GOOD[0].replace("has elevated early-stop risk", "has a risk of")
    assert c.check(memo(first=first), PAYLOAD, NAMES).reasons == ["required_phrase"]
    assert c.check(memo(first=ROSE), moved("rose"), NAMES).passed  # a mover needs no phrase


def test_a_mover_states_its_risk_and_its_direction() -> None:
    assert c.check(memo(first=FELL), moved("fell"), NAMES).passed
    no_risk = "NCT01234567's 12-month risk rose on 2026-11-16, from 9% on 2026-11-09."
    assert "risk_stated" in c.check(memo(first=no_risk), moved("rose"), NAMES).reasons
    no_direction = ROSE.replace("rose to", "is")
    assert "direction" in c.check(memo(first=no_direction), moved("rose"), NAMES).reasons
    both = FELL + " It increased its target before that."
    assert "direction" in c.check(memo(first=both), moved("fell"), NAMES).reasons


def test_a_fallen_risk_is_never_called_elevated_unless_it_is_in_the_top_100() -> None:
    elevated = FELL + " It still has elevated early-stop risk."
    result = c.check(memo(first=elevated), moved("fell"), NAMES)
    assert result.reasons == ["elevated_after_fall"]
    in_top = c.check(memo(first=elevated), moved("fell", top=True), NAMES)
    assert in_top.passed, in_top.details  # in the top 100 the phrase is required, and true
    assert "required_phrase" in c.check(memo(first=FELL), moved("fell", top=True), NAMES).reasons


def test_the_allowed_numbers_include_renderings_of_shares() -> None:
    numbers, dates = c.allowed(PAYLOAD)
    for token in ("0.446", "0.45", "0.4", "45", "44.6", "1704", "100", "5", "12", "24"):
        assert c._decimal(token) in numbers, token
    assert c._decimal("2026") not in numbers  # a date's year is no number of its own
    assert dates == {"2026-11-16", "2026-11-10"}
    assert dt.date.fromisoformat(max(dates)) == dt.date(2026, 11, 16)
