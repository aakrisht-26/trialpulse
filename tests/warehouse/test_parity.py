"""The source parity command, with API v2 mocked."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from trialpulse.warehouse import parity

STUDY = {
    "protocolSection": {
        "identificationModule": {
            "nctId": "NCT00000005",
            "briefTitle": "A Study",
            "officialTitle": "A Phase 3 Study",
            "organization": {"class": "INDUSTRY"},
        },
        "statusModule": {
            "overallStatus": "UNKNOWN",  # set by the registry without a new version
            "statusVerifiedDate": "2019-09-09",
            "startDateStruct": {"date": "2015-04", "type": "ACTUAL"},
            "primaryCompletionDateStruct": {"date": "2017-06-30", "type": "ESTIMATED"},
            "completionDateStruct": {"date": "2017-12-31", "type": "ESTIMATED"},
            "studyFirstSubmitDate": "2015-02-27",
            "studyFirstPostDateStruct": {"date": "2015-03-02", "type": "ACTUAL"},
            "lastUpdateSubmitDate": "2019-09-09",
            "lastUpdatePostDateStruct": {"date": "2019-09-09", "type": "ACTUAL"},
        },
        "sponsorCollaboratorsModule": {
            "leadSponsor": {"name": "Acme Pharma & Co.", "class": "INDUSTRY"}
        },
        "descriptionModule": {"briefSummary": "Short summary."},
        "designModule": {
            "studyType": "OBSERVATIONAL",
            "designInfo": {"interventionModel": "PARALLEL", "primaryPurpose": "TREATMENT"},
        },
        "eligibilityModule": {
            "eligibilityCriteria": (
                "Inclusion Criteria:\n\n* Age \\>= 18\n"
                "* Contact pi@hospital.org for details. Walk daily."
            ),
            "healthyVolunteers": False,
            "sex": "ALL",
            "minimumAge": "18 Years",
        },
    }
}
CHANGED = {
    "protocolSection": {
        "identificationModule": {"nctId": "NCT00000001"},
        "statusModule": {"lastUpdatePostDateStruct": {"date": "2026-01-01"}},
    }
}


@respx.mock
def test_parity_compares_unchanged_trials_column_by_column(
    warehouse: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = respx.get(parity.STUDIES_URL).mock(
        return_value=httpx.Response(200, json={"studies": [STUDY, CHANGED]})
    )
    monkeypatch.setattr(
        parity, "sample_trials", lambda con, n, seed: ["NCT00000001", "NCT00000005"]
    )
    out = tmp_path / "parity.json"
    assert parity.main(["--warehouse", str(warehouse), "--out", str(out)]) == 0
    params = route.calls.last.request.url.params
    assert params["filter.ids"] == "NCT00000001,NCT00000005"
    assert params["fields"] == parity.api_v2_fields()
    result = json.loads(out.read_text(encoding="utf-8"))
    assert (result["compared"], result["changed_since_dataset"]) == (1, 1)
    assert result["identical_rows"] == 0
    assert result["per_column"]["overall_status"] == {"agree": 0, "total": 1}
    assert result["per_column"]["eligibility_criteria_hash"] == {"agree": 1, "total": 1}
    assert result["mismatches"] == [
        {
            "nct_id": "NCT00000005",
            "columns": ["overall_status"],
            "values": {"overall_status": ["RECRUITING", "UNKNOWN"]},
            "cause": parity.UNKNOWN_CAUSE,
        }
    ]
    assert "Acme" not in out.read_text(encoding="utf-8")


def test_parity_labels_a_sponsor_renamed_without_a_version() -> None:
    renamed = json.loads(json.dumps(STUDY))
    renamed["protocolSection"]["statusModule"]["overallStatus"] = "RECRUITING"
    renamed["protocolSection"]["sponsorCollaboratorsModule"]["leadSponsor"]["name"] = "Acme USA"
    row, _ = parity.canonical_from_api_v2(STUDY)
    stored = {**row, "overall_status": "RECRUITING"}
    result = parity.compare([renamed], {"NCT00000005": stored}, ["RECRUITING"])
    assert result["mismatches"][0]["columns"] == ["lead_sponsor_name", "sponsor_key"]
    assert result["mismatches"][0]["cause"] == parity.SPONSOR_CAUSE
    assert result["mismatches"][0]["values"]["lead_sponsor_name"] == ["(hidden)", "(hidden)"]
    unexplained = json.loads(json.dumps(renamed))
    unexplained["protocolSection"]["statusModule"]["overallStatus"] = "COMPLETED"
    assert (
        parity.compare([unexplained], {"NCT00000005": stored}, ["RECRUITING"])["mismatches"][0][
            "cause"
        ]
        is None
    )


@respx.mock
def test_parity_follows_pages_and_retries_server_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parity._get.retry, "wait", lambda _: 0)  # type: ignore[attr-defined]
    route = respx.get(parity.STUDIES_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"studies": [STUDY], "nextPageToken": "abc"}),
            httpx.Response(200, json={"studies": [CHANGED]}),
        ]
    )
    pauses: list[float] = []
    with httpx.Client() as client:
        studies = parity.fetch_studies(client, ["NCT00000005", "NCT00000001"], pauses.append)
    assert len(studies) == 2
    assert route.calls.last.request.url.params["pageToken"] == "abc"
    assert pauses == [parity.SECONDS_BETWEEN_REQUESTS]


def test_parity_refuses_without_a_warehouse(tmp_path: Path) -> None:
    with pytest.raises(parity.RefusedError, match="no warehouse"):
        parity.main(["--warehouse", str(tmp_path / "missing.duckdb")])
