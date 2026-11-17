"""The fixed paragraphs of a memo (ADR 0084, memo_v2) and what the model is sent."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from trialpulse.memo import check as c
from trialpulse.memo import payload as p
from trialpulse.memo import template as t


def _load(name: str) -> ModuleType:
    """A sibling test module (the tests run with --import-mode=importlib, no packages)."""
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cases = _load("test_memo_check")
MEMO_6, MEMO_19, PAYLOAD, SENTENCES, moved = (cases.MEMO_6, cases.MEMO_19, cases.PAYLOAD,
                                              cases.SENTENCES, cases.moved)  # fmt: skip


def test_the_top_100_paragraph_says_elevated_with_the_risks_and_the_date() -> None:
    assert t.paragraph_one(PAYLOAD) == (
        "NCT01234567 has elevated early-stop risk: 14% within 12 months and 26% within 24 "
        "months, scored on 2026-11-16. It is among the 100 unflagged trials with the highest "
        "12-month risk.")  # fmt: skip


def test_a_movers_paragraph_states_the_direction_and_the_previous_risk_and_date() -> None:
    assert t.paragraph_one(moved("fell")) == (
        "The 12-month early-stop risk of NCT01234567 fell from 19% on 2026-11-09 to 14%, "
        "scored on 2026-11-16; its 24-month risk is 26%. It was chosen because its 12-month "
        "risk moved by 5 percentage points.")  # fmt: skip
    both = t.paragraph_one(moved("rose", top=True))
    assert both.startswith("NCT01234567 has elevated early-stop risk")
    assert both.endswith("Its 12-month risk rose from 9% on 2026-11-09.")
    assert "elevated" not in t.paragraph_one(moved("rose"))  # elevated for the top 100 only


def test_record_changes_carry_exactly_their_kinds_newest_first() -> None:
    """Memo 6 of the review attached the wrong kinds to dates."""
    third = t.paragraph_three(MEMO_6)
    assert third.startswith(
        "Record changes, newest first: 2026-08-18 (interventions or phase in the title); "
        "2026-08-12 (dates); 2026-05-20 (status, dates and enrollment); 2026-02-23 (status and "
        "dates); 2025-12-16 (dates); 2025-09-04 (status).")  # fmt: skip
    assert third.endswith("Before and after values are held only from go-live (2026-11-08) "
                          "onward, and no change in them is held for this trial.")  # fmt: skip


def test_no_record_change_is_said_only_when_the_payload_holds_none() -> None:
    """Memos 1, 16 and 18 said so while their payloads listed several."""
    assert "No record change" not in t.paragraph_three(MEMO_6)
    bare = {**PAYLOAD, "record_changes": [], "record_changes_total": 0, "field_changes": []}
    assert t.paragraph_three(bare).startswith("No record change is held for this trial.")


def test_a_before_and_after_value_reads_as_a_move() -> None:
    """Memo 19 of the review garbled them."""
    assert t.paragraph_three(MEMO_19).endswith(
        "Before and after values, held from go-live (2026-11-08) onward: Start date moved from "
        "2026-11-18 to 2026-11-19 on 2026-11-16. Start date moved from 2026-11-17 to 2026-11-18 "
        "on 2026-11-13.")  # fmt: skip


def test_more_changes_than_listed_are_counted() -> None:
    payload = {**MEMO_6, "record_changes_total": 14}
    assert t.paragraph_three(payload).startswith(
        "The record changed on 14 dates; the latest, newest first: 2026-08-18"
    )
    assert c.check(t.memo(payload, SENTENCES), payload).passed


def test_the_model_is_sent_the_12_month_drivers_only() -> None:
    request = t.model_request(PAYLOAD)
    assert request == {"drivers": [
        {"label": "enrollment count", "value": "1,704", "effect": "lowers the risk"},
        {"label": "sponsor early-stop rate", "value": "44.6%", "effect": "raises the risk"},
        {"label": "time since registration", "value": "36 months", "effect": "raises the risk"},
    ]}  # fmt: skip
    text = json.dumps(request)
    for value in ("NCT", "2026", "14", "record", "feature", "INDUSTRY", "RECRUITING"):
        assert value not in text, value


def test_a_built_payload_makes_a_memo_that_passes() -> None:
    payload = _load("test_memo_payload").build()
    sentences = [f"The {d['label']} is {d['display']}, which {d['direction']} the risk."
                 for d in payload["drivers"]["12"]]  # fmt: skip
    result = c.check(t.memo(payload, sentences), payload, ["Acme Pharma Inc."])
    assert result.passed, result.details
    assert p.PAYLOAD_VERSION == 2
