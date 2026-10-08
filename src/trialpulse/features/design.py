"""Design features as of the landmark (CLAUDE.md Section 8, design family).

Each entry is a SQL expression over one row of `states.AT_LANDMARK_TABLE`: the version in
effect at the landmark. Dates are compared by calendar month (see `states`).

The phase is not a versioned field (ADR 0006), so `title_phase` reads what the versioned
titles say: `title_phase` below parses one title, and the build applies it to the official
title in effect at the landmark, then to the brief title.

Intervention types come from the `interventions` config, which holds the interventions of
each version (ADR 0020). `intervention_expressions` reads the list of types of the version
in effect at the landmark.
"""

import re

from trialpulse.features.registry import INTERVENTION_TYPES
from trialpulse.features.states import month_index

EXPRESSIONS: dict[str, str] = {
    "allocation": "allocation",
    "intervention_model": "intervention_model",
    "primary_purpose": "primary_purpose",
    "masking": "masking",
    "healthy_volunteers": "healthy_volunteers",
    "sex": "sex",
    "minimum_age_years": "minimum_age_years",
    "maximum_age_years": "maximum_age_years",
    "enrollment_count": "CAST(enrollment_count AS DOUBLE)",
    "planned_duration_months": (
        f"{month_index('primary_completion_date')} - {month_index('start_date')}"
    ),
    "start_anticipated": f"{month_index('start_date')} > {month_index('landmark_date')}",
    "registered_after_start": (
        f"{month_index('start_date')} < {month_index('study_first_post_date')}"
    ),
    "registration_lag_months": (
        f"{month_index('study_first_post_date')} - {month_index('start_date')}"
    ),
    "registration_year": "year(study_first_post_date)",
}


def intervention_expressions(types: str) -> dict[str, str]:
    """Features from the list of intervention types of one version (`types`, NULL when the
    version lists no intervention)."""
    out = {
        "n_interventions": f"coalesce(len({types}), 0)",
        "n_intervention_types": f"coalesce(len(list_distinct({types})), 0)",
    }
    for kind in INTERVENTION_TYPES:
        out[f"intervention_{kind.lower()}"] = f"coalesce(list_contains({types}, '{kind}'), false)"
    return out


# The phase a title states ------------------------------------------------------------------

NO_PHASE = "NONE"
OTHER_PHASES = "OTHER"
_NUMERAL = r"(?:[0-4]|iv|i{1,3})[ab]?"
_PHASE = re.compile(
    rf"(?i)\bphase[\s\-]*({_NUMERAL}(?:\s*(?:/|-|,|&|and|to|or)\s*(?:phase[\s\-]*)?{_NUMERAL})*)\b"
)
_NUMBER = re.compile(r"(?i)[0-4]|iv|i{1,3}")
_VALUE = {"0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "i": 1, "ii": 2, "iii": 3, "iv": 4}
_LABELS: dict[frozenset[int], str] = {
    frozenset({0}): "EARLY_PHASE1",
    frozenset({1}): "PHASE1",
    frozenset({1, 2}): "PHASE1_PHASE2",
    frozenset({2}): "PHASE2",
    frozenset({2, 3}): "PHASE2_PHASE3",
    frozenset({3}): "PHASE3",
    frozenset({4}): "PHASE4",
}
PHASE_LABELS: tuple[str, ...] = (NO_PHASE, *_LABELS.values(), OTHER_PHASES)


def title_phase(title: str | None) -> str:
    """The phase a title states: "Phase 2", "phase I/II", "Phase IIb" and the like. NONE when
    it states none; OTHER for a combination outside the registry's own (such as 1 and 3)."""
    if not title:
        return NO_PHASE
    found: set[int] = set()
    for match in _PHASE.finditer(title):
        found.update(_VALUE[n.lower()] for n in _NUMBER.findall(match.group(1)))
    if not found:
        return NO_PHASE
    return _LABELS.get(frozenset(found), OTHER_PHASES)
