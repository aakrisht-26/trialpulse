"""The fixed paragraphs of a risk memo (ADR 0084, memo_v2): built from the payload alone.

Paragraph 1 states the risks, the score date and why the trial was chosen; paragraph 3 the
record changes and the before and after values held. The model writes only paragraph 2 (one
sentence per 12-month driver). Both fixed paragraphs are pure functions of the payload, and
the fabrication check requires a memo's paragraphs 1 and 3 to equal them, so a memo can never
say that no record change is held while its payload lists one, attach the wrong kinds of
change to a date, or garble a before and after value.

"elevated early-stop risk" appears only for a trial in the top 100. A trial chosen for a move
states its risk, the direction ("rose" or "fell"), and its previous risk and date.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from trialpulse.memo.payload import MOVED, TOP

NO_DRIVERS = "No driver is stored for this score."


def _join(words: Sequence[str]) -> str:
    """Words as a list in prose: "a", "a and b", "a, b and c"."""
    items = [str(w) for w in words]
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def paragraph_one(payload: Mapping[str, Any]) -> str:
    """The risks, the score date and why the trial was chosen."""
    nct = payload["nct_id"]
    r12, r24 = payload["risk_percent"]["12"], payload["risk_percent"]["24"]
    day = payload["score_date"]
    chosen = set(payload.get("chosen_as") or ())
    move = payload.get("move") if MOVED in chosen else None
    before = f"{move['previous_risk_percent_12']}% on {move['previous_score_date']}" if move else ""
    if TOP in chosen:
        text = (f"{nct} has elevated early-stop risk: {r12}% within 12 months and {r24}% "
                f"within 24 months, scored on {day}. It is among the {payload['top_n']} "
                "unflagged trials with the highest 12-month risk.")  # fmt: skip
        if move:
            text += f" Its 12-month risk {move['direction']} from {before}."
        return text
    if move:
        points = move["change_points"]
        return (f"The 12-month early-stop risk of {nct} {move['direction']} from {before} to "
                f"{r12}%, scored on {day}; its 24-month risk is {r24}%. It was chosen because "
                f"its 12-month risk moved by {points} percentage points.")  # fmt: skip
    return (f"{nct} has a 12-month early-stop risk of {r12}% and a 24-month risk of {r24}%, "
            f"scored on {day}.")  # fmt: skip


def paragraph_three(payload: Mapping[str, Any]) -> str:
    """The record changes, newest first with their exact kinds, then the values held."""
    changes = payload.get("record_changes") or []
    total = int(payload.get("record_changes_total") or len(changes))
    held = payload["values_held_from"]
    if changes:
        listed = "; ".join(f"{c['post_date']} ({_join(c['changed'])})" for c in changes)
        if total > len(changes):
            parts = [f"The record changed on {total} dates; the latest, newest first: {listed}."]
        else:
            parts = [f"Record changes, newest first: {listed}."]
    else:
        parts = ["No record change is held for this trial."]
    values = payload.get("field_changes") or []
    if values:
        parts.append(f"Before and after values, held from go-live ({held}) onward:")
        parts += [f"{_capital(v['label'])} moved from {v['before']} to {v['after']} on "
                  f"{v['post_date']}." for v in values]  # fmt: skip
    else:
        parts.append(f"Before and after values are held only from go-live ({held}) onward, and "
                     "no change in them is held for this trial.")  # fmt: skip
    return " ".join(parts)


def model_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    """What the model is sent: the 12-month drivers only, each with its label, its displayed
    value and its effect, in the payload's order."""
    return {"drivers": [
        {"label": d["label"], "value": d["display"], "effect": f"{d['direction']} the risk"}
        for d in drivers_12(payload)
    ]}  # fmt: skip


def drivers_12(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list((payload.get("drivers") or {}).get("12") or [])


def memo(payload: Mapping[str, Any], sentences: Sequence[str] | None) -> list[str]:
    """The three paragraphs: the template's first, the model's sentences, the template's third.
    A payload without 12-month drivers has a fixed second paragraph and needs no model."""
    second = " ".join(s.strip() for s in sentences or ()) if drivers_12(payload) else NO_DRIVERS
    return [paragraph_one(payload), second, paragraph_three(payload)]
