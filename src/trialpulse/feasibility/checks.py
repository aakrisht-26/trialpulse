"""Pure logic for the spike: seeded sampling, the dataset-versus-API comparison (part d)
and the list-field stability measure (part f)."""

import datetime as dt
import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

COMPARED_FIELDS: tuple[str, ...] = (
    "overall_status",
    "start_date",
    "primary_completion_date",
    "enrollment_count",
    "lead_sponsor_class",
    "why_stopped",
)
_DATE_FIELDS = frozenset({"start_date", "primary_completion_date"})
_PRECISION_PARTS = {"year": 1, "month": 2, "day": 3}


def seeded_sample(ids: Iterable[str], n: int, seed: int) -> list[str]:
    """A reproducible sample: the same ids, n and seed always give the same sorted list."""
    pool = sorted(set(ids))
    if n >= len(pool):
        return pool
    return sorted(random.Random(seed).sample(pool, n))


def date_parts(value: Any, precision: str | None = None) -> tuple[int, ...] | None:
    """(year, month, day) truncated to the value's precision.

    Strings follow the API v2 partial-date format ("2019", "2019-03" or "2019-03-14").
    Dates take an optional precision label ("year", "month" or "day")."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        value = value.date()
    if isinstance(value, dt.date):
        parts: tuple[int, ...] = (value.year, value.month, value.day)
        keep = _PRECISION_PARTS.get((precision or "day").casefold(), 3)
        return parts[:keep]
    pieces = str(value).strip()[:10].split("-")
    return tuple(int(p) for p in pieces if p)


def dates_agree(dataset_value: Any, api_value: Any, dataset_precision: str | None = None) -> bool:
    """Equal at the coarser of the two precisions. Two missing dates agree."""
    a = date_parts(dataset_value, dataset_precision)
    b = date_parts(api_value)
    if a is None or b is None:
        return a is None and b is None
    n = min(len(a), len(b))
    return a[:n] == b[:n]


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).casefold()
    return text or None


def values_agree(name: str, dataset_row: Mapping[str, Any], api_row: Mapping[str, Any]) -> bool:
    ds, api = dataset_row.get(name), api_row.get(name)
    if name in _DATE_FIELDS:
        return dates_agree(ds, api, dataset_row.get(f"{name}_precision"))
    if name == "enrollment_count":
        if ds is None or api is None:
            return ds is None and api is None
        return int(ds) == int(api)
    return normalize_text(ds) == normalize_text(api)


@dataclass
class TrialComparison:
    nct_id: str
    comparable: bool
    reason: str | None = None
    agreements: dict[str, bool] = field(default_factory=dict)


def compare_trial(
    nct_id: str,
    dataset_row: Mapping[str, Any] | None,
    api_row: Mapping[str, Any] | None,
    cutoff: dt.date,
) -> TrialComparison:
    """Compare the dataset's latest version with the official record. A trial whose
    official record was updated after the dataset cutoff is not comparable, because a
    difference would be a newer version, not an error."""
    if dataset_row is None:
        return TrialComparison(nct_id, False, "not in dataset")
    if api_row is None:
        return TrialComparison(nct_id, False, "no official record")
    updated = date_parts(api_row.get("last_update_post_date"))
    if updated is None or len(updated) < 3:
        return TrialComparison(nct_id, False, "official lastUpdatePostDate missing")
    if dt.date(*updated) > cutoff:
        return TrialComparison(nct_id, False, "official record updated after the dataset cutoff")
    agreements = {name: values_agree(name, dataset_row, api_row) for name in COMPARED_FIELDS}
    return TrialComparison(nct_id, True, None, agreements)


def summarize_comparisons(comparisons: Sequence[TrialComparison]) -> dict[str, Any]:
    comparable = [c for c in comparisons if c.comparable]
    excluded: dict[str, int] = {}
    for c in comparisons:
        if not c.comparable and c.reason:
            excluded[c.reason] = excluded.get(c.reason, 0) + 1
    per_field: dict[str, dict[str, Any]] = {}
    for name in COMPARED_FIELDS:
        agree = sum(1 for c in comparable if c.agreements[name])
        per_field[name] = {
            "agree": agree,
            "total": len(comparable),
            "share": agree / len(comparable) if comparable else None,
        }
    agree_all = sum(v["agree"] for v in per_field.values())
    total_all = sum(v["total"] for v in per_field.values())
    return {
        "sampled": len(comparisons),
        "comparable": len(comparable),
        "excluded": excluded,
        "per_field": per_field,
        "overall": {
            "agree": agree_all,
            "total": total_all,
            "share": agree_all / total_all if total_all else None,
        },
        "trials_fully_agreeing": sum(1 for c in comparable if all(c.agreements.values())),
        "mismatches": [
            {"nct_id": c.nct_id, "fields": [k for k, ok in c.agreements.items() if not ok]}
            for c in comparable
            if not all(c.agreements.values())
        ],
    }


def field_changed_after_first(snapshots: Sequence[Mapping[str, Any]], name: str) -> bool:
    """True if any later snapshot differs from the first one in this field. A change
    that is later reverted still counts: the value at some landmark differed."""
    if not snapshots:
        return False
    first = snapshots[0][name]
    return any(s[name] != first for s in snapshots[1:])


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for a proportion k / n (95% for z = 1.96)."""
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def stability_summary(
    snapshots_by_trial: Mapping[str, Sequence[Mapping[str, Any]]],
    fields: Sequence[str],
    max_change_share: float,
) -> dict[str, Any]:
    """Share of audited trials whose field changed after version 0, overall and among
    trials with more than one version, and whether it is under the allowed share."""
    trials = list(snapshots_by_trial)
    multi = [t for t in trials if len(snapshots_by_trial[t]) > 1]
    per_field: dict[str, dict[str, Any]] = {}
    for name in fields:
        changed = [t for t in trials if field_changed_after_first(snapshots_by_trial[t], name)]
        share = len(changed) / len(trials) if trials else None
        per_field[name] = {
            "changed": len(changed),
            "trials": len(trials),
            "share": share,
            "share_among_multi_version": len(changed) / len(multi) if multi else None,
            "under_threshold": share is not None and share < max_change_share,
        }
    return {
        "trials_audited": len(trials),
        "multi_version_trials": len(multi),
        "max_change_share": max_change_share,
        "fields": per_field,
    }
