"""A synthetic registry for the feature tests, and helpers to compute features on it.

`registry()` scripts a few hundred trials at random (seeded): several versions each, status
paths with suspensions, early stops and reversals, dates given to the day or the month that
slip, enrollment targets that change and close, sponsors that hand trials over or are
renamed, individuals and placeholder names, texts and intervention lists that change. The
landmark keys come from the real cohort build (final, and as of each walk-forward origin).

`compute(con, versions, keys)` runs the real feature queries on hand-built versions, for
tests that work a value out by hand. No real data anywhere.
"""

import datetime as dt
import hashlib
import random
from dataclasses import dataclass
from typing import Any

import duckdb
import pandas as pd
import pytest

from trialpulse.cohort.build import build_cohort
from trialpulse.cohort.rules import CohortRules, add_months
from trialpulse.config import ProjectConfig, load_project_config
from trialpulse.features.build import (
    FEATURE_TABLE,
    KEY_SOURCES_TABLE,
    TEXT_KEYS_TABLE,
    compute_features,
    origin_source,
)
from trialpulse.features.registry import ALLOWED_VERSION_FIELDS, FORBIDDEN_VERSION_FIELDS
from trialpulse.features.states import KEYS_TABLE, expose_versions

CUTOFF = dt.date(2026, 9, 25)
CFG = load_project_config()
RULES = CohortRules.from_config(CFG, cutoff=CUTOFF)
D = dt.date
VERSION_TYPES: dict[str, str] = {
    "nct_id": "VARCHAR",
    "nct_version": "BIGINT",
    "source": "VARCHAR",
    "effective_date": "DATE",
    "effective_date_type": "VARCHAR",
    "submitted_date": "DATE",
    "overall_status": "VARCHAR",
    "last_known_status": "VARCHAR",
    "study_type": "VARCHAR",
    "study_first_post_date": "DATE",
    "study_first_post_date_type": "VARCHAR",
    "study_first_submit_date": "DATE",
    "status_verified_date": "DATE",
    "start_date": "DATE",
    "start_date_precision": "VARCHAR",
    "start_date_type": "VARCHAR",
    "primary_completion_date": "DATE",
    "primary_completion_date_precision": "VARCHAR",
    "primary_completion_date_type": "VARCHAR",
    "completion_date": "DATE",
    "completion_date_precision": "VARCHAR",
    "completion_date_type": "VARCHAR",
    "allocation": "VARCHAR",
    "intervention_model": "VARCHAR",
    "primary_purpose": "VARCHAR",
    "masking": "VARCHAR",
    "healthy_volunteers": "BOOLEAN",
    "sex": "VARCHAR",
    "minimum_age_years": "DOUBLE",
    "maximum_age_years": "DOUBLE",
    "enrollment_count": "BIGINT",
    "enrollment_type": "VARCHAR",
    "lead_sponsor_class": "VARCHAR",
    "organization_class": "VARCHAR",
    "lead_sponsor_name": "VARCHAR",
    "sponsor_key": "VARCHAR",
    "sponsor_is_individual": "BOOLEAN",
    "brief_title_hash": "VARCHAR",
    "official_title_hash": "VARCHAR",
    "brief_summary_hash": "VARCHAR",
    "eligibility_criteria_hash": "VARCHAR",
    "why_stopped_hash": "VARCHAR",
    "content_hash": "VARCHAR",
}
assert set(ALLOWED_VERSION_FIELDS) | set(FORBIDDEN_VERSION_FIELDS) == set(VERSION_TYPES)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def version(
    nct_id: str, number: int, posted: dt.date, status: str, **fields: Any
) -> dict[str, Any]:
    """One canonical version with plain defaults: an interventional trial of a company,
    first posted on the day of its first version unless `t0` says otherwise."""
    row: dict[str, Any] = dict.fromkeys(VERSION_TYPES)
    row.update(
        nct_id=nct_id,
        nct_version=number,
        source="dataset",
        effective_date=posted,
        effective_date_type="ESTIMATED" if posted.year < 2017 else "ACTUAL",
        submitted_date=posted - dt.timedelta(days=2),
        overall_status=status,
        study_type="INTERVENTIONAL",
        study_first_post_date=fields.pop("t0", posted),
        study_first_post_date_type="ESTIMATED",
        status_verified_date=posted.replace(day=1),
        start_date_precision="day",
        primary_completion_date_precision="day",
        completion_date=D(2040, 1, 1),  # far away, so nothing lapses unless a test says so
        completion_date_precision="day",
        enrollment_count=100,
        enrollment_type="ESTIMATED",
        lead_sponsor_class="INDUSTRY",
        organization_class="INDUSTRY",
        sponsor_key="acme pharma",
        sponsor_is_individual=False,
    )
    unknown = set(fields) - set(VERSION_TYPES)
    assert not unknown, unknown
    row.update(fields)
    return row


def load_versions(con: duckdb.DuckDBPyConnection, rows: list[dict[str, Any]], table: str) -> None:
    frame = pd.DataFrame(rows, columns=list(VERSION_TYPES)).astype(object)
    con.register("versions_frame", frame)
    columns = ", ".join(f"CAST({c} AS {t}) AS {c}" for c, t in VERSION_TYPES.items())
    con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT {columns} FROM versions_frame")
    con.unregister("versions_frame")


def load_texts(con: duckdb.DuckDBPyConnection, texts: dict[str, str]) -> None:
    frame = pd.DataFrame({"text_hash": list(texts), "text": list(texts.values())}, dtype=object)
    con.register("texts_frame", frame)
    con.execute(
        "CREATE OR REPLACE TABLE texts AS SELECT CAST(text_hash AS VARCHAR) AS text_hash, "
        "CAST(text AS VARCHAR) AS text FROM texts_frame"
    )
    con.unregister("texts_frame")


def load_interventions(
    con: duckdb.DuckDBPyConnection, interventions: dict[tuple[str, int], list[str]]
) -> None:
    """`base_interventions` holds the lists; the feature queries read the view over it."""
    con.execute(
        "CREATE OR REPLACE TABLE base_interventions "
        "(nct_id VARCHAR, nct_version BIGINT, intervention_types VARCHAR[])"
    )
    rows = [(i, n, sorted(kinds)) for (i, n), kinds in interventions.items() if kinds]
    if rows:
        con.executemany("INSERT INTO base_interventions VALUES (?, ?, ?)", rows)
    restore_inputs(con)


def restore_inputs(con: duckdb.DuckDBPyConnection) -> None:
    """Point the feature queries back at the registry as loaded."""
    con.execute(
        "CREATE OR REPLACE TEMP VIEW intervention_versions AS SELECT * FROM base_interventions"
    )
    if con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'all_versions'"
    ).fetchone() != (0,):
        expose_versions(con, "all_versions", RULES)


def load_keys(con: duckdb.DuckDBPyConnection, keys: list[tuple[str, int, dt.date]]) -> None:
    con.execute(
        f"CREATE OR REPLACE TABLE {KEYS_TABLE} "
        "(trial_id VARCHAR, landmark_index BIGINT, landmark_date DATE)"
    )
    con.executemany(f"INSERT INTO {KEYS_TABLE} VALUES (?, ?, ?)", keys)


@pytest.fixture(scope="session")
def cfg() -> ProjectConfig:
    return CFG


@pytest.fixture(scope="session")
def rules() -> CohortRules:
    return RULES


def compute(
    cfg: ProjectConfig,
    rules: CohortRules,
    versions: list[dict[str, Any]],
    keys: list[tuple[str, int, dt.date]],
    texts: dict[str, str] | None = None,
    interventions: dict[tuple[str, int], list[str]] | None = None,
) -> pd.DataFrame:
    """The features of hand-built versions at the given landmark keys, by trial and index."""
    with duckdb.connect() as con:
        load_versions(con, versions, "all_versions")
        load_texts(con, texts or {})
        load_interventions(con, interventions or {})
        load_keys(con, keys)
        expose_versions(con, "all_versions", rules)
        compute_features(con, rules, cfg)
        frame: pd.DataFrame = con.execute(
            f"""SELECT f.*, t.* EXCLUDE (trial_id, landmark_index)
            FROM {FEATURE_TABLE} f JOIN {TEXT_KEYS_TABLE} t USING (trial_id, landmark_index)
            ORDER BY trial_id, landmark_index"""
        ).df()
    return frame.set_index(["trial_id", "landmark_index"])


# The scripted registry ----------------------------------------------------------------------

ORGANIZATIONS: tuple[tuple[str, str], ...] = (
    ("acme pharma", "INDUSTRY"),
    ("borealis therapeutics inc", "INDUSTRY"),
    ("cobalt biosciences", "INDUSTRY"),
    ("delta devices ltd", "INDUSTRY"),
    ("eastern university", "OTHER"),
    ("fairview hospital", "OTHER"),
    ("granite medical center", "OTHER"),
    ("harbor institute of oncology", "OTHER"),
    ("inland university hospital", "OTHER"),
    ("national institute of examples", "NIH"),
    ("federal health agency", "FED"),
    ("juniper clinical network", "NETWORK"),
    ("kestrel research foundation", "OTHER"),
    ("lumen pharmaceuticals", "INDUSTRY"),
)
NO_IDENTITY: tuple[tuple[str | None, str, bool], ...] = (
    (None, "INDIV", True),  # an individual: the warehouse stores no name and no key
    ("redacted", "INDUSTRY", False),  # a placeholder name
    ("dr a example", "OTHER", False),  # a person-named sponsor the warehouse rule missed
)
# Renames: from the date on, every newly posted version shows the new key (ADR 0018).
RENAMES: dict[str, tuple[dt.date, str]] = {
    "acme pharma": (D(2015, 3, 1), "acme pharma global"),
    "eastern university": (D(2016, 9, 1), "university of the east"),
    "cobalt biosciences": (D(2018, 2, 1), "cobalt bio"),
}
STATUS_PATH: dict[str, tuple[tuple[str, float], ...]] = {
    "NOT_YET_RECRUITING": (("NOT_YET_RECRUITING", 0.3), ("RECRUITING", 0.5), ("WITHDRAWN", 0.2)),
    "RECRUITING": (
        ("RECRUITING", 0.45),
        ("ACTIVE_NOT_RECRUITING", 0.2),
        ("SUSPENDED", 0.1),
        ("TERMINATED", 0.1),
        ("COMPLETED", 0.1),
        ("UNKNOWN", 0.05),
    ),
    "ENROLLING_BY_INVITATION": (("ENROLLING_BY_INVITATION", 0.6), ("COMPLETED", 0.4)),
    "ACTIVE_NOT_RECRUITING": (
        ("ACTIVE_NOT_RECRUITING", 0.4),
        ("COMPLETED", 0.45),
        ("TERMINATED", 0.15),
    ),
    "SUSPENDED": (("SUSPENDED", 0.3), ("RECRUITING", 0.4), ("TERMINATED", 0.3)),
    "UNKNOWN": (("UNKNOWN", 0.6), ("COMPLETED", 0.4)),
    "COMPLETED": (("COMPLETED", 0.97), ("RECRUITING", 0.03)),
    "TERMINATED": (("TERMINATED", 0.95), ("RECRUITING", 0.05)),
    "WITHDRAWN": (("WITHDRAWN", 1.0),),
}
TITLES = (
    "A Study of Compound {n} in Adults",
    "Phase 2 Trial of Compound {n}",
    "A Phase I/II Study of Device {n}",
    "Randomized Phase III Comparison of Regimen {n}",
    "Compound {n}: a Pilot Study (Phase 4)",
    "Behavioral Program {n} for Sleep",
)
KINDS = ("DRUG", "DEVICE", "BEHAVIORAL", "BIOLOGICAL", "PROCEDURE", "OTHER")
FIRST_POSTS = ((1, 20), (8, 31))


def _eligibility(rng: random.Random) -> str:
    lines = ["Inclusion Criteria:"]
    lines += [f"Adults with condition {rng.randint(1, 60)}" for _ in range(rng.randint(1, 8))]
    if rng.random() < 0.5:
        lines.append(f"Hemoglobin >= {rng.randint(8, 12)} g/dL and age at least 18 years")
    if rng.random() < 0.9:
        lines.append("Exclusion Criteria:")
        lines += [f"History of event {rng.randint(1, 60)}" for _ in range(rng.randint(0, 9))]
        if rng.random() < 0.4:
            lines.append(f"Creatinine greater than {rng.randint(1, 3)}.5 mg/dL")
    return "\n".join(lines)


@dataclass(frozen=True)
class Registry:
    versions: list[dict[str, Any]]
    texts: dict[str, str]
    interventions: dict[tuple[str, int], list[str]]


def registry(n_trials: int = 300, seed: int = 7) -> Registry:
    rng = random.Random(seed)
    # First posts on two days a year, so that landmarks fall on few distinct days and the
    # future-perturbation test, which recomputes once per landmark day, stays quick. One is
    # in the middle of a month and one at the end of a 31-day month, so that landmarks are
    # never a first of the month and half of them are clamped (31 August plus 6 months is
    # the last day of February).
    quarters = [D(year, month, day) for year in range(2012, 2020) for month, day in FIRST_POSTS]
    texts: dict[str, str] = {}
    versions: list[dict[str, Any]] = []
    interventions: dict[tuple[str, int], list[str]] = {}

    def keep(text: str) -> str:
        texts[text_hash(text)] = text
        return text_hash(text)

    for i in range(n_trials):
        nct_id = f"NCT{i + 1:08d}"
        t0 = rng.choice(quarters)
        if rng.random() < 0.12:
            key, sponsor_class, individual = rng.choice(NO_IDENTITY)
        else:
            key, sponsor_class = rng.choice(ORGANIZATIONS)
            individual = False
        study_type = "OBSERVATIONAL" if rng.random() < 0.06 else "INTERVENTIONAL"
        status = rng.choice(
            ["NOT_YET_RECRUITING", "RECRUITING", "RECRUITING", "ENROLLING_BY_INVITATION"]
        )
        month_precision = t0.year < 2017 or rng.random() < 0.3
        start = t0 + dt.timedelta(days=rng.randint(-500, 400))
        primary = start + dt.timedelta(days=rng.randint(200, 1500))
        completion = primary + dt.timedelta(days=rng.randint(0, 400))
        count, kind = rng.randint(10, 900), "ESTIMATED"
        design = {
            "allocation": rng.choice(["RANDOMIZED", "NON_RANDOMIZED", None]),
            "intervention_model": rng.choice(["PARALLEL", "SINGLE_GROUP", "CROSSOVER"]),
            "primary_purpose": rng.choice(["TREATMENT", "PREVENTION", "DIAGNOSTIC"]),
            "masking": rng.choice(["NONE", "DOUBLE", "QUADRUPLE"]),
            "healthy_volunteers": rng.choice([True, False, None]),
            "sex": rng.choice(["ALL", "FEMALE", "MALE"]),
            "minimum_age_years": rng.choice([18.0, 0.5, None]),
            "maximum_age_years": rng.choice([65.0, 80.0, None]),
        }
        title = rng.choice(TITLES).format(n=i)
        eligibility = _eligibility(rng)
        summary = f"Summary of study {i} about topic{i % 17} and theme{rng.randint(0, 9)}."
        kinds = rng.sample(KINDS, rng.randint(0, 3))
        verified = t0.replace(day=1)
        posted = t0
        for number in range(rng.randint(1, 7)):
            if number:
                step = rng.choice([0, 20, 45, 90, 181, 183, 200, 365, 400, 700])
                posted = posted + dt.timedelta(days=step)
                if rng.random() < 0.15:  # exactly on one of the trial's landmark days
                    posted = max(posted, add_months(t0, 6 * rng.randint(1, 6)))
                if posted > CUTOFF:
                    break
                choices, weights = zip(*STATUS_PATH[status], strict=True)
                status = rng.choices(choices, weights)[0]
                if rng.random() < 0.3:
                    primary += dt.timedelta(days=rng.choice([-60, 31, 95, 200, 420]))
                    completion = max(completion, primary) + dt.timedelta(days=rng.choice([0, 90]))
                if rng.random() < 0.15:
                    start += dt.timedelta(days=rng.choice([-45, 30, 75]))
                if rng.random() < 0.25:
                    count = max(1, int(count * rng.choice([0.5, 0.85, 0.95, 1.05, 1.3])))
                if status in ("ACTIVE_NOT_RECRUITING", "COMPLETED", "TERMINATED"):
                    kind = "ACTUAL" if rng.random() < 0.8 else kind
                if rng.random() < 0.7:
                    verified = posted.replace(day=1)
                if rng.random() < 0.1 and key is not None and key != "redacted":
                    key, sponsor_class = rng.choice(ORGANIZATIONS)  # the trial is handed over
                if rng.random() < 0.03:
                    study_type = "OBSERVATIONAL" if study_type == "INTERVENTIONAL" else study_type
                if rng.random() < 0.15:
                    eligibility = _eligibility(rng)
                if rng.random() < 0.1:
                    title = rng.choice(TITLES).format(n=i)
                if rng.random() < 0.1:
                    kinds = rng.sample(KINDS, rng.randint(0, 3))
                if rng.random() < 0.1:
                    design["masking"] = rng.choice(["NONE", "DOUBLE", "QUADRUPLE"])
            shown = key
            if key in RENAMES and posted >= RENAMES[key][0]:
                shown = RENAMES[key][1]
            precision = "month" if month_precision else "day"

            def given(day: dt.date, precision: str = precision) -> dt.date:
                return day.replace(day=1) if precision == "month" else day

            unknown = status == "UNKNOWN"
            versions.append(
                version(
                    nct_id,
                    number,
                    posted,
                    status,
                    t0=t0,
                    last_known_status="RECRUITING" if unknown else None,
                    study_type=study_type,
                    status_verified_date=verified,
                    start_date=None if i % 23 == 0 else given(start),
                    start_date_precision=precision,
                    start_date_type="ACTUAL" if start <= posted else "ESTIMATED",
                    primary_completion_date=None if i % 29 == 0 else given(primary),
                    primary_completion_date_precision=precision,
                    primary_completion_date_type=(
                        None
                        if i % 29 == 0 or i % 7 == 0
                        else "ACTUAL"
                        if primary <= posted
                        else "ESTIMATED"
                    ),
                    completion_date=given(completion),
                    completion_date_precision=precision,
                    completion_date_type="ACTUAL" if completion <= posted else "ESTIMATED",
                    enrollment_count=None if i % 31 == 0 else count,
                    enrollment_type=None if i % 31 == 0 else kind,
                    lead_sponsor_class=sponsor_class,
                    organization_class=sponsor_class,
                    lead_sponsor_name=None if shown is None else shown.title(),
                    sponsor_key=shown,
                    sponsor_is_individual=individual,
                    brief_title_hash=keep(title),
                    official_title_hash=None if i % 13 == 0 else keep("Official: " + title),
                    brief_summary_hash=keep(summary),
                    eligibility_criteria_hash=keep(eligibility),
                    why_stopped_hash=keep("slow accrual") if status == "TERMINATED" else None,
                    content_hash=f"c{i}-{number}",
                    **design,
                )
            )
            interventions[(nct_id, number)] = list(kinds)
    return Registry(versions, texts, interventions)


@dataclass
class World:
    con: duckdb.DuckDBPyConnection
    registry: Registry
    origins: tuple[dt.date, ...]

    def frame(self, sql: str) -> pd.DataFrame:
        result: pd.DataFrame = self.con.execute(sql).df()
        return result


@pytest.fixture(scope="session")
def world(cfg: ProjectConfig, rules: CohortRules) -> World:
    """The scripted registry in DuckDB, with the landmark keys of the real cohort build and
    the features computed once: `baseline_features`, `baseline_text_keys` and the states
    at each landmark, `baseline_at_landmark`. Tests that recompute must restore the inputs."""
    data = registry()
    con = duckdb.connect()
    load_versions(con, data.versions, "all_versions")
    load_texts(con, data.texts)
    load_interventions(con, data.interventions)
    origins = tuple(origin.date for origin in cfg.walk_forward.origins)
    con.execute("CREATE OR REPLACE TEMP VIEW versions AS SELECT * FROM all_versions")
    con.execute(
        f"CREATE TABLE {KEY_SOURCES_TABLE} "
        "(source VARCHAR, trial_id VARCHAR, landmark_index BIGINT, landmark_date DATE)"
    )
    builds = [("final", rules), *((origin_source(o), rules.as_of(o)) for o in origins)]
    for name, how in builds:
        build_cohort(con, how)
        con.execute(
            f"""INSERT INTO {KEY_SOURCES_TABLE}
            SELECT '{name}', trial_id, landmark_index, landmark_date FROM cohort_landmarks"""
        )
        con.execute(
            f"""CREATE OR REPLACE TABLE landmarks_{name.replace("-", "_")} AS
            SELECT * FROM cohort_landmarks"""
        )
    con.execute(
        f"""CREATE TABLE {KEYS_TABLE} AS
        SELECT DISTINCT trial_id, landmark_index, landmark_date FROM {KEY_SOURCES_TABLE}"""
    )
    expose_versions(con, "all_versions", rules)
    compute_features(con, rules, cfg)
    con.execute(f"CREATE TABLE baseline_features AS SELECT * FROM {FEATURE_TABLE}")
    con.execute(f"CREATE TABLE baseline_text_keys AS SELECT * FROM {TEXT_KEYS_TABLE}")
    con.execute("CREATE TABLE baseline_at_landmark AS SELECT * FROM feature_at_landmark")
    return World(con, data, origins)
