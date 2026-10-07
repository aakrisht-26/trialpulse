"""Everything docs/eda.md shows, computed once from the cohort files and the warehouse.

The charts, the tables, the sentences and the findings all read the same `Results`, so the
value in a sentence is the value in the table it cites. Which value a sentence uses is a
matter for the tests.
"""

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from trialpulse.cli import RefusedError
from trialpulse.cohort.build import warehouse_stamp
from trialpulse.cohort.rules import add_months
from trialpulse.config import ProjectConfig
from trialpulse.eda import analysis as an

DESCRIPTIVE = "descriptive only"
PHASE_LABEL = "Descriptive only, not used for modeling"

TABLE_MONTHS: tuple[int, ...] = (12, 24, 36, 60)
LAPSE_MONTHS: tuple[int, ...] = (12, 24, 60)
CURVE_MONTHS = 60
# Outcomes are censored at 2018-01-01 and the cohort starts in 2008, so curves end at 8
# years: beyond that too few trials are still followed.
STATE_MONTHS = 96
STATE_TABLE_MONTHS: tuple[int, ...] = (6, 12, 24, 36, 60, 96)
AMENDMENT_LANDMARK_MONTHS = 12  # Step 5: amendments are measured at the 12-month landmark
COVID_START = dt.date(2017, 1, 1)
COVID_END = dt.date(2023, 12, 1)
COVID_BASELINE = "2017 to 2019"
COVID_SHOCK = "March to May 2020"
COVID_AFTER = "June 2020 to December 2021"
COVID_PERIODS: tuple[tuple[str, dt.date, dt.date], ...] = (
    (COVID_BASELINE, dt.date(2017, 1, 1), dt.date(2019, 12, 1)),
    (COVID_SHOCK, dt.date(2020, 3, 1), dt.date(2020, 5, 1)),
    (COVID_AFTER, dt.date(2020, 6, 1), dt.date(2021, 12, 1)),
    ("2022 to 2023", dt.date(2022, 1, 1), dt.date(2023, 12, 1)),
)
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December")  # fmt: skip


@dataclass(frozen=True)
class Results:
    cutoff: dt.date
    before: dt.date  # modeling-relevant analysis reads nothing dated on or after this
    stamp: str  # what the warehouse is: revision, schema version, checksum
    snapshot: str  # the date of the current-record snapshot used for phase
    landmark_rows: int  # of the cohort as of `before`
    landmark_trials: int
    final_landmark_rows: int  # of the final cohort, which the descriptive sections read
    final_landmark_trials: int
    horizons: tuple[int, ...]
    spacing_months: int
    reg: an.Registrations
    sponsor: an.Rows
    sponsor_curves: dict[str, tuple[an.FloatArray, an.FloatArray]]
    lapse: an.Rows
    year: an.Rows
    by_landmark: an.Rows
    phase: an.Rows
    states: dict[str, an.FloatArray]
    amendment_rows: int
    amendment_overall: float
    amendments_outcome: an.Rows
    amendments_cif: an.Rows
    registration_years: an.Rows
    timing: an.Rows
    post_dates: an.Rows
    posting_lag: an.Rows
    covid: dict[str, Any]
    covid_summary: an.Rows

    @property
    def short(self) -> int:
        return self.horizons[0]

    @property
    def long(self) -> int:
        return self.horizons[-1]

    @property
    def first_year(self) -> int:
        return int(self.reg.year.min())

    @property
    def last_modeling_day(self) -> dt.date:
        return self.before - dt.timedelta(days=1)

    def state(self, name: str, month: int) -> float:
        return float(self.states[name][month])

    def withdrawn_share(self, month: int) -> float:
        """Withdrawn trials as a share of the trials stopped early by a month."""
        return self.state("withdrawn", month) / self.state("early_stop", month)

    def covid_peak(self) -> tuple[str, float, int]:
        """The month with the highest suspension rate: its name, the rate, its position."""
        rate = self.covid["suspended_per_1000"]
        at = int(np.argmax(rate))
        month = self.covid["months"][at].astype(object)
        return f"{MONTH_NAMES[month.month - 1]} {month.year}", float(rate[at]), at


def row(rows: an.Rows, key: str, value: Any) -> dict[str, Any]:
    for candidate in rows:
        if candidate[key] == value:
            return candidate
    raise RefusedError(f"docs/eda.md needs a row with {key} = {value!r}, and the data has none.")


def groups(rows: an.Rows) -> an.Rows:
    """The rows of a CIF table without its "All" row."""
    return [candidate for candidate in rows if candidate["group"] != an.ALL]


def followed(rows: an.Rows, key: str) -> an.Rows:
    """The rows whose cell `key` could be estimated (see `year_rows`)."""
    return [candidate for candidate in rows if candidate[key] is not None]


def year_rows(reg: an.Registrations, horizons: tuple[int, ...], before: dt.date) -> an.Rows:
    """The early-stop CIF by registration year. Outcomes are censored at `before`, so a
    year's cell is kept only where every trial registered in that year could be followed
    for the whole horizon before that date; the other cells are None."""
    years = sorted(set(reg.year.tolist()))
    rows = an.cif_by_group(reg.time, reg.event, reg.year, years, horizons)
    for candidate in groups(rows):
        last_registration = dt.date(int(candidate["group"]), 12, 31)
        for months in horizons:
            if add_months(last_registration, months) > before:
                candidate[f"cif_{months}m"] = None
    return rows


def snapshot_date(current_fields: Path) -> str:
    """The date ClinicalTrials.gov processed the data of the current-record snapshot, from
    the Step 2 part g result stored beside it."""
    record = current_fields.parent / "results" / "part_g.json"
    if record.is_file():
        stamp = json.loads(record.read_text(encoding="utf-8")).get("api_version", {})
        if stamp.get("dataTimestamp"):
            return str(stamp["dataTimestamp"])[:10]
    return "date not recorded"


def compute(sources: an.Sources, cfg: ProjectConfig, before: dt.date) -> Results:
    if cfg.dataset.cutoff is None:
        raise RefusedError("config/project.yaml has no dataset cutoff yet (Step 2 records it).")
    horizons = tuple(cfg.horizons_months)
    spacing = cfg.landmarks.spacing_months
    phase_order = (*an.PHASE_ORDER, an.OTHER_PHASE, an.NO_CURRENT_RECORD)
    with an.connect(sources) as con:
        an.check_as_of(con, before)
        counts = con.execute("SELECT count(*), count(DISTINCT trial_id) FROM landmarks").fetchone()
        final = con.execute(
            "SELECT count(*), count(DISTINCT trial_id) FROM final_landmarks"
        ).fetchone()
        reg = an.registrations(con, before)
        if len(reg.time) == 0:
            raise RefusedError(f"no landmark rows before {before.isoformat()} to analyze.")
        time, event = reg.time, reg.event
        am = an.amendment_signals(con, AMENDMENT_LANDMARK_MONTHS // spacing, spacing, before)
        covid = an.covid_period_monthly(con, COVID_START, COVID_END)
        timing = an.cif_by_group(time, event, reg.timing, an.TIMING_ORDER, TABLE_MONTHS)
        kinds = an.stop_kinds_by_group(reg, reg.timing, an.TIMING_ORDER, horizons[-1])
        for candidate in timing:
            candidate.update(kinds[candidate["group"]])
        return Results(
            cutoff=cfg.dataset.cutoff,
            before=before,
            stamp=warehouse_stamp(con),
            snapshot=snapshot_date(sources.current_fields),
            landmark_rows=int(counts[0]) if counts else 0,
            landmark_trials=int(counts[1]) if counts else 0,
            final_landmark_rows=int(final[0]) if final else 0,
            final_landmark_trials=int(final[1]) if final else 0,
            horizons=horizons,
            spacing_months=spacing,
            reg=reg,
            sponsor=an.cif_by_group(time, event, reg.sponsor_group, an.SPONSOR_ORDER, TABLE_MONTHS),
            sponsor_curves=an.cif_curves(
                time, event, reg.sponsor_group, an.SPONSOR_ORDER, CURVE_MONTHS
            ),
            lapse=an.lapse_by_group(reg, reg.sponsor_group, an.SPONSOR_ORDER, LAPSE_MONTHS),
            year=year_rows(reg, horizons, before),
            by_landmark=an.cif_by_landmark_index(con, horizons, before),
            phase=an.cif_by_group(time, event, reg.phase, phase_order, horizons),
            states=an.competing_view(reg, STATE_MONTHS),
            amendment_rows=len(am.time),
            amendment_overall=an.cif_at(am.time, am.event, [horizons[-1]])[0],
            amendments_outcome=an.amendments_by_outcome(am),
            amendments_cif=an.amendments_cif(am, horizons[-1]),
            registration_years=an.registration_by_year(con, before),
            timing=timing,
            post_dates=an.post_dates_by_year(con, before),
            posting_lag=an.posting_lag(con, before),
            covid=covid,
            covid_summary=an.covid_period_summary(covid, COVID_PERIODS),
        )
