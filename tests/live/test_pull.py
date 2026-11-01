"""The delta pull on recorded API v2 pages (ADR 0045; no network in CI).

`fixtures/` holds two pages of 4 records recorded from API v2 on 2026-11-01 with the live
`fields` parameter (the records updated on 2026-10-29 and 30), and the `/version` answer.
They were trimmed (the total set to 8, the second page made the last) and scrubbed: no
personal-data key, the names of sponsors outside the industry, government and network
classes replaced, no email address."""

import copy
import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from tenacity import wait_none

from trialpulse.ingest.ctgov_api import STUDIES_URL, VERSION_URL, ApiClient
from trialpulse.live import pull

from .conftest import RULES, SECRET

FIXTURES = Path(__file__).parent / "fixtures"
DAY = dt.date(2026, 10, 30)


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _api() -> ApiClient:
    return ApiClient(httpx.Client(), per_minute=60_000, wait=wait_none(), sleep=lambda _: None)


@respx.mock
def test_the_date_of_a_run_is_the_data_timestamp() -> None:
    respx.get(VERSION_URL).mock(return_value=httpx.Response(200, json=_load("version.json")))
    assert pull.data_date(_api()) == DAY


@respx.mock
def test_the_pull_follows_the_pages_and_converts_every_record() -> None:
    route = respx.get(STUDIES_URL).mock(
        side_effect=[
            httpx.Response(200, json=_load("delta_page_1.json")),
            httpx.Response(200, json=_load("delta_page_2.json")),
        ]
    )
    result = pull.pull_delta(_api(), dt.date(2026, 10, 27), DAY, 4)
    assert (len(result.studies), result.pages, result.total_count) == (8, 2, 8)
    first, second = (call.request.url.params for call in route.calls)
    assert first["filter.advanced"] == "AREA[LastUpdatePostDate]RANGE[2026-10-27,2026-10-30]"
    assert first["countTotal"] == "true"
    assert "pageToken" not in first
    assert second["pageToken"] == "recorded-token-2"
    assert "protocolSection.armsInterventionsModule.interventions.type" in first["fields"]
    converted = pull.convert(result.studies, RULES, SECRET)
    assert converted.quarantined == []
    assert len(converted.rows) == 8
    for row in converted.rows:
        assert row["effective_date"] in (dt.date(2026, 10, 29), DAY)
        assert row["status"]
        assert len(row["feature_hash"]) == 64
    assert any(row["intervention_types"] for row in converted.rows)
    named = [r for r in converted.rows if r["sponsor_id"] is not None]
    assert named  # organizations get a keyed id
    assert all(len(r["sponsor_id"]) == 64 for r in named)


@respx.mock
def test_a_pull_with_no_record_or_a_short_count_fails() -> None:
    respx.get(STUDIES_URL).mock(return_value=httpx.Response(200, json={"totalCount": 0}))
    with pytest.raises(pull.PullError, match="no record"):
        pull.pull_delta(_api(), DAY, DAY + dt.timedelta(days=5), 1000)
    page = _load("delta_page_1.json")
    page.pop("nextPageToken")
    respx.get(STUDIES_URL).mock(return_value=httpx.Response(200, json=page))
    with pytest.raises(pull.PullError, match="counted 8"):
        pull.pull_delta(_api(), DAY, DAY, 1000)


def _studies() -> list[dict[str, Any]]:
    return [*_load("delta_page_1.json")["studies"], *_load("delta_page_2.json")["studies"]]


def _mutated(path: str, value: Any, index: int = 0) -> list[dict[str, Any]]:
    studies = copy.deepcopy(_studies())
    node = studies[index]["protocolSection"]
    keys = path.split(".")
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    if value is _DROP:
        node.pop(keys[-1], None)
    else:
        node[keys[-1]] = value
    return studies


_DROP = object()


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        ("statusModule.overallStatus", _DROP, "overall_status"),
        ("statusModule.overallStatus", "PAUSED_FOR_REVIEW", "overall_status"),
        ("statusModule.lastUpdatePostDateStruct", _DROP, "effective_date"),
        ("identificationModule.nctId", _DROP, "nct_id"),
        ("designModule.studyType", "OBSERVATIONAL_PATIENT_REGISTRY", "study_type"),
        ("sponsorCollaboratorsModule.leadSponsor.class", "CONSORTIUM", "lead_sponsor_class"),
        ("armsInterventionsModule.interventions", [{"name": "x", "type": "GENE_EDIT"}],
         "intervention_type"),
    ],
)  # fmt: skip
def test_a_changed_shape_quarantines_the_record_with_the_check_not_the_value(
    path: str, value: Any, expected: str
) -> None:
    converted = pull.convert(_mutated(path, value), RULES, SECRET)
    assert len(converted.rows) == 7
    (bad,) = converted.quarantined
    assert expected in bad["reasons"]
    assert len(bad["payload_hash"]) == 64
    if isinstance(value, str):
        assert value not in bad["reasons"]  # the check, never the value


def test_a_renamed_field_reads_as_missing_and_the_gate_must_catch_it() -> None:
    """A renamed optional field passes validation as NULL: one of the shape changes the
    NULL gate exists for (`trialpulse.live.runs.null_gate`)."""
    converted = pull.convert(
        _mutated("designModule.enrollmentInfo", {"countRenamed": 5}), RULES, SECRET
    )
    assert converted.quarantined == []
    assert converted.rows[0]["enrollment_count"] is None


def test_repeated_intervention_pairs_count_once() -> None:
    studies = _mutated(
        "armsInterventionsModule.interventions",
        [
            {"name": "A", "type": "DRUG"},
            {"name": "A", "type": "DRUG"},
            {"name": "B", "type": "DRUG"},
        ],
    )
    assert pull.convert(studies, RULES, SECRET).rows[0]["intervention_types"] == ["DRUG", "DRUG"]
