"""The API v2 delta pull of the daily job (CLAUDE.md Sections 7 and 12, ADR 0045).

- **The date of a run** is the date of API v2's `dataTimestamp` (`/version`), read once.
- **The pull** asks for every record whose last update was posted from `since` through d
  (`AREA[LastUpdatePostDate]RANGE[since,d]`), with the `fields` parameter limited to what
  the compact row reads, 1000 records a page, following `nextPageToken` until it is absent.
  Nothing is cached on disk: the daily path never writes a page.
- **It fails** on zero records (a date the API has not reached answers 200 with
  `totalCount` 0) and on a record count that differs from `totalCount`.
- **Each record** becomes a compact row or a quarantined one. A record that fails the
  canonical contract, has an intervention type outside the contract, or lacks a trial id,
  a status or a post date is quarantined by its payload hash with the failed checks only:
  never a value, because a message with values could leak a row into a log (ADR 0040).

Personal-data keys are removed from every page before anything reads it.
"""

import datetime as dt
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from trialpulse.cohort.rules import CohortRules
from trialpulse.contracts.versions import (
    api_v2_fields,
    canonical_enum,
    canonical_from_api_v2,
    split_valid,
    typed_frame,
)
from trialpulse.ingest.ctgov_api import (
    STUDIES_URL,
    VERSION_URL,
    ApiClient,
    get_path,
    scrub_personal_data,
)
from trialpulse.live.contract import (
    compact_row,
    distinct_intervention_types,
    unknown_intervention_types,
)

INTERVENTIONS_PATH = "protocolSection.armsInterventionsModule.interventions"


class PullError(RuntimeError):
    """The pull cannot be trusted: no record, or not every record the API counted."""


def live_fields() -> str:
    """The `fields` parameter: the canonical fields plus each intervention's name and type."""
    return ",".join([api_v2_fields(), f"{INTERVENTIONS_PATH}.name", f"{INTERVENTIONS_PATH}.type"])


def delta_filter(since: dt.date, day: dt.date) -> str:
    return f"AREA[LastUpdatePostDate]RANGE[{since.isoformat()},{day.isoformat()}]"


def data_date(api: ApiClient) -> dt.date:
    """The date of the data API v2 serves (`dataTimestamp`)."""
    stamp = api.get_json(VERSION_URL).get("dataTimestamp")
    if not stamp:
        raise PullError("API v2 /version gave no dataTimestamp")
    return dt.date.fromisoformat(str(stamp)[:10])


@dataclass
class Pull:
    studies: list[dict[str, Any]]
    pages: int
    total_count: int


def pull_delta(api: ApiClient, since: dt.date, day: dt.date, page_size: int) -> Pull:
    """Every record updated from `since` through `day`, scrubbed of personal data."""
    params: dict[str, str | int] = {
        "filter.advanced": delta_filter(since, day),
        "fields": live_fields(),
        "pageSize": page_size,
        "countTotal": "true",
    }
    studies: list[dict[str, Any]] = []
    pages = 0
    total: int | None = None
    token: str | None = None
    while True:
        request = dict(params)
        if token:
            request["pageToken"] = token
            request.pop("countTotal")
        data = api.get_json(STUDIES_URL, request)
        pages += 1
        if total is None:
            total = int(data.get("totalCount") or 0)
        studies += scrub_personal_data(data.get("studies", []))
        token = data.get("nextPageToken")
        if not token:
            break
    if not studies or not total:
        raise PullError(f"the pull from {since} to {day} returned no record")
    if len(studies) != total:
        raise PullError(f"the pull returned {len(studies)} records; API v2 counted {total}")
    return Pull(studies, pages, total)


def payload_hash(study: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(study, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass
class Converted:
    rows: list[dict[str, Any]] = field(default_factory=list)
    quarantined: list[dict[str, Any]] = field(default_factory=list)  # payload hash, reasons


def interventions_of(study: Mapping[str, Any]) -> list[str]:
    items = get_path(study, INTERVENTIONS_PATH) or []
    pairs = [(i.get("name"), canonical_enum(i.get("type"))) for i in items if isinstance(i, dict)]
    return distinct_intervention_types(pairs)


def convert(studies: Sequence[Mapping[str, Any]], rules: CohortRules, secret: bytes) -> Converted:
    """Compact rows of the valid records; the others quarantined (see the module text)."""
    out = Converted()
    canonical, texts, kept = [], [], []
    for study in studies:
        row, text = canonical_from_api_v2(study)
        canonical.append(row)
        texts.append(text)
        kept.append(study)
    if not canonical:
        return out
    frame = typed_frame(pd.DataFrame(canonical))
    _, failing = split_valid(frame)
    reasons = dict(zip(failing.index, failing["reasons"], strict=True))
    for i, (row, text, study) in enumerate(zip(canonical, texts, kept, strict=True)):
        problems = [reasons[i]] if i in reasons else []
        types = interventions_of(study)
        unknown = unknown_intervention_types(types)
        if unknown:
            problems.append(f"intervention_type: {len(unknown)} value(s) outside the contract")
        if problems:
            out.quarantined.append(
                {"nct_id": row.get("nct_id"), "effective_date": row.get("effective_date"),
                 "payload_hash": payload_hash(study), "reasons": "; ".join(problems)}
            )  # fmt: skip
            continue
        official = text.get(row["official_title_hash"] or "")
        brief = text.get(row["brief_title_hash"] or "")
        out.rows.append(compact_row(row, official, brief, types, rules, secret))
    return out
