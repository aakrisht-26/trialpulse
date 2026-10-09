"""A small synthetic registry for the EDA tests, and Python twins of the EDA's rules.

Each trial follows a scripted history, so every number the analysis returns has a known
answer. The histories go through the real cohort build, and the EDA then reads the same
kinds of files it reads in production: a warehouse, the cohort Parquet files and a
current-record snapshot.

The twins (`outcome`, `state_at`, `signals_at`, `timing_of`) restate the EDA's rules in
plain Python from the scripted versions: which version is in effect on a day, what a
record shows against its first version, what the cohort built as of a date knows of an
outcome (ADR 0016). The tests compare the SQL with them row by row. No real data.
"""

import datetime as dt
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pytest

from trialpulse.cohort import build
from trialpulse.cohort.audit import DAYS_PER_MONTH
from trialpulse.cohort.rules import add_months
from trialpulse.eda import analysis as an
from trialpulse.eda.analysis import Sources
from trialpulse.eda.report import cohort_sources
from trialpulse.eval import EVENT_CENSORED, EVENT_COMPLETE, EVENT_STOP
from trialpulse.models.aalen_johansen import aalen_johansen

CUTOFF = dt.date(2026, 9, 25)  # the data cutoff of config/project.yaml
BEFORE = dt.date(2018, 1, 1)  # Section 10: modeling-relevant analysis reads nothing later
TERMINAL = {"TERMINATED": an.KIND_TERMINATED, "WITHDRAWN": an.KIND_WITHDRAWN,
            "COMPLETED": an.KIND_COMPLETED}  # fmt: skip
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
    "enrollment_type": "VARCHAR",
    "lead_sponsor_class": "VARCHAR",
}

Step = tuple[Any, str, dict[str, Any]]  # (months after the first post, or a date), status, changes
LATER = {"primary_completion": 36}  # the primary completion date moves from month 24 to 36
ACTUAL = {"enrollment_type": "ACTUAL"}
# The histories every modeling year holds. Months are counted from the first post.
HISTORIES: dict[str, list[Step]] = {
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
        (0, "RECRUITING", {"primary_completion": 10}),
        (18, "COMPLETED", {"primary_completion": 10}),
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
COVID_YEAR = 2019  # registered after 2018: descriptive only
COVID_TRIALS = 6
COVID_SUSPENDED = dt.date(2020, 4, 10)
MID_2015 = dt.date(2015, 6, 15)  # a first post in the middle of a month
# Trials on the edges of the rules. Each is (name, first post, sponsor, start, steps), with
# `start` the start date as (months after the first post, precision), or None.
EDGE_CASES: list[tuple[str, dt.date, str, tuple[int, str] | None, list[Step]]] = [
    # A version posted on the landmark day is part of the state at the landmark (Section 6).
    ("on_landmark_day", MID_2015, "INDUSTRY", (-1, "day"),
     [(0, "RECRUITING", {}), (12, "SUSPENDED", {}), (30, "COMPLETED", {})]),
    # Two versions on one post date: the higher version number is in effect (ADR 0013).
    ("shared_post_date", MID_2015, "INDUSTRY", (-1, "day"),
     [(0, "RECRUITING", {}), (9, "NOT_YET_RECRUITING", {"enrollment_count": 60}),
      (9, "RECRUITING", {}), (40, "COMPLETED", {})]),
    # Not yet recruiting, start date in the landmark's own month: the month has not ended.
    ("start_in_landmark_month", MID_2015, "OTHER", (12, "month"),
     [(0, "NOT_YET_RECRUITING", {}), (26, "WITHDRAWN", {})]),
    # Not yet recruiting, start month ended before the landmark.
    ("start_month_passed", MID_2015, "OTHER", (11, "month"),
     [(0, "NOT_YET_RECRUITING", {}), (13, "WITHDRAWN", {})]),
    # Primary completion given to the day, earlier in the landmark's month: not passed yet.
    ("completion_in_landmark_month", MID_2015, "OTHER", (-1, "day"),
     [(0, "RECRUITING", {"primary_completion": dt.date(2016, 6, 5)}),
      (20, "COMPLETED", {"primary_completion": dt.date(2016, 6, 5)})]),
    # Inputs missing in the first version: the signals built on them are not known.
    ("missing_inputs", MID_2015, "OTHER", None,
     [(0, "RECRUITING", {"primary_completion": None, "enrollment_count": None,
                         "enrollment_type": None}),
      (9, "RECRUITING", {}), (22, "TERMINATED", {})]),
    # A later version moves the start date: registration timing reads the first version.
    ("start_date_changes", MID_2015, "INDUSTRY", (2, "day"),
     [(0, "NOT_YET_RECRUITING", {}), (3, "RECRUITING", {"start": (-20, "day")}),
      (30, "COMPLETED", {"start": (-20, "day")})]),
    # Suspended and resumed before the landmark: suspended at some point, not at the landmark.
    ("suspended_and_resumed", MID_2015, "INDUSTRY", (-1, "day"),
     [(0, "RECRUITING", {}), (4, "SUSPENDED", {}), (7, "RECRUITING", {}), (26, "COMPLETED", {})]),
    # Not yet recruiting with the planned start still ahead: waiting, but not overdue.
    ("start_still_ahead", MID_2015, "OTHER", (20, "day"),
     [(0, "NOT_YET_RECRUITING", {}), (22, "RECRUITING", {}), (44, "COMPLETED", {})]),
    # The last version before the landmark exactly 6 months before it, and 5 months before.
    ("quiet_for_6_months", MID_2015, "OTHER", (-1, "day"),
     [(0, "RECRUITING", {}), (6, "RECRUITING", {}), (27, "COMPLETED", {})]),
    ("quiet_for_5_months", MID_2015, "OTHER", (-1, "day"),
     [(0, "RECRUITING", {}), (7, "RECRUITING", {}), (27, "COMPLETED", {})]),
    # Enrollment targets at and around the 10% thresholds.
    ("target_95", MID_2015, "INDUSTRY", (-1, "day"),
     [(0, "RECRUITING", {}), (8, "RECRUITING", {"enrollment_count": 95}), (25, "COMPLETED", {})]),
    ("target_90", MID_2015, "INDUSTRY", (-1, "day"),
     [(0, "RECRUITING", {}), (8, "RECRUITING", {"enrollment_count": 90}), (19, "TERMINATED", {})]),
    ("target_110", MID_2015, "INDUSTRY", (-1, "day"),
     [(0, "RECRUITING", {}), (8, "RECRUITING", {"enrollment_count": 110}), (25, "COMPLETED", {})]),
    ("target_105", MID_2015, "INDUSTRY", (-1, "day"),
     [(0, "RECRUITING", {}), (8, "RECRUITING", {"enrollment_count": 105}), (25, "COMPLETED", {})]),
    # Enrollment closed by the landmark (the count is ACTUAL): short of the target, and on it.
    ("closed_short", MID_2015, "INDUSTRY", (-1, "day"),
     [(0, "RECRUITING", {}), (9, "ACTIVE_NOT_RECRUITING", {**ACTUAL, "enrollment_count": 70}),
      (15, "TERMINATED", {**ACTUAL, "enrollment_count": 70})]),
    ("closed_on_target", MID_2015, "INDUSTRY", (-1, "day"),
     [(0, "RECRUITING", {}), (9, "ACTIVE_NOT_RECRUITING", ACTUAL), (20, "COMPLETED", ACTUAL)]),
    # Registered with enrollment already closed: there is no first target to compare with.
    ("closed_from_the_start", MID_2015, "OTHER", (-30, "day"),
     [(0, "ACTIVE_NOT_RECRUITING", {**ACTUAL, "enrollment_count": 40}),
      (18, "COMPLETED", {**ACTUAL, "enrollment_count": 40})]),
    # Dates that move the other way round from the scripted mix.
    ("completion_moved_later", MID_2015, "OTHER", (-1, "day"),
     [(0, "RECRUITING", {}), (8, "RECRUITING", {"completion": 200}),
      (33, "COMPLETED", {"completion": 200})]),
    ("primary_moved_earlier", MID_2015, "OTHER", (-1, "day"),
     [(0, "RECRUITING", {}), (8, "RECRUITING", {"primary_completion": 18}),
      (17, "COMPLETED", {"primary_completion": 18})]),
    # Censored under the UNKNOWN rule: the completion date passes and nobody verifies. The
    # last version still carries the verification of October 2015, so the record lapses on
    # 2017-11-01: 2018-01-01 already knows it.
    ("lapsed", MID_2015, "OTHER", (-1, "day"),
     [(0, "RECRUITING", {"completion": 12}),
      (10, "RECRUITING", {"completion": 12, "verified": 4})]),
    # The same record verified in April 2016 lapses on 2018-05-01. On 2018-01-01 it is an open
    # trial; only the final cohort censors it at its last verification (ADR 0016).
    ("lapses_in_2018", MID_2015, "OTHER", (-1, "day"),
     [(0, "RECRUITING", {"completion": 12}), (10, "RECRUITING", {"completion": 12})]),
    # First posted on 2017-01-01: its 12-month landmark is 2018-01-01, the first locked day.
    ("landmark_on_2018_01_01", dt.date(2017, 1, 1), "INDUSTRY", (-1, "day"),
     [(0, "RECRUITING", {}), (8, "SUSPENDED", {}), (14, "TERMINATED", {})]),
    # First posted on 2018-01-01: registered in a locked year.
    ("registered_2018_01_01", dt.date(2018, 1, 1), "INDUSTRY", (-1, "day"),
     [(0, "NOT_YET_RECRUITING", {}), (5, "WITHDRAWN", {})]),
    # Registered while SUSPENDED: no version with another status came before.
    ("registered_suspended", dt.date(2019, 6, 1), "OTHER", (-1, "day"),
     [(0, "SUSPENDED", {}), (4, "RECRUITING", {}), (24, "COMPLETED", {})]),
]  # fmt: skip
# Trials the UNKNOWN rule censors: (the last status verification, where follow-up ends; the
# first day the record counts as lapsed, which is the day after the month 24 months after
# the verification month).
LAPSES = {
    "lapsed": (add_months(MID_2015, 4), dt.date(2017, 11, 1)),
    "lapses_in_2018": (add_months(MID_2015, 10), dt.date(2018, 5, 1)),
}


@dataclass(frozen=True)
class Trial:
    nct_id: str
    name: str  # the history it follows, or the name of its edge case
    sponsor: str
    first_post: dt.date
    versions: tuple[dict[str, Any], ...]

    @property
    def year(self) -> int:
        return self.first_post.year

    @property
    def edge_case(self) -> bool:
        return self.name not in HISTORIES and self.name != "covid"


@dataclass(frozen=True)
class World:
    sources: Sources
    trials: tuple[Trial, ...]

    def named(self, name: str) -> Trial:
        (trial,) = [t for t in self.trials if t.name == name]
        return trial

    def count(self, *names: str, years: tuple[int, ...] = MODELING_YEARS) -> int:
        return sum(t.name in names and t.year in years for t in self.trials)

    @property
    def final_sources(self) -> Sources:
        """The same files with the final cohort in the place of the cohort as of 2018-01-01:
        what the analysis functions see when they are pointed at outcomes to the cutoff."""
        return replace(
            self.sources,
            landmarks=self.sources.final_landmarks,
            outcomes=self.sources.final_outcomes,
        )

    def landmark_rows(
        self, index: int, before: dt.date | None = BEFORE
    ) -> list[tuple[Trial, dt.date]]:
        """The landmark rows the cohort build made at one index, by trial id: those of the
        cohort as of `before`, or those of the final cohort when `before` is None."""
        assert before in (BEFORE, None), "the build wrote the cohort as of 2018-01-01 only"
        path = self.sources.final_landmarks if before is None else self.sources.landmarks
        by_id = {t.nct_id: t for t in self.trials}
        with duckdb.connect() as con:
            rows = con.execute(
                f"""SELECT trial_id, landmark_date FROM '{path.as_posix()}'
                WHERE landmark_index = ? ORDER BY trial_id""",
                [index],
            ).fetchall()
        return [(by_id[trial_id], day) for trial_id, day in rows]


def first_post(year: int) -> dt.date:
    return dt.date(year, 3, 1)


def _when(t0: dt.date, value: Any) -> dt.date | None:
    """A date written as months after the first post, or as a date, or missing."""
    if value is None or isinstance(value, dt.date):
        return value
    return add_months(t0, int(value))


def _versions(
    nct_id: str, sponsor: str, t0: dt.date, start: tuple[int, str] | None, steps: list[Step]
) -> tuple[dict[str, Any], ...]:
    rows = []
    for number, (at, status, changes) in enumerate(steps):
        posted = _when(t0, at)
        assert posted is not None
        fields = {"primary_completion": 24, "completion": 180, "enrollment_count": 100,
                  "enrollment_type": "ESTIMATED", "start": start, **changes}  # fmt: skip
        started = fields["start"]
        primary = _when(t0, fields["primary_completion"])
        rows.append(
            {
                "nct_id": nct_id,
                "nct_version": number,
                "effective_date": posted,
                "effective_date_type": "ESTIMATED" if posted.year < 2017 else "ACTUAL",
                "submitted_date": posted - dt.timedelta(days=2 + number),  # lags of 2, 3, 4 days
                "overall_status": status,
                "last_known_status": None,
                "study_type": "INTERVENTIONAL",
                "study_first_post_date": t0,
                "study_first_submit_date": t0 - dt.timedelta(days=5),
                "status_verified_date": _when(t0, fields.get("verified")) or posted,
                "start_date": None if started is None else add_months(t0, started[0]),
                "start_date_precision": None if started is None else started[1],
                "primary_completion_date": primary,
                "primary_completion_date_precision": None if primary is None else "day",
                # Far away by default, so no state lapses unless a script says so (ADR 0014).
                "completion_date": _when(t0, fields["completion"]),
                "completion_date_precision": "day",
                "enrollment_count": fields["enrollment_count"],
                "enrollment_type": fields["enrollment_type"],
                "lead_sponsor_class": sponsor,
            }
        )
    return tuple(rows)


def _trials() -> list[Trial]:
    trials: list[Trial] = []

    def add(name: str, sponsor: str, t0: dt.date, start: Any, steps: list[Step]) -> None:
        nct_id = f"NCT{len(trials) + 1:08d}"
        trials.append(
            Trial(nct_id, name, sponsor, t0, _versions(nct_id, sponsor, t0, start, steps))
        )

    for year in MODELING_YEARS:
        for sponsor, history, start_month, copies in YEAR_MIX:
            start = None if start_month is None else (start_month, "day")
            for _ in range(copies):
                add(history, sponsor, first_post(year), start, HISTORIES[history])
    covid: list[Step] = [
        (0, "RECRUITING", {}),
        (COVID_SUSPENDED, "SUSPENDED", {}),
        (dt.date(2020, 5, 10), "SUSPENDED", {}),  # still suspended: not a second suspension
        (dt.date(2020, 8, 1), "RECRUITING", {}),
        (dt.date(2021, 6, 1), "COMPLETED", {}),
    ]
    for _ in range(COVID_TRIALS):
        add("covid", "OTHER", first_post(COVID_YEAR), (-1, "day"), covid)
    for name, t0, sponsor, start, steps in EDGE_CASES:
        add(name, sponsor, t0, start, steps)
    return trials


# Twins ------------------------------------------------------------------------------------


def outcome(trial: Trial, before: dt.date | None = None) -> tuple[dt.date, int, bool]:
    """When follow-up ends, the event kind (0 censored), and whether the censoring is the
    UNKNOWN rule's. With `before`, what the cohort built as of that date knows (ADR 0016):
    a terminal version counts only if it was posted before that date, a record is lapsed
    only if the UNKNOWN rule applied to it on that date, and observation ends there."""
    window_end = CUTOFF if before is None else before
    known = [v for v in trial.versions if before is None or v["effective_date"] < before]
    terminal = [v for v in known if v["overall_status"] in TERMINAL]
    if terminal:
        return terminal[0]["effective_date"], TERMINAL[terminal[0]["overall_status"]], False
    if trial.name in LAPSES and LAPSES[trial.name][1] <= window_end:
        return LAPSES[trial.name][0], 0, True
    return window_end, 0, False


def state_at(trial: Trial, day: dt.date) -> dict[str, Any]:
    """The version in effect on a day: the latest posted on or before it, and the higher
    version number when two share a post date."""
    known = [v for v in trial.versions if v["effective_date"] <= day]
    return max(known, key=lambda v: (v["effective_date"], v["nct_version"]))


def month_end(day: dt.date | None) -> dt.date | None:
    return None if day is None else add_months(day.replace(day=1), 1) - dt.timedelta(days=1)


def _months_moved(first: dt.date | None, now: dt.date | None) -> int | None:
    if first is None or now is None:
        return None
    return (now.year - first.year) * 12 + now.month - first.month


def _flag(value: bool | None) -> int:
    return -1 if value is None else int(value)


def signals_at(trial: Trial, landmark: dt.date, quiet_months: int = 6) -> dict[str, int]:
    """What a record shows at a landmark against its first version: 1 yes, 0 no, -1 not
    known or outside the comparison."""
    first, now = trial.versions[0], state_at(trial, landmark)
    history = [v for v in trial.versions if v["effective_date"] <= landmark]
    waiting = now["overall_status"] == "NOT_YET_RECRUITING"
    start_end, primary_end = month_end(now["start_date"]), month_end(now["primary_completion_date"])
    primary = _months_moved(first["primary_completion_date"], now["primary_completion_date"])
    completion = _months_moved(first["completion_date"], now["completion_date"])
    target, count = first["enrollment_count"], now["enrollment_count"]
    had_target = first["enrollment_type"] == "ESTIMATED" and target is not None and target > 0
    compared = had_target and count is not None
    estimated, actual = (now["enrollment_type"] == kind for kind in ("ESTIMATED", "ACTUAL"))
    last_post = max(v["effective_date"] for v in history)
    return {
        "ever_suspended": int(any(v["overall_status"] == "SUSPENDED" for v in history)),
        "start_overdue": _flag(
            False if not waiting else None if start_end is None else start_end < landmark
        ),
        "not_yet_recruiting": int(waiting),
        "primary_completion_overdue": _flag(
            None if primary_end is None else primary_end < landmark
        ),
        "primary_completion_later": _flag(None if primary is None else primary >= 1),
        "completion_later": _flag(None if completion is None else completion >= 1),
        "primary_completion_earlier": _flag(None if primary is None else primary <= -1),
        "enrollment_closed": _flag(True if actual else False if estimated else None),
        "target_cut": _flag(10 * count <= 9 * target if compared and estimated else None),
        "target_raised": _flag(10 * count >= 11 * target if compared and estimated else None),
        "enrollment_short": _flag(10 * count <= 9 * target if compared and actual else None),
        "quiet": int(last_post <= add_months(landmark, -quiet_months)),
    }


def timing_of(trial: Trial) -> str:
    """Registration against the calendar month of the first version's start date."""
    start_end = month_end(trial.versions[0]["start_date"])
    if start_end is None:
        return an.NO_START_DATE
    if start_end >= trial.first_post:
        return an.PROSPECTIVE
    late = (trial.first_post - start_end).days <= 365
    return an.LATE_WITHIN_YEAR if late else an.LATE_OVER_YEAR


EVENT_OF_KIND = {0: EVENT_CENSORED, an.KIND_TERMINATED: EVENT_STOP,
                 an.KIND_WITHDRAWN: EVENT_STOP, an.KIND_COMPLETED: EVENT_COMPLETE}  # fmt: skip


def followed(rows: list[tuple[Trial, dt.date]], before: dt.date | None = BEFORE) -> tuple[Any, ...]:
    """Days of follow-up, event, kind and UNKNOWN-rule flag of each landmark row, from the
    twin: (time, event, kind, lapsed). `before` None follows every trial to the data cutoff."""
    ends = [outcome(trial, before) for trial, _ in rows]
    time = np.array([(end - day).days for (_, day), (end, _, _) in zip(rows, ends, strict=True)])
    kind = np.array([kind for _, kind, _ in ends])
    event = np.array([EVENT_OF_KIND[k] for k in kind.tolist()])
    return time.astype(np.float64), event, kind, np.array([lapsed for _, _, lapsed in ends])


def cif(time: Any, code: Any, months: int, cause: int = EVENT_STOP) -> float:
    """The CIF of one cause at a horizon, with the estimator of Step 8."""
    return float(aalen_johansen(time, code, cause=cause).at(months * DAYS_PER_MONTH))


# The files --------------------------------------------------------------------------------


def write_warehouse(path: Path, rows: list[dict[str, Any]]) -> None:
    frame = pd.DataFrame(rows, columns=list(VERSION_TYPES)).astype(object)
    with duckdb.connect(str(path)) as con:
        con.register("versions_frame", frame)
        columns = ", ".join(f"CAST({c} AS {t}) AS {c}" for c, t in VERSION_TYPES.items())
        con.execute(f"CREATE TABLE versions AS SELECT {columns} FROM versions_frame")
        con.unregister("versions_frame")
        con.execute("CREATE TABLE build_info (key VARCHAR, value VARCHAR)")
        con.execute(
            "INSERT INTO build_info VALUES ('schema_version', '3'), "
            "('dataset.revision', 'synthetic')"
        )


def write_current_fields(path: Path, trials: list[Trial]) -> None:
    """A current-record snapshot: one phase list per trial, and no record for the last trial
    of the first year's scripted mix."""
    phases = (["PHASE1"], ["PHASE2"], ["PHASE2", "PHASE3"], ["PHASE4"], ["NA"], [], ["PHASE3"])
    first_year = [t for t in trials if t.year == MODELING_YEARS[0] and not t.edge_case]
    missing = first_year[-1].nct_id
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
    write_warehouse(warehouse, [row for trial in trials for row in trial.versions])
    cohort_dir = root / "cohort"
    audit = root / "data_audit.md"
    args = ["--warehouse", str(warehouse), "--out-dir", str(cohort_dir), "--audit", str(audit)]
    assert build.main(args) == 0
    current_fields = root / "spike" / "current_fields.parquet"
    current_fields.parent.mkdir()
    write_current_fields(current_fields, trials)
    return World(cohort_sources(cohort_dir, warehouse, current_fields), tuple(trials))
