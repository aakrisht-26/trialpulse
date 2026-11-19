"""The three paragraphs of a memo, all built by the template (ADR 0084; memo_v4, ADR 0085)."""

import datetime as dt
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from trialpulse.memo import check as c
from trialpulse.memo import payload as p
from trialpulse.memo import template as t


def compose(payload: Any, sentences: Any) -> list[str]:
    """The template's first and third paragraphs around a hand-written second paragraph (the
    check's adversarial cases; memo_v4 itself builds every paragraph, ADR 0085)."""
    second = " ".join(s.strip() for s in sentences or ()) if t.drivers_12(payload) else t.NO_DRIVERS
    return [t.paragraph_one(payload), second, t.paragraph_three(payload)]


def _load(name: str) -> ModuleType:
    """A sibling test module (the tests run with --import-mode=importlib, no packages)."""
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cases = _load("test_memo_check")
rows = _load("test_memo_payload")
MEMO_6, MEMO_19, PAYLOAD, SENTENCES, moved = (cases.MEMO_6, cases.MEMO_19, cases.PAYLOAD,
                                              cases.SENTENCES, cases.moved)  # fmt: skip
D = dt.date
LEAD_IN = ("The sentences below name the features that contributed most to the model's "
           "12-month score; a driver is not a reason why a trial stops.")  # fmt: skip
MOVE_NOTE = "They explain the current level of the 12-month score, not its change."
HELD = ("Before and after values are held only for changes posted since 2026-11-08, when "
        "TrialPulse's live system started")  # fmt: skip


def test_the_top_100_paragraph_says_elevated_with_the_risks_and_the_date() -> None:
    assert t.paragraph_one(PAYLOAD) == (
        "NCT01234567 has elevated early-stop risk: 14% within 12 months and 26% within 24 "
        "months, scored on 2026-11-16. It is among the 100 trials with the highest 12-month "
        f"risk, counting only scores inside the validated range. {LEAD_IN}")  # fmt: skip


def test_a_movers_paragraph_states_the_direction_and_both_scores() -> None:
    assert t.paragraph_one(moved("fell")) == (
        "The 12-month early-stop risk of NCT01234567 fell from 19% in the score of 2026-11-09 "
        "to 14% in the score of 2026-11-16; its 24-month risk is 26%. It was chosen because "
        f"its 12-month risk moved by 5 percentage points. {LEAD_IN} {MOVE_NOTE}")  # fmt: skip
    both = t.paragraph_one(moved("rose", top=True))
    assert both.startswith("NCT01234567 has elevated early-stop risk")
    assert both.endswith(f"Its 12-month risk rose from 9% in the score of 2026-11-09 to 14%. "
                         f"{LEAD_IN} {MOVE_NOTE}")  # fmt: skip
    assert "elevated" not in t.paragraph_one(moved("rose"))  # elevated for the top 100 only


def test_the_lead_in_needs_drivers() -> None:
    bare = {**PAYLOAD, "drivers": {"12": [], "24": []}}
    assert LEAD_IN not in t.paragraph_one(bare)
    assert MOVE_NOTE not in t.paragraph_one({**moved("fell"), "drivers": {"12": [], "24": []}})


def test_only_a_trial_chosen_for_a_move_says_the_drivers_explain_the_level() -> None:
    """The memo_v3 audit (memo 14): after "rose from 47% ... to 55%", the lead-in read as the
    explanation of the move; the drivers explain the score's level (memo_v4)."""
    assert MOVE_NOTE not in t.paragraph_one(PAYLOAD)
    for payload in (moved("rose"), moved("fell"), moved("rose", top=True)):
        first = t.paragraph_one(payload)
        assert first.index(MOVE_NOTE) > first.index(LEAD_IN)
        assert c.check(t.memo(payload), payload, ["Acme Pharma Inc."]).passed


def test_the_first_posting_stands_apart_from_the_changes() -> None:
    """NCT07153796 (memo 6 of the v1 review): its first posting on 2025-09-04 was called a
    status change; each date's kinds now stand alone."""
    assert t.paragraph_three(MEMO_6) == (
        "Since its first posting on 2025-09-04, changes to fields the model reads were posted "
        "on 5 dates, newest first: 2026-08-18: intervention types or title phase. 2026-08-12: "
        "dates or status verification. 2026-05-20: status; dates or status verification; "
        "enrollment. 2026-02-23: status; dates or status verification. 2025-12-16: dates or "
        "status verification. Versions that changed only other fields are not counted. "
        f"{HELD}; no change to fields the model reads has been posted since then.")  # fmt: skip


def test_more_changes_than_listed_are_counted_and_named_the_latest() -> None:
    payload = {**MEMO_6, "record_changes_total": 14}
    assert "posted on 14 dates; the latest, newest first: 2026-08-18:" in t.paragraph_three(
        payload)  # fmt: skip
    assert c.check(compose(payload, SENTENCES), payload).passed


def test_one_change_and_none() -> None:
    one = {**MEMO_6, "record_changes": MEMO_6["record_changes"][:1], "record_changes_total": 1}
    assert t.paragraph_three(one).startswith(
        "Since its first posting on 2025-09-04, changes to fields the model reads were posted "
        "on 1 date: 2026-08-18: intervention types or title phase.")  # fmt: skip
    none = {**MEMO_6, "record_changes": [], "record_changes_total": 0}
    assert t.paragraph_three(none).startswith(
        "Since its first posting on 2025-09-04, no change to fields the model reads has been "
        "posted. Versions that changed only other fields are not counted.")  # fmt: skip
    unknown = {**none, "first_posted_on": "none"}
    assert t.paragraph_three(unknown).startswith(
        "No change to fields the model reads has been posted."
    )
    absent = {**unknown, "stored_markers": False}
    assert t.paragraph_three(absent).startswith("No record changes are stored for this trial. ")
    assert "not counted" not in t.paragraph_three(absent)


def test_a_before_and_after_value_reads_as_a_move() -> None:
    """Memo 19 of the v1 review garbled them."""
    assert t.paragraph_three(MEMO_19).endswith(
        f"{HELD}. Start date moved from 2026-11-18 to 2026-11-19 on 2026-11-16. Start date "
        "moved from 2026-11-17 to 2026-11-18 on 2026-11-13.")  # fmt: skip


def test_the_status_verified_month_has_its_own_sentence() -> None:
    assert "The status was last verified in 2026-10." in t.paragraph_three(PAYLOAD)
    assert "last verified" not in t.paragraph_three({**PAYLOAD, "status_verified_month": "none"})


def test_changes_since_go_live_say_what_could_be_compared() -> None:
    """Memos 6 and 14 of the v2 review listed a change of 2026-11-12, then said that no change
    was held; memo 6 of the v3 audit read "first version held since go-live" right after
    "values are held from go-live". memo_v4 says that no earlier value is held for the trial,
    without naming go-live as the cause, and never writes a double colon."""
    markers = {"post_dates": [D(2026, 11, 12), D(2026, 11, 14), D(2026, 11, 15), D(2025, 3, 1)],
               "kinds": [2, 16, 1, 1]}  # fmt: skip
    versions = [rows.held(D(2026, 11, 12)), rows.held(D(2026, 11, 14), sponsor_id="z")]
    payload = rows.build(markers=markers, versions=versions)
    third = t.paragraph_three(payload)
    assert third.endswith(
        f"{HELD}. For the change of 2026-11-15, its version is no longer held. The lead sponsor "
        "changed on 2026-11-14. For the change of 2026-11-12, no earlier value is held for this "
        "trial.")  # fmt: skip
    assert "no change" not in third.lower()
    assert "go-live" not in third
    assert ":" not in third.split(HELD, 1)[1]
    assert c.check(t.memo(payload), payload, ["Acme Pharma Inc."]).passed


def test_after_retention_the_oldest_held_version_is_not_called_the_first() -> None:
    markers = {"post_dates": [D(2027, 6, 1)], "kinds": [4]}
    payload = rows.build(markers=markers, versions=[rows.held(D(2027, 6, 1))],
                         versions_kept_from=D(2026, 12, 1))  # fmt: skip
    third = t.paragraph_three(payload)
    assert third.endswith("For the change of 2027-06-01, no earlier value is held for this "
                          "trial.")  # fmt: skip
    assert "first version held" not in third


def test_the_second_paragraph_is_one_sentence_per_12_month_driver_in_order() -> None:
    """memo_v4: the template writes what memo_v3's model was held to, exactly."""
    assert t.paragraph_two(PAYLOAD) == " ".join(SENTENCES)
    assert t.memo(PAYLOAD) == [t.paragraph_one(PAYLOAD), " ".join(SENTENCES),
                               t.paragraph_three(PAYLOAD)]  # fmt: skip
    assert t.paragraph_two({**PAYLOAD, "drivers": {"12": [], "24": []}}) == t.NO_DRIVERS
    assert t.driver_sentence(
        {
            "label": "start date",
            "display": "3 months later than in the first version",
            "direction": "raises",
        }
    ) == ("The start date is 3 months later than in the first version, which raises the risk.")


def test_a_built_payload_makes_a_memo_that_passes() -> None:
    payload = rows.build()
    result = c.check(t.memo(payload), payload, ["Acme Pharma Inc."])
    assert result.passed, result.details
    assert p.PAYLOAD_VERSION == 4
