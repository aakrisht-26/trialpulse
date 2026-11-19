"""The memo's payload and the choice of trials (ADR 0084, payload version 3), on made-up
rows."""

import datetime as dt
import json
from typing import Any

from trialpulse.features.registry import model_columns
from trialpulse.memo import payload as p

NAMES = model_columns()
DAY = dt.date(2026, 11, 16)
D = dt.date
DATES = "dates or status verification"


def score(nct: str = "NCT01234567", risk12: float = 0.142, risk24: float = 0.255,
          flagged: bool = False) -> dict[str, Any]:  # fmt: skip
    return {
        "nct_id": nct, "as_of_date": DAY, "risk_12m": risk12, "risk_24m": risk24,
        "flagged": flagged, "landmark_index": 3, "model_version": "trialpulse-m4/1",
        "driver_months": [12] * 5 + [24] * 5,
        "driver_features": [3, 8, 28, 4, 12, 3, 8, 28, 12, 4],
        "driver_values": ["DOUBLE", "120", "RECRUITING", "yes", "36", "DOUBLE", "120",
                          "RECRUITING", "36", "yes"],
        "driver_contributions": [-0.47, 0.38, 0.30, -0.29, 0.29, -0.48, 0.38, 0.30, 0.29, -0.28],
    }  # fmt: skip


STATE = {"status": "RECRUITING", "title_phase": "PHASE2", "lead_sponsor_class": "INDUSTRY",
         "enrollment_type": "ESTIMATED", "study_first_post_date": D(2024, 3, 4),
         "status_verified_date": D(2026, 10, 1),
         "official_title": "A Study of Something in Somebody", "sponsor_id": "ab" * 32}  # fmt: skip
DISPLAY = {"brief_title": "Brief Title of a Made-up Trial", "sponsor_class": "INDUSTRY",
           "sponsor_id": "cd" * 32, "display_name": "Acme Pharma Inc."}  # fmt: skip
MARKERS = {"post_dates": [D(2026, 11, 10), D(2025, 1, 2), D(2024, 3, 4)], "kinds": [6, 1, 1]}
VERSIONS = [
    {"effective_date": D(2026, 11, 8), "status": "RECRUITING", "enrollment_count": 120,
     "primary_completion_date": D(2027, 6, 30), "sponsor_id": "x", "why_stopped": "t"},
    {"effective_date": D(2026, 11, 10), "status": "RECRUITING", "enrollment_count": 80,
     "primary_completion_date": D(2028, 1, 31), "sponsor_id": "x", "why_stopped": "t"},
]  # fmt: skip


def build(**over: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "score": score(), "state": STATE, "display": DISPLAY, "markers": MARKERS,
        "versions": VERSIONS, "chosen": [p.TOP], "move": None, "names": NAMES, "top_n": 100,
        "max_changes": 10, "values_held_from": D(2026, 11, 8),
    }  # fmt: skip
    args.update(over)
    return p.build(**args)


def test_the_payload_holds_the_stored_values_as_shown() -> None:
    out = build()
    assert out["payload_version"] == 4  # memo_v4 (ADR 0085)
    assert out["risk_percent"] == {"12": 14, "24": 26}
    assert out["score_date"] == "2026-11-16"
    first = {"feature": "masking", "label": "number of masked roles", "value": "DOUBLE",
             "display": "2", "direction": "lowers"}  # fmt: skip
    assert out["drivers"]["12"][0] == first
    second = out["drivers"]["12"][1]
    assert (second["feature"], second["display"]) == ("enrollment_count",
                                                      "120, the estimated target")  # fmt: skip
    assert out["drivers"]["12"][4]["display"] == (
        "36 months before the first posting (registered late), any move of the start included"
    )
    assert out["values_held_from"] == "2026-11-08"
    assert out["status_verified_month"] == "2026-10"
    assert out["first_posted_on"] == "2024-03-04"
    assert out["record_changes_total"] == 2  # the first posting is not a change
    assert out["record_changes"] == [
        {"post_date": "2026-11-10", "changed": ["dates", "enrollment"]},
        {"post_date": "2025-01-02", "changed": ["status"]},
    ]
    assert out["since_go_live"] == [{"post_date": "2026-11-10", "held": p.COMPARED, "values": [
        {"field": "primary_completion_date", "label": "primary completion date",
         "before": "2027-06-30", "after": "2028-01-31"},
        {"field": "enrollment_count", "label": "enrollment count", "before": "120",
         "after": "80"}]}]  # fmt: skip
    assert out["top_n"] == 100
    assert "move" not in out


def test_the_first_posting_is_named_only_on_the_first_post_date() -> None:
    """ADR 0043 amended: the marker on the live state's first-post date is the first posting;
    without that date, or for trimmed markers, every marker counts and keeps its kinds."""
    trimmed = build(state={**STATE, "study_first_post_date": D(2024, 2, 1)})
    unknown = build(state={**STATE, "study_first_post_date": None})
    for out in (trimmed, unknown):
        assert out["first_posted_on"] == "none"
        assert out["record_changes_total"] == 3
        assert out["record_changes"][-1] == {"post_date": "2024-03-04", "changed": ["status"]}


def held(day: dt.date, **values: Any) -> dict[str, Any]:
    base = {"status": "RECRUITING", "start_date": D(2027, 1, 1),
            "status_verified_date": D(2026, 10, 1), "enrollment_count": 120}  # fmt: skip
    return {"effective_date": day, **base, **values}


def test_the_dates_group_is_named_exactly_only_where_two_held_versions_show_it() -> None:
    markers = {"post_dates": [D(2026, 11, 9), D(2026, 11, 12), D(2026, 11, 14), D(2026, 1, 5)],
               "kinds": [2, 2, 2 | 4, 2]}  # fmt: skip
    versions = [held(D(2026, 11, 9)),
                held(D(2026, 11, 12), status_verified_date=D(2026, 11, 1)),
                held(D(2026, 11, 14), status_verified_date=D(2026, 11, 1),
                     start_date=D(2027, 3, 1), enrollment_count=90)]  # fmt: skip
    out = build(markers=markers, versions=versions)
    assert [c["changed"] for c in out["record_changes"]] == [
        ["dates", "enrollment"],  # the start date moved
        ["status verified"],  # only the status verified date moved
        [DATES],  # the first version held since go-live: nothing to compare with
        [DATES],  # before go-live: the stored code only
    ]
    assert [e["held"] for e in out["since_go_live"]] == [p.COMPARED, p.COMPARED, p.FIRST_HELD]
    verified = out["since_go_live"][1]["values"]
    assert verified == [{"field": "status_verified_date", "label": "status verified month",
                         "before": "2026-10", "after": "2026-11"}]  # fmt: skip


def test_a_change_since_go_live_without_its_version_is_not_held() -> None:
    markers = {"post_dates": [D(2026, 11, 12)], "kinds": [1]}
    out = build(markers=markers, versions=[held(D(2026, 11, 9))])
    assert out["since_go_live"] == [{"post_date": "2026-11-12", "held": p.NOT_HELD}]
    assert out["record_changes"] == [{"post_date": "2026-11-12", "changed": ["status"]}]


def test_after_retention_a_change_without_an_earlier_version_says_so() -> None:
    """The memo_v3 review: once retention can have deleted versions posted since go-live
    (ADR 0069), the oldest held version is no longer known to be the first."""
    markers = {"post_dates": [D(2027, 6, 1)], "kinds": [4]}
    versions = [held(D(2027, 6, 1))]
    assert build(markers=markers, versions=versions)["since_go_live"][0]["held"] == p.FIRST_HELD
    kept = build(markers=markers, versions=versions, versions_kept_from=D(2026, 12, 1))
    assert kept["since_go_live"][0]["held"] == p.EARLIER_NOT_HELD
    same = build(markers=markers, versions=versions, versions_kept_from=D(2026, 11, 8))
    assert same["since_go_live"][0]["held"] == p.FIRST_HELD


def test_a_completion_date_shows_its_recorded_precision() -> None:
    markers = {"post_dates": [D(2026, 11, 12)], "kinds": [2]}
    versions = [held(D(2026, 11, 9), completion_date=D(2027, 1, 1),
                     completion_date_precision="month"),
                held(D(2026, 11, 12), completion_date=D(2027, 3, 15),
                     completion_date_precision="day")]  # fmt: skip
    values = build(markers=markers, versions=versions)["since_go_live"][0]["values"]
    assert {"field": "completion_date", "label": "study completion date", "before": "2027-01",
            "after": "2027-03-15"} in values  # fmt: skip
    assert {"field": "completion_date_precision", "label": "precision of the study completion "
            "date", "before": "month", "after": "day"} in values  # fmt: skip


def test_a_sponsor_change_is_stated_without_values() -> None:
    markers = {"post_dates": [D(2026, 11, 12)], "kinds": [16]}
    versions = [held(D(2026, 11, 9), sponsor_id="ab" * 32),
                held(D(2026, 11, 12), sponsor_id="cd" * 32)]  # fmt: skip
    out = build(markers=markers, versions=versions)
    assert out["since_go_live"][0]["values"] == [{"field": "sponsor_id", "label": "lead sponsor"}]
    text = p.canonical(out).decode("utf-8")
    assert "ab" * 32 not in text
    assert "cd" * 32 not in text


def test_same_day_versions_compare_the_last_ingested() -> None:
    first = held(D(2026, 11, 12), enrollment_count=100, ingested_at="2026-11-12T01:00:00")
    second = held(D(2026, 11, 12), enrollment_count=90, ingested_at="2026-11-12T05:00:00")
    markers = {"post_dates": [D(2026, 11, 12)], "kinds": [4]}
    for order in ([first, second], [second, first]):
        out = build(markers=markers, versions=[held(D(2026, 11, 9)), *order])
        assert out["since_go_live"][0]["values"][0]["after"] == "90"


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
    ):
        assert value not in text, value
    keys = set(build())
    assert keys <= {"payload_version", "nct_id", "score_date", "horizons_months", "risk_percent",
                    "chosen_as", "status", "phase_in_title", "sponsor_class",
                    "status_verified_month", "drivers_per_horizon", "drivers",
                    "first_posted_on", "stored_markers", "record_changes",
                    "record_changes_total", "since_go_live", "values_held_from", "top_n",
                    "move"}  # fmt: skip


def test_the_hash_follows_the_values_as_shown_only() -> None:
    first = p.payload_hash(build())
    assert p.payload_hash(build(score=score(risk12=0.1404))) == first  # still 14%
    assert p.payload_hash(build(score=score(risk12=0.150))) != first
    assert p.payload_hash(build(versions=VERSIONS[:1])) != first
    assert json.loads(p.canonical(build())) == build()


def test_a_retention_leaves_fewer_compared_changes_never_other_ones() -> None:
    """ADR 0069 deletes old versions: a change whose earlier version is gone becomes the
    first held, and no other change appears."""
    third = held(D(2026, 11, 12), status="SUSPENDED")
    markers = {"post_dates": [D(2026, 11, 10), D(2026, 11, 12)], "kinds": [6, 1]}
    full = build(markers=markers, versions=[*VERSIONS, third])
    trimmed = build(markers=markers, versions=[VERSIONS[1], third])
    assert [e["held"] for e in full["since_go_live"]] == [p.COMPARED, p.COMPARED]
    assert [e["held"] for e in trimmed["since_go_live"]] == [p.COMPARED, p.FIRST_HELD]
    assert trimmed["since_go_live"][0] == full["since_go_live"][0]


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
    move = {"previous_risk_percent_12": 9, "previous_score_date": D(2026, 11, 9),
            "direction": "rose", "change_points": 5}  # fmt: skip
    out = build(chosen=[p.MOVED], move=move)
    assert out["move"] == {"previous_risk_percent_12": 9, "previous_score_date": "2026-11-09",
                           "direction": "rose", "change_points": 5}  # fmt: skip
    assert "top_n" not in out
