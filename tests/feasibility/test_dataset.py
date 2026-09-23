"""Parts a to c on a synthetic version history with known answers."""

import datetime as dt
import shutil
from pathlib import Path

import duckdb
import pytest

from trialpulse.config import PROJECT_CONFIG_PATH, ProjectConfig, load_project_config
from trialpulse.feasibility.dataset import (
    BlockedError,
    cohort_counts,
    cohort_ids,
    latest_rows,
    pin_revision_in_config,
    profile,
    resolve_revision,
    write_data_dictionary,
)


def test_profile_counts(history_glob: str) -> None:
    with duckdb.connect() as con:
        prof = profile(con, history_glob)

    assert prof["rows"] == 11
    assert prof["trials"] == 7
    assert prof["duplicate_version_rows"] == 0
    assert prof["versions_per_trial"]["min"] == 1
    assert prof["versions_per_trial"]["max"] == 3
    assert "status_verified_date" in prof["missing_key_columns"]
    assert prof["null_rates"]["last_update_post_date"] == pytest.approx(1 / 11)
    assert prof["list_fields"]["eligibility_text"] == ["eligibility_criteria"]
    assert prof["list_fields"]["phases"] == []
    by_year = {r["year"]: r for r in prof["estimated_post_date_share_by_year"]}
    assert by_year[2010]["share"] == pytest.approx(1.0)
    assert by_year[2015]["share"] == pytest.approx(0.0)
    assert prof["date_ranges"]["study_first_post_date"] == ["2007-06-01", "2021-07-07"]


def test_per_version_columns_absent_in_the_fixture(history_glob: str) -> None:
    with duckdb.connect() as con:
        prof = profile(con, history_glob)
    assert prof["per_version_columns"] == {
        "phases": False,
        "conditions": False,
        "interventions": False,
        "arm_count": False,
        "locations": False,
    }


def test_per_version_columns_detected_by_exact_name(tmp_path: Path) -> None:
    path = tmp_path / "wide.parquet"
    with duckdb.connect() as con:
        con.execute(
            f"""COPY (SELECT 'NCT1' AS nct_id, 0 AS nct_version, 'PHASE2' AS phases,
                2 AS number_of_arms, 'x' AS pharmaceutical_class, 'y' AS allocation)
            TO '{path.as_posix()}' (FORMAT parquet)"""
        )
        prof = profile(con, path.as_posix())
    flags = prof["per_version_columns"]
    assert flags["phases"] is True
    assert flags["arm_count"] is True
    assert prof["list_fields"]["arm_count"] == ["number_of_arms"]  # not pharmaceutical_class
    assert flags["locations"] is False


def test_cohort_counts(history_glob: str, cfg: ProjectConfig) -> None:
    with duckdb.connect() as con:
        counts = cohort_counts(con, history_glob, cfg)
        ids = cohort_ids(con)

    # NCT00000003 is observational and NCT00000004 was first posted in 2007.
    assert sorted(ids) == [
        "NCT00000001",
        "NCT00000002",
        "NCT00000005",
        "NCT00000006",
        "NCT00000007",
    ]
    assert counts["cohort_trials"] == 5
    assert counts["early_stops"] == 2
    # NCT00000005's latest why_stopped is blank, which does not count as present.
    assert counts["why_stopped_coverage"] == pytest.approx(0.5)
    assert counts["status_totals"] == {
        "TERMINATED": 1,
        "COMPLETED": 1,
        "WITHDRAWN": 1,
        "UNKNOWN": 1,
        "open": 1,
    }
    assert counts["status_by_first_post_year"]["2019"] == {"WITHDRAWN": 1}
    assert counts["versions_total"] == 11
    assert counts["last_update_post_date_coverage"] == pytest.approx(10 / 11)
    assert counts["data_cutoff"] == "2021-07-07"


def test_latest_rows_returns_latest_version(history_glob: str) -> None:
    with duckdb.connect() as con:
        rows = latest_rows(con, history_glob, ["NCT00000001", "NCT00000005"])

    assert rows["NCT00000001"]["overall_status"] == "TERMINATED"
    assert rows["NCT00000001"]["enrollment_count"] == 12
    assert rows["NCT00000001"]["start_date"] == dt.date(2010, 6, 1)
    assert rows["NCT00000001"]["start_date_precision"] == "month"
    assert rows["NCT00000005"]["last_update_post_date"] == dt.date(2019, 9, 9)


def test_data_dictionary_lists_every_column(history_glob: str, tmp_path: Path) -> None:
    with duckdb.connect() as con:
        prof = profile(con, history_glob)
    out = tmp_path / "dictionary.md"

    write_data_dictionary(prof, "v2026.05.15", out)

    text = out.read_text(encoding="utf-8")
    assert "`v2026.05.15`" in text
    for col in prof["columns"]:
        assert f"| `{col['name']}` |" in text


def test_resolve_revision_uses_pinned_value() -> None:
    assert resolve_revision("v2026.05.15", lambda: []) == "v2026.05.15"


def test_resolve_revision_blocks_when_unpinned() -> None:
    with pytest.raises(BlockedError, match=r"latest tag: v2026\.05\.15"):
        resolve_revision(None, lambda: ["v2026.05.08", "v2026.05.15"])


def test_pin_revision_keeps_comments_and_loads(tmp_path: Path) -> None:
    path = tmp_path / "project.yaml"
    shutil.copy(PROJECT_CONFIG_PATH, path)

    pin_revision_in_config(path, "v2026.05.15")

    assert load_project_config(path).dataset.revision == "v2026.05.15"
    assert "# Git tag of the pinned revision" in path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="no `revision: null` line"):
        pin_revision_in_config(path, "v2026.05.22")
