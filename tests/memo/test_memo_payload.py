"""The memo's payload and the choice of trials (ADR 0084), on made-up rows."""

import datetime as dt
import json
from typing import Any

from trialpulse.features.registry import model_columns
from trialpulse.memo import payload as p

NAMES = model_columns()
DAY = dt.date(2026, 11, 16)


def score(nct: str = "NCT01234567", risk12: float = 0.142, risk24: float = 0.255,
          flagged: bool = False) -> dict[str, Any]:  # fmt: skip
    return {
        "nct_id": nct, "as_of_date": DAY, "risk_12m": risk12, "risk_24m": risk24,
        "flagged": flagged, "landmark_index": 3, "model_version": "trialpulse-m4/1",
        "driver_months": [12] * 5 + [24] * 5,
        "driver_features": [3, 8, 28, 4, 12, 3, 8, 28, 12, 4],
        "driver_values": ["DOUBLE", "0.44612", "RECRUITING", "yes", "36", "DOUBLE", "0.44612",
                          "RECRUITING", "36", "yes"],
        "driver_contributions": [-0.47, 0.38, 0.30, -0.29, 0.29, -0.48, 0.38, 0.30, 0.29, -0.28],
    }  # fmt: skip


STATE = {"status": "RECRUITING", "title_phase": "PHASE2", "lead_sponsor_class": "INDUSTRY",
         "official_title": "A Study of Something in Somebody", "sponsor_id": "ab" * 32}  # fmt: skip
DISPLAY = {"brief_title": "Brief Title of a Made-up Trial", "sponsor_class": "INDUSTRY",
           "sponsor_id": "cd" * 32, "display_name": "Acme Pharma Inc."}  # fmt: skip
MARKERS = {"post_dates": [dt.date(2026, 11, 10), dt.date(2025, 1, 2), dt.date(2024, 3, 4)],
           "kinds": [6, 1, 1]}  # fmt: skip
VERSIONS = [
    {"effective_date": dt.date(2026, 11, 8), "status": "RECRUITING", "enrollment_count": 120,
     "primary_completion_date": dt.date(2027, 6, 30), "sponsor_id": "x", "why_stopped": "t"},
    {"effective_date": dt.date(2026, 11, 10), "status": "RECRUITING", "enrollment_count": 80,
     "primary_completion_date": dt.date(2028, 1, 31), "sponsor_id": "y", "why_stopped": "t"},
]  # fmt: skip


def build(**over: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "score": score(), "state": STATE, "display": DISPLAY, "markers": MARKERS,
        "versions": VERSIONS, "chosen": [p.TOP], "move": None, "names": NAMES, "top_n": 100,
        "max_changes": 10, "values_held_from": dt.date(2026, 11, 8),
    }  # fmt: skip
    args.update(over)
    return p.build(**args)


def test_the_payload_holds_the_stored_values_as_shown() -> None:
    out = build()
    assert out["risk_percent"] == {"12": 14, "24": 26}
    assert out["score_date"] == "2026-11-16"
    first = {"feature": "masking", "label": "masking", "value": "DOUBLE",
             "display": "masked for 2 roles", "direction": "lowers"}  # fmt: skip
    assert out["drivers"]["12"][0] == first
    second = out["drivers"]["12"][1]
    assert (second["feature"], second["value"], second["display"]) == (
        "enrollment_count",
        "0.446",
        "0.446",
    )  # value at most 3 decimals; display from stored
    assert out["drivers"]["12"][4]["display"] == "registered 36 months after the start"
    assert out["values_held_from"] == "2026-11-08"
    assert out["record_changes_total"] == 3
    assert out["record_changes"][0] == {"post_date": "2026-11-10",
                                        "changed": ["dates", "enrollment"]}  # fmt: skip
    assert out["field_changes"] == [
        {"post_date": "2026-11-10", "field": "primary_completion_date",
         "label": "primary completion date", "before": "2027-06-30", "after": "2028-01-31"},
        {"post_date": "2026-11-10", "field": "enrollment_count", "label": "enrollment count",
         "before": "120", "after": "80"},
    ]  # fmt: skip
    assert out["top_n"] == 100
    assert "move" not in out


def test_the_payload_holds_no_title_name_sponsor_id_or_free_text() -> None:
    """ADR 0084, decision 4: what the builder is given beyond its columns never reaches it."""
    text = p.canonical(build()).decode("utf-8")
    for value in (
        "A Study of Something",
        "Brief Title",
        "Acme Pharma",
        "ab" * 32,
        "cd" * 32,
        "why_stopped",
        "official_title",
        "brief_title",
        "display_name",
        "sponsor_id",
    ):
        assert value not in text, value
    keys = set(build())
    assert keys <= {"payload_version", "nct_id", "score_date", "horizons_months", "risk_percent",
                    "chosen_as", "status", "phase_in_title", "sponsor_class",
                    "drivers_per_horizon", "drivers", "record_changes", "field_changes",
                    "record_changes_total", "values_held_from", "top_n", "move"}  # fmt: skip


def test_the_hash_follows_the_values_as_shown_only() -> None:
    first = p.payload_hash(build())
    assert p.payload_hash(build(score=score(risk12=0.1404))) == first  # still 14%
    assert p.payload_hash(build(score=score(risk12=0.150))) != first
    assert p.payload_hash(build(versions=VERSIONS[:1])) != first
    assert json.loads(p.canonical(build())) == build()


def test_a_retention_leaves_fewer_field_changes_never_other_ones() -> None:
    """ADR 0069 deletes old versions; the memo's payload then shows a subset of its changes."""
    third = {**VERSIONS[1], "effective_date": dt.date(2026, 11, 12), "status": "SUSPENDED"}
    held = [*VERSIONS, third]
    everything = p.field_changes(held, 10)
    after = p.field_changes(held[1:], 10)  # the oldest version deleted
    assert after
    assert all(c in everything for c in after)
    assert len(after) < len(everything)


def test_halves_round_up_and_shares_render_without_trailing_zeros() -> None:
    assert [p.percent(x) for x in (0.125, 0.135, 0.0049, 0.995)] == [13, 14, 0, 100]
    assert [p.as_shown(v) for v in (2.50, "3.000", 7, True, None, ["B", "A"], "PHASE2")] == [
        "2.5", "3", "7", "yes", "none", "A, B", "PHASE2"]  # fmt: skip


def history(nct: str, before: float, after: float, day: dt.date = DAY) -> list[dict[str, Any]]:
    return [{"nct_id": nct, "as_of_date": day - dt.timedelta(days=7), "risk_12m": before},
            {"nct_id": nct, "as_of_date": day, "risk_12m": after}]  # fmt: skip


def test_the_choice_is_the_top_unflagged_and_the_movers() -> None:
    scores = [score("NCT00000001", 0.40), score("NCT00000002", 0.35, flagged=True),
              score("NCT00000003", 0.30), score("NCT00000004", 0.10),
              score("NCT00000005", 0.08), score("NCT00000006", 0.07, flagged=True)]  # fmt: skip
    hist = (history("NCT00000004", 0.03, 0.10) + history("NCT00000003", 0.40, 0.30)
            + history("NCT00000005", 0.10, 0.08)  # moved by 0.02 only
            + history("NCT00000006", 0.01, 0.07)  # flagged: no memo; the next is too old
            + history("NCT00000001", 0.20, 0.40, DAY - dt.timedelta(days=30)))  # fmt: skip
    got = p.choose(scores, hist, top_n=2, threshold=0.05, window_days=7, today=DAY)
    assert set(got) == {"NCT00000001", "NCT00000003", "NCT00000004"}
    assert got["NCT00000001"] == ([p.TOP], None)
    assert got["NCT00000003"][0] == [p.MOVED, p.TOP]
    assert got["NCT00000003"][1] == {"previous_risk_percent_12": 40,
                                     "previous_score_date": DAY - dt.timedelta(days=7),
                                     "direction": "fell", "change_points": 10}  # fmt: skip
    chosen, move = got["NCT00000004"]
    assert chosen == [p.MOVED]
    assert move is not None
    assert move["direction"] == "rose"
    assert move["change_points"] == 7


def test_a_move_reaches_the_payload() -> None:
    move = {"previous_risk_percent_12": 9, "previous_score_date": dt.date(2026, 11, 9),
            "direction": "rose", "change_points": 5}  # fmt: skip
    out = build(chosen=[p.MOVED], move=move)
    assert out["move"] == {"previous_risk_percent_12": 9, "previous_score_date": "2026-11-09",
                           "direction": "rose", "change_points": 5}  # fmt: skip
    assert "top_n" not in out
