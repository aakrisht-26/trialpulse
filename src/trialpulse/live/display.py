"""Display rows of the API (ADR 0051): titles, sponsor display names and areas, from API v2.

    uv run python -m trialpulse.live.display --target neon

`display_rows` turns API v2 records into the rows of `serving.trial_display` and
`serving.sponsors`; the daily job calls it on each record it ingests, and the bulk command
below fills the tables once. Only served trials (scored, or listed as not scored) keep a
display row, and only sponsors that lead one keep a name (ADR 0070): the daily job prunes
the others after publication. Nothing here reads the dataset.

- The brief title is the canonical text of the title (`contracts.text.normalize_text`).
- The sponsor's keyed id follows ADR 0044; a display name only where `live.contract.
  display_name` gives one (the allowlist of ADR 0059), with the sponsor's key as the search
  key (ADR 0047). Whether a name is shown depends on how many live trials the sponsor
  leads, so it is decided when the rows are saved, from `serving.trial_display`, and again
  for every sponsor whose trials leave. A sponsor whose name is not shown gets no row in
  `serving.sponsors`, and an earlier row of its id is removed. The candidate name of a
  record lives in memory only; it is never stored unless it is shown.
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
from trialpulse.ingest.ctgov_api import (
    STUDIES_URL,
    ApiClient,
    capped_rate,
    get_path,
    scrub_personal_data,
)
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
    sponsor_name: str | None  # the record's, in memory only; None for an individual


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
        sponsor_name=None if lead.is_individual else lead.name,
    )


def display_rows(studies: Iterable[Mapping[str, Any]], secret: bytes) -> list[Display]:
    """The display rows of records; the last record of a trial wins."""
    rows: dict[str, Display] = {}
    for study in studies:
        row = display_row(study, secret)
        if row is not None:
            rows[row.nct_id] = row
    return [rows[k] for k in sorted(rows)]


def trials_led(cur: Any, sponsor_ids: Iterable[str]) -> dict[str, int]:
    """The number of trials with a display row each sponsor leads: after publication, its
    served trials (ADR 0070)."""
    rows = cur.execute(
        """SELECT sponsor_id, count(*) FROM serving.trial_display
        WHERE sponsor_id = ANY(%s) GROUP BY sponsor_id""",
        (sorted(sponsor_ids),),
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def _store_names(cur: Any, shown: Mapping[str, tuple[str, str | None]],
                 hidden: Iterable[str]) -> None:  # fmt: skip
    cur.executemany(
        """INSERT INTO serving.sponsors (sponsor_id, display_name, search_key, sponsor_class)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (sponsor_id) DO UPDATE SET display_name = EXCLUDED.display_name,
          search_key = EXCLUDED.search_key, sponsor_class = EXCLUDED.sponsor_class,
          updated_at = now()
        WHERE (serving.sponsors.display_name, serving.sponsors.sponsor_class)
          IS DISTINCT FROM (EXCLUDED.display_name, EXCLUDED.sponsor_class)""",
        [(i, name, sponsor_key(name), kind) for i, (name, kind) in sorted(shown.items())],
    )
    gone = sorted(hidden)
    if gone:
        cur.execute("DELETE FROM serving.sponsors WHERE sponsor_id = ANY(%s)", (gone,))


def save(cur: Any, rows: Sequence[Display]) -> dict[str, int]:
    """Upsert the trial rows, then decide each of their sponsors' names (ADR 0059) from the
    live trials it now leads: a name is shown only if every record of the sponsor in `rows`
    allows it (the last record's text and class are stored); every other sponsor's row is
    removed, since it may name a person."""
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
    by_sponsor: dict[str, list[Display]] = {}
    for r in rows:
        if r.sponsor_id:
            by_sponsor.setdefault(r.sponsor_id, []).append(r)
    led = trials_led(cur, by_sponsor)
    shown: dict[str, tuple[str, str | None]] = {}
    for sid, records in by_sponsor.items():
        names = [display_name(r.sponsor_name, r.sponsor_class, led.get(sid, 0)) for r in records]
        if all(names):
            shown[sid] = (str(names[-1]), records[-1].sponsor_class)
    hidden = set(by_sponsor) - set(shown)
    _store_names(cur, shown, hidden)
    return {"trials": len(rows), "sponsors_named": len(shown), "sponsors_hidden": len(hidden)}


def refresh(cur: Any, sponsor_ids: Iterable[str]) -> int:
    """Decide again the stored names of sponsors whose trials changed (a trial left): a name
    shown only because its sponsor led enough live trials goes when it leads fewer. Returns
    the names removed."""
    ids = sorted(set(sponsor_ids))
    if not ids:
        return 0
    stored = cur.execute(
        "SELECT sponsor_id, display_name, sponsor_class FROM serving.sponsors "
        "WHERE sponsor_id = ANY(%s)",
        (ids,),
    ).fetchall()
    led = trials_led(cur, ids)
    hidden = [sid for sid, name, kind in stored
              if display_name(name, kind, led.get(sid, 0)) is None]  # fmt: skip
    _store_names(cur, {}, hidden)
    return len(hidden)


def remove(cur: Any, trials: Iterable[str]) -> None:
    """The display rows of trials that left the live state, and the names their sponsors may
    no longer show (`refresh`)."""
    gone = sorted(trials)
    if gone:
        left = cur.execute(
            "DELETE FROM serving.trial_display WHERE nct_id = ANY(%s) RETURNING sponsor_id",
            (gone,),
        ).fetchall()
        refresh(cur, {r[0] for r in left if r[0]})


SERVED = """(EXISTS (SELECT 1 FROM serving.trial_scores_latest l WHERE l.nct_id = d.nct_id)
  OR EXISTS (SELECT 1 FROM serving.trials_not_scored n WHERE n.nct_id = d.nct_id))"""


def keep_served(cur: Any) -> int:
    """ADR 0070: remove the display rows of trials that are not served (neither scored nor
    listed as not scored), decide their sponsors' names again (`refresh`), and remove the
    names of sponsors that lead no served trial. Returns the trial rows removed."""
    left = cur.execute(
        f"DELETE FROM serving.trial_display d WHERE NOT {SERVED} RETURNING sponsor_id"
    ).fetchall()
    refresh(cur, {r[0] for r in left if r[0]})
    cur.execute(
        """DELETE FROM serving.sponsors s WHERE NOT EXISTS (
          SELECT 1 FROM serving.trial_display d WHERE d.sponsor_id = s.sponsor_id)"""
    )
    return len(left)


def unnamed(cur: Any) -> int:
    """Served trials without a display row (the API then gives no title or sponsor for them).
    The daily job records the count; the bulk command fills them."""
    row = cur.execute(
        """SELECT count(*) FROM (SELECT nct_id FROM serving.trial_scores_latest
          UNION SELECT nct_id FROM serving.trials_not_scored) s
        WHERE NOT EXISTS (SELECT 1 FROM serving.trial_display d WHERE d.nct_id = s.nct_id)"""
    ).fetchone()
    return int(row[0]) if row else 0


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
    parser.add_argument("--per-minute", type=float,
                        help="a lower API v2 rate than live.requests_per_minute")  # fmt: skip
    args = parser.parse_args(argv)
    import psycopg

    from trialpulse.live.daily import _dsn

    cfg = load_project_config()
    try:
        per_minute = capped_rate(cfg.live.requests_per_minute, args.per_minute)
    except ValueError as exc:
        raise RefusedError(str(exc)) from None
    key = Secrets().sponsor_id_key
    if key is None:
        raise RefusedError("SPONSOR_ID_KEY is not set (environment, .env or a secret)")
    started = clock.perf_counter()
    api = ApiClient(httpx.Client(timeout=60), per_minute=per_minute)
    studies = pull_live_records(api, cfg.live.page_size)
    rows = display_rows(studies, key.get_secret_value().encode("utf-8"))
    with psycopg.connect(_dsn(args.target), connect_timeout=10) as con, con.cursor() as cur:
        if not args.all_pulled:  # the served trials only (ADR 0070)
            served = {r[0] for r in cur.execute(
                "SELECT nct_id FROM serving.trial_scores_latest "
                "UNION SELECT nct_id FROM serving.trials_not_scored")}  # fmt: skip
            rows = [r for r in rows if r.nct_id in served]
        counts = save(cur, rows)
        if not args.all_pulled:
            counts["pruned"] = keep_served(cur)
            counts["served_without_display"] = unnamed(cur)
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
