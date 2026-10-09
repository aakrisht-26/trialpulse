"""The canonical version schema (CLAUDE.md Step 3): one row per registry version, the same for
the offline history dataset and live API v2 records, so downstream code is source-agnostic.

- Enums use the API v2 names. Older spellings are mapped (ANTICIPATED is today's ESTIMATED).
  Any other value fails validation and the row is quarantined: counted, never silently kept.
- A partial date is stored as its first day plus a precision (`day`, `month` or `year`).
  Placeholder dates (before 1901, or from 2100 on) are stored as missing.
- Ages are stored in years ("6 Months" is 0.5).
- Free text is stored once, normalized (trialpulse.contracts.text), in the warehouse's texts
  table; a version holds the text's SHA-256.
- Sponsors follow trialpulse.contracts.sponsor: no individual's name is stored, and no key
  that opens with a personal title and holds no organization word (ADR 0022).
- `last_known_status` is the submitted status behind the registry's UNKNOWN: when the
  registry shows UNKNOWN (a status it computes, never one a sponsor submits), both sources
  keep the submitted status in this column (API v2 `lastKnownStatus`). It is empty otherwise.
- `content_hash` is a SHA-256 of the canonical content: every column except the version
  number, the source and the hash itself. The same function serves both paths, so an
  unchanged record gives the same hash from either source.
"""

import datetime as dt
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd
import pandera.pandas as pa

from trialpulse.contracts.sponsor import opens_with_personal_title, sponsor
from trialpulse.contracts.text import EMAIL_PATTERN, normalize_text, text_hash

STATUSES: tuple[str, ...] = (
    "NOT_YET_RECRUITING",
    "RECRUITING",
    "ENROLLING_BY_INVITATION",
    "ACTIVE_NOT_RECRUITING",
    "SUSPENDED",
    "TERMINATED",
    "WITHDRAWN",
    "COMPLETED",
    "UNKNOWN",
    "WITHHELD",
    # expanded access records
    "AVAILABLE",
    "NO_LONGER_AVAILABLE",
    "TEMPORARILY_NOT_AVAILABLE",
    "APPROVED_FOR_MARKETING",
)
SPONSOR_CLASSES: tuple[str, ...] = (
    "INDUSTRY", "NIH", "FED", "OTHER_GOV", "NETWORK", "INDIV", "OTHER", "UNKNOWN", "AMBIG",
)  # fmt: skip
DATE_TYPES: tuple[str, ...] = ("ACTUAL", "ESTIMATED")
ENUMS: dict[str, tuple[str, ...]] = {
    "overall_status": STATUSES,
    "last_known_status": STATUSES,
    "study_type": ("INTERVENTIONAL", "OBSERVATIONAL", "EXPANDED_ACCESS"),
    "allocation": ("RANDOMIZED", "NON_RANDOMIZED", "NA"),
    "intervention_model": ("PARALLEL", "SINGLE_GROUP", "CROSSOVER", "SEQUENTIAL", "FACTORIAL"),
    "primary_purpose": (
        "TREATMENT",
        "PREVENTION",
        "DIAGNOSTIC",
        "SUPPORTIVE_CARE",
        "SCREENING",
        "HEALTH_SERVICES_RESEARCH",
        "BASIC_SCIENCE",
        "DEVICE_FEASIBILITY",
        "ECT",  # educational, counseling, training: an older registry value
        "OTHER",
    ),
    "masking": ("NONE", "SINGLE", "DOUBLE", "TRIPLE", "QUADRUPLE"),
    "sex": ("ALL", "FEMALE", "MALE"),
    "enrollment_type": DATE_TYPES,
    "effective_date_type": DATE_TYPES,
    "study_first_post_date_type": DATE_TYPES,
    "start_date_type": DATE_TYPES,
    "primary_completion_date_type": DATE_TYPES,
    "completion_date_type": DATE_TYPES,
    "lead_sponsor_class": SPONSOR_CLASSES,
    "organization_class": SPONSOR_CLASSES,
}
# Older spellings of today's values.
ENUM_ALIASES: dict[str, str] = {"ANTICIPATED": "ESTIMATED", "N/A": "NA"}
SOURCES: tuple[str, ...] = ("history", "api_v2")
PRECISIONS: tuple[str, ...] = ("day", "month", "year")
PRECISION_DATES: tuple[str, ...] = ("start_date", "primary_completion_date", "completion_date")
TEXT_FIELDS: tuple[str, ...] = (
    "brief_title",
    "official_title",
    "brief_summary",
    "eligibility_criteria",
    "why_stopped",
)
FIRST_VALID_DATE = dt.date(1901, 1, 1)
FIRST_INVALID_DATE = dt.date(2100, 1, 1)
AGE_UNITS_IN_YEARS: dict[str, float] = {
    "year": 1.0,
    "month": 1 / 12,
    "week": 7 / 365.25,
    "day": 1 / 365.25,
    "hour": 1 / (365.25 * 24),
    "minute": 1 / (365.25 * 24 * 60),
}
_AGE = re.compile(r"^(\d+(?:\.\d+)?)\s+(year|month|week|day|hour|minute)s?$", re.IGNORECASE)

# Column name -> storage type (DuckDB and PostgreSQL spell these types the same way).
COLUMN_TYPES: dict[str, str] = {
    "nct_id": "VARCHAR",
    "nct_version": "BIGINT",
    "source": "VARCHAR",
    "effective_date": "DATE",
    "effective_date_type": "VARCHAR",
    "submitted_date": "DATE",
    "overall_status": "VARCHAR",
    "last_known_status": "VARCHAR",
    "study_type": "VARCHAR",
    "study_first_post_date": "DATE",
    "study_first_post_date_type": "VARCHAR",
    "study_first_submit_date": "DATE",
    "status_verified_date": "DATE",
    **{
        f"{name}{suffix}": kind
        for name in PRECISION_DATES
        for suffix, kind in (("", "DATE"), ("_precision", "VARCHAR"), ("_type", "VARCHAR"))
    },
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
    "lead_sponsor_name": "VARCHAR",
    "sponsor_key": "VARCHAR",
    "sponsor_is_individual": "BOOLEAN",
    **{f"{field}_hash": "VARCHAR" for field in TEXT_FIELDS},
    "content_hash": "VARCHAR",
}
CANONICAL_COLUMNS: tuple[str, ...] = tuple(COLUMN_TYPES)
# Everything that describes the version's content; the content hash covers exactly these.
CONTENT_COLUMNS: tuple[str, ...] = tuple(
    c for c in CANONICAL_COLUMNS if c not in ("nct_version", "source", "content_hash")
)


def canonical_enum(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    return ENUM_ALIASES.get(text, text) or None


@dataclass(frozen=True)
class PartialDate:
    value: dt.date | None
    precision: str | None


def valid_date(value: dt.date | None) -> dt.date | None:
    """The date, or None for a placeholder (before 1901, or from 2100 on)."""
    return value if value is not None and FIRST_VALID_DATE <= value < FIRST_INVALID_DATE else None


def parse_partial_date(text: str | None) -> PartialDate:
    """'2019', '2019-03' or '2019-03-14' as the period's first day plus a precision."""
    if not text:
        return PartialDate(None, None)
    parts = str(text).strip()[:10].split("-")
    precision = {1: "year", 2: "month", 3: "day"}.get(len(parts))
    if precision is None:
        return PartialDate(None, None)
    try:
        year, month, day = ([int(p) for p in parts] + [1, 1])[:3]
        value = valid_date(dt.date(year, month, day))
    except ValueError:
        return PartialDate(None, None)
    return PartialDate(value, precision) if value else PartialDate(None, None)


def age_in_years(text: str | None) -> float | None:
    """'18 Years' -> 18.0, '6 Months' -> 0.5. 'N/A', blanks and unknown formats are missing."""
    if text is None:
        return None
    match = _AGE.match(" ".join(str(text).split()))
    if match is None:
        return None
    return float(match.group(1)) * AGE_UNITS_IN_YEARS[match.group(2).lower()]


def _render(values: pd.Series, kind: str) -> pd.Series:
    """How content_hash renders one column: by its declared type, so a value hashes the same
    whatever pandas or Python type it arrives as."""
    present = values.notna()
    out = pd.Series("", index=values.index, dtype=object)
    if not present.any():
        return out
    kept = values[present]
    if kind == "DATE":
        rendered = pd.to_datetime(kept).dt.strftime("%Y-%m-%d")
    elif kind == "BOOLEAN":
        rendered = kept.map(lambda v: "true" if bool(v) else "false")
    elif kind == "BIGINT":
        rendered = kept.map(lambda v: str(int(v)))
    elif kind == "DOUBLE":
        rendered = kept.map(lambda v: repr(float(v)))
    else:
        rendered = kept.astype(str)
    out[present] = rendered
    return out


def content_hashes(frame: pd.DataFrame) -> list[str]:
    """The content hash of every row: a SHA-256 over the content columns, rendered by type and
    joined with a unit separator."""
    if frame.empty:
        return []
    columns = [_render(frame[c], COLUMN_TYPES[c]) for c in CONTENT_COLUMNS]
    joined = columns[0].str.cat(columns[1:], sep="\x1f")
    return [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in joined]


def content_hash(row: Mapping[str, Any]) -> str:
    frame = pd.DataFrame([{c: row.get(c) for c in CONTENT_COLUMNS}], dtype=object)
    return content_hashes(frame)[0]


def hash_text(raw: str | None) -> tuple[str | None, str | None]:
    """A raw text's normalized form and its hash (both None when nothing is left)."""
    clean = normalize_text(raw)
    return (clean, text_hash(clean)) if clean else (None, None)


# API v2 JSON paths under protocolSection. No path points into a module that holds names or
# contact details of people (the lead sponsor's name is reduced by contracts.sponsor).
API_V2_PATHS: dict[str, str] = {
    "nct_id": "identificationModule.nctId",
    "organization_class": "identificationModule.organization.class",
    "brief_title": "identificationModule.briefTitle",
    "official_title": "identificationModule.officialTitle",
    "overall_status": "statusModule.overallStatus",
    "last_known_status": "statusModule.lastKnownStatus",
    "why_stopped": "statusModule.whyStopped",
    "status_verified_date": "statusModule.statusVerifiedDate",
    "start_date": "statusModule.startDateStruct.date",
    "start_date_type": "statusModule.startDateStruct.type",
    "primary_completion_date": "statusModule.primaryCompletionDateStruct.date",
    "primary_completion_date_type": "statusModule.primaryCompletionDateStruct.type",
    "completion_date": "statusModule.completionDateStruct.date",
    "completion_date_type": "statusModule.completionDateStruct.type",
    "study_first_submit_date": "statusModule.studyFirstSubmitDate",
    "study_first_post_date": "statusModule.studyFirstPostDateStruct.date",
    "study_first_post_date_type": "statusModule.studyFirstPostDateStruct.type",
    "submitted_date": "statusModule.lastUpdateSubmitDate",
    "effective_date": "statusModule.lastUpdatePostDateStruct.date",
    "effective_date_type": "statusModule.lastUpdatePostDateStruct.type",
    "lead_sponsor_name": "sponsorCollaboratorsModule.leadSponsor.name",
    "lead_sponsor_class": "sponsorCollaboratorsModule.leadSponsor.class",
    "brief_summary": "descriptionModule.briefSummary",
    "study_type": "designModule.studyType",
    "allocation": "designModule.designInfo.allocation",
    "intervention_model": "designModule.designInfo.interventionModel",
    "primary_purpose": "designModule.designInfo.primaryPurpose",
    "masking": "designModule.designInfo.maskingInfo.masking",
    "enrollment_count": "designModule.enrollmentInfo.count",
    "enrollment_type": "designModule.enrollmentInfo.type",
    "eligibility_criteria": "eligibilityModule.eligibilityCriteria",
    "healthy_volunteers": "eligibilityModule.healthyVolunteers",
    "sex": "eligibilityModule.sex",
    "minimum_age": "eligibilityModule.minimumAge",
    "maximum_age": "eligibilityModule.maximumAge",
}
_PLAIN_DATES = (
    "effective_date",
    "submitted_date",
    "study_first_post_date",
    "study_first_submit_date",
    "status_verified_date",
)
_PLAIN_ENUMS = (
    "last_known_status",
    "effective_date_type",
    "overall_status",
    "study_type",
    "study_first_post_date_type",
    "allocation",
    "intervention_model",
    "primary_purpose",
    "masking",
    "sex",
    "enrollment_type",
    "lead_sponsor_class",
    "organization_class",
)


def api_v2_fields() -> str:
    """The `fields` parameter that requests exactly what canonical_from_api_v2 reads."""
    return ",".join(f"protocolSection.{path}" for path in API_V2_PATHS.values())


def canonical_from_api_v2(study: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """One API v2 study record as a canonical row (source api_v2, no version number), plus
    its normalized texts keyed by hash."""

    def get(name: str) -> Any:
        node: Any = study.get("protocolSection", {})
        for key in API_V2_PATHS[name].split("."):
            if not isinstance(node, Mapping):
                return None
            node = node.get(key)
        return node

    row: dict[str, Any] = {"nct_id": get("nct_id"), "nct_version": None, "source": "api_v2"}
    for name in _PLAIN_DATES:
        row[name] = parse_partial_date(get(name)).value
    for name in _PLAIN_ENUMS:
        row[name] = canonical_enum(get(name))
    for name in PRECISION_DATES:
        parsed = parse_partial_date(get(name))
        row[name], row[f"{name}_precision"] = parsed.value, parsed.precision
        row[f"{name}_type"] = canonical_enum(get(f"{name}_type")) if parsed.value else None
    healthy = get("healthy_volunteers")
    row["healthy_volunteers"] = None if healthy is None else bool(healthy)
    row["minimum_age_years"] = age_in_years(get("minimum_age"))
    row["maximum_age_years"] = age_in_years(get("maximum_age"))
    count = get("enrollment_count")
    row["enrollment_count"] = None if count is None else int(count)
    lead = sponsor(get("lead_sponsor_name"), row["lead_sponsor_class"])
    row["lead_sponsor_name"], row["sponsor_key"] = lead.name, lead.key
    row["sponsor_is_individual"] = lead.is_individual
    texts: dict[str, str] = {}
    for field in TEXT_FIELDS:
        clean, key = hash_text(get(field))
        row[f"{field}_hash"] = key
        if clean and key:
            texts[key] = clean
    row = {column: row.get(column) for column in CANONICAL_COLUMNS}
    row["content_hash"] = content_hash(row)
    return row, texts


# Validation --------------------------------------------------------------------------


def _enum(name: str) -> pa.Column:
    return pa.Column(str, pa.Check.isin(ENUMS[name]), nullable=True)


def _date(nullable: bool = True) -> pa.Column:
    return pa.Column("datetime64[ns]", nullable=nullable, coerce=True)


def _hash(nullable: bool = True) -> pa.Column:
    return pa.Column(str, pa.Check.str_matches(r"^[0-9a-f]{64}$"), nullable=nullable)


def _history_has_version(df: pd.DataFrame) -> pd.Series:
    return ~(df["source"].eq("history") & df["nct_version"].isna())


def _unique_history_version(df: pd.DataFrame) -> pd.Series:
    history = df["source"].eq("history")
    return ~(history & df.duplicated(["nct_id", "nct_version"], keep=False))


def _no_individual_name(df: pd.DataFrame) -> pd.Series:
    individual = df["sponsor_is_individual"].astype(bool)
    return ~(individual & (df["lead_sponsor_name"].notna() | df["sponsor_key"].notna()))


def _no_personal_title_key(df: pd.DataFrame) -> pd.Series:
    """No stored sponsor key is a person named by title (ADR 0022). `sponsor()` never
    returns one; this keeps a row built any other way out of the warehouse."""
    return ~df["sponsor_key"].map(opens_with_personal_title).astype(bool)


def _last_known_status_only_behind_unknown(df: pd.DataFrame) -> pd.Series:
    return df["last_known_status"].isna() | df["overall_status"].eq("UNKNOWN")


def _precision_matches_date(df: pd.DataFrame) -> pd.Series:
    ok = pd.Series(True, index=df.index)
    for name in PRECISION_DATES:
        ok &= df[name].isna() == df[f"{name}_precision"].isna()
        ok &= ~(df[name].isna() & df[f"{name}_type"].notna())
    return ok


VERSION_SCHEMA = pa.DataFrameSchema(
    {
        "nct_id": pa.Column(str, pa.Check.str_matches(r"^NCT\d{8}$")),
        "nct_version": pa.Column("Int64", pa.Check.ge(0), nullable=True, coerce=True),
        "source": pa.Column(str, pa.Check.isin(SOURCES)),
        "effective_date": _date(nullable=False),
        "effective_date_type": _enum("effective_date_type"),
        "submitted_date": _date(),
        "overall_status": pa.Column(str, pa.Check.isin(STATUSES)),
        "last_known_status": _enum("last_known_status"),
        "study_type": _enum("study_type"),
        "study_first_post_date": _date(),
        "study_first_post_date_type": _enum("study_first_post_date_type"),
        "study_first_submit_date": _date(),
        "status_verified_date": _date(),
        **{
            column: spec
            for name in PRECISION_DATES
            for column, spec in (
                (name, _date()),
                (f"{name}_precision", pa.Column(str, pa.Check.isin(PRECISIONS), nullable=True)),
                (f"{name}_type", _enum(f"{name}_type")),
            )
        },
        "allocation": _enum("allocation"),
        "intervention_model": _enum("intervention_model"),
        "primary_purpose": _enum("primary_purpose"),
        "masking": _enum("masking"),
        "healthy_volunteers": pa.Column("boolean", nullable=True, coerce=True),
        "sex": _enum("sex"),
        "minimum_age_years": pa.Column(float, pa.Check.ge(0), nullable=True, coerce=True),
        "maximum_age_years": pa.Column(float, pa.Check.ge(0), nullable=True, coerce=True),
        "enrollment_count": pa.Column("Int64", pa.Check.ge(0), nullable=True, coerce=True),
        "enrollment_type": _enum("enrollment_type"),
        "lead_sponsor_class": _enum("lead_sponsor_class"),
        "organization_class": _enum("organization_class"),
        "lead_sponsor_name": pa.Column(str, nullable=True),
        "sponsor_key": pa.Column(str, nullable=True),
        "sponsor_is_individual": pa.Column(bool, coerce=True),
        **{f"{field}_hash": _hash() for field in TEXT_FIELDS},
        "content_hash": _hash(nullable=False),
    },
    checks=[
        pa.Check(_history_has_version, name="history_row_has_version"),
        pa.Check(_unique_history_version, name="unique_history_version"),
        pa.Check(_no_individual_name, name="no_individual_sponsor_name"),
        pa.Check(_no_personal_title_key, name="no_personal_title_sponsor_key"),
        pa.Check(_precision_matches_date, name="precision_and_type_match_date"),
        pa.Check(
            _last_known_status_only_behind_unknown, name="last_known_status_only_behind_unknown"
        ),
    ],
    strict=True,
    ordered=True,
)


def _no_email(series: pd.Series) -> pd.Series:
    return ~series.str.contains(EMAIL_PATTERN.pattern, regex=True)


TEXT_SCHEMA = pa.DataFrameSchema(
    {
        "text_hash": pa.Column(str, pa.Check.str_matches(r"^[0-9a-f]{64}$"), unique=True),
        "text": pa.Column(str, pa.Check(_no_email, name="no_email_address")),
    },
    strict=True,
)


def typed_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Canonical columns in order, as pandas types that keep missing values missing (nullable
    integers, floats and booleans), with a fresh index."""
    kinds = {"BIGINT": "Int64", "DOUBLE": "Float64", "BOOLEAN": "boolean", "VARCHAR": object}
    out = pd.DataFrame(index=range(len(frame)))
    for column, kind in COLUMN_TYPES.items():
        values = (
            frame[column].reset_index(drop=True)
            if column in frame
            else pd.Series([None] * len(frame), dtype=object)
        )
        if kind == "DATE":
            out[column] = pd.to_datetime(values)
        else:
            out[column] = values.astype(kinds[kind])
    return out


def split_valid(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Validate canonical rows against VERSION_SCHEMA. Returns the valid rows and the failing
    rows with a `reasons` column ("column: check" items, sorted). A failure that is not tied to
    a row (a missing column, for example) is a bug rather than bad data, and raises."""
    try:
        VERSION_SCHEMA.validate(frame, lazy=True)
    except pa.errors.SchemaErrors as err:
        cases = err.failure_cases
        if cases["index"].isna().any():
            raise
        # A dataframe-wide check reports every column of a failing row: label it "row".
        row_level = cases["schema_context"].eq("DataFrameSchema") | cases["column"].isna()
        labels = cases["column"].astype(str).where(~row_level, "row")
        reasons = (
            cases.assign(reason=labels + ": " + cases["check"].astype(str))
            .groupby("index")["reason"]
            .agg(lambda r: "; ".join(sorted(set(r))))
        )
        failing = frame.loc[reasons.index].assign(reasons=reasons.to_numpy())
        return frame.drop(index=reasons.index), failing
    return frame, frame.iloc[0:0].assign(reasons=pd.Series(dtype=object))
