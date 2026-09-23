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
