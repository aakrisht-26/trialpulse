"""The EDA numbers on the synthetic registry of conftest.py, where every answer is known."""

import datetime as dt
import shutil
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import duckdb
import numpy as np
import pytest

from trialpulse.cohort.rules import add_months
from trialpulse.eda import analysis as an

from .conftest import (
    COVID_SUSPENDED,
    COVID_TRIALS,
    COVID_YEAR,
    HISTORIES,
    MODELING_YEARS,
    World,
    first_post,
)

CUTOFF = dt.date(2026, 9, 25)
AT_12_MONTHS = 2  # landmark index
BEFORE_2017 = tuple(y for y in MODELING_YEARS if y < 2017)  # their 12-month landmark is before 2018
ENDED_BY_12_MONTHS = ("withdrawn_early", "completed_short")
EARLY_STOPS_BY_24_MONTHS = (
    "withdrawn_early",
    "stalled_withdrawn",
    "suspended_terminated",
    "slipped_terminated",
)


@pytest.fixture(scope="module")
def con(world: World) -> Iterator[duckdb.DuckDBPyConnection]:
    with an.connect(world.sources) as connection:
        yield connection


@pytest.fixture(scope="module")
def reg(con: duckdb.DuckDBPyConnection) -> an.Registrations:
    return an.registrations(con)


def test_phase_groups_follow_the_registry_names() -> None:
    assert an.phase_group(["EARLY_PHASE1"]) == an.EARLY_PHASE
    assert an.phase_group(["PHASE1", "PHASE2"]) == an.MID_PHASE
    assert an.phase_group(["PHASE2", "PHASE3"]) == an.LATE_PHASE
    assert an.phase_group(["PHASE4"]) == an.POST_APPROVAL_PHASE
    assert an.phase_group(["NA"]) == an.phase_group([]) == an.NO_PHASE
    assert an.phase_group(["PHASE1", "PHASE4"]) == an.OTHER_PHASE


def test_registrations_keep_only_landmarks_before_2018(
    con: duckdb.DuckDBPyConnection, reg: an.Registrations, world: World
) -> None:
    assert len(reg.time) == world.count(*HISTORIES)
    assert set(reg.year.tolist()) == set(MODELING_YEARS)
    everything = an.registrations(con, before=dt.date(2100, 1, 1))
    assert COVID_YEAR in everything.year  # only the date rule kept that year out
    assert len(everything.time) == len(world.trials)


def test_small_sponsor_classes_are_pooled(reg: an.Registrations) -> None:
    assert set(reg.sponsor_class.tolist()) == {"INDUSTRY", "OTHER", "NIH", "FED"}
    assert set(reg.sponsor_group.tolist()) == {"INDUSTRY", "OTHER", "NIH", an.OTHER_CLASSES}
    assert (reg.sponsor_group == an.OTHER_CLASSES).sum() == (reg.sponsor_class == "FED").sum()


def test_registration_timing_compares_the_start_date_with_the_first_post(
    reg: an.Registrations, world: World
) -> None:
    def expected(test: Callable[[int | None], bool]) -> int:
        return sum(t.year in MODELING_YEARS and test(t.start_month) for t in world.trials)

    counts = {name: int((reg.timing == name).sum()) for name in an.TIMING_ORDER}
    assert counts == {
        an.PROSPECTIVE: expected(lambda m: m is not None and m >= 0),
        an.LATE_WITHIN_YEAR: expected(lambda m: m is not None and -12 <= m < 0),
        an.LATE_OVER_YEAR: expected(lambda m: m is not None and m < -12),
        an.NO_START_DATE: expected(lambda m: m is None),
    }
    assert all(counts.values())


def test_phase_comes_from_the_current_record_snapshot(reg: an.Registrations) -> None:
    groups = set(reg.phase.tolist())
    assert {an.EARLY_PHASE, an.MID_PHASE, an.LATE_PHASE, an.POST_APPROVAL_PHASE} <= groups
    assert an.NO_PHASE in groups
    assert (reg.phase == an.NO_CURRENT_RECORD).sum() == 1


def test_cif_is_the_plain_fraction_when_nothing_is_censored(
    reg: an.Registrations, world: World
) -> None:
    """No synthetic trial is censored in its first 60 months, so the Aalen-Johansen CIF at a
    horizon is the share of trials that stopped early by then."""
    rows = an.cif_by_group(reg.time, reg.event, reg.sponsor_class, ["INDUSTRY", "OTHER"], (12, 24))
    assert [row["group"] for row in rows] == ["INDUSTRY", "OTHER", an.ALL]
    for row in rows:
        mine = [
            t
            for t in world.trials
            if t.year in MODELING_YEARS and row["group"] in (t.sponsor, an.ALL)
        ]
        assert row["trials"] == len(mine)
        assert row["cif_12m"] == pytest.approx(
            sum(t.history == "withdrawn_early" for t in mine) / len(mine)
        )
        assert row["cif_24m"] == pytest.approx(
            sum(t.history in EARLY_STOPS_BY_24_MONTHS for t in mine) / len(mine)
        )
    only_all = an.cif_by_group(reg.time, reg.event, reg.sponsor_class, ["NETWORK"], (12,))
    assert [row["group"] for row in only_all] == [an.ALL]  # a class without trials has no row


def test_cif_curves_match_the_table(reg: an.Registrations) -> None:
    rows = an.cif_by_group(reg.time, reg.event, reg.sponsor_group, an.SPONSOR_ORDER, (12, 24))
    curves = an.cif_curves(reg.time, reg.event, reg.sponsor_group, an.SPONSOR_ORDER, 36)
    for row in rows[:-1]:
        months, cif = curves[row["group"]]
        assert months[0] == 0
        assert months[-1] == 36
        assert cif[0] == 0
        assert cif[12] == pytest.approx(row["cif_12m"])
        assert cif[24] == pytest.approx(row["cif_24m"])
        assert np.all(np.diff(cif) >= 0)


def test_landmark_rows_are_the_trials_still_open_and_before_2018(
    con: duckdb.DuckDBPyConnection, world: World
) -> None:
    rows = {row["group"]: row for row in an.cif_by_landmark_index(con, (12, 24))}
    assert rows[0]["trials"] == world.count(*HISTORIES)
    still_open = [h for h in HISTORIES if h not in ENDED_BY_12_MONTHS]
    assert rows[AT_12_MONTHS]["trials"] == world.count(*still_open, years=BEFORE_2017)
    # In the 12 months after the 12-month landmark: withdrawn at 15, terminated at 16 and 20.
    stops = world.count(
        "stalled_withdrawn", "suspended_terminated", "slipped_terminated", years=BEFORE_2017
    )
    assert rows[AT_12_MONTHS]["cif_12m"] == pytest.approx(stops / rows[AT_12_MONTHS]["trials"])
    assert an.ALL not in rows


def test_the_four_states_add_up_and_the_naive_estimate_is_never_lower(
    reg: an.Registrations, world: World
) -> None:
    view = an.competing_view(reg, 60)
    total = view["terminated"] + view["withdrawn"] + view["completed"] + view["open"]
    assert np.allclose(total, 1.0)
    assert np.allclose(view["early_stop"], view["terminated"] + view["withdrawn"])
    assert np.all(view["naive_early_stop"] >= view["early_stop"] - 1e-12)
    assert view["naive_early_stop"][60] > view["early_stop"][60]
    trials = world.count(*HISTORIES)
    assert view["withdrawn"][12] == pytest.approx(world.count("withdrawn_early") / trials)
    assert view["terminated"][12] == 0
    assert view["completed"][12] == pytest.approx(world.count("completed_short") / trials)
    assert view["open"][0] == 1


def test_amendment_signals_have_the_scripted_counts(
    con: duckdb.DuckDBPyConnection, world: World
) -> None:
    am = an.amendment_signals(con, AT_12_MONTHS, quiet_months=6)

    def scripted(*histories: str) -> int:
        return world.count(*histories, years=BEFORE_2017)

    assert len(am.time) == scripted(*[h for h in HISTORIES if h not in ENDED_BY_12_MONTHS])
    yes = {key: int((values == 1).sum()) for key, values in am.signals.items()}
    assert yes == {
        "ever_suspended": scripted("suspended_terminated"),
        "start_overdue": scripted("stalled_withdrawn", "waiting_completed"),
        "not_yet_recruiting": scripted("stalled_withdrawn", "waiting_completed"),
        "primary_completion_overdue": scripted("overdue_completed"),
        "primary_completion_later": scripted("slipped_terminated", "slipped_completed"),
        "completion_later": 0,
        "primary_completion_earlier": 0,
        "enrollment_cut": scripted("cut_completed"),
        "enrollment_raised": scripted("slipped_completed"),
        "quiet": scripted(
            "stalled_withdrawn",
            "terminated_late",
            "overdue_completed",
            "waiting_completed",
            "still_open",
        ),
    }
    assert all((values >= 0).all() for values in am.signals.values())  # nothing unknown here
    moved = am.signals["primary_completion_later"] == 1
    assert set(am.slip_months[moved].tolist()) == {12.0}  # from 24 to 36 months
    assert set(am.versions.tolist()) == {1, 2}


def test_amendment_tables(con: duckdb.DuckDBPyConnection, world: World) -> None:
    am = an.amendment_signals(con, AT_12_MONTHS, quiet_months=6)
    cif = {row["key"]: row for row in an.amendments_cif(am, 24)}
    suspended = cif["ever_suspended"]
    assert suspended["with"] == world.count("suspended_terminated", years=BEFORE_2017)
    assert suspended["cif_with"] == pytest.approx(1.0)  # every one is terminated at month 16
    assert suspended["with"] + suspended["without"] + suspended["unknown"] == len(am.time)
    assert cif["enrollment_cut"]["cif_with"] == 0  # the trials with a cut target all complete
    assert "completion_later" not in cif  # no trial shows it, so there is nothing to compare
    ordered = [row["cif_with"] for row in an.amendments_cif(am, 24)]
    assert ordered == sorted(ordered, reverse=True)

    outcomes = an.amendments_by_outcome(am)
    assert [row["group"] for row in outcomes] == [label for _, label in an.LATER_OUTCOMES]
    assert sum(row["trials"] for row in outcomes) == len(am.time)
    stopped = outcomes[0]
    stops = ("stalled_withdrawn", "suspended_terminated", "slipped_terminated", "terminated_late")
    assert stopped["trials"] == world.count(*stops, years=BEFORE_2017)
    assert stopped["ever_suspended"] == pytest.approx(
        world.count("suspended_terminated", years=BEFORE_2017) / stopped["trials"]
    )
    assert stopped["median_slip_months"] == 12


def _altered(world: World, tmp_path: Path, where: str) -> an.Amendments:
    """The signals after rewriting every version that matches `where`."""
    warehouse = tmp_path / "altered.duckdb"
    shutil.copy(world.sources.warehouse, warehouse)
    with duckdb.connect(str(warehouse)) as edit:
        edit.execute(
            f"""UPDATE versions SET overall_status = 'SUSPENDED',
              primary_completion_date = primary_completion_date + INTERVAL 5 YEAR,
              completion_date = completion_date + INTERVAL 5 YEAR,
              start_date = DATE '2001-01-01', enrollment_count = 1
            WHERE {where}"""
        )
        edit.execute(
            """INSERT INTO versions BY NAME
            SELECT * REPLACE (nct_version + 100 AS nct_version, DATE '2026-09-01' AS effective_date,
              'SUSPENDED' AS overall_status, 5 AS enrollment_count)
            FROM versions WHERE nct_version = 0"""
        )
    with an.connect(replace(world.sources, warehouse=warehouse)) as con:
        return an.amendment_signals(con, AT_12_MONTHS, quiet_months=6)


def test_signals_ignore_everything_posted_after_the_landmark(
    con: duckdb.DuckDBPyConnection, world: World, tmp_path: Path
) -> None:
    """Point-in-time (Section 8, rule 1): rewrite every version posted after the 12-month
    landmark and add a later version to every trial. Nothing may change."""
    original = an.amendment_signals(con, AT_12_MONTHS, quiet_months=6)
    after = "effective_date > study_first_post_date + INTERVAL 12 MONTH"
    altered = _altered(world, tmp_path, after)
    assert np.array_equal(altered.versions, original.versions)
    assert np.array_equal(altered.slip_months, original.slip_months, equal_nan=True)
    for key, values in original.signals.items():
        assert np.array_equal(altered.signals[key], values), key


def test_signals_do_change_when_an_earlier_version_changes(
    con: duckdb.DuckDBPyConnection, world: World, tmp_path: Path
) -> None:
    """The control for the test above: the same rewrite on versions posted on or before the
    landmark is seen."""
    original = an.amendment_signals(con, AT_12_MONTHS, quiet_months=6)
    on_or_before = "nct_version > 0 AND effective_date <= study_first_post_date + INTERVAL 12 MONTH"
    altered = _altered(world, tmp_path, on_or_before)
    assert not np.array_equal(altered.signals["ever_suspended"], original.signals["ever_suspended"])
    assert not np.array_equal(altered.signals["enrollment_cut"], original.signals["enrollment_cut"])


def test_registration_by_year_marks_later_years_descriptive(
    con: duckdb.DuckDBPyConnection, world: World
) -> None:
    rows = {row["year"]: row for row in an.registration_by_year(con)}
    assert sorted(rows) == [*MODELING_YEARS, COVID_YEAR]
    assert [rows[y]["descriptive"] for y in sorted(rows)] == [False] * len(MODELING_YEARS) + [True]
    year = MODELING_YEARS[0]
    mine = [t for t in world.trials if t.year == year]
    dated = [t for t in mine if t.start_month is not None]
    late = [t for t in dated if t.start_month is not None and t.start_month < 0]
    over = [t for t in dated if t.start_month is not None and t.start_month < -12]
    assert rows[year]["trials"] == len(mine)
    assert rows[year]["share_after_start"] == pytest.approx(len(late) / len(dated))
    assert rows[year]["share_over_a_year_late"] == pytest.approx(len(over) / len(dated))
    assert rows[year]["median_days_submit_to_post"] == 5
    assert rows[COVID_YEAR]["share_after_start"] == 1.0


def test_post_dates_by_year(con: duckdb.DuckDBPyConnection) -> None:
    rows = an.post_dates_by_year(con)
    assert [row["year"] for row in rows] == sorted(row["year"] for row in rows)
    for row in rows:
        assert row["share_estimated"] == (1.0 if row["year"] < 2017 else 0.0)
        assert row["descriptive"] == (row["year"] >= 2018)
        assert row["median_days_to_post"] == 2
        assert row["p90_days_to_post"] == 2
    assert rows[0]["year"] == MODELING_YEARS[0]


def _end(trial_year: int, history: str) -> dt.date:
    """When a scripted trial leaves follow-up: its terminal version, or the data cutoff."""
    if history == "covid":
        return dt.date(2021, 6, 1)
    month, status, _ = HISTORIES[history][-1]
    terminal = status in ("TERMINATED", "WITHDRAWN", "COMPLETED")
    return add_months(first_post(trial_year), month) if terminal else CUTOFF


def test_covid_period_rates(con: duckdb.DuckDBPyConnection, world: World) -> None:
    monthly = an.covid_period_monthly(con, dt.date(2017, 1, 1), dt.date(2021, 12, 1))
    months = [m.astype(object) for m in monthly["months"]]
    assert months[0] == dt.date(2017, 1, 1)
    assert months[-1] == dt.date(2021, 12, 1)
    at = {month: i for i, month in enumerate(months)}

    april = at[COVID_SUSPENDED.replace(day=1)]
    assert monthly["suspended"][april] == COVID_TRIALS
    assert monthly["suspended"][april + 1] == 0  # a second SUSPENDED version is no new suspension
    for month, i in at.items():
        under_follow_up = sum(
            first_post(t.year) <= month < _end(t.year, t.history) for t in world.trials
        )
        assert monthly["open_trials"][i] == under_follow_up, month
    assert monthly["suspended_per_1000"][april] == pytest.approx(
        1000 * COVID_TRIALS / monthly["open_trials"][april]
    )
    # Trials first posted in March 2017 and terminated 40 months later: July 2020.
    assert monthly["terminated"][at[dt.date(2020, 7, 1)]] == world.count(
        "terminated_late", years=(2017,)
    )
    assert monthly["withdrawn"][at[dt.date(2017, 8, 1)]] == world.count(
        "withdrawn_early", years=(2017,)
    )
    assert monthly["terminated"].sum() + monthly["withdrawn"].sum() > 0

    periods = [("spring", dt.date(2020, 3, 1), dt.date(2020, 5, 1))]
    (summary,) = an.covid_period_summary(monthly, periods)
    window = slice(at[dt.date(2020, 3, 1)], at[dt.date(2020, 5, 1)] + 1)
    assert summary["months"] == 3
    assert summary["suspended_per_1000"] == pytest.approx(
        monthly["suspended_per_1000"][window].mean()
    )
    assert summary["open_trials"] == pytest.approx(monthly["open_trials"][window].mean())
