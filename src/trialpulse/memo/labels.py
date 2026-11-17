"""Plain-English labels and display values of the served features (ADR 0084, memo_v2).

The memo names a driver by a short label and shows its value as a reader would say it, never
by its column name or raw value. This table covers the 54 features of the served champion
(every main feature of the registry outside the text families, ADR 0026), written from the
registry's descriptions (docs/features.md). Each entry has a label and a kind, and the kind
says how a stored value (the text `explain.drivers.shown` made of it) is displayed:

- `category`: a level in plain words (RECRUITING is "recruiting");
- `flag`: "yes" or "no" as a phrase of its own;
- `count`: a whole number, with thousands separators from 1,000;
- `months`: "N months" (a duration or an age in months);
- `slip`: a date's move against the first version, "N months later" or "N months earlier";
- `lag`: registration against the start, "registered N months before the start";
- `years`: "N years" (an age limit);
- `year`: a calendar year as it is;
- `percent`: a ratio or rate as a percent with one decimal, with an optional suffix;
- `step`: the landmark index as "step N".

A missing value ("not given") is shown as "not given" for every kind. The display is a pure
function of the feature and its stored text, so it is deterministic. Labels hold no digit,
no number word and no word the fabrication check refuses, and no label is part of another
feature's label or display (tests hold all of this), so the check can find each driver's
sentence by its label.

The fields whose before and after values a memo shows (`payload.CHANGE_FIELDS`) have labels
and displays here too.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from trialpulse.features.registry import BY_NAME, INTERVENTION_TYPES, TEXT, model_columns

NOT_GIVEN = "not given"
CATEGORY = "category"
FLAG = "flag"
COUNT = "count"
MONTHS = "months"
SLIP = "slip"
LAG = "lag"
YEARS = "years"
YEAR = "year"
PERCENT = "percent"
STEP = "step"


@dataclass(frozen=True)
class Label:
    """How the memo names a feature and shows its value."""

    label: str
    kind: str
    levels: Mapping[str, str] = field(default_factory=dict)  # category: stored level to words
    yes: str = ""  # flag: the phrase for yes
    no: str = ""  # flag: the phrase for no
    suffix: str = ""  # percent: words after the percent


STATUS_WORDS: dict[str, str] = {
    "NOT_YET_RECRUITING": "not yet recruiting",
    "RECRUITING": "recruiting",
    "ENROLLING_BY_INVITATION": "enrolling by invitation",
    "ACTIVE_NOT_RECRUITING": "active and not recruiting",
    "SUSPENDED": "suspended",
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
# comparisons to the check, so the roles are counted in digits.
MASKING_WORDS = {"NONE": "open label", "SINGLE": "masked for 1 role",
                 "DOUBLE": "masked for 2 roles", "TRIPLE": "masked for 3 roles",
                 "QUADRUPLE": "masked for 4 roles"}  # fmt: skip
SEX_WORDS = {"ALL": "all", "FEMALE": "female only", "MALE": "male only"}
ENROLLMENT_TYPE_WORDS = {"ACTUAL": "actual", "ESTIMATED": "estimated"}
TYPE_WORDS = {kind: kind.lower().replace("_", " ") for kind in INTERVENTION_TYPES}


def _type_flag(kind: str) -> Label:
    words = TYPE_WORDS[kind]
    return Label(f"{words} intervention", FLAG, yes="listed", no="not listed")


FEATURES: dict[str, Label] = {
    # design
    "allocation": Label("allocation", CATEGORY, ALLOCATION_WORDS),
    "intervention_model": Label("intervention model", CATEGORY, MODEL_WORDS),
    "primary_purpose": Label("primary purpose", CATEGORY, PURPOSE_WORDS),
    "masking": Label("masking", CATEGORY, MASKING_WORDS),
    "healthy_volunteers": Label("healthy volunteers", FLAG, yes="accepted", no="not accepted"),
    "sex": Label("sexes eligible", CATEGORY, SEX_WORDS),
    "minimum_age_years": Label("minimum age", YEARS),
    "maximum_age_years": Label("maximum age", YEARS),
    "enrollment_count": Label("enrollment count", COUNT),
    "planned_duration_months": Label("planned duration", MONTHS),
    "start_anticipated": Label("anticipated start", FLAG,
                               yes="the start date is still ahead",
                               no="the start date is no longer ahead"),
    "registered_after_start": Label("registration after the start", FLAG,
                                    yes="registered after the trial started",
                                    no="registered before the trial started"),
    "registration_lag_months": Label("registration lag", LAG),
    "registration_year": Label("registration year", YEAR),
    "title_phase": Label("phase stated in the title", CATEGORY, PHASE_WORDS),
    "n_interventions": Label("number of interventions", COUNT),
    "n_intervention_types": Label("number of intervention types", COUNT),
    **{f"intervention_{kind.lower()}": _type_flag(kind) for kind in INTERVENTION_TYPES},
    # amendment signals
    "status": Label("recruitment status", CATEGORY, STATUS_WORDS),
    "n_versions": Label("versions posted", COUNT),
    "n_versions_recent": Label("recent versions", COUNT),
    "months_since_last_update": Label("time since the last update", MONTHS),
    "primary_completion_slip_months": Label("primary completion date slip", SLIP),
    "completion_slip_months": Label("study completion date slip", SLIP),
    "start_slip_months": Label("start date slip", SLIP),
    "enrollment_closed": Label("enrollment closed", FLAG, yes="enrollment has ended",
                               no="enrollment is still open"),
    "enrollment_target_ratio": Label("enrollment target change", PERCENT,
                                     suffix=" of the first target"),
    "enrolled_to_first_target_ratio": Label("number enrolled against the first target", PERCENT,
                                            suffix=" of the first target"),
    "ever_suspended": Label("earlier suspension", FLAG, yes="suspended at some point",
                            no="never suspended"),
    "currently_suspended": Label("current suspension", FLAG, yes="suspended now",
                                 no="not suspended now"),
    "start_overdue": Label("overdue start", FLAG,
                           yes="still not recruiting after its planned start",
                           no="not overdue"),
    "primary_completion_overdue": Label("overdue primary completion", FLAG,
                                        yes="its anticipated primary completion has passed",
                                        no="not overdue"),
    "primary_completion_reached": Label("primary completion reached", FLAG, yes="reached",
                                        no="not reached"),
    "months_since_status_verified": Label("time since the status was verified", MONTHS),
    # sponsor
    "sponsor_class": Label("sponsor class", CATEGORY, CLASS_WORDS),
    "organization_class": Label("registering organization class", CATEGORY, CLASS_WORDS),
    "sponsor_has_identity": Label("sponsor track record", FLAG,
                                  yes="the sponsor has a record of its own",
                                  no="the sponsor has no record of its own"),
    "sponsor_prior_registrations": Label("earlier registrations by the sponsor", COUNT),
    "sponsor_prior_ended": Label("ended trials by the sponsor", COUNT),
    "sponsor_prior_stopped": Label("stopped trials by the sponsor", COUNT),
    "sponsor_stop_rate": Label("sponsor early-stop rate", PERCENT),
    # competition
    "open_interventional_trials": Label("open interventional trials in the registry", COUNT),
    # time
    "landmark_index": Label("landmark step since registration", STEP),
    "months_since_t0": Label("time since registration", MONTHS),
}  # fmt: skip

# The fields whose before and after values the memo shows (payload.CHANGE_FIELDS).
FIELDS: dict[str, Label] = {
    "status": FEATURES["status"],
    "start_date": Label("start date", "date"),
    "primary_completion_date": Label("primary completion date", "date"),
    "completion_date": Label("study completion date", "date"),
    "enrollment_count": FEATURES["enrollment_count"],
    "enrollment_type": Label("enrollment type", CATEGORY, ENROLLMENT_TYPE_WORDS),
    "allocation": FEATURES["allocation"],
    "intervention_model": FEATURES["intervention_model"],
    "primary_purpose": FEATURES["primary_purpose"],
    "masking": FEATURES["masking"],
    "healthy_volunteers": FEATURES["healthy_volunteers"],
    "sex": FEATURES["sex"],
    "minimum_age_years": FEATURES["minimum_age_years"],
    "maximum_age_years": FEATURES["maximum_age_years"],
    "lead_sponsor_class": FEATURES["sponsor_class"],
    "title_phase": FEATURES["title_phase"],
    "intervention_types": Label("intervention types", "types"),
}


def served_features() -> tuple[str, ...]:
    """The features of the served champion: every main feature but the text families."""
    return tuple(name for name in model_columns() if BY_NAME[name].family != TEXT)


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


def show(entry: Label, value: str) -> str:
    """A stored value (text, as the scores store it) displayed by its entry's kind."""
    text = str(value).strip()
    if text in ("", NOT_GIVEN, "none", "None", "nan"):
        return NOT_GIVEN
    kind = entry.kind
    if kind == CATEGORY:
        return entry.levels.get(text, text.lower().replace("_", " "))
    if kind == FLAG:
        if text.lower() in ("yes", "true", "1", "1.0"):
            return entry.yes
        if text.lower() in ("no", "false", "0", "0.0"):
            return entry.no
        return NOT_GIVEN
    if kind == "date":
        return text
    if kind == "types":
        words = [TYPE_WORDS.get(t.strip(), t.strip().lower()) for t in text.split(",")]
        return ", ".join(w for w in words if w) or "none listed"
    x = _number(text)
    if x is None:
        return text
    if kind == COUNT:
        return _plain(x)
    if kind == MONTHS:
        return _months(x)
    if kind == SLIP:
        if x == 0:
            return "unchanged from the first version"
        side = "later" if x > 0 else "earlier"
        return f"{_months(abs(x))} {side} than in the first version"
    if kind == LAG:
        if x == 0:
            return "registered in the start month"
        side = "after" if x > 0 else "before"
        return f"registered {_months(abs(x))} {side} the start"
    if kind == YEARS:
        return f"{_plain(x)} year" if x == 1 else f"{_plain(x)} years"
    if kind == YEAR:
        return str(int(x)) if x.is_integer() else text
    if kind == PERCENT:
        return f"{x * 100:.1f}%{entry.suffix}"
    if kind == STEP:
        return f"step {_plain(x)}"
    raise ValueError(f"unknown kind {kind}")


def feature_label(name: str) -> str:
    return FEATURES[name].label


def feature_display(name: str, value: str) -> str:
    return show(FEATURES[name], value)


def field_label(name: str) -> str:
    return FIELDS[name].label


def field_display(name: str, value: str) -> str:
    return show(FIELDS[name], value)
