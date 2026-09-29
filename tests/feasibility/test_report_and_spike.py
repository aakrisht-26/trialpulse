"""Report rendering, the part runner, and the internal-endpoint boundary."""

import ast
from pathlib import Path
from typing import Any

import pytest

from trialpulse.config import REPO_ROOT, ProjectConfig
from trialpulse.feasibility import report, spike
from trialpulse.feasibility.dataset import BlockedError


def _g_result() -> dict[str, Any]:
    return {
        "status": "done",
        "api_version": {"apiVersion": "2.0.5", "dataTimestamp": "2026-09-22T09:00:04"},
        "delta": {
            "since": "2026-09-16",
            "filter": "AREA[LastUpdatePostDate]RANGE[2026-09-16,MAX]",
            "records": 5110,
            "total_count": 5110,
            "pages": 6,
            "pages_from_cache": 0,
            "elapsed_seconds_this_run": 12.5,
        },
        "field_presence": {
            "nct_id": {"path": "p", "applicable": 5110, "present": 5110, "share": 1.0}
        },
        "bulk": {
            "filter": "f",
            "records": 420073,
            "total_count": 420073,
            "pages": 421,
            "pages_from_cache": 0,
            "elapsed_seconds_this_run": 700.0,
            "parquet": "data/spike/current_fields.parquet",
            "summary": {
                "trials": 420073,
                "file_bytes": 30_000_000,
                "current_early_stops": 40000,
                "current_why_stopped_coverage": 0.97,
                "share_with_phases": 0.99,
                "share_with_conditions": 1.0,
                "share_with_browse_branches": 0.0,
                "share_with_mesh_terms": 0.8,
                "share_with_mesh_ancestors": 0.8,
                "share_with_interventions": 0.99,
                "share_with_arm_groups": 0.95,
                "share_with_locations": 0.9,
            },
        },
    }


def _c_result(cohort: int, stops: int) -> dict[str, Any]:
    return {
        "status": "done",
        "cohort_trials": cohort,
        "early_stops": stops,
        "why_stopped_coverage": 0.9,
        "status_totals": {},
        "status_by_first_post_year": {"2010": {"TERMINATED": 3}},
        "versions_total": 10,
        "last_update_post_date_coverage": 0.995,
        "data_cutoff": "2026-05-15",
    }


def test_report_with_blocked_dataset_parts(cfg: ProjectConfig) -> None:
    results: dict[str, Any] = {
        "a": {"status": "blocked", "reason": "access to the gated dataset has not been granted"},
        "b": {"status": "blocked", "reason": "part a must be done"},
        "g": _g_result(),
    }

    text = report.render_report(results, [], cfg)

    assert "**Decision: not stated.**" in text
    assert "| Cohort trials | >= 200,000 | part c not run | Blocked |" in text
    delta_row = "| API v2 delta pull works end to end | works | 5,110 records in 6 pages | Yes |"
    assert delta_row in text
    assert "Reason: access to the gated dataset has not been granted" in text
    assert "Not yet measured (part f has not completed)." in text
    assert "—" not in text  # no em dashes (CLAUDE.md writing style)


def test_report_criteria_verdicts(cfg: ProjectConfig) -> None:
    results: dict[str, Any] = {"c": _c_result(150_000, 25_000), "e": {"status": "pending_manual",
               "trials": ["NCT1"], "checklist": "x", "results_file": "y"}}  # fmt: skip
    manual = [{"nct_id": f"NCT{i}", "result": "pass" if i < 9 else "fail"} for i in range(10)]

    text = report.render_report(results, manual, cfg)

    assert "| Cohort trials | >= 200,000 | 150,000 | No |" in text
    assert "| Early stops in the cohort | >= 20,000 | 25,000 | Yes |" in text
    assert "| Manual check (part e) | >= 9 of 10 | 9 of 10 pass | Yes |" in text


def test_blocked_part_b_states_per_version_columns_explicitly(cfg: ProjectConfig) -> None:
    results: dict[str, Any] = {"b": {"status": "blocked", "reason": "part a must be done"}}
    text = report.render_report(results, [], cfg)
    for label in ("phases", "conditions", "interventions", "arm counts", "locations"):
        assert f"| {label} | Not verifiable yet (part b blocked) | n/a |" in text
    assert "planned separate per-version configs" in text


def test_part_f_shows_phase_group_and_the_original_run(cfg: ProjectConfig) -> None:
    field = {"changed": 3, "trials": 150, "share": 0.02, "share_among_multi_version": 0.026,
             "under_threshold": True}  # fmt: skip
    f_result = {
        "status": "done",
        "sampled": 150,
        "trials_audited": 150,
        "multi_version_trials": 114,
        "failures": {},
        "mode": "all_versions",
        "versions_fetched": 973,
        "versions_in_change_logs": 973,
        "requests_needed": 1123,
        "requests_this_run": 0,
        "elapsed_minutes_this_run": 0.0,
        "original_run": {"requests_this_run": 1123, "elapsed_minutes_this_run": 56.1,
                         "finished_at": "2026-09-23T00:30:00+00:00"},
        "fields": {"phases": dict(field, changed=6), "phase_group": field},
        "phase_group_at_version_0": {"mid": 60, "na": 40},
    }  # fmt: skip

    text = report.render_report({"f": f_result}, [], cfg)

    assert "| phase_group | 3 of 150 | 2.0% |" in text
    assert "made 1,123 requests in 56.1 minutes" in text
    assert "Groups at version 0: mid 60, na 40." in text


def test_manual_check_pending_until_ten_results() -> None:
    assert report._manual_measure([{"result": "pass"}]) == ("1 of 10 recorded", None)


@pytest.fixture
def results_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(spike, "RESULTS_DIR", tmp_path)
    return tmp_path


def test_run_part_records_blocked_and_failed(
    results_dir: Path, cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def blocked(_: spike.Context) -> dict[str, Any]:
        raise BlockedError("needs a person")

    def broken(_: spike.Context) -> dict[str, Any]:
        raise RuntimeError("boom")

    monkeypatch.setitem(spike.PARTS, "b", blocked)
    monkeypatch.setitem(spike.PARTS, "c", broken)
    ctx = spike.Context(cfg=cfg, hf_token=None)

    assert spike.run_part("b", ctx)["reason"] == "needs a person"
    assert spike.run_part("c", ctx)["status"] == "failed"
    assert spike.load_result("c")["error"] == "RuntimeError: boom"  # type: ignore[index]
    assert spike.run_part("a", ctx)["reason"] == "HF_TOKEN is not set in .env"
    assert spike.load_result("a")["status"] == "blocked"  # type: ignore[index]


def test_parse_parts() -> None:
    assert spike._parse_parts("all") == list(spike.PART_ORDER)
    assert spike._parse_parts("g, f") == ["g", "f"]
    with pytest.raises(Exception, match="unknown part"):
        spike._parse_parts("z")


def test_no_module_outside_feasibility_uses_the_internal_endpoint() -> None:
    """CLAUDE.md Step 2 acceptance: nothing outside feasibility/ may import code that
    calls the internal history endpoint."""
    src = REPO_ROOT / "src" / "trialpulse"
    feasibility = src / "feasibility"
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        if feasibility in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "trialpulse.feasibility"
            ):
                offenders.append(f"{path.name}: from {node.module}")
            if isinstance(node, ast.Import) and any(
                a.name.startswith("trialpulse.feasibility") for a in node.names
            ):
                offenders.append(f"{path.name}: import")
            if isinstance(node, ast.Constant) and "/api/int/" in str(node.value):
                offenders.append(f"{path.name}: internal endpoint URL")
    assert offenders == []


def test_record_cutoff_writes_once_and_refuses_a_different_value(
    cfg: ProjectConfig, tmp_path: Path
) -> None:
    import dataclasses
    import datetime as dt
    import re

    from trialpulse.config import PROJECT_CONFIG_PATH

    text = PROJECT_CONFIG_PATH.read_text(encoding="utf-8")
    path = tmp_path / "project.yaml"
    path.write_text(
        re.sub(r"^(\s*cutoff:\s*)\S+", r"\g<1>null", text, count=1, flags=re.M), "utf-8"
    )
    unset = cfg.model_copy(update={"dataset": cfg.dataset.model_copy(update={"cutoff": None})})
    assert spike.record_cutoff(unset, path, "2026-09-25") == "recorded"
    assert "cutoff: 2026-09-25" in path.read_text(encoding="utf-8")
    pinned = cfg.model_copy(
        update={"dataset": cfg.dataset.model_copy(update={"cutoff": dt.date(2026, 9, 25)})}
    )
    assert spike.record_cutoff(pinned, path, "2026-09-25") == "already recorded"
    with pytest.raises(ValueError, match="differs"):
        spike.record_cutoff(pinned, path, "2026-10-02")
    assert dataclasses  # imported for parity with other tests


def test_part_d_refuses_api_records_older_than_the_cutoff() -> None:
    import datetime as dt

    from trialpulse.feasibility.dataset import BlockedError

    fresh = {"dataTimestamp": "2026-09-29T09:00:05"}
    assert spike.check_records_after_cutoff(fresh, dt.date(2026, 9, 25)) == "2026-09-29T09:00:05"
    with pytest.raises(BlockedError, match="predate the cutoff"):
        spike.check_records_after_cutoff(
            {"dataTimestamp": "2026-09-22T09:00:04"}, dt.date(2026, 9, 25)
        )
    with pytest.raises(BlockedError, match="predate the cutoff"):
        spike.check_records_after_cutoff({}, dt.date(2026, 9, 25))


def test_dataset_parts_refuse_a_download_of_another_revision(
    cfg: ProjectConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from trialpulse.feasibility.dataset import BlockedError

    monkeypatch.setattr(spike, "RESULTS_DIR", tmp_path)
    (tmp_path / "part_a.json").write_text(
        json.dumps({"part": "a", "status": "done", "revision": "v2026.01.01"}), encoding="utf-8"
    )
    pinned = cfg.model_copy(
        update={"dataset": cfg.dataset.model_copy(update={"revision": "v2026.09.26"})}
    )
    with pytest.raises(BlockedError, match=r"not the pinned v2026\.09\.26"):
        spike._dataset_glob(spike.Context(cfg=pinned, hf_token=None))
    same = cfg.model_copy(
        update={"dataset": cfg.dataset.model_copy(update={"revision": "v2026.01.01"})}
    )
    assert "v2026.01.01" in spike._dataset_glob(spike.Context(cfg=same, hf_token=None))


def test_checklist_rows_show_dates_at_their_precision() -> None:
    row = {
        "nct_id": "NCT00000001", "last_update_submit_date": "2009-01-05", "overall_status":
        "RECRUITING", "study_type": "INTERVENTIONAL", "study_first_post_date": "2009-01-07",
        "start_date": "2009-01-01", "start_date_precision": "month", "start_date_type": None,
        "primary_completion_date": "2011-01-01", "primary_completion_date_precision": "year",
        "primary_completion_date_type": "ANTICIPATED", "enrollment_count": 120,
        "enrollment_type": "ESTIMATED", "lead_sponsor_class": "INDUSTRY",
    }  # fmt: skip
    out = spike.checklist_row(row)
    assert out["record_history_url"] == "https://clinicaltrials.gov/study/NCT00000001?tab=history"
    assert out["start_date"] == "2009-01"
    assert out["primary_completion_date"] == "2011"
    assert out["start_date_type"] is None
    assert out["enrollment_count"] == "120"


def test_report_shows_the_checklist_catch_up_and_evidence(cfg: ProjectConfig) -> None:
    import datetime as dt

    from trialpulse.feasibility.report import catch_up_line, render_report

    assert catch_up_line("2026-09-25", dt.date(2026, 9, 29)).endswith("4 days, about 0.1 months.")
    e = {
        "status": "pending_manual", "trials": ["NCT00000001"], "fields": ["overall_status"],
        "results_file": "docs/feasibility_manual_check.csv",
        "rows": [{"nct_id": "NCT00000001", "overall_status": "RECRUITING",
                  "record_history_url": "https://clinicaltrials.gov/study/NCT00000001?tab=history"}],
    }  # fmt: skip
    report = render_report({"e": e}, [], cfg, today=dt.date(2026, 9, 29))
    link = "[history](https://clinicaltrials.gov/study/NCT00000001?tab=history)"
    assert f"| NCT00000001 | {link} | RECRUITING |" in report
    assert "on 2026-09-29" in report


def test_the_manual_results_file_follows_the_chosen_trials(tmp_path: Path) -> None:
    from trialpulse.feasibility.dataset import BlockedError

    path = tmp_path / "manual.csv"
    spike.write_manual_results(path, ["NCT1", "NCT2"])
    assert path.read_text(encoding="utf-8").splitlines() == [
        "nct_id,result,notes",
        "NCT1,,",
        "NCT2,,",
    ]
    spike.write_manual_results(path, ["NCT3"])  # nothing recorded yet: rewritten
    assert path.read_text(encoding="utf-8").splitlines() == ["nct_id,result,notes", "NCT3,,"]
    path.write_text("nct_id,result,notes\nNCT3,pass,\n", encoding="utf-8")
    spike.write_manual_results(path, ["NCT3"])  # same trials: kept with the result
    assert "NCT3,pass," in path.read_text(encoding="utf-8")
    with pytest.raises(BlockedError, match="recorded results for other trials"):
        spike.write_manual_results(path, ["NCT4"])
