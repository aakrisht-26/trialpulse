"""The census of ADR 0042 (parity layer 4 of ADR 0038): the API v2 path against the dataset
path on every unchanged trial, with the seeded 50 as a named subset.

    uv run python -m trialpulse.warehouse.census

Laptop only: it reads the warehouse and the pinned dataset (ADR 0040) and refuses under
GitHub Actions. It writes its counts to data/census/census_<data date>.json and nothing else.
In order:

1. **The pull.** Every open or UNKNOWN interventional record of API v2 (the population of
   the census of 2026-11-01 on which ADR 0042 rests), with the live pull's fields, at 30
   requests a minute or fewer, paged by cursor and held in memory only, since a record can
   name an individual sponsor. Each record goes through the live path's conversion
   (`scrub_personal_data`, `canonical_from_api_v2`, the canonical contract); a record that
   fails is counted by reason, and fails the census if its trial is unchanged.
2. **Unchanged trials.** A record is unchanged when its last update posted date equals the
   date of the trial's last version in the warehouse (the highest version number). The
   others are counted: later on the registry, behind it, or not in the warehouse.
3. **The comparison,** column by column on the canonical rows, and for every difference the
   rule of its column (ADR 0042). The raw values of the differing records are read again:
   from API v2 by id (a record that changed since the pull is set aside and counted) and,
   for the dataset, from `raw_versions`, the pinned core files (sponsor names) and the
   interventions config. Raw values are compared in substance, not in the form of their
   source: HTML entities decoded, enums without case, spaces, hyphens and underscores, dates
   parsed.
   - **A field a served feature reads** (the sources of the compact row, `contract.SOURCES`;
     condition 2): the raw values must differ, which is a registry change made without a
     new version, counted with its cause. The same raw value with another canonical value
     is a bug, with no exception. The status is compared as the submitted status (ADR 0066):
     a difference in the registry's UNKNOWN label alone is counted, not a difference. The
     sponsor goes by the raw lead sponsor name and class; the titles by the phase they
     state, which must be equal; the intervention types by the raw (name, type) pairs.
   - **Brief summary, eligibility and the why-stopped text** are counted by cause, never
     failed (condition 3). A differing API text that still holds an HTML comment would show
     that the comment rule of condition 1 is not in force; it is counted.
   - **Any other field:** a bug fails the census too; a raw change is counted.
4. **Condition 4 on real records:** the compact rows of both sources (`contract.compact_row`,
   under a throwaway key held in memory) must have equal feature hashes on every unchanged
   trial whose differences, if any, are all in fields no served feature reads. The test in
   `tests/live/test_daily.py` shows the same on synthetic histories.
5. **The seeded 50:** 50 unchanged trials drawn with the project's seed, reported apart under
   the same rules (the literal check of the original criterion).

The census passes when no difference is left without its column's cause: no bug, no title
phase moved, no unchanged record quarantined on the API path, and no feature hash apart for
a trial without a difference in a field a served feature reads. It prints and writes counts
only: no trial id, sponsor name or key, and no text.
"""

import argparse
import datetime as dt
import html
import json
import random
import re
import secrets
import time as clock
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import duckdb
import httpx
import pandas as pd

from trialpulse.cli import RefusedError, StoppedEarlyError, run
from trialpulse.cohort.rules import CohortRules, submitted_status
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.contracts.versions import (
    API_V2_INTERVENTIONS,
    API_V2_PATHS,
    CANONICAL_COLUMNS,
    COLUMN_TYPES,
    FieldTypeError,
    canonical_from_api_v2,
    parse_partial_date,
    split_valid,
    typed_frame,
)
from trialpulse.features.design import resolved_title_phase
from trialpulse.ingest.ctgov_api import (
    MAX_PAGE_SIZE,
    STUDIES_URL,
    VERSION_URL,
    ApiClient,
    get_path,
    scrub_personal_data,
)
from trialpulse.live import contract
from trialpulse.live.pull import live_fields
from trialpulse.warehouse.build import TEMP_DIR, WAREHOUSE_PATH, dataset_glob, interventions_glob

PER_MINUTE = 30  # under the project's limit of 40 (CLAUDE.md Section 7)
REFETCH_IDS = 500  # ids per request when the differing records are read again
SAMPLE_SIZE = 50
OUT_DIR = REPO_ROOT / "data" / "census"
POPULATION_STUDY_TYPE = "AREA[StudyType]INTERVENTIONAL"
COMPARED: tuple[str, ...] = tuple(
    c for c in CANONICAL_COLUMNS if c not in ("nct_id", "nct_version", "source", "content_hash")
)
STATUS_COLUMNS: tuple[str, ...] = ("overall_status", "last_known_status")
SPONSOR_COLUMNS: tuple[str, ...] = ("sponsor_key", "sponsor_is_individual", "lead_sponsor_name")
TITLE_COLUMNS: dict[str, str] = {
    "official_title_hash": "official_title",
    "brief_title_hash": "brief_title",
}
# Texts no served feature reads (condition 3), with their API v2 field.
COUNTED_TEXTS: dict[str, str] = {
    "brief_summary_hash": "brief_summary",
    "eligibility_criteria_hash": "eligibility_criteria",
    "why_stopped_hash": "why_stopped",
}
# The canonical columns a served feature reads: the sources of the compact row's fields.
FEATURE_COLUMNS: frozenset[str] = frozenset(
    "intervention_types" if source == "intervention_type" else source
    for sources in contract.SOURCES.values()
    for source in sources
)
# raw_versions names of canonical columns that are renamed on the way in.
RAW_NAMES: dict[str, str] = {
    "effective_date": "last_update_post_date",
    "effective_date_type": "last_update_post_date_type",
    "submitted_date": "last_update_submit_date",
    "minimum_age_years": "minimum_age",
    "maximum_age_years": "maximum_age",
}
BUG = "bug: the same raw value gives another canonical value"
PHASE_MOVED = "fail: the titles state another phase"
NO_RAW = "fail: the raw values could not be read"
UNKNOWN_LABEL = "the registry's UNKNOWN label only (the submitted status is equal)"
FAILING: frozenset[str] = frozenset({BUG, PHASE_MOVED, NO_RAW})


class JsonApi(Protocol):
    def get_json(self, url: str, params: Mapping[str, str | int] | None = None) -> Any: ...


# Raw values in substance -------------------------------------------------------------------


def token(value: Any) -> Any:
    """An enum or short raw value without its source's form: no case, spaces, hyphens or
    underscores, HTML entities decoded."""
    if value is None or isinstance(value, bool | int | float | dt.date):
        return value
    return re.sub(r"[\s_\-]+", "", html.unescape(str(value))).casefold() or None


def name_form(value: Any) -> str | None:
    """A name as its source wrote it, HTML entities decoded and spaces collapsed."""
    if value is None:
        return None
    return " ".join(html.unescape(str(value)).split()) or None


def folded(value: str | None) -> str:
    return re.sub(r"[\W_]+", "", value or "", flags=re.UNICODE).casefold()


def text_class(api: str | None, dataset: str | None) -> str:
    """How two normalized texts differ (no words are kept)."""
    if not api or not dataset:
        return "one side empty"
    if "".join(api.split()) == "".join(dataset.split()):
        return "spaces only"
    if api.casefold() == dataset.casefold():
        return "case only"
    if folded(api) == folded(dataset):
        return "case or punctuation only"
    return "letters or digits"


def api_raw(study: Mapping[str, Any], column: str) -> Any:
    """The raw value behind a canonical column in an API v2 record."""
    if column in SPONSOR_COLUMNS:
        column = "lead_sponsor_name"
    elif column.endswith("_precision"):
        column = column.removesuffix("_precision")  # the date string carries its precision
    elif column in ("minimum_age_years", "maximum_age_years"):
        column = column.removesuffix("_years")
    return get_path(study, f"protocolSection.{API_V2_PATHS[column]}")


def api_pairs(study: Mapping[str, Any]) -> frozenset[tuple[Any, Any]]:
    items = get_path(study, f"protocolSection.{API_V2_INTERVENTIONS}") or []
    return frozenset((i.get("name"), token(i.get("type"))) for i in items if isinstance(i, dict))


def same_raw(column: str, api: Any, dataset: Any) -> bool:
    """Whether the two sources' raw values for a column say the same thing."""
    kind = COLUMN_TYPES.get(column)
    if column.endswith("_precision"):
        parsed = parse_partial_date(api).precision if isinstance(api, str) else None
        return bool(token(parsed) == token(dataset))
    if kind == "DATE":
        value = parse_partial_date(api).value if isinstance(api, str) else None
        return bool(value == dataset)
    if column in ("minimum_age_years", "maximum_age_years"):
        return name_form(api) == name_form(dataset) or token(api) == token(dataset)
    return bool(token(api) == token(dataset))


# The comparison ----------------------------------------------------------------------------


def equal(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, list | tuple) or isinstance(b, list | tuple):
        return list(a) == list(b)
    return bool(a == b)


def differing_columns(api_row: Mapping[str, Any], dataset_row: Mapping[str, Any]) -> list[str]:
    return [c for c in COMPARED if not equal(api_row.get(c), dataset_row.get(c))]


@dataclass(frozen=True)
class Difference:
    column: str
    cause: str

    @property
    def failing(self) -> bool:
        return self.cause in FAILING

    @property
    def in_feature(self) -> bool:
        """In a field a served feature reads, the UNKNOWN label aside."""
        return self.column in FEATURE_COLUMNS and self.cause != UNKNOWN_LABEL


@dataclass
class RowSources:
    """What the cause rules read for one differing trial."""

    api_row: Mapping[str, Any]
    dataset_row: Mapping[str, Any]
    study: Mapping[str, Any]  # the API v2 record read again
    raw: Mapping[str, Any]  # its raw_versions row, with the raw sponsor name
    pairs: frozenset[tuple[Any, Any]]  # the interventions config's (name, type) pairs
    api_texts: Mapping[str, str]  # the API record's normalized texts by hash
    dataset_texts: Mapping[str, str]  # the warehouse's texts by hash


def _texts(source: RowSources, column: str) -> tuple[str | None, str | None]:
    api_hash, dataset_hash = source.api_row.get(column), source.dataset_row.get(column)
    return source.api_texts.get(api_hash or ""), source.dataset_texts.get(dataset_hash or "")


def _title_phases(source: RowSources) -> tuple[str, str]:
    api_official, dataset_official = _texts(source, "official_title_hash")
    api_brief, dataset_brief = _texts(source, "brief_title_hash")
    return (resolved_title_phase(api_official, api_brief),
            resolved_title_phase(dataset_official, dataset_brief))  # fmt: skip


def _status_cause(source: RowSources, rules: CohortRules) -> str:
    a, d = source.api_row, source.dataset_row
    if submitted_status(rules, str(a["overall_status"]), a.get("last_known_status")) == (
        submitted_status(rules, str(d["overall_status"]), d.get("last_known_status"))
    ):
        return UNKNOWN_LABEL
    unknown = {token(label) for label in rules.unknown_labels}

    def raw_submitted(overall: Any, known: Any) -> Any:
        return token(known) if token(overall) in unknown and known is not None else token(overall)

    api = raw_submitted(api_raw(source.study, "overall_status"),
                        api_raw(source.study, "last_known_status"))  # fmt: skip
    dataset = raw_submitted(source.raw.get("overall_status"), source.raw.get("last_known_status"))
    return BUG if api == dataset else "registry: the status changed"


def _sponsor_cause(source: RowSources) -> str:
    api, dataset = (
        name_form(api_raw(source.study, "lead_sponsor_name")),
        name_form(source.raw.get("raw_sponsor_name")),
    )
    if api == dataset:
        classes_differ = not same_raw(
            "lead_sponsor_class",
            api_raw(source.study, "lead_sponsor_class"),
            source.raw.get("lead_sponsor_class"),
        )
        return "registry: the class changed under the same name" if classes_differ else BUG
    if folded(api) == folded(dataset):
        return "registry: the same name but for case or punctuation"
    return "registry: another name"


def cause_of(column: str, source: RowSources, rules: CohortRules) -> str:
    """The cause of one difference, by the rule of its column (see the module text)."""
    if column in STATUS_COLUMNS:
        return _status_cause(source, rules)
    if column in SPONSOR_COLUMNS:
        return _sponsor_cause(source)
    if column in TITLE_COLUMNS:
        api_phase, dataset_phase = _title_phases(source)
        if api_phase != dataset_phase:
            return PHASE_MOVED
        return f"title text, the same title phase: {text_class(*_texts(source, column))}"
    if column in COUNTED_TEXTS:
        return f"text: {text_class(*_texts(source, column))}"
    if column == "intervention_types":
        dataset_pairs = frozenset((n, token(t)) for n, t in source.pairs)
        same = api_pairs(source.study) == dataset_pairs
        return BUG if same else "registry: the interventions changed"
    raw_name = RAW_NAMES.get(column, column)
    if raw_name not in source.raw:
        return NO_RAW
    if same_raw(column, api_raw(source.study, column), source.raw[raw_name]):
        return BUG
    return "registry: the value changed"


def comment_left(column: str, source: RowSources) -> bool:
    """A differing API text that still holds an HTML comment (condition 1)."""
    api_text, _ = _texts(source, column)
    return api_text is not None and "<!--" in api_text


# Condition 4 and the seeded 50 --------------------------------------------------------------


def feature_hashes_equal(
    api_row: Mapping[str, Any],
    api_titles: tuple[str | None, str | None],
    dataset_row: Mapping[str, Any],
    dataset_titles: tuple[str | None, str | None],
    rules: CohortRules,
    secret: bytes,
) -> bool:
    """Whether the compact rows of the two sources have the same feature hash."""
    api = contract.compact_row(api_row, *api_titles, api_row.get("intervention_types"), rules,
                               secret)  # fmt: skip
    types = dataset_row.get("intervention_types")
    dataset = contract.compact_row(dataset_row, *dataset_titles, types, rules, secret)
    return bool(api["feature_hash"] == dataset["feature_hash"])


def seeded_sample(nct_ids: Iterable[str], seed: int, size: int = SAMPLE_SIZE) -> list[str]:
    ordered = sorted(set(nct_ids))
    return sorted(random.Random(f"{seed}:census-50").sample(ordered, min(size, len(ordered))))


@dataclass
class Tally:
    """The counts of one set of unchanged trials."""

    trials: int = 0
    trials_differing: int = 0
    trials_differing_in_a_feature: int = 0
    trials_failing: int = 0
    set_aside: int = 0
    columns: dict[str, Counter[str]] = field(default_factory=dict)
    comments_left: int = 0
    hash_no_difference: Counter[str] = field(default_factory=Counter)
    hash_outside_features_only: Counter[str] = field(default_factory=Counter)
    hash_feature_difference: Counter[str] = field(default_factory=Counter)

    @property
    def condition_4_breaks(self) -> int:
        return self.hash_no_difference["apart"] + self.hash_outside_features_only["apart"]

    @property
    def failing_differences(self) -> int:
        return sum(n for c in self.columns.values() for cause, n in c.items() if cause in FAILING)

    def add(self, differences: Sequence[Difference] | None, hashes_equal: bool,
            comments: int = 0) -> None:  # fmt: skip
        """One trial: its differences (None when it was set aside) and its hash check."""
        self.trials += 1
        if differences is None:
            self.set_aside += 1
            return
        verdict = "equal" if hashes_equal else "apart"
        if not differences:
            self.hash_no_difference[verdict] += 1
            return
        self.trials_differing += 1
        if any(d.in_feature for d in differences):
            self.trials_differing_in_a_feature += 1
            self.hash_feature_difference[verdict] += 1
        else:
            self.hash_outside_features_only[verdict] += 1
        if any(d.failing for d in differences):
            self.trials_failing += 1
        for d in differences:
            self.columns.setdefault(d.column, Counter())[d.cause] += 1
        self.comments_left += comments

    def as_dict(self) -> dict[str, Any]:
        return {
            "trials": self.trials,
            "set_aside_changed_or_missing_on_the_second_read": self.set_aside,
            "trials_with_no_difference": sum(self.hash_no_difference.values()),
            "trials_differing": self.trials_differing,
            "trials_differing_in_a_field_a_served_feature_reads": (
                self.trials_differing_in_a_feature
            ),
            "trials_failing": self.trials_failing,
            "failing_differences": self.failing_differences,
            "columns": {c: dict(sorted(n.items())) for c, n in sorted(self.columns.items())},
            "differing_api_texts_with_an_html_comment": self.comments_left,
            "feature_hash": {
                "trials_with_no_difference": dict(self.hash_no_difference),
                "trials_differing_only_in_fields_no_feature_reads": dict(
                    self.hash_outside_features_only
                ),
                "trials_differing_in_a_field_a_served_feature_reads": dict(
                    self.hash_feature_difference
                ),
                "condition_4_breaks": self.condition_4_breaks,
            },
        }


# The pull and the reads --------------------------------------------------------------------


@dataclass
class Pulled:
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    titles: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    quarantined: dict[str, tuple[dt.date | None, str]] = field(default_factory=dict)
    unreadable: Counter[str] = field(default_factory=Counter)
    pages: int = 0
    total: int = 0
    records: int = 0
    repeated: int = 0


def population_params(cfg: ProjectConfig) -> dict[str, str | int]:
    statuses = [*cfg.statuses.open, *cfg.statuses.unknown]
    return {
        "filter.overallStatus": "|".join(statuses),
        "filter.advanced": POPULATION_STUDY_TYPE,
        "fields": live_fields(),
        "pageSize": MAX_PAGE_SIZE,
    }


def _identity(study: Mapping[str, Any]) -> tuple[str | None, dt.date | None]:
    nct = get_path(study, "protocolSection.identificationModule.nctId")
    posted = get_path(study, "protocolSection.statusModule.lastUpdatePostDateStruct.date")
    return (nct if isinstance(nct, str) else None,
            parse_partial_date(posted).value if isinstance(posted, str) else None)  # fmt: skip


def pull_population(api: JsonApi, cfg: ProjectConfig) -> Pulled:
    """Every record of the population, converted as the live path converts it."""
    out = Pulled()
    params = population_params(cfg)
    token_: str | None = None
    while True:
        request = dict(params)
        if token_:
            request["pageToken"] = token_
        else:
            request["countTotal"] = "true"
        data = api.get_json(STUDIES_URL, request)
        out.pages += 1
        if not token_:
            out.total = int(data.get("totalCount") or 0)
        for study in scrub_personal_data(data.get("studies", [])):
            out.records += 1
            nct, posted = _identity(study)
            try:
                row, texts = canonical_from_api_v2(study)
            except FieldTypeError as exc:
                reason = f"{exc.field}: wrong JSON type"
            except Exception as exc:  # one bad record never stops the census
                reason = f"record: conversion failed ({type(exc).__name__})"
            else:
                key = str(row["nct_id"])
                out.repeated += key in out.rows
                out.rows[key] = row
                out.titles[key] = (texts.get(row["official_title_hash"] or ""),
                                   texts.get(row["brief_title_hash"] or ""))  # fmt: skip
                continue
            if nct is None:
                out.unreadable[reason] += 1
            else:
                out.quarantined[nct] = (posted, reason)
        token_ = data.get("nextPageToken")
        if not token_:
            break
    if out.records != out.total:
        raise StoppedEarlyError(f"the pull returned {out.records} records; API v2 counted "
                                f"{out.total}")  # fmt: skip
    if out.rows:  # the canonical contract, as the live pull applies it
        keys = list(out.rows)
        frame = typed_frame(pd.DataFrame([out.rows[k] for k in keys]))
        _, failing = split_valid(frame)
        for index, reason in zip(failing.index, failing["reasons"], strict=True):
            key = keys[int(index)]
            row = out.rows.pop(key)
            out.titles.pop(key, None)
            out.quarantined[key] = (row.get("effective_date"), str(reason))
    return out


def refetch(api: JsonApi, nct_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """The records of these trials, read again by id (scrubbed, in memory only)."""
    found: dict[str, dict[str, Any]] = {}
    for start in range(0, len(nct_ids), REFETCH_IDS):
        chunk = nct_ids[start : start + REFETCH_IDS]
        request: dict[str, str | int] = {"filter.ids": ",".join(chunk), "fields": live_fields(),
                                         "pageSize": MAX_PAGE_SIZE}  # fmt: skip
        while True:
            data = api.get_json(STUDIES_URL, request)
            for study in scrub_personal_data(data.get("studies", [])):
                nct, _ = _identity(study)
                if nct is not None:
                    found[nct] = study
            next_token = data.get("nextPageToken")
            if not next_token:
                break
            request = {**request, "pageToken": next_token}
    return found


def _records(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


@dataclass
class Inputs:
    warehouse: Path
    core: str  # the glob of the pinned core files
    interventions: str  # the glob of the pinned interventions config

    @staticmethod
    def default(cfg: ProjectConfig) -> "Inputs":
        return Inputs(WAREHOUSE_PATH, dataset_glob(cfg), interventions_glob(cfg))


def last_versions(
    con: duckdb.DuckDBPyConnection, nct_ids: Sequence[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[str | None, str | None]]]:
    """Each trial's last version in the warehouse (the highest version number), and the texts
    of its two titles."""
    con.register("census_ids", pd.DataFrame({"nct_id": pd.Series(nct_ids, dtype="object")}))
    rows = _records(con.execute(
        """SELECT v.*, ot.text AS census_official, bt.text AS census_brief
        FROM wh.versions v JOIN census_ids USING (nct_id)
        LEFT JOIN wh.texts ot ON ot.text_hash = v.official_title_hash
        LEFT JOIN wh.texts bt ON bt.text_hash = v.brief_title_hash
        QUALIFY row_number() OVER (PARTITION BY v.nct_id ORDER BY v.nct_version DESC) = 1"""
    ))  # fmt: skip
    con.unregister("census_ids")
    versions, titles = {}, {}
    for row in rows:
        key = str(row["nct_id"])
        titles[key] = (row.pop("census_official"), row.pop("census_brief"))
        versions[key] = row
    return versions, titles


def dataset_raw(
    con: duckdb.DuckDBPyConnection,
    inputs: Inputs,
    versions: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, frozenset[tuple[Any, Any]]]]:
    """For these trials' versions: the raw_versions row with the raw sponsor name of the core
    files, and the interventions config's (name, type) pairs."""
    keys = pd.DataFrame({
        "nct_id": pd.Series(list(versions), dtype="object"),
        "nct_version": pd.Series([int(v["nct_version"]) for v in versions.values()],
                                 dtype="int64"),
    })  # fmt: skip
    con.register("census_keys", keys)
    raws = _records(con.execute(
        f"""SELECT r.*, c.lead_sponsor_name AS raw_sponsor_name
        FROM wh.raw_versions r JOIN census_keys k USING (nct_id, nct_version)
        LEFT JOIN (SELECT nct_id, nct_version, any_value(lead_sponsor_name) AS lead_sponsor_name
                   FROM read_parquet('{inputs.core}', union_by_name=true)
                   GROUP BY nct_id, nct_version) c USING (nct_id, nct_version)
        QUALIFY row_number() OVER (PARTITION BY r.nct_id, r.nct_version
                                   ORDER BY r.source_file, r.source_row) = 1"""
    ))  # fmt: skip
    pairs: dict[str, set[tuple[Any, Any]]] = {}
    for nct, name, kind in con.execute(
        f"""SELECT i.nct_id, i.intervention_name, i.intervention_type
        FROM read_parquet('{inputs.interventions}') i JOIN census_keys k
        USING (nct_id, nct_version)"""
    ).fetchall():
        pairs.setdefault(str(nct), set()).add((name, kind))
    con.unregister("census_keys")
    return ({str(r["nct_id"]): r for r in raws},
            {nct: frozenset(p) for nct, p in pairs.items()})  # fmt: skip


def dataset_texts(con: duckdb.DuckDBPyConnection, hashes: Iterable[str]) -> dict[str, str]:
    wanted = sorted({h for h in hashes if h})
    if not wanted:
        return {}
    con.register("census_hashes", pd.DataFrame({"text_hash": pd.Series(wanted, dtype="object")}))
    found = dict(con.execute(
        "SELECT t.text_hash, t.text FROM wh.texts t JOIN census_hashes USING (text_hash)"
    ).fetchall())  # fmt: skip
    con.unregister("census_hashes")
    return {str(k): str(v) for k, v in found.items()}


# The census --------------------------------------------------------------------------------


def run_census(
    api: JsonApi,
    inputs: Inputs,
    cfg: ProjectConfig,
    secret: bytes,
    memory_limit: str = "3GB",
    temp_dir: Path = TEMP_DIR,
) -> dict[str, Any]:
    """The census (see the module text): the counts, with the verdict."""
    started = clock.monotonic()
    rules = CohortRules.from_config(cfg)
    stamp = api.get_json(VERSION_URL).get("dataTimestamp")
    pulled = pull_population(api, cfg)
    pulled_seconds = clock.monotonic() - started
    temp_dir.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as con:
        con.execute(f"SET memory_limit = '{memory_limit}'")
        con.execute(f"SET temp_directory = '{temp_dir.as_posix()}'")
        con.execute(f"ATTACH '{inputs.warehouse.as_posix()}' AS wh (READ_ONLY)")
        ids = sorted({*pulled.rows, *pulled.quarantined})
        versions, dataset_titles = last_versions(con, ids)

        groups: Counter[str] = Counter()
        unchanged: list[str] = []
        for nct in sorted(pulled.rows):
            stored = versions.get(nct)
            posted = pulled.rows[nct]["effective_date"]
            if stored is None:
                groups["not in the warehouse"] += 1
            elif posted == stored["effective_date"]:
                unchanged.append(nct)
            elif posted > stored["effective_date"]:
                groups["later on the registry"] += 1
            else:
                groups["behind the warehouse"] += 1
        quarantined_unchanged: Counter[str] = Counter()
        quarantined_other: Counter[str] = Counter()
        for nct, (posted, reason) in pulled.quarantined.items():
            stored = versions.get(nct)
            if stored is not None and posted == stored["effective_date"]:
                quarantined_unchanged[reason] += 1
            else:
                quarantined_other[reason] += 1

        differing = {}
        for nct in unchanged:
            columns = differing_columns(pulled.rows[nct], versions[nct])
            if columns:
                differing[nct] = columns
        studies = refetch(api, sorted(differing)) if differing else {}
        raws, pairs = dataset_raw(con, inputs, {n: versions[n] for n in differing})
        text_hashes = [versions[n].get(c) for n in differing
                       for c in (*TITLE_COLUMNS, *COUNTED_TEXTS)]  # fmt: skip
        texts = dataset_texts(con, [h for h in text_hashes if h])
    compared_seconds = clock.monotonic() - started - pulled_seconds

    sample = set(seeded_sample(unchanged, cfg.seeds.default))
    everyone, fifty = Tally(), Tally()
    for nct in unchanged:
        api_row, dataset_row = pulled.rows[nct], versions[nct]
        differences: list[Difference] | None = []
        comments = 0
        if nct in differing:
            study = studies.get(nct)
            if study is None or _identity(study)[1] != api_row["effective_date"]:
                differences = None  # changed or missing on the second read: set aside
            else:
                _, api_texts = canonical_from_api_v2(study)
                source = RowSources(api_row, dataset_row, study, raws.get(nct, {}),
                                    pairs.get(nct, frozenset()), api_texts, texts)  # fmt: skip
                differences = [Difference(c, cause_of(c, source, rules)) for c in differing[nct]]
                comments = sum(comment_left(c, source) for c in differing[nct]
                               if c in COUNTED_TEXTS or c in TITLE_COLUMNS)  # fmt: skip
        hashes_equal = feature_hashes_equal(api_row, pulled.titles[nct], dataset_row,
                                            dataset_titles[nct], rules, secret)  # fmt: skip
        everyone.add(differences, hashes_equal, comments)
        if nct in sample:
            fifty.add(differences, hashes_equal, comments)

    failures = []
    if sum(quarantined_unchanged.values()):
        failures.append("unchanged records quarantined on the API path")
    for name, tally in (("every unchanged trial", everyone), ("the seeded 50", fifty)):
        if tally.failing_differences:
            failures.append(f"{name}: differences without their column's cause")
        if tally.condition_4_breaks:
            failures.append(f"{name}: a feature hash apart without a feature difference")
    return {
        "data_timestamp": stamp,
        "dataset_revision": cfg.dataset.revision,
        "population": "open or UNKNOWN interventional records of API v2",
        "seconds": {
            "pull": round(pulled_seconds, 1),
            "compare": round(compared_seconds, 1),
            "total": round(clock.monotonic() - started, 1),
        },
        "pull": {
            "pages": pulled.pages,
            "records": pulled.records,
            "api_total": pulled.total,
            "converted": len(pulled.rows),
            "repeated_ids": pulled.repeated,
            "unreadable": dict(pulled.unreadable),
        },
        "groups": {**dict(sorted(groups.items())), "unchanged": len(unchanged)},
        "quarantined": {
            "unchanged": dict(quarantined_unchanged),
            "changed_or_not_in_the_warehouse": dict(quarantined_other),
        },
        "every_unchanged_trial": everyone.as_dict(),
        "seeded_50": fifty.as_dict(),
        "verdict": {"passes": not failures, "failures": failures},
    }


def summary_lines(result: Mapping[str, Any]) -> list[str]:
    """What the command prints: counts only."""
    groups, pull = result["groups"], result["pull"]
    lines = [
        f"API v2 data as of {result['data_timestamp']}; dataset {result['dataset_revision']}",
        f"Pulled {pull['records']:,} records in {pull['pages']} pages ({pull['api_total']:,} "
        f"counted by the API); unchanged {groups['unchanged']:,}; "
        + "; ".join(f"{k} {v:,}" for k, v in groups.items() if k != "unchanged"),
        f"Quarantined on the API path: {result['quarantined']}",
    ]
    for name in ("every_unchanged_trial", "seeded_50"):
        tally = result[name]
        lines.append(
            f"{name}: {tally['trials']:,} trials, {tally['trials_differing']:,} differing "
            f"({tally['trials_differing_in_a_field_a_served_feature_reads']:,} in a field a "
            f"served feature reads), {tally['failing_differences']:,} failing differences, "
            f"{tally['set_aside_changed_or_missing_on_the_second_read']:,} set aside"
        )
        for column, causes in tally["columns"].items():
            lines.append(f"  {column}: {causes}")
        lines.append(f"  feature hash: {tally['feature_hash']}")
        lines.append(f"  differing API texts with an HTML comment: "
                     f"{tally['differing_api_texts_with_an_html_comment']}")  # fmt: skip
    verdict = result["verdict"]
    lines.append("Census: passes" if verdict["passes"] else
                 f"Census: FAILS ({'; '.join(verdict['failures'])})")  # fmt: skip
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    from trialpulse.ingest.history import refuse_on_a_runner

    parser = argparse.ArgumentParser(description="The census of ADR 0042 (Step 14).")
    parser.add_argument("--memory-limit", default="3GB", help="DuckDB's memory limit")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    refuse_on_a_runner()  # it reads the warehouse and the pinned dataset (ADR 0040)
    cfg = load_project_config()
    inputs = Inputs.default(cfg)
    if not inputs.warehouse.is_file():
        raise RefusedError(f"no warehouse at {inputs.warehouse}")
    secret = secrets.token_bytes(32)  # the feature hashes need a key; held in memory only
    try:
        with httpx.Client(timeout=180.0, follow_redirects=True) as client:
            result = run_census(ApiClient(client, per_minute=PER_MINUTE), inputs, cfg, secret,
                                memory_limit=args.memory_limit)  # fmt: skip
    except httpx.HTTPError as error:
        raise StoppedEarlyError(f"API v2 request failed: {type(error).__name__}") from error
    args.out_dir.mkdir(parents=True, exist_ok=True)
    day = str(result["data_timestamp"] or dt.date.today().isoformat())[:10]
    out = args.out_dir / f"census_{day}.json"
    out.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    print("\n".join(summary_lines(result)))
    print(f"Counts written to {out.as_posix()}")
    return 0 if result["verdict"]["passes"] else 1


if __name__ == "__main__":
    raise SystemExit(run(main))
