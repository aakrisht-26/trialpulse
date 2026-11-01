"""Display rows of the API (ADR 0051): titles, sponsor display names and areas, from API v2.

    uv run python -m trialpulse.live.display --target neon

`display_rows` turns API v2 records into the rows of `serving.trial_display` and
`serving.sponsors`; the daily job calls it on each record it ingests, and the bulk command
below fills the tables once. Nothing here reads the dataset.

- The brief title is the canonical text of the title (`contracts.text.normalize_text`).
- The sponsor's keyed id follows ADR 0044; a display name only where `live.contract.
  display_name` gives one, with the sponsor's key as the search key (ADR 0047). A sponsor
  that may be a person gets no row in `serving.sponsors`, and an earlier row of its id is
  removed.
- Areas follow ADR 0046 (`api.areas`).
"""

import argparse
import hashlib
import json
import time as clock
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from trialpulse.api.areas import area_codes
from trialpulse.cli import RefusedError, run
from trialpulse.config import Secrets, load_project_config
from trialpulse.contracts.sponsor import sponsor, sponsor_key
from trialpulse.contracts.text import normalize_text
from trialpulse.ingest.ctgov_api import STUDIES_URL, ApiClient, get_path, scrub_personal_data
from trialpulse.live.contract import display_name, sponsor_id

ANCESTORS_PATH = "derivedSection.conditionBrowseModule.ancestors"
DISPLAY_FIELDS: tuple[str, ...] = (
    "protocolSection.identificationModule.nctId",
    "protocolSection.identificationModule.briefTitle",
    "protocolSection.sponsorCollaboratorsModule.leadSponsor.name",
    "protocolSection.sponsorCollaboratorsModule.leadSponsor.class",
    f"{ANCESTORS_PATH}.id",
    f"{ANCESTORS_PATH}.term",
)
# The records the bulk pull asks for: every status a live trial can have (ADR 0037).
LIVE_STATUSES = ("NOT_YET_RECRUITING", "RECRUITING", "ENROLLING_BY_INVITATION",
                 "ACTIVE_NOT_RECRUITING", "SUSPENDED", "UNKNOWN")  # fmt: skip


@dataclass(frozen=True)
class Display:
    nct_id: str
    brief_title: str | None
    sponsor_id: str | None
    sponsor_class: str | None
    areas: list[int]
    display_name: str | None  # the sponsor's, where it may be shown


def display_row(study: Mapping[str, Any], secret: bytes) -> Display | None:
    """The display row of one API v2 record, or None for a record without a trial id."""
    nct = get_path(study, "protocolSection.identificationModule.nctId")
    if not isinstance(nct, str):
        return None
    title = get_path(study, "protocolSection.identificationModule.briefTitle")
    name = get_path(study, "protocolSection.sponsorCollaboratorsModule.leadSponsor.name")
    kind = get_path(study, "protocolSection.sponsorCollaboratorsModule.leadSponsor.class")
    kind = kind.strip().upper() if isinstance(kind, str) else None
    lead = sponsor(name if isinstance(name, str) else None, kind)
    return Display(
        nct_id=nct,
        brief_title=normalize_text(title) if isinstance(title, str) else None,
        sponsor_id=sponsor_id(lead.key, lead.is_individual, secret),
        sponsor_class=kind,
        areas=area_codes(get_path(study, ANCESTORS_PATH)),
        display_name=None if lead.is_individual else display_name(lead.name, kind),
    )


def display_rows(studies: Iterable[Mapping[str, Any]], secret: bytes) -> list[Display]:
    """The display rows of records; the last record of a trial wins."""
    rows: dict[str, Display] = {}
    for study in studies:
        row = display_row(study, secret)
        if row is not None:
            rows[row.nct_id] = row
    return [rows[k] for k in sorted(rows)]


def save(cur: Any, rows: Sequence[Display]) -> dict[str, int]:
    """Upsert the trial rows and the sponsor names; remove the name of a sponsor id that a
    record now shows without one (it may be a person: ADR 0044)."""
    cur.executemany(
        """INSERT INTO serving.trial_display (nct_id, brief_title, sponsor_id, sponsor_class,
          areas) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (nct_id) DO UPDATE SET brief_title = EXCLUDED.brief_title,
          sponsor_id = EXCLUDED.sponsor_id, sponsor_class = EXCLUDED.sponsor_class,
          areas = EXCLUDED.areas, updated_at = now()
        WHERE (serving.trial_display.brief_title, serving.trial_display.sponsor_id,
               serving.trial_display.sponsor_class, serving.trial_display.areas)
          IS DISTINCT FROM (EXCLUDED.brief_title, EXCLUDED.sponsor_id,
                            EXCLUDED.sponsor_class, EXCLUDED.areas)""",
        [(r.nct_id, r.brief_title, r.sponsor_id, r.sponsor_class, r.areas) for r in rows],
    )
    named = {r.sponsor_id: r for r in rows if r.sponsor_id and r.display_name}
    hidden = sorted({r.sponsor_id for r in rows if r.sponsor_id and not r.display_name}
                    - set(named))  # fmt: skip
    cur.executemany(
        """INSERT INTO serving.sponsors (sponsor_id, display_name, search_key, sponsor_class)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (sponsor_id) DO UPDATE SET display_name = EXCLUDED.display_name,
          search_key = EXCLUDED.search_key, sponsor_class = EXCLUDED.sponsor_class,
          updated_at = now()
        WHERE (serving.sponsors.display_name, serving.sponsors.sponsor_class)
          IS DISTINCT FROM (EXCLUDED.display_name, EXCLUDED.sponsor_class)""",
        [(i, r.display_name, sponsor_key(r.display_name or ""), r.sponsor_class)
         for i, r in named.items()],
    )  # fmt: skip
    if hidden:
        cur.execute("DELETE FROM serving.sponsors WHERE sponsor_id = ANY(%s)", (hidden,))
    return {"trials": len(rows), "sponsors_named": len(named), "sponsors_hidden": len(hidden)}


def remove(cur: Any, trials: Iterable[str]) -> None:
    """The display rows of trials that left the live state."""
    gone = sorted(trials)
    if gone:
        cur.execute("DELETE FROM serving.trial_display WHERE nct_id = ANY(%s)", (gone,))


def pull_live_records(api: ApiClient, page_size: int) -> list[dict[str, Any]]:
    """Every record of API v2 with a status a live trial can have (no page cache)."""
    params: dict[str, str | int] = {
        "filter.overallStatus": "|".join(LIVE_STATUSES),
        "fields": ",".join(DISPLAY_FIELDS),
        "pageSize": page_size,
        "countTotal": "true",
    }
    studies: list[dict[str, Any]] = []
    total: int | None = None
    token: str | None = None
    while True:
        request = dict(params)
        if token:
            request["pageToken"] = token
            request.pop("countTotal")
        data = api.get_json(STUDIES_URL, request)
        if total is None:
            total = int(data.get("totalCount") or 0)
        studies += scrub_personal_data(data.get("studies", []))
        token = data.get("nextPageToken")
        if not token:
            break
    if len(studies) != total:
        raise RefusedError(f"the pull returned {len(studies)} records; API v2 counted {total}")
    return studies


def table_sizes(cur: Any) -> dict[str, int]:
    out = {}
    for table in ("serving.trial_display", "serving.sponsors"):
        row = cur.execute("SELECT pg_total_relation_size(%s::regclass)", (table,)).fetchone()
        out[table] = int(row[0])
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fill the API's display rows from API v2.")
    parser.add_argument("--target", choices=("local", "neon"), required=True)
    parser.add_argument(
        "--all-pulled",
        action="store_true",
        help="keep every pulled record, not only the live trials (to measure the size)",
    )
    args = parser.parse_args(argv)
    import psycopg

    from trialpulse.live.daily import _dsn

    cfg = load_project_config()
    key = Secrets().sponsor_id_key
    if key is None:
        raise RefusedError("SPONSOR_ID_KEY is not set (environment, .env or a secret)")
    started = clock.perf_counter()
    api = ApiClient(httpx.Client(timeout=60), per_minute=cfg.live.requests_per_minute)
    studies = pull_live_records(api, cfg.live.page_size)
    rows = display_rows(studies, key.get_secret_value().encode("utf-8"))
    with psycopg.connect(_dsn(args.target), connect_timeout=10) as con, con.cursor() as cur:
        if not args.all_pulled:
            live = {r[0] for r in cur.execute("SELECT nct_id FROM live.trial_state")}
            rows = [r for r in rows if r.nct_id in live]
        counts = save(cur, rows)
        con.commit()
        sizes = table_sizes(cur)
    summary = {"records": len(studies), **counts, "bytes": sizes,
               "with_areas": sum(1 for r in rows if r.areas),
               "minutes": round((clock.perf_counter() - started) / 60, 1)}  # fmt: skip
    print(json.dumps(summary, indent=2))
    digest = hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest()[:12]
    print(f"summary sha256:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
