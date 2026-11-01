"""The compact live row (ADR 0037) and the sponsor ids of ADR 0044.

A compact row holds what the served features read from one registry record and nothing
else: the latest-record fields, the phase the titles state in place of the titles, the
intervention types in place of the interventions, and a keyed sponsor id in place of the
sponsor's name and key. It is built from a canonical version row (`contracts.versions`), so
the dataset path (the bootstrap) and the API v2 path share one function.

- `status` is the submitted status (ADR 0014): the registry's UNKNOWN label is replaced by
  `last_known_status` where it has one, so a record the registry flips to UNKNOWN without a
  new version is the same record (guidance item 10).
- `feature_hash` is a SHA-256 over the compact fields. Only a difference in a field a served
  feature reads changes it, so a difference in a summary, an eligibility text or a display
  name never makes a version, a history row or a `record_change` (ADR 0042, condition 4).
- `sponsor_id` is HMAC-SHA256 of the sponsor key under the secret `SPONSOR_ID_KEY`, for a key
  with an identity (`contracts.sponsor.has_identity`), and NULL otherwise (ADR 0044). A plain
  hash could be reversed by hashing candidate names.
"""

import hashlib
import hmac
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from trialpulse.cohort.rules import CohortRules, submitted_status
from trialpulse.contracts.sponsor import ORGANIZATION_WORD, has_identity
from trialpulse.features.design import resolved_title_phase
from trialpulse.features.registry import INTERVENTION_TYPES

# Column name -> storage type, in the order of the compact row. The same names and types are
# the columns of `live.study_versions` and the latest-record part of `live.trial_state`.
LIVE_FIELDS: dict[str, str] = {
    "effective_date": "DATE",
    "status": "VARCHAR",
    "study_type": "VARCHAR",
    "study_first_post_date": "DATE",
    "status_verified_date": "DATE",
    "start_date": "DATE",
    "primary_completion_date": "DATE",
    "primary_completion_date_precision": "VARCHAR",
    "primary_completion_date_type": "VARCHAR",
    "completion_date": "DATE",
    "completion_date_precision": "VARCHAR",
    "allocation": "VARCHAR",
    "intervention_model": "VARCHAR",
    "primary_purpose": "VARCHAR",
    "masking": "VARCHAR",
    "healthy_volunteers": "BOOLEAN",
    "sex": "VARCHAR",
    "minimum_age_years": "DOUBLE",
    "maximum_age_years": "DOUBLE",
    "enrollment_count": "BIGINT",
    "enrollment_type": "VARCHAR",
    "lead_sponsor_class": "VARCHAR",
    "organization_class": "VARCHAR",
    "sponsor_id": "VARCHAR",
    "title_phase": "VARCHAR",
    "intervention_types": "VARCHAR[]",
}
# The canonical columns copied as they are (the others are derived).
COPIED: tuple[str, ...] = tuple(
    c for c in LIVE_FIELDS if c not in ("status", "sponsor_id", "title_phase", "intervention_types")
)
# The canonical fields each compact field is made from: the copied ones from themselves, the
# derived ones from their sources. A test holds every source to the feature whitelist of
# `features.registry` (CLAUDE.md Section 8, rule 2), as for the offline `versions` view.
SOURCES: dict[str, tuple[str, ...]] = {
    **{name: (name,) for name in COPIED},
    "status": ("overall_status", "last_known_status"),
    "sponsor_id": ("sponsor_key", "sponsor_is_individual"),
    "title_phase": ("official_title_hash", "brief_title_hash"),
    "intervention_types": ("intervention_type",),
}
# Change codes of an amendment marker (ADR 0043): which group of fields a version changed.
MARKER_GROUPS: dict[int, tuple[str, ...]] = {
    1: ("status",),
    2: ("start_date", "primary_completion_date", "primary_completion_date_precision",
        "primary_completion_date_type", "completion_date", "completion_date_precision",
        "status_verified_date"),
    4: ("enrollment_count", "enrollment_type"),
    8: ("study_type", "allocation", "intervention_model", "primary_purpose", "masking",
        "healthy_volunteers", "sex", "minimum_age_years", "maximum_age_years"),
    16: ("sponsor_id", "lead_sponsor_class", "organization_class"),
    32: ("title_phase", "intervention_types"),
}  # fmt: skip
HIDDEN_CLASSES: tuple[str, ...] = ("OTHER", "UNKNOWN", "AMBIG")
_PLAIN_WORD = re.compile(r"^[^\W\d_]+(?:['\u2019-][^\W\d_]+)*$")  # an inner ' or -


class MissingSecretError(RuntimeError):
    """The live path needs SPONSOR_ID_KEY and it is not set."""


def sponsor_id(key: str | None, individual: bool | None, secret: bytes) -> str | None:
    """The keyed id of a sponsor key with an identity, else None (ADR 0044)."""
    if not secret:
        raise MissingSecretError("SPONSOR_ID_KEY is not set (environment, .env or a secret)")
    if not has_identity(key, individual):
        return None
    assert key is not None
    return hmac.new(secret, key.encode("utf-8"), hashlib.sha256).hexdigest()


def display_name(name: str | None, sponsor_class: str | None) -> str | None:
    """The name shown for a sponsor, or None (ADR 0044): no name for a sponsor of class OTHER,
    UNKNOWN or AMBIG whose name is two or three plain words with no organization word. The
    stored name of an individual is already None (`contracts.sponsor`)."""
    if not name:
        return None
    words = name.split()
    plain = 2 <= len(words) <= 3 and all(_PLAIN_WORD.match(w) for w in words)
    if (
        (sponsor_class or "UNKNOWN") in HIDDEN_CLASSES
        and plain
        and not ORGANIZATION_WORD.search(name)
    ):
        return None
    return name


def distinct_intervention_types(pairs: Iterable[tuple[str | None, str | None]]) -> list[str]:
    """The type of each distinct (name, type) pair, on exact strings, sorted: what the
    dataset's interventions config gives per version. API v2 repeats pairs on some records.
    A pair without a type is left out."""
    return sorted(kind for _, kind in {(name, kind) for name, kind in pairs if kind is not None})


def unknown_intervention_types(types: Sequence[str]) -> list[str]:
    return sorted(set(types) - set(INTERVENTION_TYPES))


def compact_row(
    canonical: Mapping[str, Any],
    official_title: str | None,
    brief_title: str | None,
    intervention_types: Sequence[str] | None,
    rules: CohortRules,
    secret: bytes,
) -> dict[str, Any]:
    """The compact row of one canonical version row (either source), with `nct_id` and its
    `feature_hash`. Titles are the normalized texts; types as `distinct_intervention_types`."""
    row: dict[str, Any] = {"nct_id": canonical["nct_id"]}
    for name in COPIED:
        row[name] = canonical.get(name)
    row["status"] = submitted_status(
        rules, str(canonical["overall_status"]), canonical.get("last_known_status")
    )
    row["sponsor_id"] = sponsor_id(
        canonical.get("sponsor_key"), canonical.get("sponsor_is_individual"), secret
    )
    row["title_phase"] = resolved_title_phase(official_title, brief_title)
    row["intervention_types"] = sorted(intervention_types or [])
    row = {"nct_id": row["nct_id"], **{name: row[name] for name in LIVE_FIELDS}}
    row["feature_hash"] = feature_hash(row)
    return row


def _render(value: Any, kind: str) -> str:
    if value is None:
        return ""
    if kind == "DATE":
        return str(value)[:10]
    if kind == "BOOLEAN":
        return "true" if bool(value) else "false"
    if kind == "BIGINT":
        return str(int(value))
    if kind == "DOUBLE":
        return repr(float(value))
    if kind == "VARCHAR[]":
        return "|".join(str(v) for v in value)
    return str(value)


def feature_hash(row: Mapping[str, Any]) -> str:
    """SHA-256 over the trial id and the compact fields (not the post date's own type, not
    anything a feature does not read), rendered by type and joined with a unit separator."""
    parts = [str(row["nct_id"])]
    parts += [_render(row.get(name), kind) for name, kind in LIVE_FIELDS.items()]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def marker_code(before: Mapping[str, Any] | None, after: Mapping[str, Any]) -> int:
    """What a version changed against the state before it (ADR 0043), as the sum of the
    codes of `MARKER_GROUPS`; 0 when nothing a feature reads changed. A first version is 1."""
    if before is None:
        return 1
    code = 0
    for bit, names in MARKER_GROUPS.items():
        if any(_render(before.get(n), LIVE_FIELDS[n]) != _render(after.get(n), LIVE_FIELDS[n])
               for n in names):  # fmt: skip
            code |= bit
    return code
