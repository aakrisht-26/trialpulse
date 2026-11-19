"""The three paragraphs of a risk memo (ADR 0084; memo_v4, ADR 0085): built from the payload
alone, with no model.

Paragraph 1 states the risks, the score date and why the trial was chosen, and says that the
sentences after it name the features that contributed most to the model's 12-month score,
which are not reasons why a trial stops; for a trial chosen because its risk moved, it adds
that they explain the current level of the score, not its change. Paragraph 2 has one
sentence per 12-month driver, in the payload's order: "The <label> is <value>, which raises
the risk." (memo_v3's model was held to exactly this form, so memo_v4 writes it directly).
Paragraph 3 states the record changes: the trial's first posting apart from its changes, the
count of dates with changes to the fields the model reads (so it never contradicts a count of
versions), the latest changes newest first with their exact kinds, when the status was last
verified, and, for each change since go-live, the values before and after, or that no earlier
value is held for the trial. Every paragraph is a pure function of the payload, and every
memo still goes through the fabrication check (`memo.check`), whose rules are written apart
from this module, as a test of the template.

"elevated early-stop risk" appears only for a trial in the top 100. A trial chosen for a move
states its risk, the direction ("rose" or "fell"), and its previous risk and score date.
"""

from collections.abc import Mapping
from typing import Any

from trialpulse.memo.payload import COMPARED, EARLIER_NOT_HELD, FIRST_HELD, MOVED, TOP

NO_DRIVERS = "No driver is stored for this score."
LEAD_IN = ("The sentences below name the features that contributed most to the model's "
           "12-month score; a driver is not a reason why a trial stops.")  # fmt: skip
MOVE_NOTE = "They explain the current level of the 12-month score, not its change."
NOT_COUNTED = "Versions that changed only other fields are not counted."


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def drivers_12(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list((payload.get("drivers") or {}).get("12") or [])


def paragraph_one(payload: Mapping[str, Any]) -> str:
    """The risks, the score date, why the trial was chosen, and what paragraph 2 is."""
    nct = payload["nct_id"]
    r12, r24 = payload["risk_percent"]["12"], payload["risk_percent"]["24"]
    day = payload["score_date"]
    chosen = set(payload.get("chosen_as") or ())
    move = payload.get("move") if MOVED in chosen else None
    if TOP in chosen:
        text = (f"{nct} has elevated early-stop risk: {r12}% within 12 months and {r24}% "
                f"within 24 months, scored on {day}. It is among the {payload['top_n']} trials "
                "with the highest 12-month risk, counting only scores inside the validated "
                "range.")  # fmt: skip
        if move:
            text += (f" Its 12-month risk {move['direction']} from "
                     f"{move['previous_risk_percent_12']}% in the score of "
                     f"{move['previous_score_date']} to {r12}%.")  # fmt: skip
    elif move:
        text = (f"The 12-month early-stop risk of {nct} {move['direction']} from "
                f"{move['previous_risk_percent_12']}% in the score of "
                f"{move['previous_score_date']} to {r12}% in the score of {day}; its 24-month "
                f"risk is {r24}%. It was chosen because its 12-month risk moved by "
                f"{move['change_points']} percentage points.")  # fmt: skip
    else:
        text = (f"{nct} has a 12-month early-stop risk of {r12}% and a 24-month risk of "
                f"{r24}%, scored on {day}.")  # fmt: skip
    if not drivers_12(payload):
        return text
    return f"{text} {LEAD_IN} {MOVE_NOTE}" if move else f"{text} {LEAD_IN}"


def driver_sentence(driver: Mapping[str, Any]) -> str:
    """One driver as a sentence: "The <label> is <value>, which <raises|lowers> the risk."."""
    return f"The {driver['label']} is {driver['display']}, which {driver['direction']} the risk."


def paragraph_two(payload: Mapping[str, Any]) -> str:
    """One sentence per 12-month driver, in the payload's order, or the fixed sentence of a
    payload without one."""
    found = drivers_12(payload)
    return " ".join(driver_sentence(d) for d in found) if found else NO_DRIVERS


def _changes(payload: Mapping[str, Any]) -> list[str]:
    """The first posting, the count of change dates and the latest changes, one sentence per
    date with its kinds apart ("2026-05-20: status; dates or status verification.")."""
    if not payload.get("stored_markers", True):
        return ["No record changes are stored for this trial."]
    changes = payload.get("record_changes") or []
    total = int(payload.get("record_changes_total") or 0)
    first = payload.get("first_posted_on") or "none"
    lead = f"Since its first posting on {first}, " if first != "none" else ""
    if total == 0:
        return [_capital(f"{lead}no change to fields the model reads has been posted.")]
    dates = "1 date" if total == 1 else f"{total} dates"
    which = ":" if total == len(changes) == 1 else (
        "; the latest, newest first:" if total > len(changes) else ", newest first:")  # fmt: skip
    head = _capital(f"{lead}changes to fields the model reads were posted on {dates}{which}")
    return [head, *(f"{c['post_date']}: {'; '.join(c['changed'])}." for c in changes)]


def _since_go_live(payload: Mapping[str, Any]) -> list[str]:
    """The values before and after of each change since go-live, or why there are none."""
    held = payload["values_held_from"]
    entries = payload.get("since_go_live") or []
    opening = (f"Before and after values are held only for changes posted since {held}, when "
               "TrialPulse's live system started")  # fmt: skip
    if not entries:
        return [f"{opening}; no change to fields the model reads has been posted since then."]
    parts = [f"{opening}."]
    for entry in entries:
        day = entry["post_date"]
        if entry["held"] == COMPARED:
            values = entry.get("values") or []
            parts += [
                f"The {v['label']} changed on {day}." if "before" not in v
                else f"{_capital(v['label'])} moved from {v['before']} to {v['after']} on {day}."
                for v in values
            ] or [f"For the change of {day}, no value shown here changed."]  # fmt: skip
        elif entry["held"] in (FIRST_HELD, EARLIER_NOT_HELD):
            parts.append(f"For the change of {day}, no earlier value is held for this trial.")
        else:
            parts.append(f"For the change of {day}, its version is no longer held.")
    return parts


def paragraph_three(payload: Mapping[str, Any]) -> str:
    """The record changes (see the module text)."""
    parts = _changes(payload)
    if payload.get("stored_markers", True):
        parts.append(NOT_COUNTED)
    month = payload.get("status_verified_month") or "none"
    if month != "none":
        parts.append(f"The status was last verified in {month}.")
    parts += _since_go_live(payload)
    return " ".join(parts)


def memo(payload: Mapping[str, Any]) -> list[str]:
    """The three paragraphs of the memo."""
    return [paragraph_one(payload), paragraph_two(payload), paragraph_three(payload)]
