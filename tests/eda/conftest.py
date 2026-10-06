"""A small synthetic registry for the EDA tests. No real data.

Each trial follows one of a few scripted histories (months are counted from its first post),
so every number the analysis returns has a known answer. The histories go through the real
cohort build, and the EDA then reads the same kinds of files it reads in production: a
warehouse, the cohort Parquet files and a current-record snapshot.
"""

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest

from trialpulse.cohort import build
from trialpulse.cohort.rules import add_months
from trialpulse.eda.analysis import Sources

VERSION_TYPES = {
    "nct_id": "VARCHAR",
    "nct_version": "BIGINT",
    "effective_date": "DATE",
    "effective_date_type": "VARCHAR",
    "submitted_date": "DATE",
    "overall_status": "VARCHAR",
    "last_known_status": "VARCHAR",
    "study_type": "VARCHAR",
    "study_first_post_date": "DATE",
    "study_first_submit_date": "DATE",
    "status_verified_date": "DATE",
    "start_date": "DATE",
    "start_date_precision": "VARCHAR",
    "primary_completion_date": "DATE",
    "primary_completion_date_precision": "VARCHAR",
    "completion_date": "DATE",
    "completion_date_precision": "VARCHAR",
    "enrollment_count": "BIGINT",
    "lead_sponsor_class": "VARCHAR",
}

# A history is a list of steps (month, status, changes). Month 0 is the first post.
LATER = {"primary_completion_months": 36}
HISTORIES: dict[str, list[tuple[int, str, dict[str, Any]]]] = {
    "withdrawn_early": [(0, "NOT_YET_RECRUITING", {}), (5, "WITHDRAWN", {})],
    "stalled_withdrawn": [(0, "NOT_YET_RECRUITING", {}), (15, "WITHDRAWN", {})],
    "suspended_terminated": [(0, "RECRUITING", {}), (8, "SUSPENDED", {}), (16, "TERMINATED", {})],
    "slipped_terminated": [(0, "RECRUITING", {}), (9, "RECRUITING", LATER), (20, "TERMINATED", {})],
    "terminated_late": [(0, "RECRUITING", {}), (40, "TERMINATED", {})],
    "completed_short": [(0, "RECRUITING", {}), (10, "COMPLETED", {})],
    "slipped_completed": [
        (0, "RECRUITING", {}),
        (9, "RECRUITING", {**LATER, "enrollment_count": 150}),
        (30, "COMPLETED", {}),
    ],
    "completed_long": [
        (0, "RECRUITING", {}),
        (11, "ACTIVE_NOT_RECRUITING", {}),
        (50, "COMPLETED", {}),
    ],
    "overdue_completed": [
        (0, "RECRUITING", {"primary_completion_months": 10}),
        (18, "COMPLETED", {"primary_completion_months": 10}),
    ],
    "waiting_completed": [
        (0, "NOT_YET_RECRUITING", {}),
        (14, "RECRUITING", {}),
        (36, "COMPLETED", {}),
    ],
    "cut_completed": [
        (0, "RECRUITING", {}),
        (9, "RECRUITING", {"enrollment_count": 60}),
        (28, "COMPLETED", {}),
    ],
    "still_open": [(0, "RECRUITING", {})],
}
# What a registration year holds: (lead sponsor class, history, start month or None, copies).
# A start month above 0 is a prospective registration; below -12, more than a year late.
YEAR_MIX: list[tuple[str, str, int | None, int]] = [
    ("INDUSTRY", "withdrawn_early", 2, 2),
    ("INDUSTRY", "stalled_withdrawn", 2, 1),
    ("INDUSTRY", "suspended_terminated", -1, 1),
    ("INDUSTRY", "slipped_terminated", -1, 1),
    ("INDUSTRY", "completed_short", 1, 2),
    ("INDUSTRY", "slipped_completed", -14, 1),
    ("INDUSTRY", "completed_long", -1, 2),
    ("INDUSTRY", "overdue_completed", 1, 1),
    ("INDUSTRY", "waiting_completed", 2, 1),
    ("INDUSTRY", "cut_completed", -14, 1),
    ("OTHER", "withdrawn_early", 2, 1),
    ("OTHER", "stalled_withdrawn", 2, 1),
    ("OTHER", "suspended_terminated", -1, 1),
    ("OTHER", "terminated_late", -3, 2),
    ("OTHER", "completed_short", None, 1),
    ("OTHER", "slipped_completed", -14, 2),
    ("OTHER", "completed_long", -1, 3),
    ("OTHER", "waiting_completed", 2, 1),
    ("OTHER", "still_open", -1, 1),
    ("NIH", "completed_short", 1, 1),
    ("FED", "completed_long", -1, 1),
]
MODELING_YEARS = (2014, 2015, 2016, 2017)
FIRST_POST_MONTH = 3  # every trial of a year is first posted on 1 March
COVID_YEAR = 2019  # registered after 2018: descriptive only
COVID_TRIALS = 6
COVID_SUSPENDED = dt.date(2020, 4, 10)


@dataclass(frozen=True)
class Trial:
    nct_id: str
    year: int
    sponsor: str
    history: str
    start_month: int | None


@dataclass(frozen=True)
class World:
    sources: Sources
    trials: tuple[Trial, ...]

    def count(self, *histories: str, years: tuple[int, ...] = MODELING_YEARS) -> int:
        return sum(t.history in histories and t.year in years for t in self.trials)


def first_post(year: int) -> dt.date:
    return dt.date(year, FIRST_POST_MONTH, 1)


def _version(
    trial: Trial, number: int, posted: dt.date, status: str, **changes: Any
) -> dict[str, Any]:
    t0 = first_post(trial.year)
    row: dict[str, Any] = {
        "nct_id": trial.nct_id,
        "nct_version": number,
        "effective_date": posted,
        "effective_date_type": "ESTIMATED" if posted.year < 2017 else "ACTUAL",
        "submitted_date": posted - dt.timedelta(days=2),
        "overall_status": status,
        "last_known_status": None,
        "study_type": "INTERVENTIONAL",
        "study_first_post_date": t0,
        "study_first_submit_date": t0 - dt.timedelta(days=5),
        "status_verified_date": posted,
        "start_date": None if trial.start_month is None else add_months(t0, trial.start_month),
        "start_date_precision": None if trial.start_month is None else "day",
        "primary_completion_date": add_months(t0, changes.pop("primary_completion_months", 24)),
        "primary_completion_date_precision": "day",
        "completion_date": add_months(t0, 180),  # far away, so no state lapses (ADR 0014)
        "completion_date_precision": "day",
        "enrollment_count": 100,
        "lead_sponsor_class": trial.sponsor,
    }
    row.update(changes)
    return row


def _trials() -> list[Trial]:
    trials: list[Trial] = []
    for year in MODELING_YEARS:
        for sponsor, history, start_month, copies in YEAR_MIX:
            for _ in range(copies):
                trials.append(
                    Trial(f"NCT{len(trials) + 1:08d}", year, sponsor, history, start_month)
                )
    for _ in range(COVID_TRIALS):
        trials.append(Trial(f"NCT{len(trials) + 1:08d}", COVID_YEAR, "OTHER", "covid", -1))
    return trials


def _versions(trial: Trial) -> list[dict[str, Any]]:
    t0 = first_post(trial.year)
    if trial.history == "covid":
        steps = [
            (t0, "RECRUITING"),
            (COVID_SUSPENDED, "SUSPENDED"),
            (dt.date(2020, 5, 10), "SUSPENDED"),  # still suspended: not a second suspension
            (dt.date(2020, 8, 1), "RECRUITING"),
            (dt.date(2021, 6, 1), "COMPLETED"),
        ]
        return [_version(trial, n, posted, status) for n, (posted, status) in enumerate(steps)]
    return [
        _version(trial, n, add_months(t0, month), status, **dict(changes))
        for n, (month, status, changes) in enumerate(HISTORIES[trial.history])
    ]


def write_warehouse(path: Path, rows: list[dict[str, Any]]) -> None:
    frame = pd.DataFrame(rows, columns=list(VERSION_TYPES)).astype(object)
    with duckdb.connect(str(path)) as con:
        con.register("versions_frame", frame)
        columns = ", ".join(f"CAST({c} AS {t}) AS {c}" for c, t in VERSION_TYPES.items())
        con.execute(f"CREATE TABLE versions AS SELECT {columns} FROM versions_frame")
        con.unregister("versions_frame")
        con.execute("CREATE TABLE build_info (key VARCHAR, value VARCHAR)")
        con.execute(
            "INSERT INTO build_info VALUES ('schema_version', '2'), "
            "('dataset.revision', 'synthetic')"
        )


def write_current_fields(path: Path, trials: list[Trial]) -> None:
    """A current-record snapshot: one phase list per trial, and no record for the last trial
    of the first year."""
    phases = (["PHASE1"], ["PHASE2"], ["PHASE2", "PHASE3"], ["PHASE4"], ["NA"], [])
    missing = [t for t in trials if t.year == MODELING_YEARS[0]][-1].nct_id
    rows = [
        {"nct_id": t.nct_id, "phases": phases[i % len(phases)]}
        for i, t in enumerate(trials)
        if t.nct_id != missing
    ]
    with duckdb.connect() as con:
        con.register("snapshot", pd.DataFrame(rows))
        con.execute(
            f"COPY (SELECT nct_id, CAST(phases AS VARCHAR[]) AS phases FROM snapshot) "
            f"TO '{path.as_posix()}' (FORMAT PARQUET)"
        )


@pytest.fixture(scope="session")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    root = tmp_path_factory.mktemp("eda_world")
    trials = _trials()
    warehouse = root / "warehouse.duckdb"
    write_warehouse(warehouse, [row for trial in trials for row in _versions(trial)])
    cohort_dir = root / "cohort"
    audit = root / "data_audit.md"
    args = ["--warehouse", str(warehouse), "--out-dir", str(cohort_dir), "--audit", str(audit)]
    assert build.main(args) == 0
    current_fields = root / "spike" / "current_fields.parquet"
    current_fields.parent.mkdir()
    write_current_fields(current_fields, trials)
    sources = Sources(
        landmarks=cohort_dir / "landmarks.parquet",
        outcomes=cohort_dir / "outcomes.parquet",
        warehouse=warehouse,
        current_fields=current_fields,
    )
    return World(sources, tuple(trials))
