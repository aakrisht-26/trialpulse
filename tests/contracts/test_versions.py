"""The canonical version schema: parsing, hashing, validation and the API v2 mapping."""

import datetime as dt
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pytest
from pandera.errors import SchemaErrors

from trialpulse.contracts.text import EMAIL_MARKER, text_hash
from trialpulse.contracts.versions import (
    API_V2_PATHS,
    CANONICAL_COLUMNS,
    CONTENT_COLUMNS,
    age_in_years,
    api_v2_fields,
    canonical_enum,
    canonical_from_api_v2,
    content_hash,
    content_hashes,
    parse_partial_date,
    split_valid,
    typed_frame,
    valid_date,
)
from trialpulse.warehouse.build import _sql_enum, _sql_valid


@pytest.mark.parametrize(
    ("text", "value", "precision"),
    [
        ("2019", dt.date(2019, 1, 1), "year"),
        ("2019-03", dt.date(2019, 3, 1), "month"),
        ("2019-03-14", dt.date(2019, 3, 14), "day"),
        ("1900-01-01", None, None),  # placeholder
        ("2100-12-31", None, None),  # placeholder
        ("2099-12-31", dt.date(2099, 12, 31), "day"),
        ("2019-13", None, None),
        ("March 2019", None, None),
        ("", None, None),
        (None, None, None),
    ],
)
def test_partial_dates(text: str | None, value: dt.date | None, precision: str | None) -> None:
    parsed = parse_partial_date(text)
    assert (parsed.value, parsed.precision) == (value, precision)


@pytest.mark.parametrize(
    ("text", "years"),
    [
        ("18 Years", 18.0),
        ("1 Year", 1.0),
        ("6 Months", 0.5),
        ("14 Days", 14 / 365.25),
        ("2 weeks", 14 / 365.25),
        ("  65   Years ", 65.0),
        ("N/A", None),
        ("", None),
        (None, None),
        ("eighteen years", None),
    ],
)
def test_ages_in_years(text: str | None, years: float | None) -> None:
    assert age_in_years(text) == (pytest.approx(years) if years is not None else None)


def test_sql_twins_match_the_python_rules() -> None:
    values = ["RANDOMIZED", " randomized ", "ANTICIPATED", "anticipated", "N/A", "", None, "X"]
    dates = [dt.date(1900, 1, 1), dt.date(1901, 1, 1), dt.date(2099, 12, 31), dt.date(2100, 1, 1)]
    with duckdb.connect() as con:
        for value in values:
            sql = con.execute(f"SELECT {_sql_enum('v')} FROM (SELECT ?::VARCHAR AS v)", [value])
            assert sql.fetchone() == (canonical_enum(value),), value
        for day in dates:
            sql = con.execute(f"SELECT {_sql_valid('d')} FROM (SELECT ?::DATE AS d)", [day])
            assert sql.fetchone() == (valid_date(day),), day
    assert canonical_enum("anticipated") == "ESTIMATED"
    assert canonical_enum("N/A") == "NA"


def _study(**overrides: Any) -> dict[str, Any]:
    """A synthetic API v2 record: Markdown text, escapes, an individual sponsor."""
    values = {
        "nct_id": "NCT01234567",
        "organization_class": "OTHER",
        "brief_title": "A Study of Walking",
        "official_title": "A Phase 2 Study of Walking \\(Pilot\\)",
        "overall_status": "RECRUITING",
        "why_stopped": None,
        "status_verified_date": "2024-05",
        "start_date": "2024-01",
        "start_date_type": "ACTUAL",
        "primary_completion_date": "2025-06-30",
        "primary_completion_date_type": "ESTIMATED",
        "completion_date": "2100-12-31",
        "completion_date_type": "ESTIMATED",
        "study_first_submit_date": "2023-12-01",
        "study_first_post_date": "2023-12-05",
        "study_first_post_date_type": "ACTUAL",
        "submitted_date": "2024-05-02",
        "effective_date": "2024-05-06",
        "effective_date_type": "ACTUAL",
        "lead_sponsor_name": "Jane Doe",
        "lead_sponsor_class": "INDIV",
        "brief_summary": "Walking helps.\nIt is free.",
        "study_type": "INTERVENTIONAL",
        "allocation": "RANDOMIZED",
        "intervention_model": "PARALLEL",
        "primary_purpose": "PREVENTION",
        "masking": "NONE",
        "enrollment_count": 40,
        "enrollment_type": "ESTIMATED",
        "eligibility_criteria": "Inclusion:\n\n* Age \\>= 18\n* Contact: pi@site.org",
        "healthy_volunteers": True,
        "sex": "ALL",
        "minimum_age": "18 Years",
        "maximum_age": "6 Months",
    }
    values.update(overrides)
    study: dict[str, Any] = {"protocolSection": {}}
    for name, path in API_V2_PATHS.items():
        if values.get(name) is None:
            continue
        node = study["protocolSection"]
        *parents, leaf = path.split(".")
        for key in parents:
            node = node.setdefault(key, {})
        node[leaf] = values[name]
    return study


def test_api_v2_record_maps_to_a_valid_canonical_row() -> None:
    row, texts = canonical_from_api_v2(_study())
    assert tuple(row) == CANONICAL_COLUMNS
    assert row["source"] == "api_v2"
    assert row["nct_version"] is None
    assert row["effective_date"] == dt.date(2024, 5, 6)
    assert (row["start_date"], row["start_date_precision"]) == (dt.date(2024, 1, 1), "month")
    assert (row["completion_date"], row["completion_date_type"]) == (None, None)  # placeholder
    assert (row["minimum_age_years"], row["maximum_age_years"]) == (18.0, 0.5)
    assert (row["lead_sponsor_name"], row["sponsor_key"], row["sponsor_is_individual"]) == (
        None,
        None,
        True,
    )
    assert texts[row["official_title_hash"]] == "A Phase 2 Study of Walking (Pilot)"
    assert texts[row["brief_summary_hash"]] == "Walking helps. It is free."
    assert (
        texts[row["eligibility_criteria_hash"]] == f"Inclusion:\nAge >= 18\nContact: {EMAIL_MARKER}"
    )
    assert row["why_stopped_hash"] is None
    assert row["brief_title_hash"] == text_hash("A Study of Walking")
    valid, failing = split_valid(typed_frame(pd.DataFrame([row])))
    assert len(valid) == 1
    assert failing.empty
    assert all(path.startswith("protocolSection.") for path in api_v2_fields().split(","))


def test_content_hash_ignores_value_types_but_not_values() -> None:
    row, _ = canonical_from_api_v2(_study())
    as_pandas = {
        **row,
        "effective_date": pd.Timestamp(row["effective_date"]),
        "enrollment_count": np.int64(40),
        "healthy_volunteers": np.bool_(True),
        "minimum_age_years": np.float64(18.0),
        "why_stopped_hash": float("nan"),
    }
    assert content_hash(as_pandas) == row["content_hash"]
    assert content_hashes(typed_frame(pd.DataFrame([row]))) == [row["content_hash"]]
    # The version number and the source are not content.
    assert content_hash({**row, "nct_version": 7, "source": "history"}) == row["content_hash"]
    for column, other in (("enrollment_count", 41), ("healthy_volunteers", False)):
        assert content_hash({**row, column: other}) != row["content_hash"]
    assert "content_hash" not in CONTENT_COLUMNS


def _history_rows() -> pd.DataFrame:
    row, _ = canonical_from_api_v2(_study(lead_sponsor_name="Acme", lead_sponsor_class="OTHER"))
    base = {**row, "source": "history", "nct_version": 0}
    rows = [
        base,
        {**base, "nct_version": 1},
        {**base, "nct_id": "NCT07654321", "masking": "SEXTUPLE"},  # not canonical
        {**base, "nct_id": "NCT07654322"},  # duplicate version key, with the next row
        {**base, "nct_id": "NCT07654322"},
        {**base, "nct_id": "NCT07654323", "sponsor_is_individual": True},  # name kept
        {**base, "nct_id": "NCT07654324", "start_date_precision": None},  # no precision
        {**base, "nct_id": "NCT07654325", "nct_version": None},  # history without version
    ]
    return typed_frame(pd.DataFrame(rows))


def test_invalid_rows_are_quarantined_with_reasons() -> None:
    valid, failing = split_valid(_history_rows())
    assert list(valid["nct_id"]) == ["NCT01234567", "NCT01234567"]
    keys = failing["nct_id"] + "/" + failing.index.astype(str)
    reasons = dict(zip(keys, failing["reasons"], strict=True))
    assert reasons == {
        "NCT07654321/2": "masking: isin(('NONE', 'SINGLE', 'DOUBLE', 'TRIPLE', 'QUADRUPLE'))",
        "NCT07654322/3": "row: unique_history_version",
        "NCT07654322/4": "row: unique_history_version",
        "NCT07654323/5": "row: no_individual_sponsor_name",
        "NCT07654324/6": "row: precision_and_type_match_date",
        "NCT07654325/7": "row: history_row_has_version",
    }


def test_a_structural_failure_raises_instead_of_quarantining() -> None:
    frame = _history_rows().drop(columns=["sex"])
    with pytest.raises(SchemaErrors):
        split_valid(frame)
