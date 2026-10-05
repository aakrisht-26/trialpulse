"""Hand-built mini histories for the cohort tests. No real data."""

import datetime as dt
from typing import Any

import duckdb
import pandas as pd
import pytest

from trialpulse.cohort.build import build_cohort
from trialpulse.cohort.rules import CohortRules, add_months
from trialpulse.config import load_project_config

T0 = dt.date(2015, 1, 1)
CUTOFF = dt.date(2026, 9, 25)
VERSION_TYPES = {
    "nct_id": "VARCHAR",
    "nct_version": "BIGINT",
    "effective_date": "DATE",
    "overall_status": "VARCHAR",
    "last_known_status": "VARCHAR",
    "study_type": "VARCHAR",
    "study_first_post_date": "DATE",
    "status_verified_date": "DATE",
    "start_date": "DATE",
    "completion_date": "DATE",
    "completion_date_precision": "VARCHAR",
    "primary_completion_date": "DATE",
    "primary_completion_date_precision": "VARCHAR",
    "lead_sponsor_class": "VARCHAR",
}


def month(n: int, base: dt.date = T0) -> dt.date:
    return add_months(base, n)


def version(nct: str, n: int, posted: dt.date, status: str, **values: Any) -> dict[str, Any]:
    """A canonical version with safe defaults: interventional, verified when posted, and a
    completion date far away, so nothing lapses unless a test says so."""
    row: dict[str, Any] = {
        "nct_id": nct,
        "nct_version": n,
        "effective_date": posted,
        "overall_status": status,
        "last_known_status": None,
        "study_type": "INTERVENTIONAL",
        "study_first_post_date": T0,
        "status_verified_date": posted,
        "start_date": T0,
        "completion_date": dt.date(2030, 1, 1),
        "completion_date_precision": "day",
        "primary_completion_date": None,
        "primary_completion_date_precision": None,
        "lead_sponsor_class": "INDUSTRY",
    }
    row.update(values)
    return row


def load_versions(con: duckdb.DuckDBPyConnection, rows: list[dict[str, Any]]) -> None:
    frame = pd.DataFrame(rows, columns=list(VERSION_TYPES)).astype(object)
    con.register("versions_frame", frame)
    columns = ", ".join(f"CAST({c} AS {t}) AS {c}" for c, t in VERSION_TYPES.items())
    con.execute(f"CREATE OR REPLACE TABLE versions AS SELECT {columns} FROM versions_frame")
    con.unregister("versions_frame")


@pytest.fixture
def rules() -> CohortRules:
    return CohortRules.from_config(load_project_config(), cutoff=CUTOFF)


def cohort(rows: list[dict[str, Any]], rules: CohortRules) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    load_versions(con, rows)
    build_cohort(con, rules)
    return con
