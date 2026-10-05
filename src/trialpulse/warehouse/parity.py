"""Source parity: the API v2 path against the dataset path, on a seeded sample of trials.

    uv run python -m trialpulse.warehouse.parity --sample 200

For a seeded sample of interventional trials in the warehouse, fetch each trial's current
record from API v2 (one request per 1,000 trials, well under 40 requests per minute, only the
fields canonical_from_api_v2 reads), map it to a canonical row, and compare it column by
column with the trial's latest warehouse version. Only trials whose API record has the same
last update posted date as that version are compared: the others changed after the dataset.

The result goes to data/warehouse_parity.json and into docs/data_audit.md. Raw API records
are never stored, since they can hold an individual sponsor's name, and the result names
columns, not sponsor values.
"""

import argparse
import datetime as dt
import json
import random
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import duckdb
import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from trialpulse.cli import RefusedError, StoppedEarlyError, run
from trialpulse.config import load_project_config
from trialpulse.contracts.versions import CANONICAL_COLUMNS, api_v2_fields, canonical_from_api_v2
from trialpulse.warehouse.audit import PARITY_PATH
from trialpulse.warehouse.build import WAREHOUSE_PATH

STUDIES_URL = "https://clinicaltrials.gov/api/v2/studies"
PAGE_SIZE = 1000  # the API's maximum
SECONDS_BETWEEN_REQUESTS = 1.5  # at most 40 requests per minute (CLAUDE.md Section 7)
COMPARED: tuple[str, ...] = tuple(
    c for c in CANONICAL_COLUMNS if c not in ("nct_version", "source")
)
HIDDEN_VALUES = frozenset({"lead_sponsor_name", "sponsor_key"})
UNKNOWN_CAUSE = "UNKNOWN set by the registry without a new version"
SPONSOR_CAUSE = "sponsor name changed on the registry without a new version"
SPONSOR_COLUMNS = ["lead_sponsor_name", "sponsor_key"]


def _retryable(error: BaseException) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code == 429 or error.response.status_code >= 500
    return isinstance(error, httpx.TransportError)


@retry(
    retry=retry_if_exception(_retryable),
    stop=stop_after_attempt(6),
    wait=wait_exponential_jitter(initial=2, max=60),
    reraise=True,
)
def _get(client: httpx.Client, params: dict[str, str | int]) -> dict[str, Any]:
    response = client.get(STUDIES_URL, params=params)
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return payload


def fetch_studies(
    client: httpx.Client, nct_ids: Sequence[str], pause: Callable[[float], None] = time.sleep
) -> list[dict[str, Any]]:
    studies: list[dict[str, Any]] = []
    for start in range(0, len(nct_ids), PAGE_SIZE):
        params: dict[str, str | int] = {
            "filter.ids": ",".join(nct_ids[start : start + PAGE_SIZE]),
            "fields": api_v2_fields(),
            "pageSize": PAGE_SIZE,
        }
        while True:
            if studies or start:
                pause(SECONDS_BETWEEN_REQUESTS)
            page = _get(client, params)
            studies += page.get("studies", [])
            token = page.get("nextPageToken")
            if not token:
                break
            params["pageToken"] = token
    return studies


def sample_trials(con: duckdb.DuckDBPyConnection, n: int, seed: int) -> list[str]:
    ids = [
        r[0]
        for r in con.execute(
            "SELECT nct_id FROM trials WHERE latest_study_type = 'INTERVENTIONAL' ORDER BY nct_id"
        ).fetchall()
    ]
    return sorted(random.Random(f"{seed}:parity").sample(ids, min(n, len(ids))))


def latest_versions(
    con: duckdb.DuckDBPyConnection, nct_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    con.execute("CREATE OR REPLACE TEMP TABLE parity_ids (nct_id VARCHAR)")
    con.executemany("INSERT INTO parity_ids VALUES (?)", [(n,) for n in nct_ids])
    cursor = con.execute(
        """SELECT v.* FROM versions v JOIN parity_ids USING (nct_id)
        QUALIFY row_number() OVER (PARTITION BY v.nct_id ORDER BY v.nct_version DESC) = 1"""
    )
    names = [d[0] for d in cursor.description]
    return {row[0]: dict(zip(names, row, strict=True)) for row in cursor.fetchall()}


def _shown(column: str, value: Any) -> Any:
    if column in HIDDEN_VALUES:
        return "(hidden)"
    return value.isoformat() if isinstance(value, dt.date) else value


def _cause(
    differing: list[str],
    api: dict[str, Any],
    dataset: dict[str, Any],
    open_statuses: Sequence[str],
) -> str | None:
    """A known registry-side change behind a difference, when the difference is exactly one:
    both sides have the same last update posted date, so no version records the change."""
    # The registry shows UNKNOWN over the submitted status, which API v2 keeps in
    # last_known_status; the dataset still holds that submitted status as the overall status.
    unknown = (
        api["overall_status"] == "UNKNOWN"
        and dataset["overall_status"] in open_statuses
        and api["last_known_status"] in (None, dataset["overall_status"])
    )
    if set(differing) <= {"overall_status", "last_known_status"} and unknown:
        return UNKNOWN_CAUSE
    if differing == SPONSOR_COLUMNS and not api["sponsor_is_individual"]:
        return SPONSOR_CAUSE
    return None


def compare(
    studies: Sequence[dict[str, Any]],
    stored: dict[str, dict[str, Any]],
    open_statuses: Sequence[str],
) -> dict[str, Any]:
    per_column = {c: {"agree": 0, "total": 0} for c in COMPARED}
    mismatches: list[dict[str, Any]] = []
    compared = identical = changed = 0
    for study in studies:
        row, _ = canonical_from_api_v2(study)
        dataset = stored.get(str(row["nct_id"]))
        if dataset is None or row["effective_date"] != dataset["effective_date"]:
            changed += 1
            continue
        compared += 1
        identical += row["content_hash"] == dataset["content_hash"]
        differing = []
        for column in COMPARED:
            per_column[column]["total"] += 1
            if row[column] == dataset[column]:
                per_column[column]["agree"] += 1
            elif column != "content_hash":
                differing.append(column)
        if differing:
            cause = _cause(differing, row, dataset, open_statuses)
            mismatches.append(
                {
                    "nct_id": row["nct_id"],
                    "columns": differing,
                    "values": {c: [_shown(c, dataset[c]), _shown(c, row[c])] for c in differing},
                    "cause": cause,
                }
            )
    return {
        "compared": compared,
        "changed_since_dataset": changed,
        "identical_rows": identical,
        "per_column": per_column,
        "mismatches": mismatches,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare API v2 records with the warehouse.")
    parser.add_argument("--sample", type=int, default=200)
    parser.add_argument("--warehouse", type=Path, default=WAREHOUSE_PATH)
    parser.add_argument("--out", type=Path, default=PARITY_PATH)
    args = parser.parse_args(argv)
    if not args.warehouse.is_file():
        raise RefusedError(
            f"no warehouse at {args.warehouse}; run: uv run python -m trialpulse.warehouse.build"
        )
    cfg = load_project_config()
    with duckdb.connect(str(args.warehouse), read_only=True) as con:
        nct_ids = sample_trials(con, args.sample, cfg.seeds.default)
        stored = latest_versions(con, nct_ids)
    try:
        with httpx.Client(timeout=120) as client:
            studies = fetch_studies(client, nct_ids)
    except httpx.HTTPError as error:
        raise StoppedEarlyError(f"API v2 request failed: {type(error).__name__}") from error
    result = {
        "dataset_revision": cfg.dataset.revision,
        "checked_on": dt.datetime.now(dt.UTC).date().isoformat(),
        "seed": cfg.seeds.default,
        "sampled": len(nct_ids),
        "returned": len(studies),
        **compare(studies, stored, cfg.statuses.open),
    }
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    differing = {
        column: f"{v['agree']} of {v['total']}"
        for column, v in result["per_column"].items()
        if v["agree"] != v["total"]
    }
    print(
        f"compared {result['compared']} of {result['sampled']} sampled trials "
        f"({result['changed_since_dataset']} changed since the dataset); "
        f"identical rows: {result['identical_rows']}"
    )
    print(f"columns that differ in some trial: {differing or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
