"""The internal history client (verification only), against mocked responses."""

from typing import Any

import httpx
import pytest
import respx

from trialpulse.feasibility import history_api
from trialpulse.feasibility.dataset import BlockedError
from trialpulse.feasibility.fetch import JsonCache, JsonFetcher

BASE = history_api.INT_STUDIES_URL


def _version(conditions: list[str], n_locations: int, phases: list[str]) -> dict[str, Any]:
    return {
        "studyVersion": "int",
        "study": {
            "protocolSection": {
                "statusModule": {"overallStatus": "RECRUITING"},
                "designModule": {"phases": phases},
                "conditionsModule": {"conditions": conditions},
                "armsInterventionsModule": {
                    "interventions": [{"type": "DRUG", "name": "Drug X "}],
                    "armGroups": [{"label": "A"}],
                },
                "contactsLocationsModule": {
                    "centralContacts": [{"name": "Jane Roe", "email": "jr@example.org"}],
                    "locations": [
                        {"facility": f"Site {i}", "contacts": [{"name": "John Doe"}]}
                        for i in range(n_locations)
                    ],
                },
            }
        },
    }


def _change_log(labels: list[list[str]]) -> dict[str, Any]:
    return {
        "study": {"protocolSection": {}},
        "history": {
            "changes": [
                {"version": i, "date": f"2020-01-{i + 1:02d}", "status": "RECRUITING",
                 "moduleLabels": lab}
                for i, lab in enumerate(labels)
            ]
        },
    }  # fmt: skip


def test_project_version_normalizes_and_drops_people() -> None:
    projected = history_api.project_version(_version(["Asthma ", "asthma"], 3, ["PHASE2"]))

    assert projected == {
        "phases": ["PHASE2"],
        "conditions": ["asthma"],
        "interventions": ["DRUG|drug x"],
        "n_arm_groups": 1,
        "n_locations": 3,
        "overall_status": "RECRUITING",
    }
    assert "Jane" not in repr(projected)


def test_project_change_log_sorts_versions() -> None:
    payload = {"history": {"changes": [{"version": 1, "moduleLabels": ["X"]}, {"version": 0}]}}
    assert [c["version"] for c in history_api.project_change_log(payload)] == [0, 1]


@pytest.mark.parametrize(
    ("label", "relevant"),
    [
        ("Contacts/Locations", True),
        ("Conditions", True),
        ("Arms/Interventions", True),
        ("Study Design", True),
        ("Study Status", False),
        ("Eligibility", False),
        ("Something New", True),
    ],
)
def test_label_filter(label: str, relevant: bool) -> None:
    assert history_api.label_may_touch_audited_fields(label) is relevant


def test_versions_to_fetch_modes() -> None:
    log = history_api.project_change_log(
        _change_log([[], ["Study Status"], ["Contacts/Locations"], ["Eligibility"]])
    )
    assert history_api.versions_to_fetch(log, "all_versions") == [0, 1, 2, 3]
    assert history_api.versions_to_fetch(log, "changed_modules_only") == [0, 2]


@respx.mock
def test_stability_audit_end_to_end(fast_fetcher: JsonFetcher, cache: JsonCache) -> None:
    respx.get(f"{BASE}/NCT1", params={"history": "true"}).mock(
        return_value=httpx.Response(200, json=_change_log([[], ["Contacts/Locations"]]))
    )
    respx.get(f"{BASE}/NCT1/history/0").mock(
        return_value=httpx.Response(200, json=_version(["Asthma"], 1, ["PHASE2"]))
    )
    respx.get(f"{BASE}/NCT1/history/1").mock(
        return_value=httpx.Response(200, json=_version(["Asthma"], 4, ["PHASE2"]))
    )
    respx.get(f"{BASE}/NCT2", params={"history": "true"}).mock(
        return_value=httpx.Response(200, json=_change_log([[]]))
    )
    respx.get(f"{BASE}/NCT2/history/0").mock(
        return_value=httpx.Response(200, json=_version(["COPD"], 2, []))
    )

    result = history_api.run_stability_audit(
        httpx.Client(), cache, ["NCT1", "NCT2"], manual_fallback=["NCT1"], fetcher=fast_fetcher
    )

    assert result["mode"] == "all_versions"
    assert result["trials_audited"] == 2
    assert result["versions_fetched"] == 3
    assert result["fields"]["n_locations"]["changed"] == 1
    assert result["fields"]["n_locations"]["share"] == pytest.approx(0.5)
    assert result["fields"]["phases"]["changed"] == 0
    assert result["module_labels_seen"] == {"Contacts/Locations": 1}
    cached = "".join(repr(cache.get(k)) for k in ("NCT1/v0000", "NCT1/v0001", "NCT1/changes"))
    for person in ("Jane", "John", "jr@example.org"):
        assert person not in cached


@respx.mock
def test_unavailable_endpoint_blocks_with_manual_fallback(
    fast_fetcher: JsonFetcher, cache: JsonCache
) -> None:
    respx.get(url__startswith=BASE).mock(return_value=httpx.Response(404))
    sample = [f"NCT{i}" for i in range(8)]

    with pytest.raises(BlockedError, match=r"manual fallback.*NCT1, NCT2"):
        history_api.run_stability_audit(
            httpx.Client(), cache, sample, manual_fallback=["NCT1", "NCT2"], fetcher=fast_fetcher
        )
