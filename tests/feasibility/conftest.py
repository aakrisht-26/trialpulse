"""Synthetic fixtures for the feasibility spike tests. No network, no real data."""

from pathlib import Path

import duckdb
import httpx
import pytest
from tenacity import wait_none

from trialpulse.config import ProjectConfig, load_project_config
from trialpulse.feasibility.fetch import JsonCache, JsonFetcher, RateLimiter

# (nct_id, version, status, study_type, first_post, last_update_post, post_type, why_stopped,
#  start_date, start_precision, primary_completion, enrollment, sponsor_class, eligibility)
HISTORY_ROWS = [
    # Terminated with a reason; first posted 2010.
    ("NCT00000001", 0, "RECRUITING", "INTERVENTIONAL", "2010-05-01", "2010-05-01", "ESTIMATED",
     None, "2010-06-01", "month", "2012-01-01", 100, "INDUSTRY", "Inclusion: adults"),
    ("NCT00000001", 1, "TERMINATED", "INTERVENTIONAL", "2010-05-01", "2012-03-01", "ACTUAL",
     "Slow accrual", "2010-06-01", "month", "2012-01-01", 12, "INDUSTRY", "Inclusion: adults"),
    # Completed; first posted 2015.
    ("NCT00000002", 0, "RECRUITING", "INTERVENTIONAL", "2015-01-10", "2015-01-10", "ACTUAL",
     None, "2015-02-01", "day", "2016-12-31", 50, "OTHER", None),
    ("NCT00000002", 1, "COMPLETED", "INTERVENTIONAL", "2015-01-10", "2017-02-01", "ACTUAL",
     None, "2015-02-01", "day", "2016-12-31", 48, "OTHER", None),
    # Observational: outside the population.
    ("NCT00000003", 0, "COMPLETED", "OBSERVATIONAL", "2011-03-03", "2011-03-03", "ACTUAL",
     None, "2011-01-01", "month", None, 10, "OTHER", None),
    # First posted before 2008: outside the population.
    ("NCT00000004", 0, "TERMINATED", "INTERVENTIONAL", "2007-06-01", "2007-06-01", "ESTIMATED",
     "Funding", "2007-01-01", "month", None, 5, "NIH", None),
    # Withdrawn without a reason; one version has no post date.
    ("NCT00000005", 0, "NOT_YET_RECRUITING", "INTERVENTIONAL", "2019-04-01", "2019-04-01",
     "ACTUAL", None, "2019-06-01", "month", "2020-06-01", 30, "INDUSTRY", None),
    ("NCT00000005", 1, "WITHDRAWN", "INTERVENTIONAL", "2019-04-01", None, None,
     None, "2019-06-01", "month", "2020-06-01", 0, "INDUSTRY", None),
    ("NCT00000005", 2, "WITHDRAWN", "INTERVENTIONAL", "2019-04-01", "2019-09-09", "ACTUAL",
     "  ", "2019-06-01", "month", "2020-06-01", 0, "INDUSTRY", None),
    # Unknown status.
    ("NCT00000006", 0, "UNKNOWN", "INTERVENTIONAL", "2020-02-02", "2020-02-02", "ACTUAL",
     None, "2020-03-01", "month", "2021-01-01", 80, "OTHER", None),
    # Still open.
    ("NCT00000007", 0, "RECRUITING", "INTERVENTIONAL", "2021-07-07", "2021-07-07", "ACTUAL",
     None, "2021-08-01", "day", "2023-01-01", 200, "NETWORK", None),
]  # fmt: skip


@pytest.fixture
def cfg() -> ProjectConfig:
    return load_project_config()


@pytest.fixture
def history_glob(tmp_path: Path) -> str:
    """A tiny version-history Parquet file with the dataset card's column names."""
    path = tmp_path / "core" / "part-00000-of-00001.parquet"
    path.parent.mkdir(parents=True)
    with duckdb.connect() as con:
        con.execute(
            """CREATE TABLE h (
                nct_id VARCHAR, nct_version INTEGER, overall_status VARCHAR, study_type VARCHAR,
                study_first_post_date DATE, last_update_post_date DATE,
                last_update_post_date_type VARCHAR, why_stopped VARCHAR, start_date DATE,
                start_date_precision VARCHAR, primary_completion_date DATE,
                enrollment_count INTEGER, lead_sponsor_class VARCHAR,
                eligibility_criteria VARCHAR)"""
        )
        con.executemany(
            "INSERT INTO h VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", HISTORY_ROWS
        )
        con.execute(f"COPY h TO '{path.as_posix()}' (FORMAT parquet)")
    return (path.parent / "*.parquet").as_posix()


@pytest.fixture
def cache(tmp_path: Path) -> JsonCache:
    return JsonCache(tmp_path / "cache")


@pytest.fixture
def fast_fetcher() -> JsonFetcher:
    """A fetcher that never sleeps: no rate-limit pauses and no backoff waits."""
    return JsonFetcher(
        httpx.Client(),
        RateLimiter(60_000, sleep=lambda _: None),
        max_attempts=3,
        wait=wait_none(),
        sleep=lambda _: None,
    )
