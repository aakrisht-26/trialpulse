"""The warehouse build on a synthetic dataset: types, normalization, privacy, quarantine,
idempotency, the audit, and parity with the API v2 mapping."""

import datetime as dt
import json
from pathlib import Path

import duckdb
import pytest

from trialpulse.cli import RefusedError
from trialpulse.config import ProjectConfig
from trialpulse.contracts.text import EMAIL_MARKER
from trialpulse.contracts.versions import CANONICAL_COLUMNS, canonical_from_api_v2
from trialpulse.warehouse.audit import render_audit
from trialpulse.warehouse.build import (
    PERSONAL_COLUMNS,
    RAW_COLUMNS,
    TABLES,
    build,
    compare_with_previous,
    dataset_glob,
    table_checksums,
)

from .conftest import EMAIL, INDIVIDUAL, INVESTIGATOR, PERSON_LIKE


def _query(path: Path, sql: str) -> list[tuple[object, ...]]:
    with duckdb.connect(str(path), read_only=True) as con:
        return con.execute(sql).fetchall()


def test_tables_and_counts(warehouse: Path) -> None:
    counts = {t: _query(warehouse, f"SELECT count(*) FROM {t}")[0][0] for t in TABLES}
    assert counts["raw_versions"] == 11
    assert counts["versions"] == 8
    assert counts["versions_quarantine"] == 3
    assert counts["trials"] == 5  # NCT00000004's only version is quarantined
    stored = _query(
        warehouse,
        "SELECT overall_status, last_known_status FROM versions WHERE nct_id = 'NCT00000006'",
    )
    assert stored == [("UNKNOWN", "RECRUITING")]
    assert not (warehouse.parent / "warehouse.duckdb.building").exists()


def test_raw_columns_have_their_explicit_types(warehouse: Path) -> None:
    described = {r[0]: r[1] for r in _query(warehouse, "DESCRIBE raw_versions")}
    assert {c: described[c] for c in RAW_COLUMNS} == {c: t for c, (t, _) in RAW_COLUMNS.items()}
    # The drifting column read as INTEGER in one file is cast to BOOLEAN.
    assert _query(warehouse, "SELECT count(*) FROM raw_versions WHERE is_ppsd")[0][0] == 5


def test_no_personal_data_is_written(warehouse: Path) -> None:
    with duckdb.connect(str(warehouse), read_only=True) as con:
        columns = con.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE data_type = 'VARCHAR'"
        ).fetchall()
        assert not {c for _, c in columns} & set(PERSONAL_COLUMNS)
        for table, column in columns:
            for secret in (INVESTIGATOR, INDIVIDUAL, "Jane", PERSON_LIKE, "Smith", EMAIL):
                hits = con.execute(
                    f"SELECT count(*) FROM {table} WHERE contains({column}, ?)", [secret]
                ).fetchone()
                assert hits == (0,), (table, column, secret)


def test_versions_are_canonical(warehouse: Path) -> None:
    rows = _query(
        warehouse,
        """SELECT nct_id, nct_version, start_date, start_date_precision, completion_date,
          completion_date_type, minimum_age_years, maximum_age_years, enrollment_type,
          lead_sponsor_name, sponsor_key, sponsor_is_individual
        FROM versions ORDER BY nct_id, nct_version""",
    )
    first = rows[0]
    assert first[:8] == (
        "NCT00000001",
        0,
        dt.date(2015, 4, 1),
        "month",
        None,
        None,
        18.0,
        0.5,
    )  # the 2100-12-31 placeholder is missing, with no type
    assert first[9:] == (None, None, True)  # INDIV: no name
    by_id = {(r[0], r[1]): r for r in rows}
    assert by_id[("NCT00000002", 0)][9:] == (None, None, True)  # a person's name
    assert by_id[("NCT00000003", 0)][8] == "ESTIMATED"  # ANTICIPATED, remapped
    assert by_id[("NCT00000003", 0)][9:] == ("Acme Pharma & Co.", "acme pharma co", False)
    assert by_id[("NCT00000005", 0)][8] is None


def test_texts_are_normalized_once_and_shared_across_forms(warehouse: Path) -> None:
    html_hash, markdown_hash = (
        _query(
            warehouse,
            f"SELECT eligibility_criteria_hash FROM versions WHERE nct_id = '{nct}' LIMIT 1",
        )[0][0]
        for nct in ("NCT00000001", "NCT00000005")
    )
    assert html_hash == markdown_hash
    text = _query(warehouse, f"SELECT text FROM texts WHERE text_hash = '{html_hash}'")[0][0]
    assert (
        text == f"Inclusion Criteria:\nAge >= 18\nContact {EMAIL_MARKER} for details. Walk daily."
    )
    why = _query(
        warehouse,
        "SELECT t.text FROM versions v JOIN texts t ON t.text_hash = v.why_stopped_hash",
    )
    assert why == [("Slow accrual & funding",)]
    assert _query(warehouse, "SELECT count(*) = count(DISTINCT text_hash) FROM texts") == [(True,)]


def test_quarantine_and_trials(warehouse: Path) -> None:
    reasons = _query(
        warehouse,
        "SELECT nct_id, nct_version, reasons FROM versions_quarantine ORDER BY ALL",
    )
    assert [(r[0], r[1]) for r in reasons] == [
        ("NCT00000003", 1),
        ("NCT00000003", 1),
        ("NCT00000004", 0),
    ]
    assert reasons[0][2] == "row: unique_history_version"
    assert reasons[2][2].startswith("masking: isin(")
    trials = _query(
        warehouse,
        """SELECT nct_id, t0, first_version, last_version, n_versions, latest_status,
          quarantined_versions FROM trials ORDER BY nct_id""",
    )
    assert trials[0] == ("NCT00000001", dt.date(2015, 3, 2), 0, 1, 2, "TERMINATED", 0)
    assert trials[1][5] == "RECRUITING"
    assert trials[2][6] == 2


@pytest.mark.slow  # a second build, with a pool of worker processes
def test_building_twice_gives_identical_tables(
    tmp_path: Path, dataset: str, cfg: ProjectConfig, warehouse: Path
) -> None:
    first = table_checksums(warehouse)
    again = tmp_path / "again.duckdb"
    build(dataset, cfg, again, workers=2, temp_dir=tmp_path / "tmp")
    assert table_checksums(again) == first
    log = tmp_path / "build.json"
    assert compare_with_previous(first, log) is None
    log.write_text(json.dumps({"tables": first}), encoding="utf-8")
    assert compare_with_previous(first, log) is True
    changed = {**first, "trials": {"rows": 0, "checksum": "0"}}
    assert compare_with_previous(changed, log) is False


def test_the_api_v2_path_gives_the_same_canonical_row(warehouse: Path) -> None:
    """NCT00000005 as API v2 would return it maps to the warehouse row, hash included."""
    with duckdb.connect(str(warehouse), read_only=True) as con:
        cursor = con.execute("SELECT * FROM versions WHERE nct_id = 'NCT00000005'")
        names = [d[0] for d in cursor.description]
        stored = dict(zip(names, cursor.fetchall()[0], strict=True))
    study = {
        "protocolSection": {
            "identificationModule": {
                "nctId": "NCT00000005",
                "briefTitle": "A Study",
                "officialTitle": "A Phase 3 Study",
                "organization": {"class": "INDUSTRY"},
            },
            "statusModule": {
                "overallStatus": "RECRUITING",
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
    row, _ = canonical_from_api_v2(study)
    compared = [c for c in CANONICAL_COLUMNS if c not in ("nct_version", "source")]
    assert {c: row[c] for c in compared} == {c: stored[c] for c in compared}


def test_audit_reports_counts_without_names(
    warehouse: Path, dataset: str, cfg: ProjectConfig, tmp_path: Path
) -> None:
    with duckdb.connect(str(warehouse), read_only=True) as con:
        text = render_audit(con, dataset, cfg, parity_path=tmp_path / "none.json")
        assert render_audit(con, dataset, cfg, parity_path=tmp_path / "none.json") == text
    for heading in (
        "## Schema drift",
        "## Duplicates",
        "## Version order and post dates",
        "## Impossible and implausible dates",
        "## Enum drift",
        "## Status reversals",
        "## Text normalization and personal data",
        "## Validation (Pandera)",
    ):
        assert heading in text
    for secret in (INVESTIGATOR, INDIVIDUAL, PERSON_LIKE, EMAIL, "Acme"):
        assert secret not in text
    assert "| `is_ppsd` | BOOLEAN in 1; INTEGER in 1 | 5 | BOOLEAN |" in text
    assert "Duplicate (nct_id, nct_version) keys in the source: 1 (2 rows)." in text
    assert "| COMPLETED | 1 | 1 | 0 | 0 |" in text  # the reopened trial
    assert "| Posted on the same day as the previous version | 1 | 1 |" in text
    assert "SEXTUPLE 1 (2018 to 2018) **not canonical**" in text
    assert "—" not in text


def test_a_missing_download_is_refused(tmp_path: Path, cfg: ProjectConfig) -> None:
    with pytest.raises(RefusedError, match="not downloaded"):
        dataset_glob(cfg, raw_dir=tmp_path)


def test_the_audit_command_refuses_without_a_warehouse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trialpulse.warehouse import audit

    monkeypatch.setattr(audit, "WAREHOUSE_PATH", tmp_path / "missing.duckdb")
    with pytest.raises(RefusedError, match="no warehouse"):
        audit.main([])


def test_audit_includes_the_parity_sample_with_causes(
    warehouse: Path, dataset: str, cfg: ProjectConfig, tmp_path: Path
) -> None:
    parity = {
        "dataset_revision": cfg.dataset.revision,
        "checked_on": "2026-09-30",
        "sampled": 3,
        "compared": 2,
        "identical_rows": 1,
        "per_column": {
            "overall_status": {"agree": 1, "total": 2},
            "sex": {"agree": 2, "total": 2},
        },
        "mismatches": [
            {"nct_id": "NCT00000009", "columns": ["overall_status"], "cause": "known reason"}
        ],
    }
    path = tmp_path / "parity.json"
    path.write_text(json.dumps(parity), encoding="utf-8")
    with duckdb.connect(str(warehouse), read_only=True) as con:
        text = render_audit(con, dataset, cfg, parity_path=path)
    assert "## Source parity sample (API v2)" in text
    assert "Differences without a known cause: 0." in text
    assert "| `overall_status` | 1 of 2 |" in text
    assert "`sex`" not in text.split("## Source parity sample")[1]
    assert "- NCT00000009: overall_status; known reason" in text
    stale = {**parity, "dataset_revision": "v1999.01.01"}
    path.write_text(json.dumps(stale), encoding="utf-8")
    with duckdb.connect(str(warehouse), read_only=True) as con:
        assert "Source parity" not in render_audit(con, dataset, cfg, parity_path=path)


def test_the_api_v2_path_matches_a_version_shown_as_unknown(warehouse: Path) -> None:
    """NCT00000006: the registry's UNKNOWN over a submitted RECRUITING, from both sources."""
    with duckdb.connect(str(warehouse), read_only=True) as con:
        cursor = con.execute("SELECT * FROM versions WHERE nct_id = 'NCT00000006'")
        names = [d[0] for d in cursor.description]
        stored = dict(zip(names, cursor.fetchall()[0], strict=True))
    study = {
        "protocolSection": {
            "identificationModule": {
                "nctId": "NCT00000006",
                "briefTitle": "A Study",
                "officialTitle": "A Phase 3 Study",
                "organization": {"class": "INDUSTRY"},
            },
            "statusModule": {
                "overallStatus": "UNKNOWN",
                "lastKnownStatus": "RECRUITING",
                "statusVerifiedDate": "2016-02-02",
                "startDateStruct": {"date": "2015-04", "type": "ACTUAL"},
                "primaryCompletionDateStruct": {"date": "2017-06-30", "type": "ESTIMATED"},
                "completionDateStruct": {"date": "2017-12-31", "type": "ESTIMATED"},
                "studyFirstSubmitDate": "2015-02-27",
                "studyFirstPostDateStruct": {"date": "2015-03-02", "type": "ACTUAL"},
                "lastUpdateSubmitDate": "2016-02-02",
                "lastUpdatePostDateStruct": {"date": "2016-02-02", "type": "ACTUAL"},
            },
            "sponsorCollaboratorsModule": {
                "leadSponsor": {"name": "Acme Pharma & Co.", "class": "INDUSTRY"}
            },
            "descriptionModule": {"briefSummary": "Short summary."},
            "designModule": {
                "studyType": "INTERVENTIONAL",
                "designInfo": {
                    "allocation": "RANDOMIZED",
                    "interventionModel": "PARALLEL",
                    "primaryPurpose": "TREATMENT",
                    "maskingInfo": {"masking": "DOUBLE"},
                },
                "enrollmentInfo": {"count": 100, "type": "ESTIMATED"},
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
    row, _ = canonical_from_api_v2(study)
    assert (row["overall_status"], row["last_known_status"]) == ("UNKNOWN", "RECRUITING")
    compared = [c for c in CANONICAL_COLUMNS if c not in ("nct_version", "source")]
    assert {c: row[c] for c in compared} == {c: stored[c] for c in compared}


@pytest.mark.slow  # builds a second warehouse
def test_a_missing_and_an_empty_sponsor_name_do_not_duplicate_versions(
    tmp_path: Path, cfg: ProjectConfig
) -> None:
    """Both normalize to no name: the sponsor lookup must hold one row for them, or the
    join would stage each of these versions twice and quarantine them as duplicates."""
    from .conftest import _version, write_dataset

    rows = [
        _version("NCT00000021", 0, "2016-01-01", lead_sponsor_name=None),
        _version("NCT00000022", 0, "2016-01-01", lead_sponsor_name=""),
        _version("NCT00000023", 0, "2016-01-01"),
    ]
    glob = write_dataset(tmp_path / "raw" / "core", rows)
    path = tmp_path / "w.duckdb"
    build(glob, cfg, path, workers=1, temp_dir=tmp_path / "tmp")
    assert _query(path, "SELECT count(*) FROM versions") == [(3,)]
    assert _query(path, "SELECT count(*) FROM versions_quarantine") == [(0,)]
    assert _query(path, "SELECT count(*) FROM versions WHERE sponsor_key IS NULL") == [(2,)]


def test_large_integers_survive_the_chunk_conversion() -> None:
    """A BIGINT column with a NULL must not pass through float64 (exact only up to 2^53)."""
    import pyarrow as pa

    from trialpulse.warehouse.build import prepare_chunk

    row, _ = canonical_from_api_v2(
        {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT00000031"},
                "statusModule": {
                    "overallStatus": "RECRUITING",
                    "lastUpdatePostDateStruct": {"date": "2020-01-01"},
                },
            }
        }
    )
    base = {**row, "source": "history", "nct_version": 0}
    big = 2**53 + 1
    table = pa.Table.from_pylist(
        [
            {**base, "enrollment_count": big},
            {**base, "nct_id": "NCT00000032", "enrollment_count": None},
        ]
    )
    valid, failing = prepare_chunk(table)
    assert failing.num_rows == 0
    assert valid.column("enrollment_count").to_pylist() == [big, None]
