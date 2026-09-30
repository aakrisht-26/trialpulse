"""A small synthetic version-history dataset in the core config's layout. No real data."""

from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest

from trialpulse.config import ProjectConfig, load_project_config
from trialpulse.contracts.versions import TEXT_FIELDS
from trialpulse.warehouse.build import PERSONAL_COLUMNS, RAW_COLUMNS, build

INVESTIGATOR = "Dr Hidden Investigator"
INDIVIDUAL = "Jane Doe"
PERSON_LIKE = "John Smith, MD"
EMAIL = "pi@hospital.org"
ELIGIBILITY_HTML = (
    "<p>Inclusion Criteria:</p><ul><li>Age &gt;= 18</li>"
    f"<li>Contact {EMAIL} for details.\nWalk daily.</li></ul>"
)
# The same criteria as API v2 would return them.
ELIGIBILITY_MARKDOWN = (
    "Inclusion Criteria:\n\n* Age \\>= 18\n* Contact pi@hospital.org for details. Walk daily."
)


def _version(nct_id: str, version: int, posted: str, **values: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "nct_id": nct_id,
        "nct_version": version,
        "last_update_post_date": posted,
        "last_update_post_date_type": "ACTUAL",
        "last_update_submit_date": posted,
        "overall_status": "RECRUITING",
        "status_verified_date": posted,
        "study_type": "INTERVENTIONAL",
        "study_first_post_date": "2015-03-02",
        "study_first_post_date_type": "ACTUAL",
        "study_first_submit_date": "2015-02-27",
        "start_date": "2015-04-01",
        "start_date_precision": "month",
        "start_date_type": "ACTUAL",
        "primary_completion_date": "2017-06-30",
        "primary_completion_date_precision": "day",
        "primary_completion_date_type": "ESTIMATED",
        "completion_date": "2017-12-31",
        "completion_date_precision": "day",
        "completion_date_type": "ESTIMATED",
        "allocation": "RANDOMIZED",
        "intervention_model": "PARALLEL",
        "primary_purpose": "TREATMENT",
        "masking": "DOUBLE",
        "healthy_volunteers": False,
        "sex": "ALL",
        "minimum_age": "18 Years",
        "maximum_age": None,
        "enrollment_count": 100,
        "enrollment_type": "ESTIMATED",
        "lead_sponsor_class": "INDUSTRY",
        "organization_class": "INDUSTRY",
        "disp_first_submit_qc_date": None,
        "is_ppsd": None,
        "fdaaa801_violation": None,
        "brief_title": "A Study",
        "official_title": "A Phase 3 Study",
        "brief_summary": "Short summary.",
        "eligibility_criteria": ELIGIBILITY_MARKDOWN,
        "why_stopped": None,
        "lead_sponsor_name": "Acme Pharma &amp; Co.",
        "responsible_party_investigator_full_name": INVESTIGATOR,
        "responsible_party_investigator_title": "Professor",
        "responsible_party_investigator_affiliation": "Somewhere University",
        "responsible_party_old_name_title": INVESTIGATOR,
        "detailed_description": "Not loaded.",
    }
    row.update(values)
    return row


ROWS: list[dict[str, Any]] = [
    # An individual sponsor, HTML criteria with an email, placeholder completion date, stops.
    _version(
        "NCT00000001", 0, "2015-03-02",
        lead_sponsor_name=INDIVIDUAL, lead_sponsor_class="INDIV", organization_class="INDIV",
        eligibility_criteria=ELIGIBILITY_HTML, completion_date="2100-12-31",
        maximum_age="6 Months",
    ),
    _version(
        "NCT00000001", 1, "2016-01-10",
        lead_sponsor_name=INDIVIDUAL, lead_sponsor_class="INDIV", organization_class="INDIV",
        eligibility_criteria=ELIGIBILITY_HTML, overall_status="TERMINATED",
        why_stopped="Slow accrual &amp; funding", enrollment_count=12, enrollment_type="ACTUAL",
    ),
    # A person's name under class OTHER; completed, then reopened (a reversal).
    _version("NCT00000002", 0, "2015-03-02", lead_sponsor_name=PERSON_LIKE,
             lead_sponsor_class="OTHER", organization_class="OTHER"),
    _version("NCT00000002", 1, "2017-02-01", lead_sponsor_name=PERSON_LIKE,
             lead_sponsor_class="OTHER", overall_status="COMPLETED"),
    _version("NCT00000002", 2, "2017-02-01", lead_sponsor_name=PERSON_LIKE,
             lead_sponsor_class="OTHER", overall_status="RECRUITING"),
    # A legacy spelling, and a duplicated version key (both rows are quarantined).
    _version("NCT00000003", 0, "2016-05-05", enrollment_type="ANTICIPATED"),
    _version("NCT00000003", 1, "2016-06-06"),
    _version("NCT00000003", 1, "2016-06-07"),
    # A value outside the canonical set.
    _version("NCT00000004", 0, "2018-01-01", masking="SEXTUPLE"),
    # Ordinary, with the Markdown form of NCT00000001's criteria and missing enrollment.
    _version("NCT00000005", 0, "2019-09-09", enrollment_count=None, enrollment_type=None,
             study_type="OBSERVATIONAL", allocation=None, masking=None),
]  # fmt: skip

TYPES = {**{c: kind for c, (kind, _) in RAW_COLUMNS.items()}}
TYPES.update(dict.fromkeys((*TEXT_FIELDS, "lead_sponsor_name", *PERSONAL_COLUMNS), "VARCHAR"))
TYPES["detailed_description"] = "VARCHAR"


def write_dataset(folder: Path, rows: list[dict[str, Any]] = ROWS) -> str:
    """Two Parquet files; the second types the drifting columns as INTEGER, as in the real
    dataset."""
    folder.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows).astype(object)
    halves = {
        "part-00000-of-00002.parquet": frame.iloc[:6],
        "part-00001-of-00002.parquet": frame.iloc[6:],
    }
    with duckdb.connect() as con:
        for index, (name, part) in enumerate(halves.items()):
            types = dict(TYPES)
            if index == 1:
                types.update({"disp_first_submit_qc_date": "INTEGER", "is_ppsd": "INTEGER"})
                part = part.assign(is_ppsd=1)
            con.register("part", part)
            columns = ", ".join(f"CAST({c} AS {kind}) AS {c}" for c, kind in types.items())
            con.execute(f"COPY (SELECT {columns} FROM part) TO '{(folder / name).as_posix()}'")
            con.unregister("part")
    return (folder / "*.parquet").as_posix()


@pytest.fixture
def cfg() -> ProjectConfig:
    return load_project_config()


@pytest.fixture
def dataset(tmp_path: Path) -> str:
    return write_dataset(tmp_path / "raw" / "core")


@pytest.fixture
def warehouse(tmp_path: Path, dataset: str, cfg: ProjectConfig) -> Path:
    path = tmp_path / "warehouse.duckdb"
    build(dataset, cfg, path, workers=1, temp_dir=tmp_path / "tmp")
    return path
