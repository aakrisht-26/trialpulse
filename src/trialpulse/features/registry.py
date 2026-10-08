"""The feature registry (CLAUDE.md Section 8, rule 4).

Every feature is registered here with its name, family, source fields, whether those fields
have version history, and a description. docs/features.md is generated from this file, and
a test fails if the build computes a column that is not registered, or the other way round.

**The whitelist (rule 2).** `ALLOWED_VERSION_FIELDS` lists the fields of the canonical
version schema a feature may read. The build exposes exactly these columns to its SQL (the
`versions` view of `trialpulse.features.states`), so a field outside the list cannot be
read by accident. Left out on purpose:

- `why_stopped_hash`: label-only information (rule 3);
- `effective_date_type` and `study_first_post_date_type`: the registry recorded post dates
  from 2017 on and estimated them before, so the type would stand in for calendar time
  (`docs/eda.md`, finding 7);
- `start_date_type`: the registry recorded whether a start date is actual or anticipated
  from 2017 on only (no version with a start date carries it through 2016, about half in
  2017, nearly all afterwards), so its presence would stand in for calendar time too.
  `start_anticipated` derives the same thing from the start date in every year;
- `lead_sponsor_name`: a name; identity comes from `sponsor_key` (ADR 0018);
- `submitted_date` and `study_first_submit_date`: the version clock is the post date, when
  the public could see a version (Section 6), not the day it was submitted;
- `start_date_precision` and `completion_date_type`: no feature reads them. Date precision
  must not be a feature (ADR 0021), and a field joins the whitelist only with a feature
  that names it as a source. A test requires every whitelisted field to be so named;
- `source` and `content_hash`: bookkeeping of the pipeline, not registry content.

The view the queries read also shows the submitted status in `overall_status` and nothing in
`last_known_status` (see `trialpulse.features.states`), so the registry's UNKNOWN label,
which is computed long after the version it sits on, cannot be read either.

Intervention types come from the `interventions` config of the same dataset revision, which
is per version (ADR 0020). A field without version history may appear only in the
sensitivity set (`main=False`), never in a model's main features (ADR 0006).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from trialpulse.reports import markdown_table

DESIGN = "design"
AMENDMENTS = "amendments"
SPONSOR = "sponsor"
COMPETITION = "competition"
TEXT = "text"
TIME = "time"
SENSITIVITY = "sensitivity"
FAMILIES: tuple[str, ...] = (DESIGN, AMENDMENTS, SPONSOR, COMPETITION, TEXT, TIME, SENSITIVITY)

NUMBER = "number"
FLAG = "flag"
CATEGORY = "category"

KEY_COLUMNS: tuple[str, ...] = ("trial_id", "landmark_index", "landmark_date")

# Fields of the canonical version schema that features may read.
ALLOWED_VERSION_FIELDS: tuple[str, ...] = (
    "nct_id",
    "nct_version",
    "effective_date",
    "overall_status",
    "last_known_status",
    "study_type",
    "study_first_post_date",
    "status_verified_date",
    "start_date",
    "primary_completion_date",
    "primary_completion_date_precision",
    "primary_completion_date_type",
    "completion_date",
    "completion_date_precision",
    "allocation",
    "intervention_model",
    "primary_purpose",
    "masking",
    "healthy_volunteers",
    "sex",
    "minimum_age_years",
    "maximum_age_years",
    "enrollment_count",
    "enrollment_type",
    "lead_sponsor_class",
    "organization_class",
    "sponsor_key",
    "sponsor_is_individual",
    "brief_title_hash",
    "official_title_hash",
    "brief_summary_hash",
    "eligibility_criteria_hash",
)
# Version fields that exist and must never reach a feature.
FORBIDDEN_VERSION_FIELDS: tuple[str, ...] = (
    "why_stopped_hash",
    "effective_date_type",
    "study_first_post_date_type",
    "start_date_type",
    "start_date_precision",
    "completion_date_type",
    "lead_sponsor_name",
    "source",
    "content_hash",
    "submitted_date",
    "study_first_submit_date",
)
# Per-version fields of the `interventions` config (ADR 0020).
INTERVENTION_FIELDS: tuple[str, ...] = ("intervention_type",)
# Current-record fields without version history: the sensitivity set only (ADR 0006).
CURRENT_RECORD_FIELDS: tuple[str, ...] = ("phases",)

INTERVENTION_TYPES: tuple[str, ...] = (
    "DRUG",
    "PROCEDURE",
    "OTHER",
    "BIOLOGICAL",
    "DEVICE",
    "BEHAVIORAL",
    "RADIATION",
    "DIETARY_SUPPLEMENT",
    "DIAGNOSTIC_TEST",
    "GENETIC",
    "COMBINATION_PRODUCT",
)
TEXT_COMPONENTS = 32  # per text field; equals features.text_components in the config
TEXT_FIELDS: Mapping[str, str] = {
    "eligibility": "eligibility_criteria_hash",
    "summary": "brief_summary_hash",
}


@dataclass(frozen=True)
class Feature:
    name: str
    family: str
    kind: str  # number, flag or category
    sources: tuple[str, ...]
    description: str
    versioned: bool = True  # every source field has version history
    fitted: bool = False  # fitted per origin on its training rows; not a column of the build
    main: bool = True  # False: the sensitivity set, never a model's main features


def _f(name: str, family: str, kind: str, sources: str, description: str) -> Feature:
    return Feature(name, family, kind, tuple(sources.split()), description)


_START = "start_date"
_PCD = "primary_completion_date"
_STATUS = "overall_status last_known_status"

_DESIGN: tuple[Feature, ...] = (
    _f("allocation", DESIGN, CATEGORY, "allocation", "Randomized, non-randomized or not given."),
    _f("intervention_model", DESIGN, CATEGORY, "intervention_model",
       "Single group, parallel, crossover, factorial or sequential."),
    _f("primary_purpose", DESIGN, CATEGORY, "primary_purpose",
       "Treatment, prevention, diagnostic and the other registry purposes."),
    _f("masking", DESIGN, CATEGORY, "masking", "None, single, double, triple or quadruple."),
    _f("healthy_volunteers", DESIGN, FLAG, "healthy_volunteers",
       "The trial accepts healthy volunteers."),
    _f("sex", DESIGN, CATEGORY, "sex", "All, female or male."),
    _f("minimum_age_years", DESIGN, NUMBER, "minimum_age_years",
       "Lower age limit in years; missing when none is given."),
    _f("maximum_age_years", DESIGN, NUMBER, "maximum_age_years",
       "Upper age limit in years; missing when none is given."),
    _f("enrollment_count", DESIGN, NUMBER, "enrollment_count",
       "The enrollment count: a target while its type is ESTIMATED, the number enrolled once "
       "it is ACTUAL. Read it together with enrollment_closed."),
    _f("planned_duration_months", DESIGN, NUMBER,
       f"{_START} {_PCD}",
       "Calendar months from the start month to the primary completion month."),
    _f("start_anticipated", DESIGN, FLAG, _START,
       "The start date is still anticipated: its month lies after the landmark's month."),
    _f("registered_after_start", DESIGN, FLAG, f"{_START} study_first_post_date",
       "The start month is earlier than the month of first posting (retrospective "
       "registration)."),
    _f("registration_lag_months", DESIGN, NUMBER, f"{_START} study_first_post_date",
       "Calendar months from the start month to the month of first posting; negative when "
       "the trial was registered before its start."),
    _f("registration_year", DESIGN, NUMBER, "study_first_post_date",
       "Year of first posting."),
    _f("title_phase", DESIGN, CATEGORY, "official_title_hash brief_title_hash",
       "The phase the title states (ADR 0006): from the official title, else the brief title; "
       "NONE when neither states one."),
    _f("n_interventions", DESIGN, NUMBER, "intervention_type",
       "Number of interventions listed (ADR 0020); 0 when none is listed."),
    _f("n_intervention_types", DESIGN, NUMBER, "intervention_type",
       "Number of distinct intervention types listed (ADR 0020)."),
    *(
        _f(f"intervention_{kind.lower()}", DESIGN, FLAG, "intervention_type",
           f"At least one intervention of type {kind} is listed (ADR 0020).")
        for kind in INTERVENTION_TYPES
    ),
)  # fmt: skip

_AMENDMENTS: tuple[Feature, ...] = (
    _f("status", AMENDMENTS, CATEGORY, _STATUS,
       "The submitted status at the landmark (ADR 0014): one of the open statuses."),
    _f("n_versions", AMENDMENTS, NUMBER, "nct_version effective_date",
       "Versions posted on or before the landmark."),
    _f("n_versions_recent", AMENDMENTS, NUMBER, "nct_version effective_date",
       "Versions posted in the 6 months up to the landmark."),
    _f("months_since_last_update", AMENDMENTS, NUMBER, "effective_date",
       "Calendar months from the month of the latest version to the landmark's month."),
    _f("primary_completion_slip_months", AMENDMENTS, NUMBER, _PCD,
       "Calendar months the primary completion date has moved against the first version; "
       "positive is later."),
    _f("completion_slip_months", AMENDMENTS, NUMBER, "completion_date",
       "Calendar months the completion date has moved against the first version."),
    _f("start_slip_months", AMENDMENTS, NUMBER, _START,
       "Calendar months the start date has moved against the first version."),
    _f("enrollment_closed", AMENDMENTS, FLAG, "enrollment_type",
       "The enrollment type is ACTUAL: enrollment has ended and the count is the number "
       "enrolled."),
    _f("enrollment_target_ratio", AMENDMENTS, NUMBER, "enrollment_count enrollment_type",
       "The enrollment target over the first version's target, while both are ESTIMATED."),
    _f("enrolled_to_first_target_ratio", AMENDMENTS, NUMBER, "enrollment_count enrollment_type",
       "The number enrolled over the first version's target, once the count is ACTUAL and "
       "the first version gave a target."),
    _f("ever_suspended", AMENDMENTS, FLAG, _STATUS,
       "Some version posted on or before the landmark shows SUSPENDED."),
    _f("currently_suspended", AMENDMENTS, FLAG, _STATUS,
       "The status at the landmark is SUSPENDED."),
    _f("start_overdue", AMENDMENTS, FLAG, f"{_STATUS} {_START}",
       "Still not yet recruiting although the start month ended before the landmark's month."),
    _f("primary_completion_overdue", AMENDMENTS, FLAG, f"{_PCD} primary_completion_date_type",
       "The primary completion date is still anticipated and its month ended before the "
       "landmark's month."),
    _f("primary_completion_reached", AMENDMENTS, FLAG, "primary_completion_date_type",
       "The registry shows the primary completion date as ACTUAL: it has been reached."),
    _f("months_since_status_verified", AMENDMENTS, NUMBER, "status_verified_date",
       "Calendar months from the status-verified month to the landmark's month."),
)  # fmt: skip

_SPONSOR: tuple[Feature, ...] = (
    _f("sponsor_class", SPONSOR, CATEGORY, "lead_sponsor_class",
       "Lead sponsor class at the landmark."),
    _f("organization_class", SPONSOR, CATEGORY, "organization_class",
       "Class of the organization that registered the record."),
    _f("sponsor_has_identity", SPONSOR, FLAG, "sponsor_key sponsor_is_individual",
       "The sponsor has a track record of its own: not an individual, a person-named "
       "sponsor or a placeholder name (ADR 0018)."),
    _f("sponsor_prior_registrations", SPONSOR, NUMBER,
       "sponsor_key study_type effective_date",
       "Other interventional trials the sponsor registered before the landmark, counting "
       "names it replaced (ADR 0018); missing without an identity."),
    _f("sponsor_prior_ended", SPONSOR, NUMBER,
       f"sponsor_key study_type effective_date {_STATUS}",
       "The sponsor's other interventional trials shown as completed, terminated or "
       "withdrawn just before the landmark; missing without an identity."),
    _f("sponsor_prior_stopped", SPONSOR, NUMBER,
       f"sponsor_key study_type effective_date {_STATUS}",
       "Of those, the trials shown as terminated or withdrawn."),
    Feature("sponsor_stop_rate", SPONSOR, NUMBER,
            ("sponsor_key", "lead_sponsor_class", "overall_status", "last_known_status"),
            "The sponsor's early-stop rate among its ended trials, smoothed toward the rate "
            "of its sponsor class with a prior weight of 10. The class rates are fitted on "
            "each origin's training rows. A sponsor without an identity gets the class rate.",
            fitted=True),
)  # fmt: skip

_COMPETITION: tuple[Feature, ...] = (
    _f("open_interventional_trials", COMPETITION, NUMBER,
       f"{_STATUS} study_type effective_date status_verified_date completion_date "
       f"completion_date_precision {_PCD} primary_completion_date_precision",
       "Interventional trials in the registry that are open at the landmark, the row's own "
       "trial included (ADR 0007). Lapsed records do not count as open; the lapse rule takes "
       "a completion date at the end of its precision period (ADR 0014), the one place where "
       "a date's precision is read."),
)  # fmt: skip

_TEXT: tuple[Feature, ...] = (
    _f("eligibility_n_inclusion", TEXT, NUMBER, "eligibility_criteria_hash",
       "Lines of inclusion criteria."),
    _f("eligibility_n_exclusion", TEXT, NUMBER, "eligibility_criteria_hash",
       "Lines of exclusion criteria; 0 when the text has no exclusion heading."),
    _f("eligibility_length", TEXT, NUMBER, "eligibility_criteria_hash",
       "Characters of the eligibility criteria."),
    _f("eligibility_n_thresholds", TEXT, NUMBER, "eligibility_criteria_hash",
       "Comparisons with a number in the criteria (lab and other numeric thresholds)."),
    _f("eligibility_age_span_years", TEXT, NUMBER, "minimum_age_years maximum_age_years",
       "Upper age limit minus lower age limit; missing when either is open."),
    *(
        Feature(f"{field}_svd_{i:02d}", TEXT, NUMBER, (column,),
                f"Component {i} of the {field} text: TF-IDF of the hashed words, reduced by "
                "truncated SVD fitted on each origin's training rows (ADR 0019).",
                fitted=True)
        for field, column in TEXT_FIELDS.items()
        for i in range(1, TEXT_COMPONENTS + 1)
    ),
)  # fmt: skip

_TIME: tuple[Feature, ...] = (
    _f("landmark_index", TIME, NUMBER, "study_first_post_date", "The landmark's index k."),
    _f("months_since_t0", TIME, NUMBER, "study_first_post_date",
       "Calendar months from first posting to the landmark."),
)  # fmt: skip

_SENSITIVITY: tuple[Feature, ...] = (
    Feature("phase_current_record", SENSITIVITY, CATEGORY, ("phases",),
            "The phase of the current registry record. It has no version history and may "
            "have been edited after the outcome, so it is kept apart for a labeled "
            "sensitivity analysis and is never a main feature (ADR 0006).",
            versioned=False, main=False),
)  # fmt: skip

FEATURES: tuple[Feature, ...] = (
    *_DESIGN,
    *_AMENDMENTS,
    *_SPONSOR,
    *_COMPETITION,
    *_TEXT,
    *_TIME,
    *_SENSITIVITY,
)
BY_NAME: Mapping[str, Feature] = {feature.name: feature for feature in FEATURES}


def built_columns() -> tuple[str, ...]:
    """The feature columns of the build's table, beside its key columns: the main features
    that are computed as of the landmark (not fitted per origin)."""
    return tuple(f.name for f in FEATURES if f.main and not f.fitted and f.name not in KEY_COLUMNS)


def fitted_columns() -> tuple[str, ...]:
    return tuple(f.name for f in FEATURES if f.fitted)


def model_columns() -> tuple[str, ...]:
    """Every main feature a model may use, in registry order."""
    return tuple(f.name for f in FEATURES if f.main)


def matrix_columns() -> tuple[str, ...]:
    """The columns of an origin's feature matrix (`trialpulse.features.frame`): the keys,
    then every main feature. `landmark_index` is both a key and a feature and appears once."""
    return (*KEY_COLUMNS, *(name for name in model_columns() if name not in KEY_COLUMNS))


def check_registry() -> None:
    """The registry's own rules: unique names, known families, and sources on the
    whitelist. A feature outside the whitelist must be marked unversioned and kept out of
    the main features."""
    names = [f.name for f in FEATURES]
    if len(names) != len(set(names)):
        raise ValueError("feature names must be unique")
    allowed = {*ALLOWED_VERSION_FIELDS, *INTERVENTION_FIELDS}
    for feature in FEATURES:
        if feature.family not in FAMILIES:
            raise ValueError(f"{feature.name}: unknown family {feature.family}")
        if feature.kind not in (NUMBER, FLAG, CATEGORY):
            raise ValueError(f"{feature.name}: unknown kind {feature.kind}")
        outside = set(feature.sources) - allowed
        if feature.versioned and outside:
            raise ValueError(f"{feature.name} reads fields outside the whitelist: {outside}")
        if not feature.versioned and feature.main:
            raise ValueError(f"{feature.name} has no version history and cannot be a main feature")
        if not feature.sources or not feature.description:
            raise ValueError(f"{feature.name} needs source fields and a description")


def features_markdown(null_rates: Mapping[str, float | None], notes: Sequence[str]) -> str:
    """docs/features.md: one table per family, from the registry."""
    check_registry()
    lines = [
        "# Features",
        "",
        "Research demo. Not medical advice. Not for patient decision-making.",
        "",
        "This file is generated by `uv run python -m trialpulse.features.build` from "
        "`src/trialpulse/features/registry.py` (CLAUDE.md Step 7). Do not edit it by hand.",
        "",
        *notes,
        "",
    ]
    for family in FAMILIES:
        members = [f for f in FEATURES if f.family == family]
        if not members:
            continue
        lines += [f"## {family.capitalize()}", ""]
        rows = []
        for feature in members:
            rate = null_rates.get(feature.name)
            how = "fitted per origin" if feature.fitted else "as of the landmark"
            if not feature.main:
                how = "sensitivity only"
            rows.append(
                [
                    f"`{feature.name}`",
                    feature.kind,
                    ", ".join(f"`{s}`" for s in feature.sources),
                    "yes" if feature.versioned else "no",
                    how,
                    "" if rate is None else f"{rate:.1%}",
                    feature.description,
                ]
            )
        lines += markdown_table(
            ["Feature", "Kind", "Source fields", "Versioned", "Computed", "Missing", "Description"],
            rows,
        )
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
