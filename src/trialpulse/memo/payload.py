"""The payload of a risk memo and the choice of trials (ADR 0084): stored values only.

A payload holds what the memo may say and nothing else: the trial's registry id, its served
risks as whole percents and their date, the five drivers per horizon (feature, value as shown,
direction), its record changes (the amendment markers' dates and the groups of fields they
changed, and, for versions held since go-live, each changed field's value before and after),
its sponsor class, status and the phase stated in its title, and why it was chosen. It never
holds a title, a sponsor name, a sponsor id or any free text: the builder takes only the
columns listed here, and a test checks that what it is given beyond them never reaches the
payload. The SHA-256 of its canonical JSON is the payload hash; a memo is regenerated only
when it changes.

Everything here is a pure function of rows already read, so it is tested offline.
"""

import datetime as dt
import hashlib
import itertools
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

PAYLOAD_VERSION = 1
HORIZONS = (12, 24)
DRIVERS_PER_HORIZON = 5
TOP = "top_100"
MOVED = "moved"
# The groups of an amendment marker's code (live.contract.MARKER_GROUPS), in words.
GROUP_NAMES: dict[int, str] = {
    1: "status", 2: "dates", 4: "enrollment", 8: "design", 16: "sponsor",
    32: "interventions or phase in the title",
}  # fmt: skip
# Fields whose before and after values a payload may show: values, never names or texts.
CHANGE_FIELDS: tuple[str, ...] = (
    "status", "start_date", "primary_completion_date", "completion_date", "enrollment_count",
    "enrollment_type", "allocation", "intervention_model", "primary_purpose", "masking",
    "healthy_volunteers", "sex", "minimum_age_years", "maximum_age_years",
    "lead_sponsor_class", "title_phase", "intervention_types",
)  # fmt: skip


def percent(risk: float) -> int:
    """A risk as a whole percent, halves rounded up (0.125 is 13)."""
    return math.floor(float(risk) * 100 + 0.5)


def as_shown(value: Any) -> str:
    """A stored value as the payload shows it: a date in ISO form, a list joined, a flag as
    yes or no, a number with at most 3 decimals and no trailing zeros, text as stored."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return ", ".join(sorted(as_shown(v) for v in value)) or "none"
    if isinstance(value, (int, float)):
        return _number(float(value))
    text = str(value)
    try:
        return _number(float(text)) if text.strip() and text.strip()[0] in "-0123456789" else text
    except ValueError:
        return text


def _number(x: float) -> str:
    if math.isnan(x):
        return "none"
    if float(x).is_integer():
        return str(int(x))
    return f"{x:.3f}".rstrip("0").rstrip(".")


def drivers(row: Mapping[str, Any], names: Sequence[str]) -> dict[str, list[dict[str, str]]]:
    """The stored drivers of a score row (serving.trial_scores_latest), per horizon."""
    out: dict[str, list[dict[str, str]]] = {str(h): [] for h in HORIZONS}
    for months, feature, value, contribution in zip(
        row["driver_months"], row["driver_features"], row["driver_values"],
        row["driver_contributions"], strict=True,
    ):  # fmt: skip
        out.setdefault(str(months), []).append(
            {"feature": names[int(feature)], "value": as_shown(value),
             "direction": "raises" if float(contribution) > 0 else "lowers"}
        )  # fmt: skip
    return out


def record_changes(markers: Mapping[str, Any] | None, limit: int) -> list[dict[str, Any]]:
    """The latest `limit` amendment markers, newest first: the post date and what changed."""
    if not markers:
        return []
    pairs = sorted(zip(markers["post_dates"], markers["kinds"], strict=True), reverse=True)
    return [{"post_date": as_shown(day),
             "changed": [n for bit, n in GROUP_NAMES.items() if int(kind) & bit] or ["record"]}
            for day, kind in pairs[:limit]]  # fmt: skip


def field_changes(versions: Sequence[Mapping[str, Any]], limit: int) -> list[dict[str, str]]:
    """The changed values between consecutive versions held since go-live (live.study_versions,
    oldest first), newest first, at most `limit`. The first held version has nothing before
    it, so it shows no change."""
    ordered = sorted(versions, key=lambda v: v["effective_date"])
    found: list[dict[str, str]] = []
    for before, after in itertools.pairwise(ordered):
        for name in CHANGE_FIELDS:
            old, new = as_shown(before.get(name)), as_shown(after.get(name))
            if old != new:
                found.append({"post_date": as_shown(after["effective_date"]), "field": name,
                              "before": old, "after": new})  # fmt: skip
    found.sort(key=lambda c: c["post_date"], reverse=True)
    return found[:limit]


def build(
    score: Mapping[str, Any],
    state: Mapping[str, Any],
    display: Mapping[str, Any] | None,
    markers: Mapping[str, Any] | None,
    versions: Sequence[Mapping[str, Any]],
    chosen: Iterable[str],
    move: Mapping[str, Any] | None,
    names: Sequence[str],
    top_n: int,
    max_changes: int,
) -> dict[str, Any]:
    """The payload of one trial. `score` is its serving.trial_scores_latest row, `state` its
    live.trial_state row, `display` its serving.trial_display row (only the sponsor class is
    read), `markers` its serving.amendment_markers row, `versions` its live.study_versions."""
    reasons = sorted(set(chosen))
    sponsor_class = (display or {}).get("sponsor_class") or state.get("lead_sponsor_class")
    payload: dict[str, Any] = {
        "payload_version": PAYLOAD_VERSION,
        "nct_id": str(score["nct_id"]),
        "score_date": as_shown(score["as_of_date"]),
        "horizons_months": list(HORIZONS),
        "risk_percent": {"12": percent(score["risk_12m"]), "24": percent(score["risk_24m"])},
        "chosen_as": reasons,
        "status": as_shown(state.get("status")),
        "phase_in_title": as_shown(state.get("title_phase")),
        "sponsor_class": as_shown(sponsor_class),
        "drivers_per_horizon": DRIVERS_PER_HORIZON,
        "drivers": drivers(score, names),
        "record_changes": record_changes(markers, max_changes),
        "field_changes": field_changes(versions, max_changes),
    }
    if TOP in reasons:
        payload["top_n"] = int(top_n)
    if MOVED in reasons and move is not None:
        payload["move"] = {
            "previous_risk_percent_12": int(move["previous_risk_percent_12"]),
            "previous_score_date": as_shown(move["previous_score_date"]),
            "direction": str(move["direction"]),
            "change_points": int(move["change_points"]),
        }
    return payload


def canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")  # fmt: skip


def payload_hash(payload: Mapping[str, Any]) -> bytes:
    return hashlib.sha256(canonical(payload)).digest()


def choose(
    scores: Sequence[Mapping[str, Any]],
    history: Sequence[Mapping[str, Any]],
    top_n: int,
    threshold: float,
    window_days: int,
    today: dt.date,
) -> dict[str, tuple[list[str], dict[str, Any] | None]]:
    """The trials to write a memo for, each with why and, for a move, its move.

    `scores` are serving.trial_scores_latest rows (nct_id, risk_12m, flagged); `history` the
    last two serving.trial_score_history rows of each trial (nct_id, as_of_date, risk_12m,
    flagged). The top `top_n` unflagged trials by 12-month risk (ties by registry id), and the
    unflagged trials whose latest history row, dated within `window_days` of `today`, moved
    the 12-month risk by `threshold` or more from the row before it. Flagged trials get no
    memo (ADR 0084)."""
    unflagged = {str(s["nct_id"]): s for s in scores if not s["flagged"]}
    ranked = sorted(unflagged.values(), key=lambda s: (-float(s["risk_12m"]), str(s["nct_id"])))
    out: dict[str, tuple[list[str], dict[str, Any] | None]] = {
        str(s["nct_id"]): ([TOP], None) for s in ranked[:top_n]
    }
    by_trial: dict[str, list[Mapping[str, Any]]] = {}
    for row in history:
        by_trial.setdefault(str(row["nct_id"]), []).append(row)
    for nct, rows in sorted(by_trial.items()):
        if nct not in unflagged or len(rows) < 2:
            continue
        before, latest = sorted(rows, key=lambda r: r["as_of_date"])[-2:]
        if (today - latest["as_of_date"]).days > window_days:
            continue
        delta = float(latest["risk_12m"]) - float(before["risk_12m"])
        if abs(delta) < threshold:
            continue
        current = percent(unflagged[nct]["risk_12m"])
        move = {"previous_risk_percent_12": percent(before["risk_12m"]),
                "previous_score_date": before["as_of_date"],
                "direction": "rose" if delta > 0 else "fell",
                "change_points": abs(current - percent(before["risk_12m"]))}  # fmt: skip
        reasons = out.get(nct, ([], None))[0]
        out[nct] = (sorted({*reasons, MOVED}), move)
    return out
