"""Parity layer 5b of the gap (`gap.parity`) on synthetic tables written here: hidden versions
by cause, the API records the release lacks, field differences and the scaled analog. No real
row is used."""

import datetime as dt
from typing import Any

import duckdb
import pandas as pd
import pytest

from trialpulse.gap import parity

CUTOFF, RELEASE = dt.date(2026, 9, 25), dt.date(2026, 11, 4)
D = dt.date


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    out = duckdb.connect()
    out.execute("CREATE TABLE gap_versions (nct_id VARCHAR, nct_version BIGINT, "
                "effective_date DATE, failed BOOLEAN)")  # fmt: skip
    out.executemany("INSERT INTO gap_versions VALUES (?, ?, ?, ?)", [
        ("NCT1", 5, D(2026, 10, 1), False),  # superseded by a later post date
        ("NCT1", 6, D(2026, 10, 8), False),  # same post date as version 7
        ("NCT1", 7, D(2026, 10, 8), False),  # the latest: shown
        ("NCT2", 3, D(2026, 10, 2), True),   # failed review
        ("NCT2", 4, D(2026, 10, 3), False),  # the latest public; the pull holds a later one
        ("NCT3", 1, D(2026, 10, 9), False),  # the latest; no API record
        ("NCT4", 0, D(2026, 11, 3), False),  # the latest: shown
    ])  # fmt: skip
    out.execute("CREATE TABLE api (nct_id VARCHAR, effective_date DATE)")
    out.executemany("INSERT INTO api VALUES (?, ?)", [
        ("NCT1", D(2026, 10, 8)), ("NCT2", D(2026, 11, 5)), ("NCT4", D(2026, 11, 3)),
        ("NCT5", D(2026, 11, 3)),  # a record the release lacks
    ])  # fmt: skip
    return out


def test_hidden_versions_are_counted_by_cause(con: duckdb.DuckDBPyConnection) -> None:
    found = parity.hidden(con, CUTOFF, RELEASE)
    assert found["gap_versions"] == 7
    assert found["hidden_versions"] == 3
    assert found["trials_with_a_hidden_version"] == 2
    assert found["hidden_by_cause"] == {
        "failed review": 1,
        "same post date as a later-numbered version": 1,
        "superseded by a later post date": 1,
    }
    assert found["latest_public_versions"] == {
        "other: no API record of the trial": 1,
        "shown by the pull": 2,
        "the pull holds a record posted after the release": 1,
    }


def test_records_the_release_lacks_are_counted_by_post_date(
    con: duckdb.DuckDBPyConnection,
) -> None:
    found = parity.lacking(con, CUTOFF, RELEASE)
    assert found["by_post_date"] == {
        "2026-10-08": {"records": 1, "not_in_the_release": 0},
        "2026-11-03": {"records": 2, "not_in_the_release": 1},
    }
    assert found["records_posted_after_the_release"] == 1


def test_field_differences_compare_the_last_version_of_the_post_date() -> None:
    con = duckdb.connect()
    base: dict[str, Any] = {"nct_id": "NCT1", "effective_date": D(2026, 10, 8),
                            "start_date": D(2026, 1, 1), "content_hash": "h",
                            "official_title_hash": "o", "brief_title_hash": None,
                            "official_title": "A Phase 2 Study", "brief_title": None}  # fmt: skip
    other = {**base, "nct_id": "NCT2", "official_title_hash": "x",
             "official_title": "A Phase 3 Study", "content_hash": "y"}  # fmt: skip
    api = pd.DataFrame([base, other])
    rel = pd.DataFrame([
        {**base, "nct_version": 6, "start_date": D(2025, 1, 1), "content_hash": "z"},
        {**base, "nct_version": 7},  # the last of the day: the one compared
        {**base, "nct_id": "NCT2", "nct_version": 0},
    ])  # fmt: skip
    con.register("api", api)
    con.register("rel_versions", rel)
    found = parity.field_differences(con, ["start_date", "official_title_hash", "brief_title_hash"])
    assert found["versions_both_hold"] == 2
    assert found["content_hash_equal"] == 1
    assert found["by_column"] == {"official_title_hash": 1}
    assert found["versions_whose_titles_state_another_phase"] == 1
    assert found["versions_apart_in_a_feature_column_but_titles"] == 0


def test_the_analog_is_scaled_to_the_gap() -> None:
    found = parity.scaled_analog(40)
    assert found["scaled_hidden_versions"] == round(17_748 * 40 / 67)
    assert found["scaled_scored_rows_apart"] == round(4_932 * 40 / 67)


def test_the_parity_refuses_on_a_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    from trialpulse.cli import RefusedError

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(RefusedError, match="ADR 0040"):
        parity.main([])
