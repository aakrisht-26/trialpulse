"""The EDA numbers on the synthetic registry of conftest.py, where every answer is known.

Most tests compare the SQL with the Python twins of conftest.py, row by row. The edge cases
of the registry (a version on the landmark day, two versions on one post date, month
precision, missing inputs, the first locked day) are then named one by one.
"""

import datetime as dt
import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import duckdb
import numpy as np
import pytest

from trialpulse.cli import RefusedError
from trialpulse.cohort.audit import DAYS_PER_MONTH
from trialpulse.eda import analysis as an
from trialpulse.eval import EVENT_CENSORED, EVENT_COMPLETE, EVENT_STOP

from .conftest import (
    BEFORE,
    COVID_SUSPENDED,
    COVID_TRIALS,
    COVID_YEAR,
    HISTORIES,
    MODELING_YEARS,
    Trial,
    World,
    month_end,
    outcome,
    signals_at,
    state_at,
    timing_of,
)
from .conftest import (
    cif as _cif,
)
from .conftest import (
    followed as _followed,
)

AT_12_MONTHS = 2  # landmark index


@pytest.fixture(scope="module")
def con(world: World) -> Iterator[duckdb.DuckDBPyConnection]:
    with an.connect(world.sources) as connection:
        yield connection


@pytest.fixture(scope="module")
def final_con(world: World) -> Iterator[duckdb.DuckDBPyConnection]:
    """The analysis pointed at the final cohort, with outcomes through the data cutoff."""
    with an.connect(world.final_sources) as connection:
        yield connection


@pytest.fixture(scope="module")
def reg(con: duckdb.DuckDBPyConnection) -> an.Registrations:
    return an.registrations(con)


@pytest.fixture(scope="module")
def registered(world: World) -> list[Trial]:
    """The trials with a registration landmark before 2018, by trial id."""
    return [trial for trial, _ in world.landmark_rows(0)]


@pytest.fixture(scope="module")
def signals(con: duckdb.DuckDBPyConnection) -> an.Amendments:
    return an.amendment_signals(con, AT_12_MONTHS, quiet_months=6)


def test_phase_groups_follow_the_registry_names() -> None:
    assert an.phase_group(["EARLY_PHASE1"]) == an.phase_group(["PHASE1"]) == an.EARLY_PHASE
    assert an.phase_group(["PHASE1", "PHASE2"]) == an.phase_group(["PHASE2"]) == an.MID_PHASE
    assert an.phase_group(["PHASE2", "PHASE3"]) == an.phase_group(["PHASE3"]) == an.LATE_PHASE
    assert an.phase_group(["PHASE4"]) == an.POST_APPROVAL_PHASE
    assert an.phase_group(["NA"]) == an.phase_group([]) == an.NO_PHASE
    assert an.phase_group(["PHASE1", "PHASE4"]) == an.OTHER_PHASE


def test_a_hand_worked_cif_with_a_censored_trial() -> None:
    """Four trials: an early stop at 10 days, a censoring at 20, an early stop at 30 and a
    completion at 40. After the first stop 3 remain; the censored one leaves 2 at risk on
    day 30, so the second stop adds 0.75 * 1/2: the CIF is 0.25, then 0.625."""
    time = np.array([10.0, 20.0, 30.0, 40.0])
    event = np.array([EVENT_STOP, EVENT_CENSORED, EVENT_STOP, EVENT_COMPLETE])
    months = [15 / DAYS_PER_MONTH, 35 / DAYS_PER_MONTH, 45 / DAYS_PER_MONTH]
    assert an.cif_at(time, event, months) == pytest.approx([0.25, 0.625, 0.625])
    assert an.cif_at(time, event, months, cause=EVENT_COMPLETE) == pytest.approx([0, 0, 0.375])


def test_registrations_keep_only_landmarks_before_2018(
    con: duckdb.DuckDBPyConnection,
    final_con: duckdb.DuckDBPyConnection,
    reg: an.Registrations,
    world: World,
    registered: list[Trial],
) -> None:
    assert len(reg.time) == len(registered)
    assert set(reg.year.tolist()) == set(MODELING_YEARS)
    assert world.named("landmark_on_2018_01_01") in registered  # first posted 2017-01-01
    assert world.named("registered_2018_01_01") not in registered  # 2018-01-01 is locked
    # The cohort as of 2018-01-01 holds no later registration at all, whatever the date asked.
    assert set(an.registrations(con, before=dt.date(2100, 1, 1)).year.tolist()) == set(
        MODELING_YEARS
    )
    # On the final cohort the date alone keeps the later registrations out.
    everything = an.registrations(final_con, before=dt.date(2100, 1, 1))
    assert {COVID_YEAR, 2018} <= set(everything.year.tolist())
    assert len(everything.time) == len(world.trials)
    assert set(an.registrations(final_con).year.tolist()) == set(MODELING_YEARS)


def test_tables_that_were_not_built_as_of_the_date_are_refused(
    con: duckdb.DuckDBPyConnection, final_con: duckdb.DuckDBPyConnection
) -> None:
    an.check_as_of(con, BEFORE)
    with pytest.raises(RefusedError, match=r"dated on or after 2018-01-01.*ADR 0016"):
        an.check_as_of(final_con, BEFORE)
    with pytest.raises(RefusedError, match="dated on or after 2017-06-01"):
        an.check_as_of(con, dt.date(2017, 6, 1))


def test_outcomes_are_what_2018_01_01_knew(
    final_con: duckdb.DuckDBPyConnection,
    reg: an.Registrations,
    world: World,
    registered: list[Trial],
) -> None:
    """Section 10 and ADR 0016: an event posted on or after 2018-01-01 is not observed yet.
    Row by row against the twin, then one trial by name, then the final cohort, which
    follows the same trials to the data cutoff."""
    rows = world.landmark_rows(0)
    time, event, kind, lapsed = _followed(rows)
    assert np.array_equal(reg.time, time)
    assert np.array_equal(reg.event, event)
    assert np.array_equal(reg.kind, kind)
    assert np.array_equal(reg.lapsed, lapsed)

    late = registered.index(world.named("landmark_on_2018_01_01"))  # terminated 2018-03-01
    assert reg.event[late] == EVENT_CENSORED
    assert reg.time[late] == (BEFORE - dt.date(2017, 1, 1)).days
    assert not reg.lapsed[late]
    assert (reg.event == EVENT_STOP).sum() < sum(
        outcome(trial)[1] in (an.KIND_TERMINATED, an.KIND_WITHDRAWN) for trial in registered
    )

    to_cutoff = an.registrations(final_con, before=dt.date(2100, 1, 1))
    everyone = world.landmark_rows(0, None)
    _, _, kind_to_cutoff, lapsed_to_cutoff = _followed(everyone, None)
    assert np.array_equal(to_cutoff.kind, kind_to_cutoff)
    # A trial still open at the data cutoff is censored too, but not by the UNKNOWN rule.
    assert np.array_equal(to_cutoff.lapsed, lapsed_to_cutoff)
    assert (to_cutoff.event == EVENT_CENSORED).sum() > to_cutoff.lapsed.sum() == 2


def test_a_lapse_after_2018_is_not_known_to_the_modeling_tables(
    final_con: duckdb.DuckDBPyConnection,
    reg: an.Registrations,
    world: World,
    registered: list[Trial],
) -> None:
    """ADR 0016. The record of "lapses_in_2018" was last verified in April 2016, so the
    UNKNOWN rule applies to it from 2018-05-01. On 2018-01-01 it is an open trial, followed
    to that day, with a row at every landmark before it. The final cohort censors it at its
    last verification, and cutting the final cohort at 2018-01-01 keeps that hindsight."""
    trial = world.named("lapses_in_2018")
    at = registered.index(trial)
    assert reg.event[at] == EVENT_CENSORED
    assert not reg.lapsed[at]
    assert reg.time[at] == (BEFORE - trial.first_post).days
    assert [sum(t is trial for t, _ in world.landmark_rows(k)) for k in range(7)] == [1] * 6 + [0]

    cut = an.registrations(final_con)  # the final cohort cut at 2018-01-01: the earlier report
    in_final = [t for t, day in world.landmark_rows(0, None) if day < BEFORE]
    there = in_final.index(trial)
    assert cut.lapsed[there]
    assert cut.time[there] == (outcome(trial)[0] - trial.first_post).days < reg.time[at]
    rows_in_final = [sum(t is trial for t, _ in world.landmark_rows(k, None)) for k in range(7)]
    assert rows_in_final == [1, 1, 0, 0, 0, 0, 0]


def test_the_unknown_rule_censoring_is_told_apart(
    reg: an.Registrations, world: World, registered: list[Trial]
) -> None:
    trial = world.named("lapsed")
    lapsed = registered.index(trial)
    assert reg.lapsed.sum() == 1
    assert reg.lapsed[lapsed]
    assert reg.event[lapsed] == EVENT_CENSORED
    assert reg.time[lapsed] == (outcome(trial)[0] - trial.first_post).days

    rows = an.lapse_by_group(reg, reg.sponsor_group, an.SPONSOR_ORDER, (12, 24))
    by_group = {row["group"]: row for row in rows}
    assert by_group["OTHER"]["lapsed"] == by_group[an.ALL]["lapsed"] == 1
    assert by_group["INDUSTRY"]["lapsed"] == 0
    assert by_group["INDUSTRY"]["lapse_24m"] == 0
    code = np.where(reg.lapsed, an.KIND_LAPSED, reg.kind)
    other = reg.sponsor_group == "OTHER"
    for months in (12, 24):
        expected = _cif(reg.time[other], code[other], months, an.KIND_LAPSED)
        assert by_group["OTHER"][f"lapse_{months}m"] == pytest.approx(expected)
        assert expected > 0


def test_small_sponsor_classes_are_pooled(reg: an.Registrations) -> None:
    assert set(reg.sponsor_class.tolist()) == {"INDUSTRY", "OTHER", "NIH", "FED"}
    assert set(reg.sponsor_group.tolist()) == {"INDUSTRY", "OTHER", "NIH", an.OTHER_CLASSES}
    assert (reg.sponsor_group == an.OTHER_CLASSES).sum() == (reg.sponsor_class == "FED").sum()


def test_registration_timing_reads_the_first_version_by_calendar_month(
    reg: an.Registrations, world: World, registered: list[Trial]
) -> None:
    assert reg.timing.tolist() == [timing_of(trial) for trial in registered]
    assert all((reg.timing == name).any() for name in an.TIMING_ORDER)
    # The start date moves 22 months earlier in a later version; the first version counts.
    changed = registered.index(world.named("start_date_changes"))
    assert reg.timing[changed] == an.PROSPECTIVE


def test_phase_comes_from_the_current_record_snapshot(reg: an.Registrations) -> None:
    groups = set(reg.phase.tolist())
    assert {an.EARLY_PHASE, an.MID_PHASE, an.LATE_PHASE, an.POST_APPROVAL_PHASE} <= groups
    assert an.NO_PHASE in groups
    assert (reg.phase == an.NO_CURRENT_RECORD).sum() == 1


def test_cif_tables_match_the_estimator_on_the_twin_outcomes(
    reg: an.Registrations, world: World, registered: list[Trial]
) -> None:
    time, event, kind, _ = _followed(world.landmark_rows(0))
    sponsor = np.array([trial.sponsor for trial in registered])
    rows = an.cif_by_group(reg.time, reg.event, reg.sponsor_class, ["INDUSTRY", "OTHER"], (12, 24))
    assert [row["group"] for row in rows] == ["INDUSTRY", "OTHER", an.ALL]
    for row in rows:
        mask = np.ones(len(sponsor), bool) if row["group"] == an.ALL else sponsor == row["group"]
        assert row["trials"] == mask.sum()
        assert row["early_stops"] == (event[mask] == EVENT_STOP).sum()
        assert row["early_stops"] != (event[mask] == EVENT_COMPLETE).sum()
        for months in (12, 24):
            expected = _cif(time[mask], event[mask], months)
            assert row[f"cif_{months}m"] == pytest.approx(expected)
            assert 0 < expected < 1
    only_all = an.cif_by_group(reg.time, reg.event, reg.sponsor_class, ["NETWORK"], (12,))
    assert [row["group"] for row in only_all] == [an.ALL]  # a class without trials has no row

    kinds = an.stop_kinds_by_group(reg, reg.sponsor_class, ["INDUSTRY"], 24)
    mask = sponsor == "INDUSTRY"
    withdrawn = _cif(time[mask], kind[mask], 24, an.KIND_WITHDRAWN)
    terminated = _cif(time[mask], kind[mask], 24, an.KIND_TERMINATED)
    assert kinds["INDUSTRY"] == pytest.approx({"withdrawn": withdrawn, "terminated": terminated})
    assert withdrawn != pytest.approx(terminated)
    assert withdrawn + terminated == pytest.approx(rows[0]["cif_24m"])


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


def test_landmark_rows_are_before_2018_with_outcomes_censored_there(
    con: duckdb.DuckDBPyConnection, world: World
) -> None:
    rows = {row["group"]: row for row in an.cif_by_landmark_index(con, (12, 24))}
    assert an.ALL not in rows
    for index, row in rows.items():
        mine = world.landmark_rows(index)
        time, event, _, _ = _followed(mine)
        assert row["trials"] == len(mine)
        assert row["early_stops"] == (event == EVENT_STOP).sum()
        assert row["cif_12m"] == pytest.approx(_cif(time, event, 12))
    # The 12-month landmark of the trial first posted on 2017-01-01 is 2018-01-01: locked.
    at_12 = [trial.name for trial, _ in world.landmark_rows(AT_12_MONTHS)]
    assert "landmark_on_2018_01_01" not in at_12
    assert "on_landmark_day" in at_12


def test_the_four_states_add_up_and_the_naive_estimate_is_pinned(
    reg: an.Registrations, world: World
) -> None:
    view = an.competing_view(reg, 48)
    total = view["terminated"] + view["withdrawn"] + view["completed"] + view["open"]
    assert np.allclose(total, 1.0)
    assert view["open"][0] == 1
    time, event, kind, _ = _followed(world.landmark_rows(0))
    for name, cause in (("terminated", an.KIND_TERMINATED), ("withdrawn", an.KIND_WITHDRAWN),
                        ("completed", an.KIND_COMPLETED)):  # fmt: skip
        assert view[name][24] == pytest.approx(_cif(time, kind, 24, cause)), name
    assert view["early_stop"][24] == pytest.approx(_cif(time, event, 24))
    # The naive estimate treats a completion as a censoring: the CIF of early stop when
    # completions are recoded as censored, and so above the competing-risks estimate.
    as_censoring = np.where(event == EVENT_STOP, EVENT_STOP, EVENT_CENSORED)
    assert view["naive_early_stop"][36] == pytest.approx(_cif(time, as_censoring, 36))
    assert view["naive_early_stop"][36] > view["early_stop"][36] + 0.01


def test_amendment_signals_match_the_twin_row_by_row(signals: an.Amendments, world: World) -> None:
    rows = world.landmark_rows(AT_12_MONTHS)
    assert len(signals.time) == len(rows) > 0
    time, event, _, _ = _followed(rows)
    assert np.array_equal(signals.time, time)
    assert np.array_equal(signals.event, event)
    expected = [signals_at(trial, day) for trial, day in rows]
    for key, _, _ in an.SIGNALS:
        mine = [row[key] for row in expected]
        assert signals.signals[key].tolist() == mine, key
        assert {0, 1} <= set(mine), key  # the registry shows the signal both ways
    assert signals.versions.tolist() == [
        sum(v["effective_date"] <= day for v in trial.versions) for trial, day in rows
    ]
    moved = signals.signals["primary_completion_later"] == 1
    assert set(signals.slip_months[moved].tolist()) == {12.0}  # from month 24 to month 36
    assert np.isnan(signals.slip_months).sum() == 1  # the trial with no first primary date


def test_the_edge_cases_of_the_state_at_the_landmark(signals: an.Amendments, world: World) -> None:
    names = [trial.name for trial, _ in world.landmark_rows(AT_12_MONTHS)]

    def shown(name: str) -> dict[str, int]:
        at = names.index(name)
        return {key: int(values[at]) for key, values in signals.signals.items()}

    # A version posted on the landmark day is in effect on that day.
    assert shown("on_landmark_day")["ever_suspended"] == 1
    # On a shared post date the higher version number wins: recruiting, target unchanged.
    tied = shown("shared_post_date")
    assert (tied["not_yet_recruiting"], tied["target_cut"]) == (0, 0)
    # Suspended at some point is not suspended now.
    assert shown("suspended_and_resumed")["ever_suspended"] == 1
    # Waiting is overdue only once the start month has ended.
    assert shown("start_month_passed")["start_overdue"] == 1
    for waiting in ("start_in_landmark_month", "start_still_ahead"):
        assert (shown(waiting)["not_yet_recruiting"], shown(waiting)["start_overdue"]) == (1, 0)
    # A primary completion day earlier in the landmark's month has not passed, by month.
    assert shown("completion_in_landmark_month")["primary_completion_overdue"] == 0
    assert shown("overdue_completed")["primary_completion_overdue"] == 1
    # The quiet rule is 6 months, inclusive.
    assert shown("quiet_for_6_months")["quiet"] == 1
    assert shown("quiet_for_5_months")["quiet"] == 0
    # The 10% thresholds are inclusive, and 5% is under them.
    assert [shown(f"target_{n}")["target_cut"] for n in (90, 95)] == [1, 0]
    assert [shown(f"target_{n}")["target_raised"] for n in (110, 105)] == [1, 0]
    # Once the count is ACTUAL it is the number enrolled, not a target.
    short = shown("closed_short")
    assert (short["enrollment_closed"], short["enrollment_short"], short["target_cut"]) == (
        1,
        1,
        -1,
    )
    assert shown("closed_on_target")["enrollment_short"] == 0
    assert shown("closed_from_the_start")["enrollment_short"] == -1
    assert shown("target_90")["enrollment_short"] == -1
    # Missing inputs make a signal unknown, never "no".
    missing = shown("missing_inputs")
    for key in ("primary_completion_later", "primary_completion_earlier", "target_cut"):
        assert missing[key] == -1, key


def test_amendment_tables(signals: an.Amendments, world: World) -> None:
    rows = world.landmark_rows(AT_12_MONTHS)
    expected = [signals_at(trial, day) for trial, day in rows]
    time, event, _, _ = _followed(rows)
    table = an.amendments_cif(signals, 24)
    assert [r["cif_with"] for r in table] == sorted((r["cif_with"] for r in table), reverse=True)
    assert {row["key"] for row in table} == {key for key, _, _ in an.SIGNALS}
    for row in table:
        flag = np.array([e[row["key"]] for e in expected])
        counts = ((flag == 1).sum(), (flag == 0).sum(), (flag == -1).sum())
        assert (row["with"], row["without"], row["outside"]) == counts, row["key"]
        with_signal = _cif(time[flag == 1], event[flag == 1], 24)
        without = _cif(time[flag == 0], event[flag == 0], 24)
        assert row["cif_with"] == pytest.approx(with_signal), row["key"]
        assert row["cif_without"] == pytest.approx(without), row["key"]
        assert row["signal"] == an.SIGNAL_LABELS[row["key"]]
    among = {row["key"]: row["among"] for row in table}
    assert among["target_cut"] == among["target_raised"] == an.COUNT_ESTIMATED
    assert among["enrollment_short"] == an.COUNT_ACTUAL
    assert among["ever_suspended"] == an.EVERYONE

    outcomes = an.amendments_by_outcome(signals)
    assert [row["group"] for row in outcomes] == [label for _, label in an.LATER_OUTCOMES]
    assert sum(row["trials"] for row in outcomes) == len(rows)
    for code, row in zip((EVENT_STOP, EVENT_COMPLETE, EVENT_CENSORED), outcomes, strict=True):
        assert row["trials"] == (event == code).sum()
        for key in ("ever_suspended", "target_cut", "enrollment_short"):
            flag = np.array([e[key] for e in expected])[event == code]
            known = flag[flag >= 0]  # the share is taken among the rows where it is known
            if known.size:
                assert row[key] == pytest.approx(known.mean()), key
            else:
                assert row[key] is None, key  # no row to take a share of
    stopped = outcomes[0]
    assert stopped["median_slip_months"] == 12
    assert stopped["median_versions"] == 2
    assert stopped["target_cut"] < 1


def _altered(world: World, tmp_path: Path, where: str) -> an.Amendments:
    """The signals after rewriting every version that matches `where`."""
    warehouse = tmp_path / "altered.duckdb"
    shutil.copy(world.sources.warehouse, warehouse)
    with duckdb.connect(str(warehouse)) as edit:
        edit.execute(
            f"""UPDATE versions SET overall_status = 'SUSPENDED',
              primary_completion_date = primary_completion_date + INTERVAL 5 YEAR,
              completion_date = completion_date + INTERVAL 5 YEAR,
              start_date = DATE '2001-01-01', enrollment_count = 1, enrollment_type = 'ACTUAL'
            WHERE {where}"""
        )
        edit.execute(
            """INSERT INTO versions BY NAME
            SELECT * REPLACE (nct_version + 100 AS nct_version,
              DATE '2026-09-01' AS effective_date, 'SUSPENDED' AS overall_status,
              5 AS enrollment_count)
            FROM versions WHERE nct_version = 0"""
        )
    with an.connect(replace(world.sources, warehouse=warehouse)) as con:
        return an.amendment_signals(con, AT_12_MONTHS, quiet_months=6)


def test_signals_ignore_everything_posted_after_the_landmark(
    signals: an.Amendments, world: World, tmp_path: Path
) -> None:
    """Point-in-time (Section 8, rule 1): rewrite every version posted after the 12-month
    landmark and add a later version to every trial. Nothing may change."""
    after = "effective_date > study_first_post_date + INTERVAL 12 MONTH"
    altered = _altered(world, tmp_path, after)
    assert np.array_equal(altered.versions, signals.versions)
    assert np.array_equal(altered.slip_months, signals.slip_months, equal_nan=True)
    for key, values in signals.signals.items():
        assert np.array_equal(altered.signals[key], values), key


def test_signals_do_change_when_a_version_on_or_before_the_landmark_changes(
    signals: an.Amendments, world: World, tmp_path: Path
) -> None:
    """The control for the test above: the same rewrite on versions posted on or before the
    landmark is seen, and so is a rewrite of the landmark day alone."""
    on_or_before = "nct_version > 0 AND effective_date <= study_first_post_date + INTERVAL 12 MONTH"
    altered = _altered(world, tmp_path, on_or_before)
    for key in ("ever_suspended", "enrollment_closed"):
        assert not np.array_equal(altered.signals[key], signals.signals[key]), key

    landmark_day = tmp_path / "day"
    landmark_day.mkdir()
    on_the_day = "effective_date = study_first_post_date + INTERVAL 12 MONTH"
    altered = _altered(world, landmark_day, on_the_day)
    names = [trial.name for trial, _ in world.landmark_rows(AT_12_MONTHS)]
    before, after = signals.signals["enrollment_closed"], altered.signals["enrollment_closed"]
    assert [names[i] for i in np.flatnonzero(before != after)] == ["on_landmark_day"]


def test_registration_by_year_is_by_month_and_marks_later_years(
    con: duckdb.DuckDBPyConnection, world: World
) -> None:
    rows = {row["year"]: row for row in an.registration_by_year(con)}
    assert sorted(rows) == [*MODELING_YEARS, 2018, COVID_YEAR]
    assert [rows[y]["descriptive"] for y in sorted(rows)] == [False] * 4 + [True, True]
    for year in (2015, 2018):
        # Years before 2018 come from the cohort as of 2018-01-01, later years from the final.
        cohort = world.landmark_rows(0) if year < BEFORE.year else world.landmark_rows(0, None)
        mine = [t for t, _ in cohort if t.year == year]
        starts = [(t, month_end(t.versions[0]["start_date"])) for t in mine]
        dated = [(t, end) for t, end in starts if end is not None]
        late = [(t.first_post - end).days for t, end in dated if end < t.first_post]
        by_day = [t for t, _ in dated if t.versions[0]["start_date_precision"] == "day"]
        row = rows[year]
        assert row["trials"] == len(mine)
        assert row["with_start_date"] == len(dated)
        assert row["share_after_start"] == pytest.approx(len(late) / len(dated))
        assert row["share_day_precision"] == pytest.approx(len(by_day) / len(dated))
        assert row["median_days_late"] == pytest.approx(np.median(late))
        assert row["share_over_a_year_late"] == pytest.approx(
            sum(days > 365 for days in late) / len(dated)
        )
        assert row["median_days_submit_to_post"] == 5
    assert 0 < rows[2015]["share_day_precision"] < 1
    assert 0 < rows[2015]["share_over_a_year_late"] < rows[2015]["share_after_start"] < 1
    assert rows[2015]["with_start_date"] < rows[2015]["trials"]


def test_post_dates_and_the_posting_lag(con: duckdb.DuckDBPyConnection, world: World) -> None:
    versions = [v for trial in world.trials for v in trial.versions]

    def lags(rows: list[dict]) -> list[int]:
        return [(v["effective_date"] - v["submitted_date"]).days for v in rows]

    by_year = an.post_dates_by_year(con)
    assert [row["year"] for row in by_year] == sorted({v["effective_date"].year for v in versions})
    for row in by_year:
        mine = [v for v in versions if v["effective_date"].year == row["year"]]
        assert row["versions"] == len(mine)
        assert row["share_estimated"] == (1.0 if row["year"] < 2017 else 0.0)
        assert row["descriptive"] == (row["year"] >= 2018)
        assert row["median_days_to_post"] == pytest.approx(np.median(lags(mine)))
        assert row["p90_days_to_post"] == pytest.approx(np.percentile(lags(mine), 90))
    assert any(row["median_days_to_post"] != row["p90_days_to_post"] for row in by_year)

    table = an.posting_lag(con)
    assert [(row["date_type"], row["is_first"]) for row in table] == [
        ("ACTUAL", True), ("ACTUAL", False), ("ESTIMATED", True), ("ESTIMATED", False),
    ]  # fmt: skip
    for row in table:
        mine = [
            v
            for v in versions
            if v["effective_date"] < BEFORE
            and v["effective_date_type"] == row["date_type"]
            and (v["nct_version"] == 0) == row["is_first"]
        ]
        assert row["versions"] == len(mine) > 0
        assert row["median_days"] == pytest.approx(np.median(lags(mine)))
        assert row["p90_days"] == pytest.approx(np.percentile(lags(mine), 90))
    first, later = table[0], table[1]
    assert first["median_days"] == 2  # a first version is posted 2 days after submission here
    assert later["median_days"] > first["median_days"]


def test_covid_period_rates(con: duckdb.DuckDBPyConnection, world: World) -> None:
    monthly = an.covid_period_monthly(con, dt.date(2017, 1, 1), dt.date(2021, 12, 1))
    months = [m.astype(object) for m in monthly["months"]]
    assert months[0] == dt.date(2017, 1, 1)
    assert months[-1] == dt.date(2021, 12, 1)
    at = {month: i for i, month in enumerate(months)}

    def posted_in(month: dt.date, trial: Trial, status: str) -> int:
        return sum(
            v["overall_status"] == status and v["effective_date"].replace(day=1) == month
            for v in trial.versions
        )

    for month, i in at.items():
        under_follow_up = sum(t.first_post <= month < outcome(t)[0] for t in world.trials)
        assert monthly["open_trials"][i] == under_follow_up, month
        for key, status in (("terminated", "TERMINATED"), ("withdrawn", "WITHDRAWN")):
            assert monthly[key][i] == sum(posted_in(month, t, status) for t in world.trials), month
        # A suspension is a SUSPENDED version that follows a version with another status.
        suspensions = sum(
            now["overall_status"] == "SUSPENDED"
            and previous["overall_status"] != "SUSPENDED"
            and now["effective_date"].replace(day=1) == month
            for t in world.trials
            for previous, now in zip(t.versions, t.versions[1:], strict=False)
        )
        assert monthly["suspended"][i] == suspensions, month

    april = at[COVID_SUSPENDED.replace(day=1)]
    assert monthly["suspended"][april] == COVID_TRIALS
    assert monthly["suspended"][april + 1] == 0  # a second SUSPENDED version is no new suspension
    assert monthly["suspended"][at[dt.date(2019, 6, 1)]] == 0  # registered while suspended
    assert monthly["suspended_per_1000"][april] == pytest.approx(
        1000 * COVID_TRIALS / monthly["open_trials"][april]
    )
    assert monthly["terminated"].sum() > 0
    assert monthly["withdrawn"].sum() > 0

    periods = [("spring", dt.date(2020, 3, 1), dt.date(2020, 5, 1))]
    (summary,) = an.covid_period_summary(monthly, periods)
    window = slice(at[dt.date(2020, 3, 1)], at[dt.date(2020, 5, 1)] + 1)
    assert summary["months"] == 3
    assert summary["suspended_per_1000"] == pytest.approx(
        monthly["suspended_per_1000"][window].mean()
    )
    assert summary["open_trials"] == pytest.approx(monthly["open_trials"][window].mean())


def test_the_state_twin_itself(world: World) -> None:
    """The twin's two rules, on the edge cases they exist for."""
    tied = world.named("shared_post_date")
    shared = tied.versions[1]["effective_date"]
    assert tied.versions[2]["effective_date"] == shared
    assert state_at(tied, shared)["nct_version"] == 2
    on_day = world.named("on_landmark_day")
    landmark = on_day.versions[1]["effective_date"]
    assert state_at(on_day, landmark)["overall_status"] == "SUSPENDED"
    assert state_at(on_day, landmark - dt.timedelta(days=1))["overall_status"] == "RECRUITING"
    assert HISTORIES["still_open"][-1][1] == "RECRUITING"
