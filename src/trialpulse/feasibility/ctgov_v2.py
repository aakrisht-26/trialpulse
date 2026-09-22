"""ClinicalTrials.gov API v2 access for the spike (part g, and the official side of part d)."""

import datetime as dt
import hashlib
import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from trialpulse.feasibility.fetch import JsonCache, JsonFetcher, scrub_personal_data

API_BASE = "https://clinicaltrials.gov/api/v2"
STUDIES_URL = f"{API_BASE}/studies"
VERSION_URL = f"{API_BASE}/version"
MAX_PAGE_SIZE = 1000  # larger values are silently clamped by the API
REQUESTS_PER_MINUTE = 40  # guidance is about 50 per minute per IP (CLAUDE.md Section 7)

_PS = "protocolSection"
_STATUS = f"{_PS}.statusModule"
_DESIGN = f"{_PS}.designModule"
_ELIG = f"{_PS}.eligibilityModule"
_ARMS = f"{_PS}.armsInterventionsModule"

# Every field TrialPulse plans to ingest from API v2: canonical name -> JSON path.
# No path points into a module that holds names or contact details of people.
FIELD_PATHS: dict[str, str] = {
    "nct_id": f"{_PS}.identificationModule.nctId",
    "organization_class": f"{_PS}.identificationModule.organization.class",
    "overall_status": f"{_STATUS}.overallStatus",
    "why_stopped": f"{_STATUS}.whyStopped",
    "status_verified_date": f"{_STATUS}.statusVerifiedDate",
    "start_date": f"{_STATUS}.startDateStruct.date",
    "start_date_type": f"{_STATUS}.startDateStruct.type",
    "primary_completion_date": f"{_STATUS}.primaryCompletionDateStruct.date",
    "primary_completion_date_type": f"{_STATUS}.primaryCompletionDateStruct.type",
    "completion_date": f"{_STATUS}.completionDateStruct.date",
    "completion_date_type": f"{_STATUS}.completionDateStruct.type",
    "study_first_post_date": f"{_STATUS}.studyFirstPostDateStruct.date",
    "last_update_post_date": f"{_STATUS}.lastUpdatePostDateStruct.date",
    "last_update_post_date_type": f"{_STATUS}.lastUpdatePostDateStruct.type",
    "lead_sponsor_name": f"{_PS}.sponsorCollaboratorsModule.leadSponsor.name",
    "lead_sponsor_class": f"{_PS}.sponsorCollaboratorsModule.leadSponsor.class",
    "study_type": f"{_DESIGN}.studyType",
    "phases": f"{_DESIGN}.phases",
    "allocation": f"{_DESIGN}.designInfo.allocation",
    "intervention_model": f"{_DESIGN}.designInfo.interventionModel",
    "primary_purpose": f"{_DESIGN}.designInfo.primaryPurpose",
    "masking": f"{_DESIGN}.designInfo.maskingInfo.masking",
    "enrollment_count": f"{_DESIGN}.enrollmentInfo.count",
    "enrollment_type": f"{_DESIGN}.enrollmentInfo.type",
    "brief_summary": f"{_PS}.descriptionModule.briefSummary",
    "eligibility_criteria": f"{_ELIG}.eligibilityCriteria",
    "healthy_volunteers": f"{_ELIG}.healthyVolunteers",
    "sex": f"{_ELIG}.sex",
    "minimum_age": f"{_ELIG}.minimumAge",
    "maximum_age": f"{_ELIG}.maximumAge",
    "conditions": f"{_PS}.conditionsModule.conditions",
    "intervention_types": f"{_ARMS}.interventions.type",
    "arm_group_labels": f"{_ARMS}.armGroups.label",
    "location_countries": f"{_PS}.contactsLocationsModule.locations.country",
    "condition_browse_branches": "derivedSection.conditionBrowseModule.browseBranches.abbrev",
    "condition_mesh_terms": "derivedSection.conditionBrowseModule.meshes.term",
    "condition_mesh_ancestors": "derivedSection.conditionBrowseModule.ancestors.term",
}

# Fields that only apply to some records, for honest presence rates.
_STOP_STATUSES = frozenset({"TERMINATED", "WITHDRAWN", "SUSPENDED"})


def _interventional(study: dict[str, Any]) -> bool:
    return bool(get_path(study, FIELD_PATHS["study_type"]) == "INTERVENTIONAL")


APPLICABILITY: dict[str, Callable[[dict[str, Any]], bool]] = {
    "why_stopped": lambda s: bool(get_path(s, FIELD_PATHS["overall_status"]) in _STOP_STATUSES),
    "phases": _interventional,
    "allocation": _interventional,
    "intervention_model": _interventional,
    "primary_purpose": _interventional,
    "masking": _interventional,
}

# Fields requested in the cohort-wide bulk pull (current-record-only candidates plus the
# fields that part d compares). Small on purpose: about 420K records.
BULK_FIELD_NAMES: tuple[str, ...] = (
    "nct_id",
    "study_type",
    "overall_status",
    "why_stopped",
    "study_first_post_date",
    "last_update_post_date",
    "start_date",
    "primary_completion_date",
    "enrollment_count",
    "lead_sponsor_class",
    "phases",
    "conditions",
    "intervention_types",
    "arm_group_labels",
    "location_countries",
    "condition_browse_branches",
    "condition_mesh_terms",
    "condition_mesh_ancestors",
)

# Parquet schema of the normalized bulk pull (DuckDB types).
BULK_COLUMNS: dict[str, str] = {
    "nct_id": "VARCHAR",
    "study_type": "VARCHAR",
    "overall_status": "VARCHAR",
    "why_stopped": "VARCHAR",
    "study_first_post_date": "VARCHAR",
    "last_update_post_date": "VARCHAR",
    "start_date": "VARCHAR",
    "primary_completion_date": "VARCHAR",
    "enrollment_count": "BIGINT",
    "lead_sponsor_class": "VARCHAR",
    "phases": "VARCHAR[]",
    "conditions": "VARCHAR[]",
    "browse_branches": "VARCHAR[]",
    "mesh_terms": "VARCHAR[]",
    "mesh_ancestors": "VARCHAR[]",
    "intervention_types": "VARCHAR[]",
    "n_interventions": "INTEGER",
    "n_arm_groups": "INTEGER",
    "n_locations": "INTEGER",
    "n_countries": "INTEGER",
}


def get_path(record: Any, path: str) -> Any:
    """Follow a dotted path. Lists met on the way are mapped over and flattened, so
    "a.items.name" returns the list of every item's name. Missing keys give None."""
    if isinstance(record, list):
        values: list[Any] = []
        for item in record:
            value = get_path(item, path)
            if value is None:
                continue
            values.extend(value if isinstance(value, list) else [value])
        return values
    head, _, rest = path.partition(".")
    if not isinstance(record, dict) or head not in record:
        return None
    value = record[head]
    return get_path(value, rest) if rest else value


def is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, list | dict):
        return len(value) > 0
    return True


def last_update_filter(since: dt.date) -> str:
    return f"AREA[LastUpdatePostDate]RANGE[{since.isoformat()},MAX]"


def cohort_filter(study_type: str, min_first_post_date: dt.date) -> str:
    return (
        f"AREA[StudyType]{study_type} AND "
        f"AREA[StudyFirstPostDate]RANGE[{min_first_post_date.isoformat()},MAX]"
    )


def fields_param(names: Iterable[str]) -> str:
    return ",".join(FIELD_PATHS[name] for name in names)


@dataclass(frozen=True)
class Page:
    index: int
    studies: list[dict[str, Any]]
    total_count: int | None
    from_cache: bool


def query_cache_prefix(name: str, params: dict[str, str | int]) -> str:
    """A cache folder per distinct query, so different queries never share pages."""
    digest = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]
    return f"{name}/{digest}"


def iter_pages(
    fetcher: JsonFetcher, cache: JsonCache, cache_prefix: str, params: dict[str, str | int]
) -> Iterator[Page]:
    """Yield every page of a /studies query, following nextPageToken until it is absent.

    Each page is cached with its nextPageToken, so an interrupted pull resumes from the
    last cached page. Page tokens can expire; if a resumed pull fails on a stale token,
    delete the query's cache folder and start again.
    """
    index = 0
    while True:
        key = f"{cache_prefix}/page_{index:05d}"
        cached = cache.get(key)
        from_cache = cached is not None
        if cached is None:
            request = dict(params)
            if index == 0:
                request["countTotal"] = "true"
            else:
                previous = cache.get(f"{cache_prefix}/page_{index - 1:05d}")
                if previous is None or not previous.get("nextPageToken"):
                    raise RuntimeError(f"missing page token to resume at page {index}")
                request["pageToken"] = previous["nextPageToken"]
            data = fetcher.get(STUDIES_URL, params=request)
            cached = {
                "studies": scrub_personal_data(data.get("studies", [])),
                "nextPageToken": data.get("nextPageToken"),
                "totalCount": data.get("totalCount"),
            }
            cache.put(key, cached)
        yield Page(index, cached["studies"], cached.get("totalCount"), from_cache)
        if not cached.get("nextPageToken"):
            return
        index += 1


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    return [str(item) for item in items if is_present(item)]


def normalize_bulk_record(study: dict[str, Any]) -> dict[str, Any]:
    """Flatten one bulk-pull study into the BULK_COLUMNS schema."""

    def field(name: str) -> Any:
        return get_path(study, FIELD_PATHS[name])

    interventions = _str_list(field("intervention_types"))
    countries = _str_list(field("location_countries"))
    return {
        "nct_id": field("nct_id"),
        "study_type": field("study_type"),
        "overall_status": field("overall_status"),
        "why_stopped": field("why_stopped"),
        "study_first_post_date": field("study_first_post_date"),
        "last_update_post_date": field("last_update_post_date"),
        "start_date": field("start_date"),
        "primary_completion_date": field("primary_completion_date"),
        "enrollment_count": field("enrollment_count"),
        "lead_sponsor_class": field("lead_sponsor_class"),
        "phases": sorted(_str_list(field("phases"))),
        "conditions": _str_list(field("conditions")),
        "browse_branches": sorted(set(_str_list(field("condition_browse_branches")))),
        "mesh_terms": sorted(set(_str_list(field("condition_mesh_terms")))),
        "mesh_ancestors": sorted(set(_str_list(field("condition_mesh_ancestors")))),
        "intervention_types": sorted(set(interventions)),
        "n_interventions": len(interventions),
        "n_arm_groups": len(_str_list(field("arm_group_labels"))),
        "n_locations": len(countries),
        "n_countries": len(set(countries)),
    }


def field_presence(studies: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """For each ingested field: its JSON path and how often it is present among the
    records it applies to."""
    result: dict[str, dict[str, Any]] = {}
    for name, path in FIELD_PATHS.items():
        applies = APPLICABILITY.get(name)
        applicable = [s for s in studies if applies is None or applies(s)]
        present = sum(1 for s in applicable if is_present(get_path(s, path)))
        result[name] = {
            "path": path,
            "applicable": len(applicable),
            "present": present,
            "share": present / len(applicable) if applicable else None,
        }
    return result
