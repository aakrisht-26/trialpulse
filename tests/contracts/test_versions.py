"""The canonical version schema: parsing, hashing, validation and the API v2 mapping."""

import datetime as dt
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pytest
from pandera.errors import SchemaErrors

from trialpulse.cohort.rules import CohortRules, submitted_status
from trialpulse.config import load_project_config
from trialpulse.contracts.text import EMAIL_MARKER, text_hash
from trialpulse.contracts.versions import (
    API_V2_PATHS,
    CANONICAL_COLUMNS,
    CONTENT_COLUMNS,
    HASHED_FIELDS,
    STATUSES,
    SUBMITTED_STATUS,
    FieldTypeError,
    age_in_years,
    api_v2_fields,
    canonical_enum,
    canonical_from_api_v2,
    content_hash,
    content_hashes,
    distinct_intervention_types,
    parse_partial_date,
    split_valid,
    strip_space_separators,
    submitted_statuses,
    typed_frame,
    valid_date,
)
from trialpulse.warehouse.build import _sql_enum, _sql_valid, intervention_lists_sql


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
    # Only space separators (Unicode Zs) are trimmed, on either path: a tab or a line break
    # stays, so the value fails the contract on both (the two paths agree on every input).
    nbsp, ideographic, zero_width = chr(0xA0), chr(0x3000), chr(0x200B)
    values += ["drug\t", "\ndrug", " drug\r\n", "\t", "  ", f"{nbsp}drug{ideographic}"]
    values += [f"drug{zero_width}", "drug\x85", f"drug{chr(0x2028)}", "\x1fdrug"]
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
    assert canonical_enum(f"{nbsp}drug{ideographic}") == "DRUG"
    assert canonical_enum("drug\t") == "DRUG\t"


def test_space_separators_are_exactly_what_duckdb_trims() -> None:
    """Every code point of the Basic Multilingual Plane, padded on both sides, is trimmed by
    DuckDB exactly when the contract's Python rule trims it."""
    points = [cp for cp in range(0x10000) if not 0xD800 <= cp <= 0xDFFF]
    with duckdb.connect() as con:
        rows = con.execute(
            "SELECT cp FROM range(65536) t(cp) WHERE (cp < 55296 OR cp > 57343) "
            "AND trim(chr(cp::INTEGER) || 'x' || chr(cp::INTEGER)) = 'x'"
        ).fetchall()
    trimmed = {int(cp) for (cp,) in rows}
    expected = {cp for cp in points if strip_space_separators(f"{chr(cp)}x{chr(cp)}") == "x"}
    assert trimmed == expected
    assert {0x20, 0xA0, 0x3000} <= trimmed
    assert not {0x09, 0x0A, 0x0D, 0x200B} & trimmed


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
    for column, other in (
        ("enrollment_count", 41),
        ("healthy_volunteers", False),
        ("overall_status", "ACTIVE_NOT_RECRUITING"),
    ):
        assert content_hash({**row, column: other}) != row["content_hash"]
    assert "content_hash" not in CONTENT_COLUMNS


def test_the_hash_reads_the_submitted_status_not_the_registrys_label() -> None:
    """ADR 0066: a record the registry relabels UNKNOWN without a new version, with the same
    submitted status, hashes as before; both status columns stay in the row."""
    recruiting, _ = canonical_from_api_v2(_study())
    relabeled, _ = canonical_from_api_v2(
        _study(overall_status="UNKNOWN", last_known_status="RECRUITING")
    )
    assert (relabeled["overall_status"], relabeled["last_known_status"]) == (
        "UNKNOWN",
        "RECRUITING",
    )
    assert relabeled["content_hash"] == recruiting["content_hash"]
    other, _ = canonical_from_api_v2(
        _study(overall_status="UNKNOWN", last_known_status="ACTIVE_NOT_RECRUITING")
    )
    assert other["content_hash"] != recruiting["content_hash"]
    bare, _ = canonical_from_api_v2(_study(overall_status="UNKNOWN"))  # no submitted status left
    assert bare["content_hash"] not in (recruiting["content_hash"], other["content_hash"])
    frame = typed_frame(pd.DataFrame([recruiting, relabeled, other, bare]))
    hashes = content_hashes(frame)
    assert hashes == [r["content_hash"] for r in (recruiting, relabeled, other, bare)]
    assert SUBMITTED_STATUS in HASHED_FIELDS
    assert not {"overall_status", "last_known_status"} & set(HASHED_FIELDS)
    assert {"overall_status", "last_known_status"} <= set(CONTENT_COLUMNS)


def test_the_contract_and_the_cohort_agree_on_the_submitted_status() -> None:
    """The hash's rule is the one ADR 0014 sets and the cohort and the features apply."""
    rules = CohortRules.from_config(load_project_config())
    pairs = [(o, k) for o in STATUSES for k in (None, *STATUSES)]
    frame = pd.DataFrame(pairs, columns=["overall_status", "last_known_status"], dtype=object)
    shown = submitted_statuses(frame["overall_status"], frame["last_known_status"])
    assert shown.tolist() == [submitted_status(rules, o, k) for o, k in pairs]


def _with_interventions(study: dict[str, Any], interventions: Any) -> dict[str, Any]:
    study["protocolSection"]["armsInterventionsModule"] = {"interventions": interventions}
    return study


def test_api_v2_interventions_become_one_type_per_distinct_pair() -> None:
    """ADR 0067: exact strings, a repeated pair once, the type in canonical form, a pair
    without a type left out, sorted; an empty list when none is listed. ADR 0073: a pair
    without a name (missing or empty) is left out too, as the pinned config held none."""
    interventions = [
        {"name": "Aspirin", "type": "DRUG"},
        {"name": "Aspirin", "type": "DRUG"},  # a repeated pair counts once
        {"name": "aspirin", "type": "DRUG"},  # another name on exact strings
        {"name": "Walk", "type": " behavioral "},
        {"name": "Unnamed", "type": None},  # no type: left out
        {"type": "DEVICE"},  # no name: left out (ADR 0073)
        {"name": "", "type": "OTHER"},  # an empty name: left out
        {"name": None, "type": "DRUG"},
    ]
    row, _ = canonical_from_api_v2(_with_interventions(_study(), interventions))
    assert row["intervention_types"] == ["BEHAVIORAL", "DRUG", "DRUG"]
    valid, failing = split_valid(typed_frame(pd.DataFrame([row])))
    assert len(valid) == 1
    assert failing.empty
    none, _ = canonical_from_api_v2(_study())
    assert none["intervention_types"] == []
    assert none["content_hash"] != row["content_hash"]  # the hash covers the types
    fields = api_v2_fields().split(",")
    for key in ("name", "type"):
        assert f"protocolSection.armsInterventionsModule.interventions.{key}" in fields


@pytest.mark.parametrize(
    ("value", "field"),
    [
        ({"name": "A"}, "interventions"),
        (["A"], "interventions"),
        ([{"name": ["A"], "type": "DRUG"}], "intervention_name"),
        ([{"name": "A", "type": 3}], "intervention_type"),
    ],
)
def test_interventions_of_the_wrong_json_type_fail_the_record(value: Any, field: str) -> None:
    with pytest.raises(FieldTypeError) as caught:
        canonical_from_api_v2(_with_interventions(_study(), value))
    assert caught.value.field == field
    assert str(value) not in str(caught.value)


def test_intervention_types_outside_the_contract_or_unsorted_are_quarantined() -> None:
    row, _ = canonical_from_api_v2(_study())
    rows = [
        {**row, "intervention_types": ["DEVICE", "DRUG"]},
        {**row, "nct_id": "NCT07654321", "intervention_types": ["GENE_EDIT"]},
        {**row, "nct_id": "NCT07654322", "intervention_types": ["DRUG", "DEVICE"]},
        {**row, "nct_id": "NCT07654323", "intervention_types": None},
        # Arrow hands lists over as numpy arrays.
        {**row, "nct_id": "NCT07654324", "intervention_types": np.array(["DRUG"], dtype=object)},
    ]
    valid, failing = split_valid(typed_frame(pd.DataFrame(rows)))
    assert list(valid["nct_id"]) == ["NCT01234567", "NCT07654324"]
    assert dict(zip(failing["nct_id"], failing["reasons"], strict=True)) == {
        "NCT07654321": "intervention_types: known_intervention_types",
        "NCT07654322": "intervention_types: sorted_intervention_types",
        "NCT07654323": "intervention_types: not_nullable",
    }


def test_the_sql_rule_for_intervention_types_matches_the_python_rule() -> None:
    pairs = [
        ("A", "DRUG"), ("A", "DRUG"), ("a", "DRUG"), ("B", " device "), ("C", None),
        (None, "OTHER"), (None, "OTHER"), ("D", ""), ("E", "drug"), ("F", "drug\t"),
        ("G", "\nbiological"), ("", "DEVICE"), ("H", "other"),
    ]  # fmt: skip
    with duckdb.connect() as con:
        con.execute(
            "CREATE TABLE config (nct_id VARCHAR, nct_version BIGINT, intervention_name VARCHAR, "
            "intervention_type VARCHAR)"
        )
        con.executemany("INSERT INTO config VALUES ('NCT00000001', 0, ?, ?)", pairs)
        con.execute("INSERT INTO config VALUES ('NCT00000002', 0, 'X', NULL)")  # no type at all
        con.execute("INSERT INTO config VALUES ('NCT00000003', 0, NULL, 'DRUG')")  # no name
        got = {(n, v): t for n, v, t in con.execute(intervention_lists_sql("config")).fetchall()}
    expected = distinct_intervention_types((name, canonical_enum(kind)) for name, kind in pairs)
    # A tab or a line break is not trimmed on either path: the list keeps the raw type, which
    # the contract then refuses (known_intervention_types) on both paths alike.
    # A pair without a name, missing or empty, is left out on both paths (ADR 0073).
    assert expected == ["\nBIOLOGICAL", "DEVICE", "DRUG", "DRUG", "DRUG", "DRUG\t", "OTHER"]
    assert got == {("NCT00000001", 0): expected}  # the warehouse gives the others empty lists


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
        # A key that names a person by title, as a row not built by `sponsor` could carry.
        {**base, "nct_id": "NCT07654326", "sponsor_key": "dr a example"},
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
        "NCT07654326/8": "row: no_personal_title_sponsor_key",
    }


def test_a_structural_failure_raises_instead_of_quarantining() -> None:
    frame = _history_rows().drop(columns=["sex"])
    with pytest.raises(SchemaErrors):
        split_valid(frame)
