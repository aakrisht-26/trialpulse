"""Design and amendment features on hand-built histories, where every value is known."""

import datetime as dt

import pandas as pd
import pytest

from trialpulse.cohort.rules import CohortRules
from trialpulse.config import ProjectConfig
from trialpulse.features.design import NO_PHASE, OTHER_PHASES, PHASE_LABELS, title_phase

from .conftest import D, compute, text_hash, version

T0 = D(2015, 1, 1)
L1, L2, L3 = D(2015, 7, 1), D(2016, 1, 1), D(2016, 7, 1)


def keys(trial: str, *dates: dt.date, first_index: int = 0) -> list[tuple[str, int, dt.date]]:
    return [(trial, first_index + i, day) for i, day in enumerate(dates)]


def test_the_state_at_a_landmark_is_the_latest_version_posted_on_or_before_it(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """A version posted on the landmark day is known there; one posted the next day is not.
    When two versions share a post date, the higher version number is in effect."""
    rows = [
        version("T", 0, T0, "RECRUITING", masking="NONE"),
        version("T", 1, L1, "RECRUITING", t0=T0, masking="DOUBLE"),
        version("T", 2, D(2015, 7, 2), "RECRUITING", t0=T0, masking="QUADRUPLE"),
        version("T", 3, L2, "RECRUITING", t0=T0, masking="LOWER"),
        version("T", 4, L2, "RECRUITING", t0=T0, masking="HIGHER"),
    ]
    out = compute(cfg, rules, rows, keys("T", T0, L1, L2, L3))
    assert out["masking"].tolist() == ["NONE", "DOUBLE", "HIGHER", "HIGHER"]
    assert out["n_versions"].tolist() == [1, 2, 5, 5]
    # The recent window is the 6 months up to the landmark: after the day 6 months before it.
    # At L1 that leaves out version 0 (posted exactly 6 months before) and counts version 1;
    # at L2 it leaves out version 1 and counts 2, 3 and 4.
    assert out["n_versions_recent"].tolist() == [1, 1, 3, 0]
    assert out["months_since_last_update"].tolist() == [0, 0, 0, 6]
    assert out["months_since_t0"].tolist() == [0, 6, 12, 18]
    assert out["registration_year"].tolist() == [2015] * 4


def test_dates_are_compared_by_calendar_month_whatever_their_precision(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """The registry gave dates to the month through 2016 and to the day afterwards. The same
    trial entered both ways must get the same features."""

    def history(trial: str, precision: str) -> list[dict]:
        def given(day: dt.date) -> dt.date:
            return day.replace(day=1) if precision == "month" else day

        dates = {"start_date_precision": precision, "primary_completion_date_precision": precision}
        return [
            version(trial, 0, T0, "NOT_YET_RECRUITING", start_date=given(D(2015, 3, 20)),
                    primary_completion_date=given(D(2016, 11, 5)),
                    completion_date=given(D(2017, 1, 25)), **dates),
            version(trial, 1, D(2015, 9, 15), "NOT_YET_RECRUITING", t0=T0,
                    start_date=given(D(2015, 10, 28)),
                    primary_completion_date=given(D(2017, 2, 27)),
                    completion_date=given(D(2017, 1, 25)), **dates),
        ]  # fmt: skip

    rows = history("DAY", "day") + history("MONTH", "month")
    out = compute(cfg, rules, rows, keys("DAY", T0, L1, L2) + keys("MONTH", T0, L1, L2))
    day, month = out.loc["DAY"], out.loc["MONTH"]
    compared = [c for c in out.columns if c not in ("eligibility_hash", "summary_hash")]
    pd.testing.assert_frame_equal(day[compared], month[compared])
    assert day["planned_duration_months"].tolist() == [20, 20, 16]  # March 2015 to November 2016
    assert day["registration_lag_months"].tolist() == [-2, -2, -9]  # registered before the start
    assert day["registered_after_start"].tolist() == [False, False, False]
    assert day["start_slip_months"].tolist() == [0, 0, 7]
    assert day["primary_completion_slip_months"].tolist() == [0, 0, 3]
    assert day["completion_slip_months"].tolist() == [0, 0, 0]
    # Still not recruiting. At L1 (July) the start month, March, has ended; at L2 the start
    # date has moved to October, which has ended too.
    assert day["start_overdue"].tolist() == [False, True, True]
    # "Actual or anticipated" comes from the start date itself (the registry recorded the
    # type from 2017 on only): anticipated in January, before the start month.
    assert day["start_anticipated"].tolist() == [True, False, False]
    assert day["months_since_status_verified"].tolist() == [0, 6, 4]


def test_overdue_flags_wait_for_the_month_to_end(cfg: ProjectConfig, rules: CohortRules) -> None:
    rows = [
        version("SAME", 0, T0, "NOT_YET_RECRUITING", start_date=D(2015, 7, 20),
                primary_completion_date=D(2015, 7, 25)),
        version("PAST", 0, T0, "NOT_YET_RECRUITING", start_date=D(2015, 6, 30),
                primary_completion_date=D(2015, 6, 30)),
        version("OPEN", 0, T0, "RECRUITING", start_date=D(2014, 6, 1)),
    ]  # fmt: skip
    out = compute(cfg, rules, rows, keys("SAME", L1) + keys("PAST", L1) + keys("OPEN", L1))
    at = out.xs(0, level="landmark_index")
    # The landmark's own month has not ended yet; the month before it has.
    assert at["start_overdue"].to_dict() == {"SAME": False, "PAST": True, "OPEN": False}
    overdue = at["primary_completion_overdue"]
    assert overdue["SAME"] is False or not overdue["SAME"]
    assert bool(overdue["PAST"])
    assert pd.isna(overdue["OPEN"])  # no primary completion date: not known
    assert bool(at.loc["OPEN", "registered_after_start"])
    assert at.loc["OPEN", "registration_lag_months"] == 7


MID_T0 = D(2015, 1, 20)
MID_L1, MID_L2 = D(2015, 7, 20), D(2016, 1, 20)


def test_a_landmark_in_the_middle_of_a_month_compares_months_not_days(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """Landmarks fall on the day of the month the trial was first posted. Here that is the
    20th, and every date below lies in the landmark's own month, some days before it or
    after it. Compared by day, each feature would give another answer."""
    rows = [
        # Dates a few days before the landmark of 20 July 2015, in the same month.
        version("EARLIER", 0, MID_T0, "NOT_YET_RECRUITING", start_date=D(2015, 7, 5),
                primary_completion_date=D(2015, 7, 10),
                primary_completion_date_type="ESTIMATED", status_verified_date=D(2015, 1, 1)),
        version("EARLIER", 1, D(2015, 6, 25), "NOT_YET_RECRUITING", t0=MID_T0,
                start_date=D(2015, 7, 5), primary_completion_date=D(2015, 7, 10),
                primary_completion_date_type="ESTIMATED", status_verified_date=D(2015, 6, 30)),
        # A start date a few days after the landmark, in the same month.
        version("LATER", 0, MID_T0, "NOT_YET_RECRUITING", start_date=D(2015, 7, 25),
                primary_completion_date=D(2015, 8, 1)),
        # Started 15 days before it was first posted, in the same month.
        version("SAME", 0, MID_T0, "RECRUITING", start_date=D(2015, 1, 5),
                primary_completion_date=D(2016, 3, 31), completion_date=D(2016, 3, 31)),
        version("SAME", 1, D(2015, 3, 1), "RECRUITING", t0=MID_T0, start_date=D(2014, 12, 31),
                primary_completion_date=D(2016, 4, 1), completion_date=D(2016, 4, 1)),
    ]  # fmt: skip
    wanted = keys("EARLIER", MID_L1, first_index=1) + keys("LATER", MID_L1, first_index=1)
    out = compute(cfg, rules, rows, wanted + keys("SAME", MID_T0, MID_L1))
    earlier = out.loc[("EARLIER", 1)]
    assert not earlier["start_overdue"]  # July has not ended on 20 July
    assert not earlier["primary_completion_overdue"]
    assert not earlier["start_anticipated"]  # the start month is the landmark's month
    assert earlier["months_since_last_update"] == 1  # posted in June, 25 days before
    assert earlier["months_since_status_verified"] == 1
    assert earlier["planned_duration_months"] == 0
    assert earlier["months_since_t0"] == 6
    later = out.loc[("LATER", 1)]
    assert not later["start_anticipated"]  # 25 July is not a later month than 20 July
    assert not later["start_overdue"]
    assert later["planned_duration_months"] == 1  # 25 July to 1 August: the next month
    same = out.loc["SAME"]
    assert same["registered_after_start"].tolist() == [False, True]  # January, then December
    assert same["registration_lag_months"].tolist() == [0, 1]
    assert same["start_slip_months"].tolist() == [0, -1]  # five days earlier, a month before
    assert same["primary_completion_slip_months"].tolist() == [0, 1]  # one day later: April
    assert same["completion_slip_months"].tolist() == [0, 1]
    assert same["months_since_last_update"].tolist() == [0, 4]


def test_a_primary_completion_date_that_is_actual_was_reached_not_missed(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    past = D(2015, 3, 15)
    rows = [
        version("DONE", 0, T0, "ACTIVE_NOT_RECRUITING", primary_completion_date=past,
                primary_completion_date_type="ACTUAL"),
        version("LATE", 0, T0, "RECRUITING", primary_completion_date=past,
                primary_completion_date_type="ESTIMATED"),
        version("BLANK", 0, T0, "RECRUITING", primary_completion_date=past),
        version("AHEAD", 0, T0, "RECRUITING", primary_completion_date=D(2016, 3, 15),
                primary_completion_date_type="ESTIMATED"),
        version("NONE", 0, T0, "RECRUITING"),
    ]  # fmt: skip
    names = ("DONE", "LATE", "BLANK", "AHEAD", "NONE")
    out = compute(cfg, rules, rows, [(name, 1, L1) for name in names])
    at = out.xs(1, level="landmark_index")
    overdue = at["primary_completion_overdue"]
    assert [bool(overdue[n]) for n in ("DONE", "LATE", "BLANK", "AHEAD")] == [
        False,  # reached in March, as the registry says
        True,  # still anticipated, and March has ended
        True,  # the registry does not say: a past date is overdue
        False,
    ]
    assert pd.isna(overdue["NONE"])
    reached = at["primary_completion_reached"]
    assert [bool(reached[n]) for n in ("DONE", "LATE", "AHEAD")] == [True, False, False]
    assert pd.isna(reached["BLANK"])
    assert pd.isna(reached["NONE"])


def test_each_version_is_recent_at_one_landmark_also_when_a_landmark_day_is_clamped(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """First posted on 31 August, the landmarks are 28 February, 31 August, 29 February.
    "Six months before 28 February" is 28 August, which would count the versions of 29 to
    31 August a second time. The recent window starts at the previous landmark instead."""
    t0 = D(2014, 8, 31)
    landmarks = [t0, D(2015, 2, 28), D(2015, 8, 31), D(2016, 2, 29)]
    rows = [
        version("T", 0, t0, "RECRUITING"),
        version("T", 1, D(2014, 9, 1), "RECRUITING", t0=t0),
        version("T", 2, D(2015, 2, 28), "RECRUITING", t0=t0),
        version("T", 3, D(2015, 3, 1), "RECRUITING", t0=t0),
    ]
    out = compute(cfg, rules, rows, keys("T", *landmarks)).loc["T"]
    assert out["n_versions"].tolist() == [1, 3, 4, 4]
    assert out["n_versions_recent"].tolist() == [1, 2, 1, 0]
    assert out["n_versions_recent"].sum() == len(rows)  # each version once
    assert cfg.features.recent_versions_months == cfg.landmarks.spacing_months == 6


def test_the_registrys_later_unknown_label_changes_no_feature(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """The registry labels a record UNKNOWN when nobody has verified it for two years, and
    the dataset shows that label on the record's last version, whenever that was posted. So
    the label says that no later version exists. A record must get the same features with
    the label as without it, and it must count the same in everybody's competition."""

    def history(labeled: bool) -> list[dict]:
        status = {"last_known_status": "RECRUITING"} if labeled else {}
        return [
            version("T", 0, T0, "NOT_YET_RECRUITING", completion_date=D(2015, 12, 1)),
            version("T", 1, D(2015, 4, 1), "UNKNOWN" if labeled else "RECRUITING", t0=T0,
                    completion_date=D(2015, 12, 1), **status),
            version("X", 0, T0, "RECRUITING"),
        ]  # fmt: skip

    wanted = keys("T", T0, L1, L2, L3) + keys("X", T0, L1, L2, L3)
    with_label = compute(cfg, rules, history(labeled=True), wanted)
    without = compute(cfg, rules, history(labeled=False), wanted)
    pd.testing.assert_frame_equal(with_label, without)
    assert with_label.loc["T"]["status"].tolist() == [
        "NOT_YET_RECRUITING", "RECRUITING", "RECRUITING", "RECRUITING",
    ]  # fmt: skip
    assert with_label.loc["X"]["open_interventional_trials"].tolist() == [2, 2, 2, 2]


def test_an_enrollment_count_is_a_target_until_enrollment_closes(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """Decided in the Step 5 review: the change against the first target is one feature
    while the count is ESTIMATED and another once it is ACTUAL."""
    rows = [
        version("T", 0, T0, "RECRUITING", enrollment_count=100),
        version("T", 1, D(2015, 5, 1), "RECRUITING", t0=T0, enrollment_count=80),
        version("T", 2, D(2015, 11, 1), "ACTIVE_NOT_RECRUITING", t0=T0, enrollment_count=70,
                enrollment_type="ACTUAL"),
        version("CLOSED", 0, T0, "ACTIVE_NOT_RECRUITING", enrollment_count=40,
                enrollment_type="ACTUAL"),
        version("BLANK", 0, T0, "RECRUITING", enrollment_count=None, enrollment_type=None),
        version("BLANK", 1, D(2015, 5, 1), "RECRUITING", t0=T0, enrollment_count=50),
    ]  # fmt: skip
    out = compute(
        cfg, rules, rows, keys("T", T0, L1, L2) + keys("CLOSED", T0) + keys("BLANK", T0, L1)
    )
    trial = out.loc["T"]
    assert trial["enrollment_closed"].tolist() == [False, False, True]
    assert trial["enrollment_target_ratio"].tolist()[:2] == [1.0, 0.8]
    assert pd.isna(trial["enrollment_target_ratio"].iloc[2])
    assert trial["enrolled_to_first_target_ratio"].isna().tolist() == [True, True, False]
    assert trial["enrolled_to_first_target_ratio"].iloc[2] == pytest.approx(0.7)
    assert trial["enrollment_count"].tolist() == [100.0, 80.0, 70.0]
    closed = out.loc[("CLOSED", 0)]
    assert bool(closed["enrollment_closed"])
    # Registered with enrollment already closed: there is no first target to compare with.
    assert pd.isna(closed["enrollment_target_ratio"])
    assert pd.isna(closed["enrolled_to_first_target_ratio"])
    blank = out.loc["BLANK"]
    assert pd.isna(blank["enrollment_closed"].iloc[0])
    assert not bool(blank["enrollment_closed"].iloc[1])
    assert blank["enrollment_target_ratio"].isna().all()  # the first version gave no target


def test_status_history(cfg: ProjectConfig, rules: CohortRules) -> None:
    rows = [
        version("T", 0, T0, "RECRUITING"),
        version("T", 1, D(2015, 4, 1), "SUSPENDED", t0=T0),
        version("T", 2, D(2015, 10, 1), "UNKNOWN", t0=T0, last_known_status="RECRUITING"),
    ]
    out = compute(cfg, rules, rows, keys("T", T0, L1, L2)).loc["T"]
    assert out["status"].tolist() == ["RECRUITING", "SUSPENDED", "RECRUITING"]  # ADR 0014
    assert out["ever_suspended"].tolist() == [False, True, True]
    assert out["currently_suspended"].tolist() == [False, True, False]


def test_design_fields_titles_and_interventions_come_from_the_state(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    plain, phase_2, phase_3 = "A Study in Adults", "Phase 2 Study", "Official Phase III Trial"
    texts = {text_hash(t): t for t in (plain, phase_2, phase_3)}
    rows = [
        version("T", 0, T0, "RECRUITING", allocation="RANDOMIZED", sex="ALL",
                healthy_volunteers=False, minimum_age_years=18.0, maximum_age_years=65.0,
                brief_title_hash=text_hash(phase_2), official_title_hash=text_hash(plain),
                start_date=D(2014, 12, 1), start_date_type="ACTUAL",
                intervention_model="PARALLEL", primary_purpose="TREATMENT"),
        version("T", 1, D(2015, 3, 1), "RECRUITING", t0=T0, allocation="NON_RANDOMIZED",
                sex="FEMALE", healthy_volunteers=True, minimum_age_years=None,
                brief_title_hash=text_hash(phase_2), official_title_hash=text_hash(phase_3)),
        version("T", 2, D(2015, 9, 1), "RECRUITING", t0=T0,
                brief_title_hash=text_hash(plain), official_title_hash=None),
    ]  # fmt: skip
    interventions = {("T", 0): ["DRUG", "DRUG", "DEVICE"], ("T", 1): ["BEHAVIORAL"]}
    out = compute(cfg, rules, rows, keys("T", T0, L1, L2), texts, interventions).loc["T"]
    assert out["allocation"].tolist() == ["RANDOMIZED", "NON_RANDOMIZED", None]
    assert out["sex"].tolist() == ["ALL", "FEMALE", None]
    assert out["healthy_volunteers"].tolist()[:2] == [False, True]
    assert out["eligibility_age_span_years"].tolist()[0] == 47.0
    assert out["eligibility_age_span_years"].isna().tolist() == [False, True, True]
    assert not out["start_anticipated"].iloc[0]  # started in December 2014
    assert out["start_anticipated"].isna().tolist() == [False, True, True]  # no start date
    # The official title wins when it states a phase; otherwise the brief title is read.
    assert out["title_phase"].tolist() == ["PHASE2", "PHASE3", NO_PHASE]
    assert out["n_interventions"].tolist() == [3, 1, 0]
    assert out["n_intervention_types"].tolist() == [2, 1, 0]
    assert out["intervention_drug"].tolist() == [True, False, False]
    assert out["intervention_device"].tolist() == [True, False, False]
    assert out["intervention_behavioral"].tolist() == [False, True, False]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("A Phase 2 Study of X", "PHASE2"),
        ("Phase II Trial", "PHASE2"),
        ("phase IIb dose finding", "PHASE2"),
        ("A Phase 1/2 Study", "PHASE1_PHASE2"),
        ("Phase I-II study of Y", "PHASE1_PHASE2"),
        ("A Phase 1b/2a Trial", "PHASE1_PHASE2"),
        ("Phase II/III Randomized Trial", "PHASE2_PHASE3"),
        ("Phase 2 and Phase 3 study", "PHASE2_PHASE3"),
        ("A PHASE III, RANDOMIZED TRIAL", "PHASE3"),
        ("Phase-3 study", "PHASE3"),
        ("Post-marketing Phase IV Surveillance", "PHASE4"),
        ("Early Phase 0 Microdosing", "EARLY_PHASE1"),
        ("Phase 1 study, then a phase 3 extension", OTHER_PHASES),
        ("Phase in Patients With Asthma", NO_PHASE),
        ("Biphasic Insulin in Adults", NO_PHASE),
        ("Two-phase Sampling Design", NO_PHASE),
        ("", NO_PHASE),
        (None, NO_PHASE),
    ],
)
def test_the_phase_a_title_states(title: str | None, expected: str) -> None:
    assert title_phase(title) == expected
    assert expected in PHASE_LABELS
