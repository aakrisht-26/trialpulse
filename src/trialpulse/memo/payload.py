"""The payload of a risk memo and the choice of trials (ADR 0084): stored values only.

A payload holds what the memo may say and nothing else: the trial's registry id, its served
risks as whole percents and their date, the five drivers per horizon (feature, value as shown,
direction, and a plain-English label and display value from `memo.labels`), its record
changes, the date from which before and after values are held, its sponsor class, status, the
phase stated in its title and the month its status was last verified, and why it was chosen.
It never holds a title, a sponsor name, a sponsor id or any free text: the builder takes only
the columns listed here, and a test checks that what it is given beyond them never reaches
the payload. The SHA-256 of its canonical JSON is the payload hash; a memo is regenerated only
when it changes.

The record changes (payload version 3, memo_v3) are read through `trialpulse.markers`: the
marker on the trial's first-post date (from the live state) is its first posting, kept apart
as `first_posted_on` and counted nowhere; every other marker is a change to the fields the
model reads, named by its groups. The dates group is named "dates" or "status verified" only
where two versions held since go-live show which changed, and "dates or status verification"
elsewhere. For each change since go-live, `since_go_live` says whether it could be compared
with an earlier held version and, if so, which shown fields moved from what to what; a
trial's first version held since go-live has no earlier value to compare.

Everything here is a pure function of rows already read, so it is tested offline.
"""

import datetime as dt
import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from trialpulse import markers as mk
from trialpulse.memo import labels

PAYLOAD_VERSION = 4  # memo_v4: the labels and displays of ADR 0085
HORIZONS = (12, 24)
DRIVERS_PER_HORIZON = 5
TOP = "top_100"
MOVED = "moved"
# Fields whose before and after values a payload may show: every field of an amendment
# marker's groups (live.contract.MARKER_GROUPS), values only (a sponsor's change is stated
# without them).
CHANGE_FIELDS: tuple[str, ...] = tuple(labels.FIELDS)
# The dates group: the schedule's dates, with their precision and type, and the status
# verified date, which the memo names apart (memo_v3).
SCHEDULE_FIELDS: tuple[str, ...] = (
    "start_date", "primary_completion_date", "primary_completion_date_precision",
    "primary_completion_date_type", "completion_date", "completion_date_precision",
)  # fmt: skip
VERIFIED_FIELD = "status_verified_date"
DATES_NAME = mk.NAMES[mk.DATES]
SCHEDULE_NAME = "dates"
VERIFIED_NAME = "status verified"
COMPARED = "compared"  # an earlier held version shows the values before
FIRST_HELD = "first_held"  # the first version held since go-live: nothing to compare with
EARLIER_NOT_HELD = "earlier_not_held"  # no earlier version is still held (retention ran)
NOT_HELD = "not_held"  # no version of that date is held any more


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


def _date(value: Any) -> dt.date | None:
    """A stored date as a date; None when missing."""
    if value is None or str(value).strip() in ("", "none", "None"):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def drivers(row: Mapping[str, Any], names: Sequence[str],
            state: Mapping[str, Any] | None = None) -> dict[str, list[dict[str, str]]]:  # fmt: skip
    """The stored drivers of a score row (serving.trial_scores_latest), per horizon, each with
    its label and display value (`memo.labels`) beside the feature and value as stored; the
    enrollment count's display reads the latest record's enrollment type."""
    enrollment_type = (state or {}).get("enrollment_type")
    out: dict[str, list[dict[str, str]]] = {str(h): [] for h in HORIZONS}
    for months, feature, value, contribution in zip(
        row["driver_months"], row["driver_features"], row["driver_values"],
        row["driver_contributions"], strict=True,
    ):  # fmt: skip
        name = names[int(feature)]
        out.setdefault(str(months), []).append(
            {"feature": name, "label": labels.feature_label(name), "value": as_shown(value),
             "display": labels.feature_display(name, str(value), enrollment_type),
             "direction": "raises" if float(contribution) > 0 else "lowers"}
        )  # fmt: skip
    return out


def _held(versions: Sequence[Mapping[str, Any]]) -> dict[dt.date, Mapping[str, Any]]:
    """The versions held since go-live (live.study_versions) by post date, the last ingested
    of each date (ties by feature hash, so the choice is deterministic)."""
    out: dict[dt.date, Mapping[str, Any]] = {}
    dated = [(day, v) for v in versions if (day := _date(v["effective_date"])) is not None]
    for day, version in sorted(dated, key=lambda dv: (dv[0], str(dv[1].get("ingested_at")),
                                                      str(dv[1].get("feature_hash")))):  # fmt: skip
        out[day] = version
    return out


def _diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    return [name for name in CHANGE_FIELDS
            if as_shown(before.get(name)) != as_shown(after.get(name))]  # fmt: skip


Held = Mapping[dt.date, Mapping[str, Any]]
Comparison = tuple[str, list[str], Mapping[str, Any] | None, Mapping[str, Any] | None]


def _comparison(day: dt.date, held: Held, none_deleted: bool) -> Comparison:
    """How a change of `day` compares with the versions held: compared (with the fields
    that moved, and the versions before and after), the first held since go-live (only
    while retention cannot have deleted a version posted since go-live, `none_deleted`),
    no earlier version still held, or not held."""
    if day not in held:
        return NOT_HELD, [], None, None
    earlier = [d for d in held if d < day]
    if not earlier:
        return (FIRST_HELD if none_deleted else EARLIER_NOT_HELD), [], None, None
    before, after = held[earlier[-1]], held[day]
    return COMPARED, _diff(before, after), before, after


def _kinds(marker: mk.Marker, moved: Sequence[str] | None) -> list[str]:
    """The groups of a change in words; the dates group exactly where the held versions show
    which of its fields moved (`moved`, None when they cannot)."""
    words = marker.changed()
    if DATES_NAME not in words or moved is None:
        return words
    exact = ([SCHEDULE_NAME] if any(f in SCHEDULE_FIELDS for f in moved) else []) + (
        [VERIFIED_NAME] if VERIFIED_FIELD in moved else [])  # fmt: skip
    if not exact:
        return words
    i = words.index(DATES_NAME)
    return [*words[:i], *exact, *words[i + 1 :]]


def _value_change(name: str, before: Mapping[str, Any], after: Mapping[str, Any]
                  ) -> dict[str, str]:  # fmt: skip
    """A field's change as the memo shows it; a completion date at its recorded precision."""
    entry = {"field": name, "label": labels.field_label(name)}
    if labels.FIELDS[name].kind == labels.SPONSOR_ID:  # stated, never shown
        return entry

    def shown(row: Mapping[str, Any]) -> str:
        return labels.field_display(name, as_shown(row.get(name)), row.get(f"{name}_precision"))

    return {**entry, "before": shown(before), "after": shown(after)}


def record_changes(
    stored: Mapping[str, Any] | None,
    versions: Sequence[Mapping[str, Any]],
    first_post_date: dt.date | None,
    values_held_from: dt.date,
    limit: int,
    versions_kept_from: dt.date | None = None,
) -> dict[str, Any]:
    """The record-change part of a payload (see the module text): the first posting, the
    latest `limit` changes newest first with their groups in words, their count, and the
    changes since go-live with what moved. `versions_kept_from` is the date from which
    retention deletes no version (ADR 0069); while it is on or before go-live, no version
    posted since go-live can have been deleted."""
    read = mk.read((stored or {}).get("post_dates") or [], (stored or {}).get("kinds") or [],
                   first_post_date)  # fmt: skip
    first = next((m.post_date for m in read if m.first_posted), None)
    changes = sorted(mk.changes(read), key=lambda m: m.post_date, reverse=True)
    held = _held(versions)
    none_deleted = (versions_kept_from or values_held_from) <= values_held_from
    listed: list[dict[str, Any]] = []
    since: list[dict[str, Any]] = []
    for marker in changes[:limit]:
        state, moved, before, after = (_comparison(marker.post_date, held, none_deleted)
                                       if marker.post_date >= values_held_from
                                       else (NOT_HELD, [], None, None))  # fmt: skip
        exact = moved if state == COMPARED else None
        listed.append({"post_date": as_shown(marker.post_date),
                       "changed": _kinds(marker, exact)})  # fmt: skip
        if marker.post_date < values_held_from:
            continue
        entry: dict[str, Any] = {"post_date": as_shown(marker.post_date), "held": state}
        if state == COMPARED and before is not None and after is not None:
            entry["values"] = [_value_change(name, before, after) for name in moved]
        since.append(entry)
    return {
        "first_posted_on": as_shown(first),
        "stored_markers": stored is not None,
        "record_changes": listed,
        "record_changes_total": len(changes),
        "since_go_live": since,
    }


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
    values_held_from: dt.date,
    versions_kept_from: dt.date | None = None,
) -> dict[str, Any]:
    """The payload of one trial. `score` is its serving.trial_scores_latest row, `state` its
    live.trial_state row (status, title phase, sponsor class, enrollment type, first-post date
    and status verified date are read), `display` its serving.trial_display row (only the
    sponsor class is read), `markers` its serving.amendment_markers row, `versions` its
    live.study_versions; `versions_kept_from` the date from which retention keeps every
    version (the run's date less `live.version_retention_months`)."""
    reasons = sorted(set(chosen))
    sponsor_class = (display or {}).get("sponsor_class") or state.get("lead_sponsor_class")
    verified = _date(state.get("status_verified_date"))
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
        "status_verified_month": verified.strftime("%Y-%m") if verified else "none",
        "drivers_per_horizon": DRIVERS_PER_HORIZON,
        "drivers": drivers(score, names, state),
        **record_changes(markers, versions, _date(state.get("study_first_post_date")),
                         values_held_from, max_changes, versions_kept_from),
        "values_held_from": as_shown(values_held_from),
    }  # fmt: skip
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
