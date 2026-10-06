"""The numbers behind docs/eda.md (CLAUDE.md Step 5).

Every function returns plain rows (lists of dicts) or arrays; `report.py` turns them into
tables and `figures.py` into charts.

Two rules decide what may inform modeling (CLAUDE.md Section 10):

- **Modeling-relevant analysis uses landmarks before 2018-01-01 only.** Every function that
  takes `before` restricts its landmarks to dates before it. Outcomes are still followed to
  the data cutoff: the rule limits when a prediction is made, not how long it is followed.
- **Anything later is descriptive only.** `covid_period_monthly` is descriptive by purpose,
  and `registration_by_year` and `post_dates_by_year` flag each later year.

Phase is not versioned (ADR 0006), so the phase breakdown uses each trial's current-record
phase and is descriptive only: a phase edited after the outcome could leak it.

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

from trialpulse.cohort.audit import DAYS_PER_MONTH, MODELING_EDA_BEFORE
from trialpulse.cohort.rules import period_end_sql
from trialpulse.dates import days_between
from trialpulse.eval import EVENT_CENSORED, EVENT_COMPLETE, EVENT_STOP
from trialpulse.eval.ipcw import kaplan_meier
from trialpulse.models.aalen_johansen import aalen_johansen

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
Rows = list[dict[str, Any]]

# Event kinds of the four-state view: the early stop split by its terminal status.
KIND_TERMINATED = 1
KIND_COMPLETED = 2
KIND_WITHDRAWN = 3

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

# Registration timing: where the first version's start date falls against the first post.
PROSPECTIVE = "Registered on or before the start"
LATE_WITHIN_YEAR = "Registered up to 1 year after the start"
LATE_OVER_YEAR = "Registered more than 1 year after the start"
NO_START_DATE = "No start date"
TIMING_ORDER: tuple[str, ...] = (PROSPECTIVE, LATE_WITHIN_YEAR, LATE_OVER_YEAR, NO_START_DATE)

ALL = "All"


@dataclass(frozen=True)
class Sources:
    landmarks: Path
    outcomes: Path
    warehouse: Path
    current_fields: Path


def connect(sources: Sources) -> duckdb.DuckDBPyConnection:
    """One connection with the cohort files as views and the warehouse attached read-only."""
    con = duckdb.connect()
    con.execute(f"ATTACH '{sources.warehouse.as_posix()}' AS wh (READ_ONLY)")
    for name, path in (
        ("landmarks", sources.landmarks),
        ("outcomes", sources.outcomes),
        ("current_fields", sources.current_fields),
    ):
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{path.as_posix()}')")
    return con


def phase_group(phases: Sequence[str]) -> str:
    return PHASE_GROUPS.get(frozenset(phases), OTHER_PHASE)


def _column(data: dict[str, Any], name: str, fill: Any) -> npt.NDArray[Any]:
    """A fetched column as a plain array: DuckDB returns a masked array where a column has
    NULLs, and `fill` takes their place."""
    return np.asarray(np.ma.filled(np.ma.asarray(data[name]), fill))


def _days(data: dict[str, Any], start: str, end: str) -> FloatArray:
    return days_between(
        np.asarray(data[start]).astype("datetime64[D]"),
        np.asarray(data[end]).astype("datetime64[D]"),
    )


# Cumulative incidence ---------------------------------------------------------------------


@dataclass(frozen=True)
class Registrations:
    """L0 landmark rows: one per trial open at registration."""

    time: FloatArray  # days from L0 to the event or censoring
    event: IntArray  # 0 censored, 1 early stop, 2 completion
    kind: IntArray  # 0 censored, 1 terminated, 2 completed, 3 withdrawn
    sponsor_class: npt.NDArray[Any]  # the lead sponsor class at registration
    sponsor_group: npt.NDArray[Any]  # the three large classes, and the rest pooled
    year: IntArray  # registration year
    timing: npt.NDArray[Any]  # registration against the first version's start date
    phase: npt.NDArray[Any]  # current-record phase group: descriptive only


def registrations(
    con: duckdb.DuckDBPyConnection, before: dt.date = MODELING_EDA_BEFORE
) -> Registrations:
    start_end = period_end_sql("f.start_date", "f.start_date_precision")
    data = con.execute(
        f"""WITH first_version AS (
          SELECT nct_id, start_date, start_date_precision FROM wh.versions
          QUALIFY row_number() OVER (PARTITION BY nct_id ORDER BY nct_version) = 1
        )
        SELECT l.landmark_date, l.event, l.event_date, l.stratum,
          year(l.landmark_date) AS y,
          CASE WHEN l.event = {EVENT_STOP} AND o.terminal_status = 'WITHDRAWN'
            THEN {KIND_WITHDRAWN} ELSE l.event END AS kind,
          CASE WHEN f.start_date IS NULL THEN 3
            WHEN {start_end} >= l.landmark_date THEN 0
            WHEN date_diff('day', {start_end}, l.landmark_date) <= 365 THEN 1
            ELSE 2 END AS timing,
          c.nct_id IS NOT NULL AS has_record,
          coalesce(array_to_string(list_sort(c.phases), '|'), '') AS phases
        FROM landmarks l
        JOIN outcomes o USING (trial_id)
        LEFT JOIN first_version f ON f.nct_id = l.trial_id
        LEFT JOIN current_fields c ON c.nct_id = l.trial_id
        WHERE l.landmark_index = 0 AND l.landmark_date < DATE '{before.isoformat()}'
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
        sponsor_class=sponsor_class,
        sponsor_group=np.where(np.isin(sponsor_class, MAIN_CLASSES), sponsor_class, OTHER_CLASSES),
        year=np.asarray(data["y"], dtype=np.int64),
        timing=np.asarray(TIMING_ORDER)[np.asarray(data["timing"], dtype=np.int64)],
        phase=np.asarray(phases),
    )


def cif_at(time: FloatArray, event: IntArray, months: Sequence[int]) -> list[float]:
    """The early-stop CIF (Aalen-Johansen, completion competing) at each horizon."""
    curve = aalen_johansen(time, event)
    return [float(curve.at(m * DAYS_PER_MONTH)) for m in months]


def cif_by_group(
    time: FloatArray,
    event: IntArray,
    groups: npt.NDArray[Any],
    order: Sequence[Any],
    months: Sequence[int],
) -> Rows:
    """One row per group in `order` that has rows, then all rows together: trials, early
    stops, and the early-stop CIF at each horizon."""
    rows: Rows = []
    masks = [(g, groups == g) for g in order] + [(ALL, np.ones(len(groups), dtype=np.bool_))]
    for name, mask in masks:
        if not mask.any():
            continue
        row: dict[str, Any] = {
            "group": name,
            "trials": int(mask.sum()),
            "early_stops": int((event[mask] == EVENT_STOP).sum()),
        }
        for m, value in zip(months, cif_at(time[mask], event[mask], months), strict=True):
            row[f"cif_{m}m"] = value
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
    data = con.execute(
        f"""SELECT landmark_index, landmark_date, event, event_date FROM landmarks
        WHERE landmark_date < DATE '{before.isoformat()}' ORDER BY trial_id, landmark_index"""
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

# (key, label, what it compares). Each is 1 (yes), 0 (no) or -1 (not known: an input is
# missing) per landmark row, from versions posted on or before the landmark.
SIGNALS: tuple[tuple[str, str], ...] = (
    ("ever_suspended", "Suspended at some point"),
    ("start_overdue", "Not yet recruiting, planned start passed"),
    ("not_yet_recruiting", "Still not yet recruiting"),
    ("primary_completion_overdue", "Primary completion date passed"),
    ("primary_completion_later", "Primary completion date moved later"),
    ("completion_later", "Completion date moved later"),
    ("primary_completion_earlier", "Primary completion date moved earlier"),
    ("enrollment_cut", "Enrollment target cut by 10% or more"),
    ("enrollment_raised", "Enrollment target raised by 10% or more"),
    ("quiet", "No version posted in the last 6 months"),
)
LATER_OUTCOMES: tuple[tuple[int, str], ...] = (
    (EVENT_STOP, "Stopped early later"),
    (EVENT_COMPLETE, "Completed later"),
    (EVENT_CENSORED, "No outcome by the end of follow-up"),
)


@dataclass(frozen=True)
class Amendments:
    """Landmark rows at one landmark index, with what each record showed at the landmark."""

    time: FloatArray  # days from the landmark to the event or censoring
    event: IntArray
    signals: dict[str, IntArray]
    versions: IntArray  # versions posted on or before the landmark
    slip_months: FloatArray  # primary completion date against version 0, NaN if unknown


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
    start_end = period_end_sql("s.start_date", "s.start_date_precision")
    primary_end = period_end_sql("s.primary_completion_date", "s.primary_completion_date_precision")

    def flag(condition: str) -> str:
        return f"coalesce(CAST(({condition}) AS TINYINT), -1)"

    def months_moved(now: str, first: str) -> str:
        return f"date_diff('month', {first}, {now})"

    data = con.execute(
        f"""WITH l AS (
          SELECT trial_id, landmark_date, event, event_date FROM landmarks
          WHERE landmark_index = {int(landmark_index)}
            AND landmark_date < DATE '{before.isoformat()}'
        ),
        states AS (
          SELECT * FROM wh.versions
          QUALIFY row_number() OVER (
            PARTITION BY nct_id, effective_date ORDER BY nct_version DESC) = 1
        ),
        first_version AS (
          SELECT nct_id, primary_completion_date AS pc0, completion_date AS c0,
            enrollment_count AS n0
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
          CAST({months_moved("s.primary_completion_date", "f.pc0")} AS DOUBLE) AS slip_months,
          {flag("h.ever_suspended")} AS ever_suspended,
          {flag(f"s.overall_status = 'NOT_YET_RECRUITING' AND {start_end} < l.landmark_date")}
            AS start_overdue,
          {flag("s.overall_status = 'NOT_YET_RECRUITING'")} AS not_yet_recruiting,
          {flag(f"{primary_end} < l.landmark_date")} AS primary_completion_overdue,
          {flag(months_moved("s.primary_completion_date", "f.pc0") + " >= 1")}
            AS primary_completion_later,
          {flag(months_moved("s.completion_date", "f.c0") + " >= 1")} AS completion_later,
          {flag(months_moved("s.primary_completion_date", "f.pc0") + " <= -1")}
            AS primary_completion_earlier,
          {flag("CASE WHEN f.n0 > 0 THEN s.enrollment_count <= 0.9 * f.n0 END")}
            AS enrollment_cut,
          {flag("CASE WHEN f.n0 > 0 THEN s.enrollment_count >= 1.1 * f.n0 END")}
            AS enrollment_raised,
          {flag(f"h.last_post <= l.landmark_date - INTERVAL {int(quiet_months)} MONTH")} AS quiet
        FROM l
        JOIN first_version f ON f.nct_id = l.trial_id
        JOIN history h USING (trial_id)
        ASOF JOIN states s ON s.nct_id = l.trial_id AND l.landmark_date >= s.effective_date
        ORDER BY l.trial_id"""
    ).fetchnumpy()
    return Amendments(
        time=_days(data, "landmark_date", "event_date"),
        event=np.asarray(data["event"], dtype=np.int64),
        signals={key: np.asarray(data[key], dtype=np.int64) for key, _ in SIGNALS},
        versions=np.asarray(data["versions"], dtype=np.int64),
        slip_months=_column(data, "slip_months", np.nan).astype(np.float64),
    )


def amendments_by_outcome(am: Amendments) -> Rows:
    """Per later outcome of the landmark row: how many rows, the share showing each signal
    (among rows where it is known), the median number of versions, and the median slip of
    the primary completion date among rows where it moved later."""
    rows: Rows = []
    for code, label in LATER_OUTCOMES:
        mask = am.event == code
        row: dict[str, Any] = {"group": label, "trials": int(mask.sum())}
        for key, _ in SIGNALS:
            known = mask & (am.signals[key] >= 0)
            row[key] = float(am.signals[key][known].mean()) if known.any() else None
        later = mask & (am.signals["primary_completion_later"] == 1)
        row["median_versions"] = float(np.median(am.versions[mask])) if mask.any() else None
        row["median_slip_months"] = float(np.median(am.slip_months[later])) if later.any() else None
        rows.append(row)
    return rows


def amendments_cif(am: Amendments, months: int) -> Rows:
    """Per signal: rows with and without it, and the early-stop CIF `months` after the
    landmark in each group (Aalen-Johansen, so censored rows count for as long as they
    were followed). Sorted by the CIF with the signal, highest first."""
    rows: Rows = []
    for key, label in SIGNALS:
        with_signal, without = am.signals[key] == 1, am.signals[key] == 0
        if not with_signal.any() or not without.any():
            continue
        cif_with = cif_at(am.time[with_signal], am.event[with_signal], [months])[0]
        cif_without = cif_at(am.time[without], am.event[without], [months])[0]
        rows.append(
            {
                "key": key,
                "signal": label,
                "with": int(with_signal.sum()),
                "without": int(without.sum()),
                "unknown": int((am.signals[key] < 0).sum()),
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
    """Per registration year, for trials open at registration (L0 rows): the share registered
    after they started (the start date's whole period is before the first-post date), the
    median days from start to registration among those, the share registered more than a
    year late, and the median days from first submission to first posting. Years from
    `before` on are flagged descriptive."""
    start_end = period_end_sql("f.start_date", "f.start_date_precision")
    cursor = con.execute(
        f"""WITH first_version AS (
          SELECT nct_id, start_date, start_date_precision, study_first_submit_date
          FROM wh.versions
          QUALIFY row_number() OVER (PARTITION BY nct_id ORDER BY nct_version) = 1
        ),
        r AS (
          SELECT year(l.landmark_date) AS year, l.landmark_date AS t0, {start_end} AS start_end,
            f.study_first_submit_date AS submitted
          FROM landmarks l JOIN first_version f ON f.nct_id = l.trial_id
          WHERE l.landmark_index = 0
        )
        SELECT year, count(*) AS trials,
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
    submission to posting. A property of the registry's records, not of outcomes; years
    from `before` on are still flagged descriptive."""
    cursor = con.execute(
        """SELECT year(v.effective_date) AS year, count(*) AS versions,
          avg(CASE WHEN v.effective_date_type = 'ESTIMATED' THEN 1.0 ELSE 0.0 END)
            AS share_estimated,
          median(date_diff('day', v.submitted_date, v.effective_date)) AS median_days_to_post,
          quantile_cont(date_diff('day', v.submitted_date, v.effective_date), 0.9)
            AS p90_days_to_post
        FROM wh.versions v
        JOIN (SELECT DISTINCT trial_id FROM landmarks) c ON c.trial_id = v.nct_id
        GROUP BY 1 ORDER BY 1"""
    )
    rows = _records(cursor)
    for row in rows:
        row["descriptive"] = row["year"] >= before.year
    return rows


def _records(cursor: duckdb.DuckDBPyConnection) -> Rows:
    names = [d[0] for d in cursor.description or []]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


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
    here may shape a model. Per calendar month from `start` to `end`, for cohort trials: how
    many were under follow-up at the start of the month, and how many were terminated,
    withdrawn, or newly suspended (a SUSPENDED version after a version that was not) during
    it, per 1,000 trials under follow-up."""
    months = f"""SELECT CAST(m AS DATE) AS month_start
      FROM generate_series(DATE '{start.isoformat()}', DATE '{end.isoformat()}',
        INTERVAL 1 MONTH) AS s(m)"""
    data = con.execute(
        f"""WITH months AS ({months}),
        cohort AS (SELECT DISTINCT trial_id FROM landmarks),
        o AS (
          SELECT o.t0, o.event_date, o.terminal_status
          FROM outcomes o JOIN cohort c USING (trial_id)
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
          WHERE overall_status = 'SUSPENDED' AND previous IS DISTINCT FROM 'SUSPENDED'
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
