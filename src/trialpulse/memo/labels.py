"""Plain-English labels and display values of the served features (ADR 0084, memo_v3).

The memo names a driver by a short label and shows its value as a reader would say it, never
by its column name or raw value. This table covers the 54 features of the served champion
(every main feature of the registry outside the text families, ADR 0026), written from the
registry's descriptions (docs/features.md). Each entry has a label and a kind, and the kind
says how a stored value (the text `explain.drivers.shown` made of it) is displayed:

- `category`: a level in plain words (RECRUITING is "recruiting");
- `flag`: "yes" or "no" as a phrase of its own;
- `count`: a whole number, with thousands separators from 1,000;
- `enrollment`: the enrollment count with its type from the latest record ("10, the
  estimated target"; "2, the actual number enrolled");
- `months`: "N months" (a duration or an age in months);
- `moved`: a date's move against the first version in plain words ("39 months later than in
  the first version"), labeled by the date itself, never as a signed "slip";
- `start`: the start as now recorded against the first posting ("44 months after the first
  posting, any move of the start included"), so a start that moved is not read as an early
  registration by design;
- `years`: "N years" (an age limit);
- `year`: a calendar year as it is;
- `percent`: a ratio or rate as a percent with one decimal;
- `checkpoint`: the landmark the score is computed at, as "the 24-month checkpoint";
- `recent`: the versions of the recent window, "124 in the last 6 months".

A missing value ("not given" as stored) is shown as "not stated in the record" for every kind,
so a missing flag never reads as its "no". The display is a pure
function of the feature, its stored text and, for the enrollment count, the latest record's
enrollment type, so it is deterministic. Every label is a singular noun phrase, so "The
<label> is <value>" reads as English; labels hold no digit, no apostrophe, no number word and
no word the fabrication check refuses; and no label appears in another feature's label or
displayed value (tests hold all of this), so the check can find each driver's sentence by its
label and hold it to its value word for word.

The fields whose before and after values a memo shows (`FIELDS`, every field of
`live.contract.MARKER_GROUPS`) have labels and displays here too; the lead sponsor's change is
stated without values, since a sponsor id is never shown.
"""

import datetime as dt
import functools
import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from trialpulse.config import load_project_config
from trialpulse.features.registry import BY_NAME, INTERVENTION_TYPES, TEXT, model_columns

NOT_GIVEN = "not given"  # a missing value as the scores store it
MISSING = ("", NOT_GIVEN, "none", "None", "nan")
NOT_STATED = "not stated in the record"  # a missing value as the memo shows it
CATEGORY = "category"
FLAG = "flag"
COUNT = "count"
ENROLLMENT = "enrollment"
MONTHS = "months"
MOVED = "moved"
START = "start"
YEARS = "years"
YEAR = "year"
PERCENT = "percent"
CHECKPOINT = "checkpoint"
RECENT = "recent"
DATE = "date"  # a field's date, as stored (ISO)
MONTH = "month"  # a field's month, for the status verified date (YYYY-MM)
TYPES = "types"  # a field's intervention types
SPONSOR_ID = "sponsor_id"  # the lead sponsor's id: its change is stated, its values never


@dataclass(frozen=True)
class Label:
    """How the memo names a feature (or a field) and shows its value."""

    label: str
    kind: str
    levels: Mapping[str, str] = field(default_factory=dict)  # category: stored level to words
    yes: str = ""  # flag: the phrase for yes
    no: str = ""  # flag: the phrase for no


OPEN_STATUS_WORDS: dict[str, str] = {
    "NOT_YET_RECRUITING": "not yet recruiting",
    "RECRUITING": "recruiting",
    "ENROLLING_BY_INVITATION": "enrolling by invitation",
    "ACTIVE_NOT_RECRUITING": "active and not recruiting",
    "SUSPENDED": "suspended",
}
# A served trial's status is always open (ADR 0014); a change of the status field may also
# end in a terminal one.
STATUS_WORDS: dict[str, str] = {
    **OPEN_STATUS_WORDS,
    "COMPLETED": "completed",
    "TERMINATED": "terminated",
    "WITHDRAWN": "withdrawn",
    "UNKNOWN": "unknown",
}
CLASS_WORDS: dict[str, str] = {
    "INDUSTRY": "industry",
    "NIH": "US national research institutes",
    "FED": "US federal agency",
    "OTHER_GOV": "other government",
    "NETWORK": "research network",
    "INDIV": "individual",
    "OTHER": "other, mostly universities and hospitals",
    "UNKNOWN": "unknown",
    "AMBIG": "ambiguous",
}
PHASE_WORDS: dict[str, str] = {
    "NONE": "no phase stated",
    "EARLY_PHASE1": "early phase 1",
    "PHASE1": "phase 1",
    "PHASE1_PHASE2": "phase 1/2",
    "PHASE2": "phase 2",
    "PHASE2_PHASE3": "phase 2/3",
    "PHASE3": "phase 3",
    "PHASE4": "phase 4",
    "OTHER": "another combination of phases",
}
ALLOCATION_WORDS = {"RANDOMIZED": "randomized", "NON_RANDOMIZED": "not randomized",
                    "NA": "not applicable"}  # fmt: skip
MODEL_WORDS = {"PARALLEL": "parallel groups", "SINGLE_GROUP": "single group",
               "CROSSOVER": "crossover", "SEQUENTIAL": "sequential groups",
               "FACTORIAL": "factorial"}  # fmt: skip
PURPOSE_WORDS = {
    "TREATMENT": "treatment", "PREVENTION": "prevention", "DIAGNOSTIC": "diagnostic",
    "SUPPORTIVE_CARE": "supportive care", "SCREENING": "screening",
    "HEALTH_SERVICES_RESEARCH": "health services research", "BASIC_SCIENCE": "basic science",
    "DEVICE_FEASIBILITY": "device feasibility",
    "ECT": "educational, counseling or training", "OTHER": "other",
}  # fmt: skip
# The registry's masking levels count the masked roles; "double" and "triple" would read as
# comparisons to the check, so the label is the count and the levels are digits.
MASKING_WORDS = {"NONE": "none (open label)", "SINGLE": "1", "DOUBLE": "2", "TRIPLE": "3",
                 "QUADRUPLE": "4"}  # fmt: skip
SEX_WORDS = {"ALL": "all sexes", "FEMALE": "female only", "MALE": "male only"}
ENROLLMENT_TYPE_WORDS = {"ACTUAL": "actual", "ESTIMATED": "estimated"}
DATE_TYPE_WORDS = {"ACTUAL": "actual", "ESTIMATED": "anticipated"}
PRECISION_WORDS = {"day": "day", "month": "month", "year": "year"}
STUDY_TYPE_WORDS = {"INTERVENTIONAL": "interventional", "OBSERVATIONAL": "observational",
                    "EXPANDED_ACCESS": "expanded access"}  # fmt: skip
TYPE_WORDS = {kind: kind.lower().replace("_", " ") for kind in INTERVENTION_TYPES}


def _type_flag(kind: str) -> Label:
    words = TYPE_WORDS[kind]
    return Label(f"{words} intervention", FLAG, yes="listed", no="not listed")


FEATURES: dict[str, Label] = {
    # design
    "allocation": Label("allocation", CATEGORY, ALLOCATION_WORDS),
    "intervention_model": Label("intervention model", CATEGORY, MODEL_WORDS),
    "primary_purpose": Label("primary purpose", CATEGORY, PURPOSE_WORDS),
    "masking": Label("number of masked roles", CATEGORY, MASKING_WORDS),
    "healthy_volunteers": Label("admission of healthy volunteers", FLAG, yes="allowed",
                                no="not allowed"),
    "sex": Label("sex eligibility", CATEGORY, SEX_WORDS),
    "minimum_age_years": Label("minimum age", YEARS),
    "maximum_age_years": Label("maximum age", YEARS),
    "enrollment_count": Label("enrollment count", ENROLLMENT),
    "planned_duration_months": Label("planned time from start to primary completion", MONTHS),
    "start_anticipated": Label("start of the trial as recorded", FLAG, yes="still ahead",
                               no="this month or earlier"),
    "registered_after_start": Label("timing of the first posting", FLAG,
                                    yes="after the recorded start month (registered late)",
                                    no="not after the recorded start month"),
    "registration_lag_months": Label("start as now recorded", START),
    "registration_year": Label("year of the first posting", YEAR),
    "title_phase": Label("phase stated in the title", CATEGORY, PHASE_WORDS),
    "n_interventions": Label("number of interventions listed", COUNT),
    "n_intervention_types": Label("number of intervention types listed", COUNT),
    **{f"intervention_{kind.lower()}": _type_flag(kind) for kind in INTERVENTION_TYPES},
    # amendment signals
    "status": Label("recruitment status", CATEGORY, OPEN_STATUS_WORDS),
    "n_versions": Label("total number of versions posted", COUNT),
    "n_versions_recent": Label("number of recent versions", RECENT),
    "months_since_last_update": Label("time since the last update", MONTHS),
    "primary_completion_slip_months": Label("primary completion date", MOVED),
    "completion_slip_months": Label("study completion date", MOVED),
    "start_slip_months": Label("start date", MOVED),
    "enrollment_closed": Label("enrollment period", FLAG,
                               yes="closed, so the count is the number enrolled",
                               no="not closed, so the count is a target"),
    "enrollment_target_ratio": Label(
        "current enrollment target as a share of the target in the first version", PERCENT),
    "enrolled_to_first_target_ratio": Label(
        "number enrolled as a share of the target in the first version", PERCENT),
    "ever_suspended": Label("suspension history", FLAG,
                            yes="suspended at some point, now or earlier", no="never suspended"),
    "currently_suspended": Label("suspension status", FLAG, yes="suspended now",
                                 no="not suspended now"),
    "start_overdue": Label("start timing", FLAG,
                           yes="overdue, still not recruiting after the planned start month",
                           no="not overdue"),
    "primary_completion_overdue": Label(
        "primary completion timing", FLAG,
        yes="overdue, the anticipated primary completion month has passed", no="not overdue"),
    "primary_completion_reached": Label("primary completion milestone", FLAG,
                                        yes="reached, as the registry shows",
                                        no="not shown as reached in the registry"),
    "months_since_status_verified": Label("time since the status was last verified", MONTHS),
    # sponsor
    "sponsor_class": Label("sponsor class", CATEGORY, CLASS_WORDS),
    "organization_class": Label("class of the registering organization", CATEGORY, CLASS_WORDS),
    "sponsor_has_identity": Label("track record of the sponsor", FLAG, yes="available",
                                  no="not available, so the class rate is used"),
    "sponsor_prior_registrations": Label("number of earlier registrations by the sponsor", COUNT),
    "sponsor_prior_ended": Label(
        "number of earlier trials of the sponsor that ended, by completion or early stop", COUNT),
    "sponsor_prior_stopped": Label("number of earlier trials of the sponsor that stopped early",
                                   COUNT),
    "sponsor_stop_rate": Label("early-stop rate of the sponsor, smoothed toward its class rate",
                               PERCENT),
    # competition
    "open_interventional_trials": Label("number of open interventional trials in the registry",
                                        COUNT),
    # time (served at the last landmark passed, ADR 0034)
    "landmark_index": Label("registration checkpoint used by the score", CHECKPOINT),
    "months_since_t0": Label("time since the first posting, as the score counts it", MONTHS),
}  # fmt: skip

# The fields whose before and after values the memo shows: every field of an amendment
# marker's groups (live.contract.MARKER_GROUPS).
FIELDS: dict[str, Label] = {
    "status": Label("recruitment status", CATEGORY, STATUS_WORDS),
    "start_date": Label("start date", DATE),
    "primary_completion_date": Label("primary completion date", DATE),
    "primary_completion_date_precision": Label("precision of the primary completion date",
                                               CATEGORY, PRECISION_WORDS),
    "primary_completion_date_type": Label("primary completion date type", CATEGORY,
                                          DATE_TYPE_WORDS),
    "completion_date": Label("study completion date", DATE),
    "completion_date_precision": Label("precision of the study completion date", CATEGORY,
                                       PRECISION_WORDS),
    "status_verified_date": Label("status verified month", MONTH),
    "enrollment_count": Label("enrollment count", COUNT),
    "enrollment_type": Label("enrollment type", CATEGORY, ENROLLMENT_TYPE_WORDS),
    "study_type": Label("study type", CATEGORY, STUDY_TYPE_WORDS),
    "allocation": FEATURES["allocation"],
    "intervention_model": FEATURES["intervention_model"],
    "primary_purpose": FEATURES["primary_purpose"],
    "masking": FEATURES["masking"],
    "healthy_volunteers": FEATURES["healthy_volunteers"],
    "sex": FEATURES["sex"],
    "minimum_age_years": FEATURES["minimum_age_years"],
    "maximum_age_years": FEATURES["maximum_age_years"],
    "sponsor_id": Label("lead sponsor", SPONSOR_ID),
    "lead_sponsor_class": FEATURES["sponsor_class"],
    "organization_class": FEATURES["organization_class"],
    "title_phase": FEATURES["title_phase"],
    "intervention_types": Label("intervention types", TYPES),
}  # fmt: skip


def served_features() -> tuple[str, ...]:
    """The features of the served champion: every main feature but the text families."""
    return tuple(name for name in model_columns() if BY_NAME[name].family != TEXT)


@functools.cache
def spacing_months() -> int:
    """The months between landmarks (Section 6), from config/project.yaml."""
    return int(load_project_config().landmarks.spacing_months)


def _number(text: str) -> float | None:
    try:
        x = float(text.replace(",", "").strip())
    except ValueError:
        return None
    return None if math.isnan(x) else x


def _plain(x: float) -> str:
    """A number as digits: a whole number with separators from 1,000, else up to 3 decimals."""
    if float(x).is_integer():
        return f"{int(x):,}"
    return f"{x:,.3f}".rstrip("0").rstrip(".")


def _months(n: float) -> str:
    return f"{_plain(n)} month" if abs(n) == 1 else f"{_plain(n)} months"


def _month(text: str) -> str:
    """A stored date as its month, YYYY-MM (the registry gives the status verified date as a
    month)."""
    try:
        return dt.date.fromisoformat(text[:10]).strftime("%Y-%m")
    except ValueError:
        return text


def show(entry: Label, value: str, enrollment_type: str | None = None,
         precision: str | None = None) -> str:  # fmt: skip
    """A stored value (text, as the scores store it) displayed by its entry's kind; a date
    at its recorded precision when one is given ("2027-01" for a month)."""
    text = str(value).strip()
    if text in MISSING:
        return NOT_STATED
    kind = entry.kind
    if kind == CATEGORY:
        return entry.levels.get(text, text.lower().replace("_", " "))
    if kind == FLAG:
        if text.lower() in ("yes", "true", "1", "1.0"):
            return entry.yes
        if text.lower() in ("no", "false", "0", "0.0"):
            return entry.no
        return NOT_STATED
    if kind == DATE:
        return {"month": text[:7], "year": text[:4]}.get(str(precision or "").lower(), text)
    if kind == MONTH:
        return _month(text)
    if kind == TYPES:
        words = [TYPE_WORDS.get(t.strip(), t.strip().lower()) for t in text.split(",")]
        return ", ".join(w for w in words if w) or "none listed"
    x = _number(text)
    if x is None:
        return text
    if kind == COUNT:
        return _plain(x)
    if kind == ENROLLMENT:
        kinds = {"ESTIMATED": "the estimated target", "ACTUAL": "the actual number enrolled"}
        meant = kinds.get(str(enrollment_type or "").upper())
        return f"{_plain(x)}, {meant}" if meant else _plain(x)
    if kind == MONTHS:
        return _months(x)
    if kind == MOVED:
        if x == 0:
            return "in the same month as in the first version"
        side = "later" if x > 0 else "earlier"
        return f"{_months(abs(x))} {side} than in the first version"
    if kind == START:  # the months from the start month to the month of first posting
        if x == 0:
            return "in the month of the first posting"
        if x < 0:  # registered before the start
            return f"{_months(abs(x))} after the first posting, any move of the start included"
        return (f"{_months(x)} before the first posting (registered late), any move of the "
                "start included")  # fmt: skip
    if kind == YEARS:
        return f"{_plain(x)} year" if x == 1 else f"{_plain(x)} years"
    if kind == YEAR:
        return str(int(x)) if x.is_integer() else text
    if kind == PERCENT:
        return f"{x * 100:.1f}%"
    if kind == CHECKPOINT:
        if x == 0:
            return "the first posting itself"
        return f"the {_plain(x * spacing_months())}-month checkpoint"
    if kind == RECENT:
        return f"{_plain(x)} in the last {spacing_months()} months"
    raise ValueError(f"unknown kind {kind}")


def feature_label(name: str) -> str:
    return FEATURES[name].label


def feature_display(name: str, value: str, enrollment_type: str | None = None) -> str:
    """A feature's stored value as the memo shows it; the enrollment count reads the latest
    record's enrollment type."""
    return show(FEATURES[name], value, enrollment_type)


def field_label(name: str) -> str:
    return FIELDS[name].label


def field_display(name: str, value: str, precision: str | None = None) -> str:
    """A field's stored value as the memo shows it; a date at `precision` when given."""
    return show(FIELDS[name], value, precision=precision)
