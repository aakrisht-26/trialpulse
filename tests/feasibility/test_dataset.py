"""Parts a to c on a synthetic version history with known answers."""

import datetime as dt
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


def _unpinned_config(tmp_path: Path) -> Path:
    """The project config with revision and cutoff unpinned, whatever the real file holds."""
    import re

    text = PROJECT_CONFIG_PATH.read_text(encoding="utf-8")
    text = re.sub(r"^(\s*revision:\s*)\S+", r"\g<1>null", text, count=1, flags=re.MULTILINE)
    text = re.sub(r"^(\s*cutoff:\s*)\S+", r"\g<1>null", text, count=1, flags=re.MULTILINE)
    path = tmp_path / "project.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_pin_revision_keeps_comments_and_loads(tmp_path: Path) -> None:
    path = _unpinned_config(tmp_path)

    pin_revision_in_config(path, "v2026.05.15")

    assert load_project_config(path).dataset.revision == "v2026.05.15"
    assert "# Git tag of the pinned revision" in path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="no `revision: null` line"):
        pin_revision_in_config(path, "v2026.05.22")


def test_pin_cutoff_keeps_comments_and_loads(tmp_path: Path) -> None:
    from trialpulse.feasibility.dataset import pin_cutoff_in_config

    path = _unpinned_config(tmp_path)
    pin_cutoff_in_config(path, dt.date(2026, 9, 25))
    assert load_project_config(path).dataset.cutoff == dt.date(2026, 9, 25)
    assert "# max last_update_post_date" in path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="no `cutoff: null` line"):
        pin_cutoff_in_config(path, dt.date(2026, 10, 2))


def test_profile_reports_evidence_for_the_per_version_answer(tmp_path: Path) -> None:
    glob = (tmp_path / "v.parquet").as_posix()
    duckdb.sql(
        f"""COPY (SELECT 'NCT1' AS nct_id, 0 AS nct_version, 'PARALLEL' AS intervention_model,
            'RANDOMIZED' AS allocation, 'Dr X' AS responsible_party_investigator_full_name,
            [1, 2] AS some_list) TO '{glob}' (FORMAT parquet)"""
    )
    with duckdb.connect() as con:
        result = profile(con, glob)
    assert result["field_name_matches"]["interventions"] == ["intervention_model"]
    assert result["field_name_matches"]["locations"] == []  # "allocation" is not a location
    assert result["nested_columns"] == ["some_list"]
    assert result["personal_data_columns"] == ["responsible_party_investigator_full_name"]


def test_the_cohort_uses_each_trials_latest_version(tmp_path: Path, cfg: ProjectConfig) -> None:
    glob = (tmp_path / "v.parquet").as_posix()
    duckdb.sql(
        f"""COPY (SELECT * FROM (VALUES
            ('NCT1', 0, 'INTERVENTIONAL', 'RECRUITING', DATE '2015-01-01', DATE '2015-01-02', NULL),
            ('NCT1', 1, 'OBSERVATIONAL', 'COMPLETED', DATE '2015-01-01', DATE '2016-01-02', NULL),
            ('NCT2', 0, 'INTERVENTIONAL', 'TERMINATED', DATE '2015-01-01', DATE '2016-01-02', 'x')
        ) AS t(nct_id, nct_version, study_type, overall_status, study_first_post_date,
               last_update_post_date, why_stopped)) TO '{glob}' (FORMAT parquet)"""
    )
    with duckdb.connect() as con:
        counts = cohort_counts(con, glob, cfg)
        assert cohort_ids(con) == ["NCT2"]  # NCT1's latest version is observational
    assert counts["cohort_trials"] == 1
    assert counts["trials_with_changing_study_type"] == 1


def test_profile_reports_type_drift_and_personal_data_counts(tmp_path: Path) -> None:
    duckdb.sql(
        f"""COPY (SELECT 'NCT1' AS nct_id, 0 AS nct_version, NULL::INTEGER AS flag,
            'Contact me at someone@example.org' AS ipd_sharing_description,
            'Jane Roe, PhD' AS lead_sponsor_name, 'INDIV' AS lead_sponsor_class)
            TO '{(tmp_path / "a.parquet").as_posix()}' (FORMAT parquet)"""
    )
    duckdb.sql(
        f"""COPY (SELECT 'NCT2' AS nct_id, 0 AS nct_version, true AS flag,
            'No sharing' AS ipd_sharing_description, 'Acme Pharma' AS lead_sponsor_name,
            'INDUSTRY' AS lead_sponsor_class)
            TO '{(tmp_path / "b.parquet").as_posix()}' (FORMAT parquet)"""
    )
    glob = (tmp_path / "*.parquet").as_posix()
    with duckdb.connect() as con:
        result = profile(con, glob)
    assert result["type_drift"] == {"flag": {"BOOLEAN": ["b.parquet"], "INTEGER": ["a.parquet"]}}
    assert result["personal_data_in_text"] == {
        "ipd_sharing_description with an email address": 1,
        "lead_sponsor_name with a degree title (PhD, MD, MPH and similar)": 1,
        "INDIV sponsor class": 1,
    }
    assert "someone@example.org" not in str(result)  # counts only, never values
    path = tmp_path / "dictionary.md"
    write_data_dictionary(result, "v1", path)
    text = path.read_text(encoding="utf-8")
    assert "(differs by file: BOOLEAN in 1; INTEGER in 1)" in text
