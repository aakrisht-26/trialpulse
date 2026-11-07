"""The census of ADR 0042 on the synthetic warehouse, against a fake API v2: each column's
rule, the failures, condition 4, the seeded 50, and output that names nothing."""

import copy
import datetime as dt
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from trialpulse.cli import RefusedError, StoppedEarlyError
from trialpulse.config import REPO_ROOT, ProjectConfig
from trialpulse.ingest.ctgov_api import STUDIES_URL, VERSION_URL
from trialpulse.warehouse import census

SECRET = b"a throwaway key for the census tests"
SUMMARY = "Short summary."
ELIGIBILITY = (
    "Inclusion Criteria:\n\n* Age \\>= 18\n* Contact pi@hospital.org for details. Walk daily."
)


def study(nct: str, posted: str, **change: Any) -> dict[str, Any]:
    """An API v2 record as the synthetic dataset's defaults would give it (see
    tests/warehouse/conftest.py), with `change` applied to its fields by name."""
    fields: dict[str, Any] = {
        "brief_title": "A Study", "official_title": "A Phase 3 Study",
        "organization_class": "INDUSTRY", "overall_status": "RECRUITING",
        "last_known_status": None, "status_verified_date": posted,
        "start_date": "2015-04", "start_date_type": "ACTUAL",
        "primary_completion_date": "2017-06-30", "primary_completion_date_type": "ESTIMATED",
        "completion_date": "2017-12-31", "completion_date_type": "ESTIMATED",
        "study_first_submit_date": "2015-02-27", "study_first_post_date": "2015-03-02",
        "study_first_post_date_type": "ACTUAL", "submitted_date": posted,
        "effective_date": posted, "effective_date_type": "ACTUAL",
        "lead_sponsor_name": "Acme Pharma & Co.", "lead_sponsor_class": "INDUSTRY",
        "brief_summary": SUMMARY, "study_type": "INTERVENTIONAL", "allocation": "RANDOMIZED",
        "intervention_model": "PARALLEL", "primary_purpose": "TREATMENT", "masking": "DOUBLE",
        "enrollment_count": 100, "enrollment_type": "ESTIMATED",
        "eligibility_criteria": ELIGIBILITY, "healthy_volunteers": False, "sex": "ALL",
        "minimum_age": "18 Years",
    }  # fmt: skip
    interventions = change.pop("interventions", None)
    fields.update(change)
    record: dict[str, Any] = {"protocolSection": {}}
    for name, path in census.API_V2_PATHS.items():
        value = nct if name == "nct_id" else fields.get(name)
        if value is None:
            continue
        node = record["protocolSection"]
        *parents, leaf = path.split(".")
        for key in parents:
            node = node.setdefault(key, {})
        node[leaf] = value
    if interventions is not None:
        module = record["protocolSection"].setdefault("armsInterventionsModule", {})
        module["interventions"] = [{"name": n, "type": t} for n, t in interventions]
    return record


def nct5(**change: Any) -> dict[str, Any]:
    """NCT00000005 unchanged since the dataset (its row in the synthetic warehouse)."""
    base: dict[str, Any] = {
        "study_type": "OBSERVATIONAL", "allocation": None, "masking": None,
        "enrollment_count": None, "enrollment_type": None,
        "interventions": [("Aspirin", "DRUG"), ("aspirin", "DRUG"), ("Placebo", "DRUG"),
                          ("Saline", "OTHER")],
    }  # fmt: skip
    base.update(change)
    return study("NCT00000005", "2019-09-09", **base)


def nct6(**change: Any) -> dict[str, Any]:
    """NCT00000006: the dataset stores the registry's UNKNOWN over RECRUITING."""
    base: dict[str, Any] = {"overall_status": "UNKNOWN", "last_known_status": "RECRUITING"}
    base.update(change)
    return study("NCT00000006", "2016-02-02", **base)


class FakeApi:
    """API v2 in memory: the pull pages through `pulled` two records a page, and a read by id
    returns `current` (the pulled records unless a test changes one)."""

    def __init__(self, pulled: list[dict[str, Any]], total: int | None = None) -> None:
        self.pulled = pulled
        self.current = {census._identity(s)[0]: copy.deepcopy(s) for s in pulled}
        self.total = len(pulled) if total is None else total
        self.requests: list[Mapping[str, Any]] = []

    def get_json(self, url: str, params: Mapping[str, str | int] | None = None) -> Any:
        params = dict(params or {})
        self.requests.append(params)
        if url == VERSION_URL:
            return {"dataTimestamp": "2026-11-05T09:00:00"}
        assert url == STUDIES_URL
        if "filter.ids" in params:
            wanted = str(params["filter.ids"]).split(",")
            return {"studies": [copy.deepcopy(self.current[n]) for n in wanted
                                if n in self.current]}  # fmt: skip
        start = int(params.get("pageToken", 0))
        page: dict[str, Any] = {"studies": copy.deepcopy(self.pulled[start : start + 2])}
        if "pageToken" not in params:
            page["totalCount"] = self.total
        if start + 2 < len(self.pulled):
            page["nextPageToken"] = str(start + 2)
        return page


@pytest.fixture
def inputs(warehouse: Path, dataset: str, interventions: str) -> census.Inputs:
    return census.Inputs(warehouse, dataset, interventions)


def _run(api: FakeApi, inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path) -> dict[str, Any]:
    return census.run_census(api, inputs, cfg, SECRET, memory_limit="1GB",
                             temp_dir=tmp_path / "tmp")  # fmt: skip


def _causes(result: dict[str, Any], column: str) -> dict[str, int]:
    return dict(result["every_unchanged_trial"]["columns"].get(column, {}))


def test_unchanged_records_that_match_pass_with_equal_feature_hashes(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path
) -> None:
    # NCT00000006 without the registry's UNKNOWN label, as the API can show it.
    shown = nct6(overall_status="RECRUITING", last_known_status=None)
    api = FakeApi([nct5(), study("NCT00000009", "2020-01-01"), shown])
    result = _run(api, inputs, cfg, tmp_path)
    assert result["groups"] == {"not in the warehouse": 1, "unchanged": 2}
    everyone = result["every_unchanged_trial"]
    assert everyone["trials"] == 2
    # NCT00000006 differs in the registry's UNKNOWN label only: not a feature difference.
    assert everyone["trials_differing_in_a_field_a_served_feature_reads"] == 0
    assert _causes(result, "overall_status") == {census.UNKNOWN_LABEL: 1}
    assert _causes(result, "last_known_status") == {census.UNKNOWN_LABEL: 1}
    assert everyone["feature_hash"]["trials_with_no_difference"] == {"equal": 1}
    assert everyone["feature_hash"]["trials_differing_only_in_fields_no_feature_reads"] == {
        "equal": 1
    }
    assert result["verdict"] == {"passes": True, "failures": []}
    # The pull pages by cursor with the live pull's fields, the population filter and the
    # largest page, and the differing record is read again by id.
    first = api.requests[1]
    assert first["fields"] == census.live_fields()
    assert first["pageSize"] == 1000
    assert first["filter.advanced"] == census.POPULATION_STUDY_TYPE
    assert "UNKNOWN" in str(first["filter.overallStatus"]).split("|")
    assert "SUSPENDED" in str(first["filter.overallStatus"]).split("|")
    assert any(r.get("pageToken") == "2" for r in api.requests)
    assert any(r.get("filter.ids") == "NCT00000006" for r in api.requests)


def test_registry_changes_without_a_new_version_are_counted_with_their_cause(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path
) -> None:
    api = FakeApi([
        nct5(lead_sponsor_name="Acme Holdings", official_title="A Phase 3 study",
             brief_summary="Short summary, edited.", sex="FEMALE"),
        nct6(organization_class="OTHER"),
    ])  # fmt: skip
    result = _run(api, inputs, cfg, tmp_path)
    assert _causes(result, "sponsor_key") == {"registry: another name": 1}
    assert _causes(result, "lead_sponsor_name") == {"registry: another name": 1}
    assert _causes(result, "official_title_hash") == {
        "title text, the same title phase: case only": 1
    }
    assert _causes(result, "brief_summary_hash") == {"text: letters or digits": 1}
    assert _causes(result, "sex") == {"registry: the value changed": 1}
    assert _causes(result, "organization_class") == {"registry: the value changed": 1}
    everyone = result["every_unchanged_trial"]
    assert everyone["trials_differing_in_a_field_a_served_feature_reads"] == 2
    assert everyone["failing_differences"] == 0
    assert everyone["feature_hash"]["trials_differing_in_a_field_a_served_feature_reads"] == {
        "apart": 2
    }
    assert result["verdict"]["passes"]


def test_dates_their_precision_and_ages_changed_on_the_registry_are_counted(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path
) -> None:
    api = FakeApi([nct5(start_date="2015-04-20", completion_date="2018-01-31",
                        minimum_age="21 Years")])  # fmt: skip
    result = _run(api, inputs, cfg, tmp_path)
    for column in ("start_date", "start_date_precision", "completion_date",
                   "minimum_age_years"):  # fmt: skip
        assert _causes(result, column) == {"registry: the value changed": 1}, column
    assert result["verdict"]["passes"]


def test_the_same_raw_value_with_another_canonical_value_is_a_bug(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = census.canonical_from_api_v2

    def broken(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        row, texts = real(record)
        return {**row, "primary_purpose": "PREVENTION", "sponsor_key": "another key",
                "start_date": dt.date(2015, 5, 1), "completion_date_precision": "month",
                "minimum_age_years": 21.0}, texts  # fmt: skip

    monkeypatch.setattr(census, "canonical_from_api_v2", broken)
    result = _run(FakeApi([nct5()]), inputs, cfg, tmp_path)
    for column in ("primary_purpose", "sponsor_key", "start_date", "completion_date_precision",
                   "minimum_age_years"):  # fmt: skip
        assert _causes(result, column) == {census.BUG: 1}, column
    assert result["every_unchanged_trial"]["trials_failing"] == 1
    assert not result["verdict"]["passes"]
    assert (
        "every unchanged trial: differences without their column's cause"
        in (result["verdict"]["failures"])
    )


def test_titles_that_state_another_phase_fail(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path
) -> None:
    result = _run(FakeApi([nct5(official_title="A Phase 2 Study")]), inputs, cfg, tmp_path)
    assert _causes(result, "official_title_hash") == {census.PHASE_MOVED: 1}
    assert not result["verdict"]["passes"]


def test_intervention_types_go_by_the_raw_pairs(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = nct5(interventions=[("Aspirin", "DRUG"), ("Saline", "OTHER")])
    result = _run(FakeApi([changed]), inputs, cfg, tmp_path)
    assert _causes(result, "intervention_types") == {"registry: the interventions changed": 1}
    assert result["verdict"]["passes"]

    real = census.canonical_from_api_v2

    def broken(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        row, texts = real(record)
        return {**row, "intervention_types": ["DRUG"]}, texts

    monkeypatch.setattr(census, "canonical_from_api_v2", broken)
    result = _run(FakeApi([nct5()]), inputs, cfg, tmp_path)
    assert _causes(result, "intervention_types") == {census.BUG: 1}
    assert not result["verdict"]["passes"]


def test_an_intervention_without_a_name_is_no_difference_and_is_counted(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path
) -> None:
    """ADR 0073: an API v2 entry without a name is left out on the live path, as the pinned
    config held none, so the unchanged trial does not differ; the record is counted."""
    pairs = [("Aspirin", "DRUG"), ("aspirin", "DRUG"), ("Placebo", "DRUG"), ("Saline", "OTHER"),
             (None, "DEVICE"), ("", "OTHER")]  # fmt: skip
    nameless = nct5(interventions=pairs)
    result = _run(FakeApi([nameless]), inputs, cfg, tmp_path)
    assert "intervention_types" not in result["every_unchanged_trial"].get("columns", {})
    assert result["every_unchanged_trial"]["trials_with_no_difference"] == 1
    assert result["nameless_interventions"] == {
        "records": 1, "unchanged_records": 1,
        "unchanged_records_differing_in_intervention_types": 0,
    }  # fmt: skip
    assert result["verdict"]["passes"]
    assert census.api_pairs(nameless) == census.api_pairs(nct5())


def test_a_status_changed_without_a_new_version_is_counted(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path
) -> None:
    result = _run(FakeApi([nct6(last_known_status="ACTIVE_NOT_RECRUITING")]), inputs, cfg,
                  tmp_path)  # fmt: skip
    assert _causes(result, "last_known_status") == {"registry: the status changed": 1}
    assert result["every_unchanged_trial"]["trials_differing_in_a_field_a_served_feature_reads"]
    assert result["verdict"]["passes"]


def test_an_unchanged_record_quarantined_on_the_api_path_fails(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path
) -> None:
    result = _run(FakeApi([nct6(masking="SEXTUPLE"), nct5()]), inputs, cfg, tmp_path)
    assert sum(result["quarantined"]["unchanged"].values()) == 1
    assert result["groups"]["unchanged"] == 1
    assert result["verdict"]["failures"] == ["unchanged records quarantined on the API path"]
    later = study("NCT00000006", "2026-10-01", masking="SEXTUPLE")
    result = _run(FakeApi([later]), inputs, cfg, tmp_path)
    assert sum(result["quarantined"]["changed_or_not_in_the_warehouse"].values()) == 1
    assert result["verdict"]["passes"]


def test_records_changed_since_the_dataset_or_during_the_census_are_set_aside(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path
) -> None:
    api = FakeApi([study("NCT00000005", "2026-10-01"), nct6(organization_class="OTHER")])
    api.current["NCT00000006"] = nct6(organization_class="OTHER", effective_date="2026-11-04")
    result = _run(api, inputs, cfg, tmp_path)
    assert result["groups"] == {"later on the registry": 1, "unchanged": 1}
    everyone = result["every_unchanged_trial"]
    assert everyone["set_aside_changed_or_missing_on_the_second_read"] == 1
    assert everyone["columns"] == {}


def test_a_record_is_compared_with_the_trials_last_version(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path
) -> None:
    """NCT00000001 has versions posted 2015-03-02 and 2016-01-10: the API's record of
    2016-01-10 is unchanged, and one of 2015-03-02 is behind the warehouse."""
    last = _run(FakeApi([study("NCT00000001", "2016-01-10")]), inputs, cfg, tmp_path)
    assert last["groups"] == {"unchanged": 1}
    first = _run(FakeApi([study("NCT00000001", "2015-03-02")]), inputs, cfg, tmp_path)
    assert first["groups"] == {"behind the warehouse": 1, "unchanged": 0}


def test_a_feature_hash_apart_without_a_feature_difference_breaks_condition_4(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(census, "feature_hashes_equal", lambda *a, **k: False)
    result = _run(FakeApi([nct5(brief_summary="Another summary.")]), inputs, cfg, tmp_path)
    everyone = result["every_unchanged_trial"]
    assert everyone["feature_hash"]["condition_4_breaks"] == 1
    assert (
        "every unchanged trial: a feature hash apart without a feature difference"
        in (result["verdict"]["failures"])
    )


def test_a_differing_text_that_keeps_an_html_comment_is_counted(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path
) -> None:
    result = _run(FakeApi([nct5(brief_summary="Short <!-- x --> summary.")]), inputs, cfg,
                  tmp_path)  # fmt: skip
    assert result["every_unchanged_trial"]["differing_api_texts_with_an_html_comment"] == 1
    # A line that is only a comment is dropped by normalize_text (condition 1): no difference.
    result = _run(FakeApi([nct5(brief_summary=f"{SUMMARY}\n\n<!-- -->\n")]), inputs, cfg,
                  tmp_path)  # fmt: skip
    assert result["every_unchanged_trial"]["trials_differing"] == 0


def test_the_seeded_50_is_a_fixed_draw_reported_apart(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path
) -> None:
    ids = [f"NCT{n:08d}" for n in range(1, 200)]
    first = census.seeded_sample(ids, 42)
    assert first == census.seeded_sample(list(reversed(ids)), 42) == sorted(first)
    assert len(first) == len(set(first)) == 50
    assert census.seeded_sample(ids, 43) != first
    assert census.seeded_sample(ids[:7], 42) == ids[:7]
    result = _run(FakeApi([nct5(sex="FEMALE"), nct6()]), inputs, cfg, tmp_path)
    assert result["seeded_50"]["trials"] == 2  # fewer than 50 unchanged: all of them
    assert result["seeded_50"]["columns"] == result["every_unchanged_trial"]["columns"]


@pytest.mark.slow  # it starts worker processes; CI runs every test
def test_the_pull_converts_in_worker_processes_as_it_does_in_one(cfg: ProjectConfig) -> None:
    pages = [nct5(), nct6(masking="SEXTUPLE"), study("NCT00000009", "2020-01-01")]
    said: list[str] = []
    inline = census.pull_population(FakeApi(pages), cfg)
    pooled = census.pull_population(FakeApi(pages), cfg, workers=2, say=said.append)
    assert pooled.rows == inline.rows
    assert pooled.titles == inline.titles
    assert pooled.quarantined == inline.quarantined
    assert (pooled.pages, pooled.records, pooled.total) == (2, 3, 3)
    assert len(inline.rows) == 2
    assert said == []  # a progress line every 20 pages


def test_a_pull_that_misses_records_stops(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path
) -> None:
    with pytest.raises(StoppedEarlyError, match="API v2 counted 3"):
        _run(FakeApi([nct5()], total=3), inputs, cfg, tmp_path)


def test_the_printed_summary_names_nothing(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path
) -> None:
    api = FakeApi([nct5(lead_sponsor_name="Acme Holdings", brief_summary="Secret words."),
                   nct6(official_title="A Phase 2 Study")])  # fmt: skip
    result = _run(api, inputs, cfg, tmp_path)
    printed = "\n".join(census.summary_lines(result)) + repr(result)
    for value in ("NCT0000", "Acme", "Secret", "Phase 2 Study", "Short summary", "pi@"):
        assert value not in printed, value
    assert printed.count("Census: FAILS") == 1


def test_the_command_refuses_on_a_github_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(census, "run_census", lambda *a, **k: pytest.fail("it ran"))
    with pytest.raises(RefusedError, match="ADR 0040"):
        census.main([])


def test_the_census_reads_the_columns_the_compact_row_is_made_from() -> None:
    expected = {
        *census.contract.COPIED, "overall_status", "last_known_status", "sponsor_key",
        "sponsor_is_individual", "official_title_hash", "brief_title_hash", "intervention_types",
    }  # fmt: skip
    assert expected == census.FEATURE_COLUMNS
    assert set(census.COMPARED) >= census.FEATURE_COLUMNS
    assert not census.FEATURE_COLUMNS & set(census.COUNTED_TEXTS)


def test_a_difference_whose_raw_values_cannot_be_read_fails(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing raw_versions row, core-file sponsor name or interventions config row is never
    read as a registry change."""
    api = FakeApi([
        nct5(lead_sponsor_name="Acme Holdings", sex="FEMALE",
             interventions=[("Aspirin", "DRUG")]),
        nct6(last_known_status="ACTIVE_NOT_RECRUITING"),
    ])  # fmt: skip
    monkeypatch.setattr(census, "dataset_raw", lambda *a, **k: ({}, {}))
    result = _run(api, inputs, cfg, tmp_path)
    for column in ("lead_sponsor_name", "sex", "intervention_types", "last_known_status"):
        assert _causes(result, column) == {census.NO_RAW: 1}, column
    assert not result["verdict"]["passes"]

    real = census.dataset_raw

    def no_sponsor_name(*args: Any, **kwargs: Any) -> Any:
        raws, pairs = real(*args, **kwargs)
        return {n: {**r, "raw_sponsor_name": None} for n, r in raws.items()}, pairs

    monkeypatch.setattr(census, "dataset_raw", no_sponsor_name)
    result = _run(FakeApi([nct5(lead_sponsor_name="Acme Holdings")]), inputs, cfg, tmp_path)
    assert _causes(result, "lead_sponsor_name") == {census.NO_RAW: 1}
    assert not result["verdict"]["passes"]


def test_a_differing_trial_missing_on_the_second_read_fails(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path
) -> None:
    api = FakeApi([nct5(), nct6(organization_class="OTHER")])
    del api.current["NCT00000006"]
    result = _run(api, inputs, cfg, tmp_path)
    everyone = result["every_unchanged_trial"]
    assert everyone["set_aside_changed_or_missing_on_the_second_read"] == 1
    assert everyone["missing_on_the_second_read"] == 1
    assert result["verdict"]["failures"] == [
        "every unchanged trial: differing trials missing on the second read",
        "the seeded 50: differing trials missing on the second read",
    ]


def test_a_pull_that_repeats_an_id_fails(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path
) -> None:
    """With as many records as API v2 counted, a repeated id stands for a record never seen."""
    result = _run(FakeApi([nct5(), nct5()]), inputs, cfg, tmp_path)
    assert result["pull"]["repeated_ids"] == 1
    assert result["verdict"]["failures"] == [
        "the pull repeated an id or returned an unreadable record"
    ]


def test_registry_changes_in_too_many_trials_of_one_column_fail(
    inputs: census.Inputs, cfg: ProjectConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conversion and the census read API v2 through the same paths, so a wrong path would
    show as registry changes in nearly every record: the share is bounded."""
    api = FakeApi([nct5(sex="FEMALE"), nct6()])
    assert _run(api, inputs, cfg, tmp_path)["verdict"]["passes"]  # below the floor
    monkeypatch.setattr(census, "REGISTRY_FLOOR", 0)
    result = _run(api, inputs, cfg, tmp_path)
    assert result["verdict"]["failures"] == [
        "sex: registry changes in more than 2% of the unchanged trials (a wrong API path?)"
    ]


def test_the_api_paths_resolve_on_recorded_api_v2_records() -> None:
    """The paths the conversion and the census share, checked on records API v2 returned
    (tests/live/fixtures): each resolves in some record but the two these records lack."""
    fixtures = REPO_ROOT / "tests" / "live" / "fixtures"
    pages = [json.loads((fixtures / f"delta_page_{n}.json").read_text(encoding="utf-8"))
             for n in (1, 2)]  # fmt: skip
    studies = [s for page in pages for s in page["studies"]]
    unresolved = {
        name for name, path in census.API_V2_PATHS.items()
        if all(census.get_path(s, f"protocolSection.{path}") is None for s in studies)
    }  # fmt: skip
    # No recorded record is UNKNOWN or stopped; the census of 2026-11-04 read the last known
    # status of 9,866 UNKNOWN records through its path.
    assert unresolved == {"last_known_status", "why_stopped"}
