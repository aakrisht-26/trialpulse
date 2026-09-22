"""API v2 helpers: paths, filters, resumable pagination and normalization."""

import datetime as dt
from typing import Any

import httpx
import respx

from trialpulse.feasibility import ctgov_v2
from trialpulse.feasibility.fetch import PERSONAL_DATA_KEYS, JsonCache, JsonFetcher


def _study(nct_id: str, status: str = "RECRUITING", **extra: Any) -> dict[str, Any]:
    ps: dict[str, Any] = {
        "identificationModule": {"nctId": nct_id},
        "statusModule": {
            "overallStatus": status,
            "lastUpdatePostDateStruct": {"date": "2026-09-20", "type": "ACTUAL"},
            "studyFirstPostDateStruct": {"date": "2012-01-05"},
            "startDateStruct": {"date": "2012-02"},
        },
        "designModule": {
            "studyType": "INTERVENTIONAL",
            "phases": ["PHASE3", "PHASE2"],
            "enrollmentInfo": {"count": 120, "type": "ESTIMATED"},
        },
        "sponsorCollaboratorsModule": {"leadSponsor": {"class": "INDUSTRY"}},
        "conditionsModule": {"conditions": ["Asthma", "COPD"]},
        "armsInterventionsModule": {
            "interventions": [{"type": "DRUG"}, {"type": "DRUG"}, {"type": "DEVICE"}],
            "armGroups": [{"label": "A"}, {"label": "B"}],
        },
        "contactsLocationsModule": {
            "locations": [{"country": "France"}, {"country": "France"}, {"country": "Chile"}]
        },
    }
    ps["statusModule"].update(extra)
    return {
        "protocolSection": ps,
        "derivedSection": {
            "conditionBrowseModule": {
                "browseBranches": [{"abbrev": "BXM"}, {"abbrev": "BC08"}, {"abbrev": "BXM"}]
            }
        },
    }


def test_get_path_maps_over_lists() -> None:
    study = _study("NCT1")
    assert ctgov_v2.get_path(study, ctgov_v2.FIELD_PATHS["nct_id"]) == "NCT1"
    assert ctgov_v2.get_path(study, ctgov_v2.FIELD_PATHS["location_countries"]) == [
        "France",
        "France",
        "Chile",
    ]
    assert ctgov_v2.get_path(study, "protocolSection.missing.path") is None
    assert ctgov_v2.get_path({"a": [{"b": 1}, {"c": 2}]}, "a.b") == [1]


def test_filters_use_area_syntax() -> None:
    assert (
        ctgov_v2.last_update_filter(dt.date(2026, 9, 16))
        == "AREA[LastUpdatePostDate]RANGE[2026-09-16,MAX]"
    )
    assert ctgov_v2.cohort_filter("INTERVENTIONAL", dt.date(2008, 1, 1)) == (
        "AREA[StudyType]INTERVENTIONAL AND AREA[StudyFirstPostDate]RANGE[2008-01-01,MAX]"
    )


def test_no_ingested_path_reaches_personal_data() -> None:
    forbidden = PERSONAL_DATA_KEYS | {"responsibleParty"}
    for path in ctgov_v2.FIELD_PATHS.values():
        assert not forbidden & set(path.split(".")), path


def test_normalize_bulk_record() -> None:
    row = ctgov_v2.normalize_bulk_record(_study("NCT1", "TERMINATED", whyStopped="Funding"))

    assert set(row) == set(ctgov_v2.BULK_COLUMNS)
    assert row["phases"] == ["PHASE2", "PHASE3"]
    assert row["browse_branches"] == ["BC08", "BXM"]
    assert row["intervention_types"] == ["DEVICE", "DRUG"]
    assert row["n_interventions"] == 3
    assert row["n_arm_groups"] == 2
    assert (row["n_locations"], row["n_countries"]) == (3, 2)
    assert row["why_stopped"] == "Funding"
    assert row["start_date"] == "2012-02"


def test_field_presence_uses_applicable_records() -> None:
    studies = [_study("NCT1", "TERMINATED", whyStopped="Funding"), _study("NCT2")]

    presence = ctgov_v2.field_presence(studies)

    assert presence["why_stopped"] == {
        "path": ctgov_v2.FIELD_PATHS["why_stopped"],
        "applicable": 1,
        "present": 1,
        "share": 1.0,
    }
    assert presence["nct_id"]["share"] == 1.0
    assert presence["eligibility_criteria"]["share"] == 0.0


def _page(studies: list[dict[str, Any]], token: str | None, total: int | None = None) -> Any:
    body: dict[str, Any] = {"studies": studies}
    if token:
        body["nextPageToken"] = token
    if total is not None:
        body["totalCount"] = total
    return httpx.Response(200, json=body)


@respx.mock
def test_iter_pages_follows_tokens_and_resumes_from_cache(
    fast_fetcher: JsonFetcher, cache: JsonCache
) -> None:
    route = respx.get(ctgov_v2.STUDIES_URL).mock(
        side_effect=[_page([_study("NCT1")], "tok1", total=2), _page([_study("NCT2")], None)]
    )
    params: dict[str, str | int] = {"filter.advanced": "x", "pageSize": 1}

    pages = list(ctgov_v2.iter_pages(fast_fetcher, cache, "q", params))

    assert [len(p.studies) for p in pages] == [1, 1]
    assert pages[0].total_count == 2
    first, second = route.calls
    assert first.request.url.params["countTotal"] == "true"
    assert "pageToken" not in first.request.url.params
    assert second.request.url.params["pageToken"] == "tok1"

    again = list(ctgov_v2.iter_pages(fast_fetcher, cache, "q", params))
    assert [p.from_cache for p in again] == [True, True]
    assert route.call_count == 2


@respx.mock
def test_iter_pages_scrubs_before_caching(fast_fetcher: JsonFetcher, cache: JsonCache) -> None:
    study = _study("NCT1")
    study["protocolSection"]["contactsLocationsModule"]["centralContacts"] = [{"name": "X Y"}]
    respx.get(ctgov_v2.STUDIES_URL).mock(return_value=_page([study], None))

    list(ctgov_v2.iter_pages(fast_fetcher, cache, "q", {"pageSize": 1}))

    assert "centralContacts" not in repr(cache.get("q/page_00000"))


def test_query_cache_prefix_differs_by_query() -> None:
    a = ctgov_v2.query_cache_prefix("delta", {"filter.advanced": "a"})
    b = ctgov_v2.query_cache_prefix("delta", {"filter.advanced": "b"})
    assert a != b
    assert a == ctgov_v2.query_cache_prefix("delta", {"filter.advanced": "a"})
