"""The numbers behind docs/eda.md (CLAUDE.md Step 5).

Every function returns plain rows (lists of dicts) or arrays; `document.py` turns them into
tables and `charts.py` into figures.

Two rules decide what may inform modeling (CLAUDE.md Section 10):

- **Modeling-relevant analysis reads nothing dated on or after 2018-01-01.** Its tables
  (`landmarks` and `outcomes`) are the cohort as it would have been built on that date
  (ADR 0016): only versions posted before it are known, and observation ends there. So an
  outcome, a censoring under the UNKNOWN rule and a trial's place in the cohort are what
  was known on that date, exactly as for a model trained at the 2018 origin (Section 6).
  `check_as_of` refuses tables that hold anything later. Every function that takes
  `before` also keeps only landmarks before that date and censors each outcome there,
  which changes nothing on those tables. The locked test years stay unseen.
- **Anything later is descriptive only,** and reads the final cohort (`final_landmarks`,
  `final_outcomes`): `covid_period_monthly` by purpose, and the rows of
  `registration_by_year` and `post_dates_by_year` for the later years, which are flagged.

Phase is not versioned (ADR 0006), so the phase breakdown uses each trial's current-record
phase and is descriptive only: a phase edited after the outcome could leak it.

Dates in the registry are given to the month through 2016 and mostly to the day from 2017.
Comparisons of dates here are made by calendar month, so that a change of precision is not
read as a change of behavior.

Nothing here reads a name or a free text: counts, dates, statuses and classes only.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import numpy.typing as npt

from trialpulse.cli import RefusedError
from trialpulse.cohort.audit import DAYS_PER_MONTH, MODELING_EDA_BEFORE
from trialpulse.dates import days_between
from trialpulse.eval import EVENT_CENSORED, EVENT_COMPLETE, EVENT_STOP
from trialpulse.eval.ipcw import kaplan_meier
from trialpulse.models.aalen_johansen import aalen_johansen

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
BoolArray = npt.NDArray[np.bool_]
Rows = list[dict[str, Any]]

# Event kinds of the detailed views: the early stop split by its terminal status, and
# censoring under the UNKNOWN rule (ADR 0014) told apart from censoring at the end of the
# observation window.
KIND_TERMINATED = 1
KIND_COMPLETED = 2
KIND_WITHDRAWN = 3
KIND_LAPSED = 4

# Current-record phases (API v2 names) to phase groups.
EARLY_PHASE = "Early (Early Phase 1, Phase 1)"
MID_PHASE = "Mid (Phase 1/2, Phase 2)"
LATE_PHASE = "Late (Phase 2/3, Phase 3)"
POST_APPROVAL_PHASE = "Post-approval (Phase 4)"
NO_PHASE = "No phase (N/A)"
OTHER_PHASE = "Other combination"
NO_CURRENT_RECORD = "No current record"
PHASE_GROUPS: dict[frozenset[str], str] = {
    frozenset({"EARLY_PHASE1"}): EARLY_PHASE,
    frozenset({"PHASE1"}): EARLY_PHASE,
    frozenset({"PHASE1", "PHASE2"}): MID_PHASE,
    frozenset({"PHASE2"}): MID_PHASE,
    frozenset({"PHASE2", "PHASE3"}): LATE_PHASE,
    frozenset({"PHASE3"}): LATE_PHASE,
    frozenset({"PHASE4"}): POST_APPROVAL_PHASE,
    frozenset({"NA"}): NO_PHASE,
    frozenset(): NO_PHASE,
}
PHASE_ORDER: tuple[str, ...] = (EARLY_PHASE, MID_PHASE, LATE_PHASE, POST_APPROVAL_PHASE, NO_PHASE)

MAIN_CLASSES: tuple[str, ...] = ("INDUSTRY", "OTHER", "NIH")
OTHER_CLASSES = "Remaining classes"
SPONSOR_ORDER: tuple[str, ...] = (*MAIN_CLASSES, OTHER_CLASSES)

# Registration timing: the month of the first version's start date against the month of
# the first post.
PROSPECTIVE = "Registered in or before the start month"
LATE_WITHIN_YEAR = "Registered up to 1 year after the start month"
LATE_OVER_YEAR = "Registered more than 1 year after the start month"
NO_START_DATE = "No start date"
TIMING_ORDER: tuple[str, ...] = (PROSPECTIVE, LATE_WITHIN_YEAR, LATE_OVER_YEAR, NO_START_DATE)

ALL = "All"


@dataclass(frozen=True)
class Sources:
    landmarks: Path  # the cohort as of the modeling date (ADR 0016): modeling-relevant
    outcomes: Path
    final_landmarks: Path  # the final cohort, outcomes through the data cutoff: descriptive
    final_outcomes: Path
    warehouse: Path
    current_fields: Path


def connect(sources: Sources) -> duckdb.DuckDBPyConnection:
    """One connection with the cohort files as views and the warehouse attached read-only."""
    con = duckdb.connect()
    con.execute(f"ATTACH '{sources.warehouse.as_posix()}' AS wh (READ_ONLY)")
    for name, path in (
        ("landmarks", sources.landmarks),
        ("outcomes", sources.outcomes),
        ("final_landmarks", sources.final_landmarks),
        ("final_outcomes", sources.final_outcomes),
        ("current_fields", sources.current_fields),
    ):
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{path.as_posix()}')")
    return con


def check_as_of(con: duckdb.DuckDBPyConnection, before: dt.date) -> None:
    """Refuse modeling-relevant tables that were not built as of `before`: a landmark on or
    after it, an event dated on or after it, or a censoring after it."""
    row = con.execute(
        f"""SELECT count(*) FROM landmarks
        WHERE landmark_date >= {_day(before)} OR event_date > {_day(before)}
          OR (event <> {EVENT_CENSORED} AND event_date >= {_day(before)})"""
    ).fetchone()
    late = con.execute(
        f"""SELECT count(*) FROM outcomes
        WHERE event_date > {_day(before)}
          OR (event <> {EVENT_CENSORED} AND event_date >= {_day(before)})"""
    ).fetchone()
    count = (row[0] if row else 0) + (late[0] if late else 0)
    if count:
        raise RefusedError(
            f"{count} rows of the modeling-relevant cohort are dated on or after "
            f"{before.isoformat()}: the EDA needs the cohort built as of that date (ADR 0016). "
            "Run: uv run python -m trialpulse.cohort.build"
        )


def phase_group(phases: Sequence[str]) -> str:
    return PHASE_GROUPS.get(frozenset(phases), OTHER_PHASE)


def month_end_sql(date: str, precision: str) -> str:
    """The last day of the calendar month a date falls in (of its year, for a date given
    only to the year). A date given to the day and one given to the month are then compared
    the same way."""
    return (
        f"(CASE {precision} WHEN 'year' THEN make_date(year({date}), 12, 31) "
        f"ELSE last_day({date}) END)"
    )


def _day(day: dt.date) -> str:
    return f"DATE '{day.isoformat()}'"


def _censored(alias: str, before: dt.date) -> tuple[str, str]:
    """SQL for a landmark row's outcome as known on `before`: (event code, event date). An
    event on or after that date is not observed yet, so the row is censored there."""
    event = (
        f"(CASE WHEN {alias}.event_date >= {_day(before)} THEN {EVENT_CENSORED} "
        f"ELSE {alias}.event END)"
    )
    return event, f"least({alias}.event_date, {_day(before)})"


def _column(data: dict[str, Any], name: str, fill: Any) -> npt.NDArray[Any]:
    """A fetched column as a plain array: DuckDB returns a masked array where a column has
    NULLs, and `fill` takes their place."""
    return np.asarray(np.ma.filled(np.ma.asarray(data[name]), fill))


def _days(data: dict[str, Any], start: str, end: str) -> FloatArray:
    return days_between(
        np.asarray(data[start]).astype("datetime64[D]"),
        np.asarray(data[end]).astype("datetime64[D]"),
    )


def _records(cursor: duckdb.DuckDBPyConnection) -> Rows:
    names = [d[0] for d in cursor.description or []]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


# Cumulative incidence ---------------------------------------------------------------------


@dataclass(frozen=True)
class Registrations:
    """L0 landmark rows: one per trial open at registration, with the outcome as known on
    the `before` date."""

    time: FloatArray  # days from L0 to the event or censoring
    event: IntArray  # 0 censored, 1 early stop, 2 completion
    kind: IntArray  # 0 censored, 1 terminated, 2 completed, 3 withdrawn
    lapsed: BoolArray  # censored under the UNKNOWN rule (ADR 0014), not by the window's end
    sponsor_class: npt.NDArray[Any]  # the lead sponsor class at registration
    sponsor_group: npt.NDArray[Any]  # the three large classes, and the rest pooled
    year: IntArray  # registration year
    timing: npt.NDArray[Any]  # registration month against the first version's start month
    phase: npt.NDArray[Any]  # current-record phase group: descriptive only


def registrations(
    con: duckdb.DuckDBPyConnection, before: dt.date = MODELING_EDA_BEFORE
) -> Registrations:
    event, event_date = _censored("l", before)
    start_month_end = month_end_sql("f.start_date", "f.start_date_precision")
    data = con.execute(
        f"""WITH first_version AS (
          SELECT nct_id, start_date, start_date_precision FROM wh.versions
          QUALIFY row_number() OVER (PARTITION BY nct_id ORDER BY nct_version) = 1
        )
        SELECT l.landmark_date, {event} AS event, {event_date} AS event_date, l.stratum,
          year(l.landmark_date) AS y,
          CASE WHEN {event} = {EVENT_STOP} AND o.terminal_status = 'WITHDRAWN'
            THEN {KIND_WITHDRAWN} ELSE {event} END AS kind,
          coalesce({event} = {EVENT_CENSORED} AND o.censor_reason = 'unknown'
            AND l.event_date < {_day(before)}, false) AS lapsed,
          CASE WHEN f.start_date IS NULL THEN 3
            WHEN {start_month_end} >= l.landmark_date THEN 0
            WHEN date_diff('day', {start_month_end}, l.landmark_date) <= 365 THEN 1
            ELSE 2 END AS timing,
          c.nct_id IS NOT NULL AS has_record,
          coalesce(array_to_string(list_sort(c.phases), '|'), '') AS phases
        FROM landmarks l
        JOIN outcomes o USING (trial_id)
        LEFT JOIN first_version f ON f.nct_id = l.trial_id
        LEFT JOIN current_fields c ON c.nct_id = l.trial_id
        WHERE l.landmark_index = 0 AND l.landmark_date < {_day(before)}
        ORDER BY l.trial_id"""
    ).fetchnumpy()
    sponsor_class = np.asarray(data["stratum"]).astype(str)
    phases = [
        phase_group(p.split("|") if p else []) if found else NO_CURRENT_RECORD
        for p, found in zip(
            np.asarray(data["phases"]).tolist(),
            np.asarray(data["has_record"]).tolist(),
            strict=True,
        )
    ]
    return Registrations(
        time=_days(data, "landmark_date", "event_date"),
        event=np.asarray(data["event"], dtype=np.int64),
        kind=np.asarray(data["kind"], dtype=np.int64),
        lapsed=np.asarray(data["lapsed"], dtype=np.bool_),
        sponsor_class=sponsor_class,
        sponsor_group=np.where(np.isin(sponsor_class, MAIN_CLASSES), sponsor_class, OTHER_CLASSES),
        year=np.asarray(data["y"], dtype=np.int64),
        timing=np.asarray(TIMING_ORDER)[np.asarray(data["timing"], dtype=np.int64)],
        phase=np.asarray(phases),
    )


def cif_at(
    time: FloatArray, event: IntArray, months: Sequence[int], cause: int = EVENT_STOP
) -> list[float]:
    """The CIF of one cause (Aalen-Johansen, every other event competing) at each horizon. A
    horizon of m months is m times 365.25 / 12 days after the landmark."""
    curve = aalen_johansen(time, event, cause=cause)
    return [float(curve.at(m * DAYS_PER_MONTH)) for m in months]


def _masks(groups: npt.NDArray[Any], order: Sequence[Any]) -> list[tuple[Any, BoolArray]]:
    """Each group in `order` that has rows, then all rows together."""
    named = [(name, groups == name) for name in order]
    everyone = (ALL, np.ones(len(groups), dtype=np.bool_))
    return [(name, mask) for name, mask in [*named, everyone] if mask.any()]


def cif_by_group(
    time: FloatArray,
    event: IntArray,
    groups: npt.NDArray[Any],
    order: Sequence[Any],
    months: Sequence[int],
) -> Rows:
    """One row per group in `order` that has rows, then all rows together: trials, early
    stops observed, and the early-stop CIF at each horizon."""
    rows: Rows = []
    for name, mask in _masks(groups, order):
        row: dict[str, Any] = {
            "group": name,
            "trials": int(mask.sum()),
            "early_stops": int((event[mask] == EVENT_STOP).sum()),
        }
        for m, value in zip(months, cif_at(time[mask], event[mask], months), strict=True):
            row[f"cif_{m}m"] = value
        rows.append(row)
    return rows


def stop_kinds_by_group(
    reg: Registrations, groups: npt.NDArray[Any], order: Sequence[Any], month: int
) -> dict[Any, dict[str, float]]:
    """Per group: the CIF of withdrawal and of termination at one horizon. The two add up to
    the early-stop CIF."""
    return {
        name: {
            "withdrawn": cif_at(reg.time[mask], reg.kind[mask], [month], KIND_WITHDRAWN)[0],
            "terminated": cif_at(reg.time[mask], reg.kind[mask], [month], KIND_TERMINATED)[0],
        }
        for name, mask in _masks(groups, order)
    }


def lapse_by_group(
    reg: Registrations, groups: npt.NDArray[Any], order: Sequence[Any], months: Sequence[int]
) -> Rows:
    """Per group: trials censored under the UNKNOWN rule (ADR 0014: the record passed its
    completion date and went 2 years without a status verification), and the cumulative
    incidence of that censoring at each horizon, with early stop and completion competing.
    The estimator of the early-stop CIF treats this censoring as uninformative; this shows
    how much of it there is, and for whom."""
    code = np.where(reg.lapsed, KIND_LAPSED, reg.kind)
    rows: Rows = []
    for name, mask in _masks(groups, order):
        row: dict[str, Any] = {
            "group": name,
            "trials": int(mask.sum()),
            "lapsed": int(reg.lapsed[mask].sum()),
        }
        values = cif_at(reg.time[mask], code[mask], months, KIND_LAPSED)
        for m, value in zip(months, values, strict=True):
            row[f"lapse_{m}m"] = value
        rows.append(row)
    return rows


def cif_curves(
    time: FloatArray,
    event: IntArray,
    groups: npt.NDArray[Any],
    order: Sequence[Any],
    max_months: int,
) -> dict[str, tuple[FloatArray, FloatArray]]:
    """The early-stop CIF by month since the landmark for each group: (months, cif)."""
    months = np.arange(0, max_months + 1, dtype=np.float64)
    out: dict[str, tuple[FloatArray, FloatArray]] = {}
    for name in order:
        mask = groups == name
        if mask.any():
            curve = aalen_johansen(time[mask], event[mask])
            out[str(name)] = (months, curve.at(months * DAYS_PER_MONTH).astype(np.float64))
    return out


def cif_by_landmark_index(
    con: duckdb.DuckDBPyConnection, months: Sequence[int], before: dt.date = MODELING_EDA_BEFORE
) -> Rows:
    """The early-stop CIF over the horizons after each landmark, by landmark index."""
    event, event_date = _censored("l", before)
    data = con.execute(
        f"""SELECT l.landmark_index, l.landmark_date, {event} AS event,
          {event_date} AS event_date
        FROM landmarks l WHERE l.landmark_date < {_day(before)}
        ORDER BY l.trial_id, l.landmark_index"""
    ).fetchnumpy()
    index = np.asarray(data["landmark_index"], dtype=np.int64)
    rows = cif_by_group(
        _days(data, "landmark_date", "event_date"),
        np.asarray(data["event"], dtype=np.int64),
        index,
        sorted(set(index.tolist())),
        months,
    )
    return [row for row in rows if row["group"] != ALL]


def competing_view(reg: Registrations, max_months: int) -> dict[str, FloatArray]:
    """By month since registration: the share terminated, withdrawn, completed and still
    open (Aalen-Johansen, so the four add up to 1), and the naive early-stop estimate that
    treats a completion as censoring (1 minus Kaplan-Meier), which overstates the risk."""
    months = np.arange(0, max_months + 1, dtype=np.float64)
    days = months * DAYS_PER_MONTH

    def cif(cause: int) -> FloatArray:
        return aalen_johansen(reg.time, reg.kind, cause=cause).at(days).astype(np.float64)

    terminated, withdrawn, completed = (
        cif(KIND_TERMINATED),
        cif(KIND_WITHDRAWN),
        cif(KIND_COMPLETED),
    )
    naive = 1.0 - kaplan_meier(reg.time, reg.event == EVENT_STOP).at(days)
    return {
        "months": months,
        "terminated": terminated,
        "withdrawn": withdrawn,
        "early_stop": terminated + withdrawn,
        "completed": completed,
        "open": 1.0 - terminated - withdrawn - completed,
        "naive_early_stop": naive.astype(np.float64),
    }


# Amendments at a landmark -----------------------------------------------------------------

EVERYONE = "all trials"
COUNT_ESTIMATED = "trials with an ESTIMATED enrollment count"
COUNT_ACTUAL = "trials with an ACTUAL enrollment count"
# (key, label, the trials it is compared among). Each is 1 (yes), 0 (no) or -1 (outside the
# comparison, or an input is missing) per landmark row, from versions posted on or before
# the landmark. The enrollment count is a target while its type is ESTIMATED and the number
# enrolled once it is ACTUAL, so a change of the count is read with the type.
SIGNALS: tuple[tuple[str, str, str], ...] = (
    ("ever_suspended", "Suspended at some point", EVERYONE),
    ("start_overdue", "Not yet recruiting, planned start month passed", EVERYONE),
    ("not_yet_recruiting", "Still not yet recruiting", EVERYONE),
    ("primary_completion_overdue", "Primary completion month passed", EVERYONE),
    ("primary_completion_later", "Primary completion date moved later", EVERYONE),
    ("completion_later", "Completion date moved later", EVERYONE),
    ("primary_completion_earlier", "Primary completion date moved earlier", EVERYONE),
    ("enrollment_closed", "Enrollment count is ACTUAL (enrollment closed)", EVERYONE),
    ("target_cut", "Enrollment target cut by 10% or more", COUNT_ESTIMATED),
    ("target_raised", "Enrollment target raised by 10% or more", COUNT_ESTIMATED),
    ("enrollment_short", "Enrolled 10% or more below the first target", COUNT_ACTUAL),
    ("quiet", "No version posted in the last 6 months", EVERYONE),
)
SIGNAL_LABELS = {key: label for key, label, _ in SIGNALS}
LATER_OUTCOMES: tuple[tuple[int, str], ...] = (
    (EVENT_STOP, "Stopped early later"),
    (EVENT_COMPLETE, "Completed later"),
    (EVENT_CENSORED, "No outcome observed"),
)


@dataclass(frozen=True)
class Amendments:
    """Landmark rows at one landmark index, with what each record showed at the landmark and
    the outcome as known on the `before` date."""

    time: FloatArray  # days from the landmark to the event or censoring
    event: IntArray
    signals: dict[str, IntArray]
    versions: IntArray  # versions posted on or before the landmark
    slip_months: FloatArray  # primary completion date against the first version, NaN if unknown


def amendment_signals(
    con: duckdb.DuckDBPyConnection,
    landmark_index: int,
    quiet_months: int,
    before: dt.date = MODELING_EDA_BEFORE,
) -> Amendments:
    """What each record showed at the landmark, against its first version. The state at the
    landmark is the latest version posted on or before it (the higher version number when
    two share a post date); nothing posted later is read. Dates are compared by calendar
    month, so a date that only gained a day of precision has not moved."""
    event, event_date = _censored("l", before)
    start_month_end = month_end_sql("s.start_date", "s.start_date_precision")
    primary_month_end = month_end_sql(
        "s.primary_completion_date", "s.primary_completion_date_precision"
    )
    primary_moved = "date_diff('month', f.pc0, s.primary_completion_date)"
    completion_moved = "date_diff('month', f.c0, s.completion_date)"
    target = "s.enrollment_type = 'ESTIMATED' AND f.t0 = 'ESTIMATED' AND f.n0 > 0"
    enrolled = "s.enrollment_type = 'ACTUAL' AND f.t0 = 'ESTIMATED' AND f.n0 > 0"
    conditions = {
        "ever_suspended": "h.ever_suspended",
        "start_overdue": (
            f"s.overall_status = 'NOT_YET_RECRUITING' AND {start_month_end} < l.landmark_date"
        ),
        "not_yet_recruiting": "s.overall_status = 'NOT_YET_RECRUITING'",
        "primary_completion_overdue": f"{primary_month_end} < l.landmark_date",
        "primary_completion_later": f"{primary_moved} >= 1",
        "completion_later": f"{completion_moved} >= 1",
        "primary_completion_earlier": f"{primary_moved} <= -1",
        "enrollment_closed": (
            "CASE s.enrollment_type WHEN 'ACTUAL' THEN true WHEN 'ESTIMATED' THEN false END"
        ),
        "target_cut": f"CASE WHEN {target} THEN s.enrollment_count <= 0.9 * f.n0 END",
        "target_raised": f"CASE WHEN {target} THEN s.enrollment_count >= 1.1 * f.n0 END",
        "enrollment_short": f"CASE WHEN {enrolled} THEN s.enrollment_count <= 0.9 * f.n0 END",
        "quiet": f"h.last_post <= l.landmark_date - INTERVAL {int(quiet_months)} MONTH",
    }
    flags = ",\n          ".join(
        f"coalesce(CAST(({conditions[key]}) AS TINYINT), -1) AS {key}" for key, _, _ in SIGNALS
    )
    data = con.execute(
        f"""WITH l AS (
          SELECT l.trial_id, l.landmark_date, {event} AS event, {event_date} AS event_date
          FROM landmarks l
          WHERE l.landmark_index = {int(landmark_index)} AND l.landmark_date < {_day(before)}
        ),
        states AS (
          SELECT nct_id, effective_date, overall_status, start_date, start_date_precision,
            primary_completion_date, primary_completion_date_precision, completion_date,
            enrollment_count, enrollment_type
          FROM wh.versions
          QUALIFY row_number() OVER (
            PARTITION BY nct_id, effective_date ORDER BY nct_version DESC) = 1
        ),
        first_version AS (
          SELECT nct_id, primary_completion_date AS pc0, completion_date AS c0,
            enrollment_count AS n0, enrollment_type AS t0
          FROM wh.versions
          QUALIFY row_number() OVER (PARTITION BY nct_id ORDER BY nct_version) = 1
        ),
        history AS (
          SELECT l.trial_id, count(*) AS versions, max(v.effective_date) AS last_post,
            bool_or(v.overall_status = 'SUSPENDED') AS ever_suspended
          FROM l JOIN wh.versions v
            ON v.nct_id = l.trial_id AND v.effective_date <= l.landmark_date
          GROUP BY l.trial_id
        )
        SELECT l.landmark_date, l.event, l.event_date, h.versions,
          CAST({primary_moved} AS DOUBLE) AS slip_months,
          {flags}
        FROM l
        JOIN first_version f ON f.nct_id = l.trial_id
        JOIN history h USING (trial_id)
        ASOF JOIN states s ON s.nct_id = l.trial_id AND l.landmark_date >= s.effective_date
        ORDER BY l.trial_id"""
    ).fetchnumpy()
    return Amendments(
        time=_days(data, "landmark_date", "event_date"),
        event=np.asarray(data["event"], dtype=np.int64),
        signals={key: np.asarray(data[key], dtype=np.int64) for key, _, _ in SIGNALS},
        versions=np.asarray(data["versions"], dtype=np.int64),
        slip_months=_column(data, "slip_months", np.nan).astype(np.float64),
    )


def amendments_by_outcome(am: Amendments) -> Rows:
    """Per later outcome of the landmark row: how many rows, the share showing each signal
    (among the rows where the signal is defined and known), the median number of versions,
    and the median slip of the primary completion date among rows where it moved later."""
    rows: Rows = []
    for code, label in LATER_OUTCOMES:
        mask = am.event == code
        row: dict[str, Any] = {"group": label, "trials": int(mask.sum())}
        for key, _, _ in SIGNALS:
            known = mask & (am.signals[key] >= 0)
            row[key] = float(am.signals[key][known].mean()) if known.any() else None
        later = mask & (am.signals["primary_completion_later"] == 1)
        row["median_versions"] = float(np.median(am.versions[mask])) if mask.any() else None
        row["median_slip_months"] = float(np.median(am.slip_months[later])) if later.any() else None
        rows.append(row)
    return rows


def amendments_cif(am: Amendments, months: int) -> Rows:
    """Per signal: rows with and without it among the trials it is compared among, and the
    early-stop CIF `months` after the landmark in each group (Aalen-Johansen, so censored
    rows count for as long as they were followed). Sorted by the CIF with the signal,
    highest first."""
    rows: Rows = []
    for key, label, among in SIGNALS:
        with_signal, without = am.signals[key] == 1, am.signals[key] == 0
        if not with_signal.any() or not without.any():
            continue
        cif_with = cif_at(am.time[with_signal], am.event[with_signal], [months])[0]
        cif_without = cif_at(am.time[without], am.event[without], [months])[0]
        rows.append(
            {
                "key": key,
                "signal": label,
                "among": among,
                "with": int(with_signal.sum()),
                "without": int(without.sum()),
                "outside": int((am.signals[key] < 0).sum()),
                "cif_with": cif_with,
                "cif_without": cif_without,
                "ratio": cif_with / cif_without if cif_without > 0 else None,
            }
        )
    return sorted(rows, key=lambda r: -r["cif_with"])


# Registration and post dates --------------------------------------------------------------


def registration_by_year(
    con: duckdb.DuckDBPyConnection, before: dt.date = MODELING_EDA_BEFORE
) -> Rows:
    """Per registration year, for trials open at registration (L0 rows): the share whose
    first version gives the start date to the day, the share registered after the start
    month (among trials with a start date: the start date's calendar month ended before the
    first-post date), the median days from the end of the start month to registration among
    those, the share registered more than a year late, and the median days from first
    submission to first posting. Trials registered before `before` come from the cohort as
    of that date; later years come from the final cohort and are flagged descriptive."""
    start_month_end = month_end_sql("f.start_date", "f.start_date_precision")
    cursor = con.execute(
        f"""WITH first_version AS (
          SELECT nct_id, start_date, start_date_precision, study_first_submit_date
          FROM wh.versions
          QUALIFY row_number() OVER (PARTITION BY nct_id ORDER BY nct_version) = 1
        ),
        r AS (
          SELECT year(l.landmark_date) AS year, l.landmark_date AS t0,
            {start_month_end} AS start_end, f.start_date_precision AS precision,
            f.study_first_submit_date AS submitted
          FROM (
            SELECT trial_id, landmark_date FROM landmarks
            WHERE landmark_index = 0 AND landmark_date < {_day(before)}
            UNION ALL
            SELECT trial_id, landmark_date FROM final_landmarks
            WHERE landmark_index = 0 AND landmark_date >= {_day(before)}
          ) l JOIN first_version f ON f.nct_id = l.trial_id
        )
        SELECT year, count(*) AS trials, count(start_end) AS with_start_date,
          avg(CASE WHEN precision = 'day' THEN 1.0 WHEN start_end IS NOT NULL THEN 0.0 END)
            AS share_day_precision,
          avg(CASE WHEN start_end < t0 THEN 1.0 WHEN start_end IS NOT NULL THEN 0.0 END)
            AS share_after_start,
          median(date_diff('day', start_end, t0)) FILTER (WHERE start_end < t0)
            AS median_days_late,
          avg(CASE WHEN date_diff('day', start_end, t0) > 365 THEN 1.0
                   WHEN start_end IS NOT NULL THEN 0.0 END) AS share_over_a_year_late,
          median(date_diff('day', submitted, t0)) AS median_days_submit_to_post
        FROM r GROUP BY year ORDER BY year"""
    )
    rows = _records(cursor)
    for row in rows:
        row["descriptive"] = row["year"] >= before.year
    return rows


def post_dates_by_year(
    con: duckdb.DuckDBPyConnection, before: dt.date = MODELING_EDA_BEFORE
) -> Rows:
    """Per year of the version clock, for versions of cohort trials: how many, the share
    whose post date is ESTIMATED, and the median and 90th percentile of the days from
    submission to posting. A property of the registry's records, not of outcomes. Versions
    posted before `before` are those of the trials in the cohort as of that date; later
    versions are those of the final cohort's trials, and their years are flagged
    descriptive."""
    cursor = con.execute(
        f"""SELECT year(v.effective_date) AS year, count(*) AS versions,
          avg(CASE WHEN v.effective_date_type = 'ESTIMATED' THEN 1.0 ELSE 0.0 END)
            AS share_estimated,
          median(date_diff('day', v.submitted_date, v.effective_date)) AS median_days_to_post,
          quantile_cont(date_diff('day', v.submitted_date, v.effective_date), 0.9)
            AS p90_days_to_post
        FROM wh.versions v
        LEFT JOIN (SELECT DISTINCT trial_id FROM landmarks) a ON a.trial_id = v.nct_id
        LEFT JOIN (SELECT DISTINCT trial_id FROM final_landmarks) f ON f.trial_id = v.nct_id
        WHERE CASE WHEN v.effective_date < {_day(before)} THEN a.trial_id IS NOT NULL
                   ELSE f.trial_id IS NOT NULL END
        GROUP BY 1 ORDER BY 1"""
    )
    rows = _records(cursor)
    for row in rows:
        row["descriptive"] = row["year"] >= before.year
    return rows


def posting_lag(con: duckdb.DuckDBPyConnection, before: dt.date = MODELING_EDA_BEFORE) -> Rows:
    """Days from submission to posting for versions of cohort trials posted before `before`,
    by whether the post date is ESTIMATED or ACTUAL and whether the version is the trial's
    first: versions, median and 90th percentile. The ACTUAL rows show how far a recorded
    post date trails its submission, which is the likely size of the error in an estimated
    one. First versions set time zero and every landmark, so they are shown apart."""
    cursor = con.execute(
        f"""WITH v AS (
          SELECT v.effective_date, v.effective_date_type, v.submitted_date,
            row_number() OVER (PARTITION BY v.nct_id ORDER BY v.nct_version) = 1 AS is_first
          FROM wh.versions v
          JOIN (SELECT DISTINCT trial_id FROM landmarks) c ON c.trial_id = v.nct_id
        )
        SELECT effective_date_type AS date_type, is_first, count(*) AS versions,
          median(date_diff('day', submitted_date, effective_date)) AS median_days,
          quantile_cont(date_diff('day', submitted_date, effective_date), 0.9) AS p90_days
        FROM v
        WHERE effective_date < {_day(before)} AND effective_date_type IN ('ESTIMATED', 'ACTUAL')
        GROUP BY 1, 2 ORDER BY 1, 2 DESC"""
    )
    return _records(cursor)


# The COVID period: descriptive only -------------------------------------------------------

COVID_SERIES: tuple[tuple[str, str], ...] = (
    ("suspended", "Suspended"),
    ("terminated", "Terminated"),
    ("withdrawn", "Withdrawn"),
)


def covid_period_monthly(
    con: duckdb.DuckDBPyConnection, start: dt.date, end: dt.date
) -> dict[str, Any]:
    """DESCRIPTIVE ONLY: it looks at calendar time after 2018-01-01 (Section 10), so nothing
    here may shape a model, and it reads the final cohort, with outcomes through the data
    cutoff. Per calendar month from `start` to `end`, for cohort trials: how
    many were under follow-up at the start of the month, and how many were terminated,
    withdrawn, or newly suspended (a SUSPENDED version that follows a version with another
    status) during it, per 1,000 trials under follow-up."""
    months = f"""SELECT CAST(m AS DATE) AS month_start
      FROM generate_series({_day(start)}, {_day(end)}, INTERVAL 1 MONTH) AS s(m)"""
    data = con.execute(
        f"""WITH months AS ({months}),
        cohort AS (SELECT DISTINCT trial_id FROM final_landmarks),
        o AS (
          SELECT o.t0, o.event_date, o.terminal_status
          FROM final_outcomes o JOIN cohort c USING (trial_id)
        ),
        open_trials AS (
          SELECT m.month_start, count(*) AS n FROM months m JOIN o
            ON o.t0 <= m.month_start AND o.event_date > m.month_start
          GROUP BY 1
        ),
        stops AS (
          SELECT date_trunc('month', event_date) AS month_start,
            count(*) FILTER (WHERE terminal_status = 'TERMINATED') AS terminated,
            count(*) FILTER (WHERE terminal_status = 'WITHDRAWN') AS withdrawn
          FROM o WHERE terminal_status IN ('TERMINATED', 'WITHDRAWN') GROUP BY 1
        ),
        status_changes AS (
          SELECT v.effective_date, v.overall_status,
            lag(v.overall_status) OVER (
              PARTITION BY v.nct_id ORDER BY v.effective_date, v.nct_version) AS previous
          FROM wh.versions v JOIN cohort c ON c.trial_id = v.nct_id
        ),
        suspensions AS (
          SELECT date_trunc('month', effective_date) AS month_start, count(*) AS suspended
          FROM status_changes
          WHERE overall_status = 'SUSPENDED' AND previous <> 'SUSPENDED'
          GROUP BY 1
        )
        SELECT m.month_start, coalesce(n.n, 0) AS open_trials,
          coalesce(st.terminated, 0) AS terminated, coalesce(st.withdrawn, 0) AS withdrawn,
          coalesce(su.suspended, 0) AS suspended
        FROM months m
        LEFT JOIN open_trials n USING (month_start)
        LEFT JOIN stops st ON st.month_start = m.month_start
        LEFT JOIN suspensions su ON su.month_start = m.month_start
        ORDER BY m.month_start"""
    ).fetchnumpy()
    open_trials = np.asarray(data["open_trials"], dtype=np.float64)
    out: dict[str, Any] = {
        "months": np.asarray(data["month_start"]).astype("datetime64[D]"),
        "open_trials": open_trials,
    }
    for key, _ in COVID_SERIES:
        count = np.asarray(data[key], dtype=np.float64)
        out[key] = count
        out[f"{key}_per_1000"] = np.divide(
            1000 * count, open_trials, out=np.zeros_like(count), where=open_trials > 0
        )
    return out


def covid_period_summary(
    monthly: dict[str, Any], periods: Sequence[tuple[str, dt.date, dt.date]]
) -> Rows:
    """DESCRIPTIVE ONLY. Average monthly rates per 1,000 trials under follow-up over named
    periods (first and last month, both included)."""
    months = monthly["months"]
    rows: Rows = []
    for label, first, last in periods:
        mask = (months >= np.datetime64(first)) & (months <= np.datetime64(last))
        row: dict[str, Any] = {
            "period": label,
            "months": int(mask.sum()),
            "open_trials": float(monthly["open_trials"][mask].mean()),
        }
        for key, _ in COVID_SERIES:
            row[f"{key}_per_1000"] = float(monthly[f"{key}_per_1000"][mask].mean())
        rows.append(row)
    return rows
