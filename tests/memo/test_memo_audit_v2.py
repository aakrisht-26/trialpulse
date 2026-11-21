"""The 40 confirmed findings of the memo_v2 audit (data/memo_review/2026-11-18-audit.md, ADR
0084 amended for memo_v3), each as a case memo_v3 must make impossible.

A finding about paragraph 1 or 3 is impossible when the v3 template cannot say it and the
template rule refuses the v2 wording; a finding about paragraph 2 is impossible when the v3
label and display cannot make the v2 sentence and the v3 check refuses that sentence while
passing the faithful one. Dates and counts are the audited memos'; registry ids are made up.
"""

import datetime as dt
from collections.abc import Callable, Sequence
from typing import Any

import pytest

from trialpulse import markers as mk
from trialpulse.features.registry import model_columns
from trialpulse.memo import check as c
from trialpulse.memo import labels as lab
from trialpulse.memo import payload as p
from trialpulse.memo import template as t


def compose(payload: Any, sentences: Any) -> list[str]:
    """The template's first and third paragraphs around a hand-written second paragraph (the
    check's adversarial cases; memo_v4 itself builds every paragraph, ADR 0085)."""
    second = " ".join(s.strip() for s in sentences or ()) if t.drivers_12(payload) else t.NO_DRIVERS
    return [t.paragraph_one(payload), second, t.paragraph_three(payload)]


NAMES = model_columns()
INDEX = {name: i for i, name in enumerate(NAMES)}
D = dt.date
GO_LIVE = D(2026, 11, 8)
SPONSORS = ["Acme Pharma Inc."]


def payload_of(
    markers: Sequence[tuple[dt.date, int]] = (),
    first_post: dt.date | None = None,
    versions: Sequence[dict[str, Any]] = (),
    drivers: Sequence[tuple[str, str, float]] = (("status", "ACTIVE_NOT_RECRUITING", 0.3),),
    chosen: tuple[str, ...] = (p.TOP,),
    move: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A v3 payload from rows: markers (date, code), the first-post date, held versions and
    the 12-month drivers (feature, stored value, contribution); the 24-month drivers repeat
    them."""
    feats = [INDEX[f] for f, _, _ in drivers] * 2
    score = {"nct_id": "NCT09999999", "as_of_date": D(2026, 11, 13), "risk_12m": 0.55,
             "risk_24m": 0.63, "driver_months": [12] * len(drivers) + [24] * len(drivers),
             "driver_features": feats, "driver_values": [v for _, v, _ in drivers] * 2,
             "driver_contributions": [w for _, _, w in drivers] * 2,
             # the sponsor rate's stored inputs (migration 0010, memo_v5)
             "sponsor_has_record": True, "sponsor_other_ended": 1,
             "sponsor_class_rate": 0.16522, "sponsor_rate_class": "OTHER"}  # fmt: skip
    rows = {"post_dates": [d for d, _ in markers], "kinds": [k for _, k in markers]}
    return p.build(score, {"study_first_post_date": first_post, **(state or {})}, None, rows,
                   versions, chosen, move, NAMES, 100, 10, GO_LIVE)  # fmt: skip


def faithful(payload: dict[str, Any]) -> list[str]:
    return [f"The {d['label']} is {d['display']}, which {d['direction']} the risk."
            for d in t.drivers_12(payload)]  # fmt: skip


def memo(payload: dict[str, Any], sentences: list[str] | None = None) -> list[str]:
    return compose(payload, faithful(payload) if sentences is None else sentences)


def passes(payload: dict[str, Any], paragraphs: list[str]) -> c.CheckResult:
    return c.check(paragraphs, payload, SPONSORS)


def refused_sentence(feature: str, value: str, old: str, kind: str | None = None) -> None:
    """The v2 sentence about a driver is refused by the v3 check; the faithful one passes."""
    payload = payload_of(drivers=[(feature, value, 0.3)],
                         state={"enrollment_type": kind} if kind else None)  # fmt: skip
    assert passes(payload, memo(payload)).passed
    result = passes(payload, memo(payload, [old]))
    assert not result.passed
    assert {"driver_label", "driver_value"} & set(result.reasons), result.details


def refused_third(payload: dict[str, Any], old: str) -> None:
    """The v2 wording of paragraph 3 is refused by the template rule (and a v2 count that
    included the first posting by the number rule too)."""
    good = memo(payload)
    assert passes(payload, good).passed
    result = passes(payload, [good[0], good[1], old])
    assert "template" in result.reasons
    assert set(result.reasons) <= {"template", "number"}


# Paragraph 3 (high): the first posting named a status change ------------------------------


def first_posting_is_not_a_change(first: dt.date, total: int) -> None:
    later = [(first + dt.timedelta(days=30 * k), 2) for k in range(1, total)]
    payload = payload_of(markers=[(first, 1), *later], first_post=first)
    third = t.paragraph_three(payload)
    assert f"{first}: status" not in third
    assert f"{first} (status)" not in third
    assert third.startswith(f"Since its first posting on {first}, changes to fields the model "
                            f"reads were posted on {total - 1} date")  # fmt: skip
    read = mk.read([first], [1], first)  # the API's timeline reads it the same way
    assert read[0].first_posted
    assert read[0].changed() == []
    refused_third(payload, f"Record changes, newest first: {first} (status).")


# Paragraph 3 (high): the count against the versions posted --------------------------------


def count_says_what_it_counts(versions: str, total: int) -> None:
    first = D(2024, 5, 20)
    later = [(first + dt.timedelta(days=40 * k), 2) for k in range(1, total)]
    payload = payload_of(markers=[(first, 1), *later], first_post=first,
                         drivers=[("n_versions", versions, 0.3)])  # fmt: skip
    text = memo(payload)
    assert f"The total number of versions posted is {versions}" in text[1]
    assert f"changes to fields the model reads were posted on {total - 1} dates" in text[2]
    assert t.NOT_COUNTED in text[2]
    assert "The record changed on" not in text[2]
    refused_third(payload, f"The record changed on {total} dates.")


# Paragraph 3 (high): a change since go-live without an earlier held version ---------------


def first_held_is_named(chosen: tuple[str, ...], move: dict[str, Any] | None) -> None:
    payload = payload_of(markers=[(D(2024, 3, 1), 1), (D(2026, 11, 12), 2)],
                         first_post=D(2024, 3, 1),
                         versions=[{"effective_date": D(2026, 11, 12), "status": "RECRUITING"}],
                         chosen=chosen, move=move)  # fmt: skip
    third = t.paragraph_three(payload)
    # memo_v4 (ADR 0085): no earlier value is held, with no claim about go-live as the cause
    assert (
        "For the change of 2026-11-12, no earlier version of this trial is held, so there "
        "is nothing to compare it with."
    ) in third  # memo_v6
    assert "no change in them is held" not in third
    assert "has been posted since then" not in third
    refused_third(payload, "Before and after values are held only from go-live (2026-11-08) "
                           "onward, and no change in them is held for this trial.")  # fmt: skip


MOVE_14 = {"previous_risk_percent_12": 47, "previous_score_date": D(2026, 11, 3),
           "direction": "rose", "change_points": 8}  # fmt: skip


# Paragraph 3 (medium): the dates group and the count ---------------------------------------


def dates_group_names_verification() -> None:
    held = {"status": "RECRUITING", "start_date": D(2027, 1, 1)}
    payload = payload_of(
        markers=[(D(2024, 3, 1), 1), (D(2026, 10, 13), 2), (D(2026, 11, 12), 2)],
        first_post=D(2024, 3, 1),
        versions=[{"effective_date": D(2026, 11, 9), **held,
                   "status_verified_date": D(2026, 10, 1)},
                  {"effective_date": D(2026, 11, 12), **held,
                   "status_verified_date": D(2026, 11, 1)}],
    )  # fmt: skip
    third = t.paragraph_three(payload)
    assert "2026-10-13: dates or status verification." in third
    # memo_v5: one name for the group; the moved value below says it was the verification
    assert "2026-11-12: dates or status verification." in third
    assert "(dates)" not in third
    assert "Status verified month moved from 2026-10 to 2026-11 on 2026-11-12." in third


def count_leaves_out_the_first_posting() -> None:
    first = D(2023, 6, 5)
    later = [(first + dt.timedelta(days=50 * k), 2) for k in range(1, 11)]
    payload = payload_of(markers=[(first, 1), *later], first_post=first)
    assert payload["record_changes_total"] == 10
    assert "posted on 10 dates, newest first. " in t.paragraph_three(payload)


# Paragraph 2 (medium and low): labels and displays ------------------------------------------


def smoothing_is_named(value: str, old: str) -> None:
    # memo_v5: the display says exactly how the rate was made, from its stored inputs
    inputs = lab.RateInputs(True, 1, "OTHER", 0.165)
    assert lab.feature_display("sponsor_stop_rate", value, rate=inputs).endswith(
        "smoothed toward 16.5%, the class rate for other sponsors (mostly universities and "
        "hospitals)"
    )
    refused_sentence("sponsor_stop_rate", value, old)


def enrollment_count_has_its_type(value: str, kind: str, shown: str, old: str) -> None:
    assert lab.feature_display("enrollment_count", value, kind) == shown
    refused_sentence("enrollment_count", value, old, kind)


def check_holds_the_words() -> None:
    payload = payload_of(drivers=[("completion_slip_months", "-33", 0.3)])
    good = faithful(payload)[0]
    assert "33 months earlier than in the first version" in good
    flipped = good.replace("earlier", "later")
    assert "driver_value" in passes(payload, memo(payload, [flipped])).reasons


def ended_and_stopped_differ(value: str, old: str) -> None:
    ended, stopped = (lab.feature_label("sponsor_prior_ended"),
                      lab.feature_label("sponsor_prior_stopped"))  # fmt: skip
    assert "by completion or early stop" in ended
    assert "stopped early" in stopped
    refused_sentence("sponsor_prior_ended", value, old)


def moved_date_reads_plainly(value: str, old: str) -> None:
    assert not any("slip" in e.label for e in lab.FEATURES.values())
    payload = payload_of(drivers=[("completion_slip_months", value, 0.3)])
    assert faithful(payload)[0].startswith(
        f"The study completion date is {abs(int(value))} months earlier than in the first version"
    )
    assert lab.feature_label("completion_slip_months") == "study completion date"
    assert old.split(" is ")[0].lower() not in {e.label for e in lab.FEATURES.values()}


def suspension_history_claims_no_earlier_one() -> None:
    entry = lab.FEATURES["ever_suspended"]
    assert "earlier suspension" not in entry.label
    assert entry.label == "past or current suspension"  # memo_v4: no "earlier" claim
    assert entry.yes == "recorded"
    refused_sentence(
        "ever_suspended",
        "yes",
        "Earlier suspension is suspended at some point, which raises the risk.",
    )


def registration_lag_counts_the_move_once() -> None:
    shown = lab.feature_display("registration_lag_months", "-44")
    assert shown == "44 months after the first posting, any move of the start included"
    refused_sentence("registration_lag_months", "-44",
                     "Registration lag is registered 44 months before the start, which raises "
                     "the risk.")  # fmt: skip


def checkpoint_is_plain() -> None:
    label = lab.feature_label("landmark_index")
    assert "landmark" not in label
    assert "step" not in label
    assert lab.feature_display("landmark_index", "4") == (  # memo_v4: never in months
        "stage 4 (stages run 0 to 6, a stage every 6 months after the first posting)"
    )
    refused_sentence(
        "landmark_index", "4", "Landmark step since registration is step 4, which raises the risk."
    )


def enrolled_share_says_target_once(value: str, old: str) -> None:
    shown = lab.feature_display("enrolled_to_first_target_ratio", value)
    assert "target" not in shown
    sentence = faithful(payload_of(drivers=[("enrolled_to_first_target_ratio", value, 0.3)]))[0]
    assert sentence.count("target") == 1
    refused_sentence("enrolled_to_first_target_ratio", value, old)


def count_labels_take_is(feature: str, value: str, old: str) -> None:
    assert lab.feature_label(feature).startswith(("number of", "total number of"))
    refused_sentence(feature, value, old)


def suspension_status_is_no_tautology() -> None:
    assert lab.feature_label("currently_suspended") == "state of the trial"  # memo_v5
    refused_sentence(
        "currently_suspended", "yes", "Current suspension is suspended now, which raises the risk."
    )


# Paragraph 1 (low) -----------------------------------------------------------------------


def no_unflagged_jargon() -> None:
    payload = payload_of()
    first, third = t.paragraph_one(payload), t.paragraph_three(payload)
    assert "unflagged" not in first
    assert "inside the validated range" in first
    assert "when TrialPulse's live system started" in third  # go-live is defined


def lead_in_names_the_horizon_and_no_cause() -> None:
    first = t.paragraph_one(payload_of())
    assert first.endswith(t.LEAD_IN)
    assert "12-month score" in t.LEAD_IN
    assert "not a reason why a trial stops" in t.LEAD_IN


def move_has_both_scores() -> None:
    payload = payload_of(chosen=(p.MOVED, p.TOP), move=MOVE_14)
    first = t.paragraph_one(payload)
    assert "Its 12-month risk rose from 47% in the score of 2026-11-03 to 55%." in first
    old = first.replace("rose from 47% in the score of 2026-11-03 to 55%",
                        "rose from 47% on 2026-11-03")  # fmt: skip
    assert passes(payload, [old, *memo(payload)[1:]]).reasons == ["template"]


# Paragraph 3 (low): kinds of one date ----------------------------------------------------


def kinds_of_a_date_stand_apart() -> None:
    payload = payload_of(markers=[(D(2023, 9, 1), 1), (D(2024, 10, 2), 2 | 4 | 32)],
                         first_post=D(2023, 9, 1))  # fmt: skip
    third = t.paragraph_three(payload)
    assert ("2024-10-02: dates or status verification; enrollment; interventions or phase named "
            "in the title.") in third  # fmt: skip
    assert " and interventions" not in third


def api_and_memo_share_the_words() -> None:
    payload = payload_of(markers=[(D(2023, 9, 1), 1), (D(2024, 10, 2), 32)],
                         first_post=D(2023, 9, 1))  # fmt: skip
    assert payload["record_changes"][0]["changed"] == mk.names(32)
    assert mk.names(32) == ["interventions or phase named in the title"]  # memo_v6


Case = tuple[str, Callable[..., None], tuple[Any, ...]]
CASES: list[Case] = [
    ("01_memo2_first_posting_as_status", first_posting_is_not_a_change, (D(2024, 10, 28), 9)),
    ("02_memo3_first_posting_as_status", first_posting_is_not_a_change, (D(2024, 8, 16), 6)),
    ("03_memo4_count_against_versions", count_says_what_it_counts, ("21", 12)),
    ("04_memo5_count_against_versions", count_says_what_it_counts, ("18", 13)),
    ("05_memo6_change_after_go_live_none_held", first_held_is_named, ((p.TOP,), None)),
    ("06_memo7_first_posting_as_status", first_posting_is_not_a_change, (D(2025, 9, 4), 6)),
    ("07_memo10_first_posting_as_status", first_posting_is_not_a_change, (D(2025, 2, 4), 7)),
    ("08_memo14_change_after_go_live_none_held", first_held_is_named,
     ((p.MOVED, p.TOP), MOVE_14)),
    ("09_memo15_first_posting_as_status", first_posting_is_not_a_change, (D(2024, 1, 3), 7)),
    ("10_memo16_first_posting_as_status", first_posting_is_not_a_change, (D(2024, 4, 17), 4)),
    ("11_memo17_first_posting_as_status", first_posting_is_not_a_change, (D(2024, 10, 17), 7)),
    ("12_memo19_first_posting_as_status", first_posting_is_not_a_change, (D(2024, 7, 26), 8)),
    ("13_memo20_first_posting_as_status", first_posting_is_not_a_change, (D(2023, 8, 14), 10)),
    ("14_memo1_dates_hide_reverification", dates_group_names_verification, ()),
    ("15_memo2_rate_hides_smoothing", smoothing_is_named,
     ("0.241", "Sponsor early-stop rate is 24.1%, which raises the risk.")),
    ("16_memo2_count_without_type", enrollment_count_has_its_type,
     ("2", "ACTUAL", "2, the actual number enrolled",
      "Enrollment count is 2, which raises the risk.")),
    ("17_memo2_check_ignores_words", check_holds_the_words, ()),
    ("18_memo9_ended_reads_as_stopped", ended_and_stopped_differ,
     ("1065", "Ended trials by the sponsor is 1,065, which raises the risk.")),
    ("19_memo11_slip_earlier", moved_date_reads_plainly,
     ("-60", "Study completion date slip is 60 months earlier than in the first version.")),
    ("20_memo11_count_holds_registration", count_leaves_out_the_first_posting, ()),
    ("21_memo15_earlier_suspension", suspension_history_claims_no_earlier_one, ()),
    ("22_memo15_count_without_type", enrollment_count_has_its_type,
     ("10", "ESTIMATED", "10, the estimated target",
      "Enrollment count is 10, which raises the risk.")),
    ("23_memo16_lag_counts_the_slip_twice", registration_lag_counts_the_move_once, ()),
    ("24_memo17_landmark_step", checkpoint_is_plain, ()),
    ("25_memo19_ended_beside_stopped", ended_and_stopped_differ,
     ("3075", "Ended trials by the sponsor is 3,075, which raises the risk.")),
    ("26_memo1_unflagged_and_go_live_jargon", no_unflagged_jargon, ()),
    ("27_memo1_causal_reading", lead_in_names_the_horizon_and_no_cause, ()),
    ("28_memo1_first_target_twice", enrolled_share_says_target_once,
     ("0.194", "Number enrolled against the first target is 19.4% of the first target, which "
      "raises the risk.")),
    ("29_memo1_plural_label_with_is", count_labels_take_is,
     ("sponsor_prior_stopped", "573",
      "Stopped trials by the sponsor is 573, which raises the risk.")),
    ("30_memo3_smoothed_rate_looks_like_a_slip", smoothing_is_named,
     ("0.244", "Sponsor early-stop rate is 24.4%, which raises the risk.")),
    ("31_memo7_interventions_or_phase_in_a_list", kinds_of_a_date_stand_apart, ()),
    ("32_memo8_horizon_not_named", lead_in_names_the_horizon_and_no_cause, ()),
    ("33_memo8_slip_earlier", moved_date_reads_plainly,
     ("-43", "Study completion date slip is 43 months earlier than in the first version.")),
    ("34_memo10_suspension_tautology", suspension_status_is_no_tautology, ()),
    ("35_memo11_unflagged_jargon", no_unflagged_jargon, ()),
    ("36_memo11_first_target_twice", enrolled_share_says_target_once,
     ("0.049", "Number enrolled against the first target is 4.9% of the first target, which "
      "raises the risk.")),
    ("37_memo11_plural_label_with_is", count_labels_take_is,
     ("sponsor_prior_registrations", "4", "Earlier registrations by the sponsor is 4, which raises "
      "the risk.")),
    ("38_memo12_suspension_tautology", suspension_status_is_no_tautology, ()),
    ("39_memo14_move_date_ambiguous", move_has_both_scores, ()),
    ("40_memo14_kinds_ambiguous_and_words_differ", api_and_memo_share_the_words, ()),
]  # fmt: skip


def test_every_confirmed_finding_has_a_case() -> None:
    assert len(CASES) == 40
    assert [int(name[:2]) for name, _, _ in CASES] == list(range(1, 41))


@pytest.mark.parametrize(("name", "case", "args"), CASES, ids=[n for n, _, _ in CASES])
def test_an_audit_finding_is_impossible_in_memo_v3(
    name: str, case: Callable[..., None], args: tuple[Any, ...]
) -> None:
    case(*args)
