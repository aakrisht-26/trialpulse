"""The cause-specific Cox models at registration (CLAUDE.md Section 9), on simulated rows
whose hazards are known."""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from trialpulse import tracking
from trialpulse.config import ProjectConfig, load_project_config
from trialpulse.models import cox

from .conftest import simulated_rows

# The simulation of conftest.simulated_rows: the early-stop hazard is multiplied by e for a
# trial not yet recruiting and by e^0.7 for a company; the completion hazard by e^-0.02 per
# planned month.
NOT_YET_RECRUITING = float(np.exp(1.0))
COMPANY = float(np.exp(0.7))
PER_PLANNED_YEAR = float(np.exp(-0.24))


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    rows, time, event = simulated_rows(20_000, seed=11)
    return cox.analyze(dict(rows.features), time, event)


def _ratio(result: dict[str, Any], cause: str, column: str) -> dict[str, float]:
    ratios: dict[str, float] = result["causes"][cause]["ratios"][column]
    return ratios


def test_the_design_compares_each_category_with_its_reference() -> None:
    rows, _, _ = simulated_rows(3_000, seed=12)
    design, terms = cox.covariates(dict(rows.features))
    by_column = {t.column: t for t in terms}
    assert list(design.columns) == [t.column for t in terms]
    assert design.notna().all().all()
    # OTHER is the reference class: the two other classes of the simulation get a column.
    assert by_column["sponsor_class_industry"].reference == "OTHER"
    assert "sponsor_class_other" not in by_column
    assert (
        design["sponsor_class_industry"].sum()
        == (rows.features["sponsor_class"] == "INDUSTRY").sum()
    )
    # A row whose class is missing belongs to "any smaller class".
    missing = sum(v is None for v in rows.features["sponsor_class"])
    assert design["sponsor_class_rest"].sum() == missing > 0
    assert by_column["status_not_yet_recruiting"].reference == "RECRUITING"
    assert by_column["masked"].reference == "open label"
    masked = np.isin(rows.features["masking"], ["DOUBLE", "QUADRUPLE"])
    assert design["masked"].sum() == masked.sum()
    assert design["masking_not_given"].sum() == sum(v is None for v in rows.features["masking"])
    # Numbers: per doubling, per year, per 10 points, with the median for a missing value.
    enrollment = rows.features["enrollment_count"]
    assert design["enrollment_log2"].to_numpy() == pytest.approx(np.log2(enrollment + 1.0))
    assert by_column["enrollment_log2"].reference == ""
    assert design["planned_years"].max() <= 15.0
    assert design["sponsor_stop_rate_10"].to_numpy() == pytest.approx(
        rows.features["sponsor_stop_rate"] * 10.0
    )
    assert design["registration_year"].min() == 0.0
    assert set(design["children_eligible"].unique()) == {0.0, 1.0}


def test_the_models_recover_the_hazard_ratios_of_the_simulation(result: dict[str, Any]) -> None:
    stop = "early stop"
    waiting = _ratio(result, stop, "status_not_yet_recruiting")
    assert waiting["hazard_ratio"] == pytest.approx(NOT_YET_RECRUITING, rel=0.12)
    assert waiting["ci_low"] < NOT_YET_RECRUITING < waiting["ci_high"]
    assert waiting["p"] < 0.001
    company = _ratio(result, stop, "sponsor_class_industry")
    assert company["hazard_ratio"] == pytest.approx(COMPANY, rel=0.12)
    # A smaller target stops more often: the ratio per doubling is below 1.
    assert _ratio(result, stop, "enrollment_log2")["ci_high"] < 1.0
    # The planned duration acts on completion, not on stopping.
    planned = _ratio(result, "completion", "planned_years")
    assert planned["hazard_ratio"] == pytest.approx(PER_PLANNED_YEAR, rel=0.05)
    assert planned["ci_low"] < PER_PLANNED_YEAR < planned["ci_high"]
    unrelated = _ratio(result, stop, "planned_years")
    assert unrelated["ci_low"] < 1.0 < unrelated["ci_high"]
    # A characteristic with no effect in the simulation: its interval holds 1.
    masked = _ratio(result, stop, "masked")
    assert masked["ci_low"] < 1.0 < masked["ci_high"]


def test_each_cause_treats_the_other_end_as_censoring(result: dict[str, Any]) -> None:
    rows, time, event = simulated_rows(20_000, seed=11)
    assert result["rows"] == int((time > 0).sum())
    assert result["causes"]["early stop"]["events"] == int(((event == 1) & (time > 0)).sum())
    assert result["causes"]["completion"]["events"] == int(((event == 2) & (time > 0)).sum())
    assert result["censored"] == int(((event == 0) & (time > 0)).sum())
    for cause in ("early stop", "completion"):
        info = result["causes"][cause]
        assert info["events_first_year"] + info["events_later"] == info["events"]
        assert 0.5 < info["concordance"] < 1.0
    assert len(rows) == 20_000


def test_proportional_hazards_are_checked_two_ways(result: dict[str, Any]) -> None:
    """The simulated hazards are proportional: the ratio of the first year and the ratio of
    the time after it agree with the single one, and every covariate carries the
    Schoenfeld-residual test."""
    waiting = _ratio(result, "early stop", "status_not_yet_recruiting")
    assert waiting["hazard_ratio_first_year"] == pytest.approx(NOT_YET_RECRUITING, rel=0.25)
    assert waiting["hazard_ratio_later"] == pytest.approx(NOT_YET_RECRUITING, rel=0.25)
    for cause in result["causes"].values():
        for values in cause["ratios"].values():
            assert values["schoenfeld_statistic"] >= 0.0
            assert 0.0 <= values["schoenfeld_p"] <= 1.0


@pytest.mark.parametrize("whole_days", [False, True])
def test_the_schoenfeld_test_agrees_with_lifelines(whole_days: bool) -> None:
    """The test is computed in this project because lifelines' own is too slow for every
    trial. On times without ties the two give the same statistic; with whole days, where
    lifelines handles tied events another way (Efron, and ranks in row order), they stay
    close."""
    from lifelines import CoxPHFitter
    from lifelines.statistics import proportional_hazard_test

    rng = np.random.default_rng(21)
    n = 1_500
    x = np.column_stack([rng.normal(size=n), rng.random(n) < 0.4, rng.normal(size=n)]).astype(float)
    hazard = 0.002 * np.exp(0.6 * x[:, 0] + 0.9 * x[:, 1])
    raw = rng.exponential(1.0 / hazard)
    # The second covariate stops mattering after day 150: its hazards are not proportional.
    late = 150.0 + rng.exponential(1.0 / (0.002 * np.exp(0.6 * x[:, 0])))
    raw = np.where((x[:, 1] > 0) & (raw > 150.0), late, raw)
    censor = rng.exponential(900.0, n)
    time = np.minimum(raw, censor)
    event = raw <= censor
    if whole_days:
        time = np.floor(time) + 1.0
    import pandas as pd

    data = pd.DataFrame(x, columns=["a", "b", "c"])
    data["time"], data["event"] = time, event.astype(int)
    fitter = CoxPHFitter().fit(data, duration_col="time", event_col="event")
    theirs = proportional_hazard_test(fitter, data, time_transform="rank").summary
    names = ["a", "b", "c"]
    statistic, p_value = cox.schoenfeld_test(
        x, time, event, fitter.params_[names].to_numpy(),
        fitter.variance_matrix_.loc[names, names].to_numpy(),
    )  # fmt: skip
    tolerance = 0.08 if whole_days else 1e-6
    assert statistic == pytest.approx(theirs.loc[names, "test_statistic"].to_numpy(), rel=tolerance)
    assert p_value == pytest.approx(theirs.loc[names, "p"].to_numpy(), rel=max(tolerance * 4, 1e-5))
    assert p_value[1] < 0.001  # the covariate whose effect ends is found
    assert p_value[2] > 0.01  # the one with no effect at all is not


def test_a_ratio_that_changes_over_time_shows_in_the_two_windows() -> None:
    """Companies stop early at three times the rate in the first year and at the usual rate
    afterwards: the single ratio is an average, the two windows show the change, and the
    Schoenfeld test rejects."""
    rng = np.random.default_rng(13)
    rows, _, _ = simulated_rows(12_000, seed=13, end=None)
    company = rows.features["sponsor_class"] == "INDUSTRY"
    base = 0.0006
    early = rng.exponential(1.0 / (base * np.where(company, 3.0, 1.0)))
    late = 365.0 + rng.exponential(1.0 / base, len(company))
    stop = np.where(early < 365.0, early, late)
    complete = rng.exponential(1.0 / 0.0008, len(company))
    time = np.floor(np.minimum(stop, complete)) + 1.0
    event = np.where(stop < complete, 1, 2).astype(np.int64)
    found = cox.analyze(dict(rows.features), time, event)
    ratio = found["causes"]["early stop"]["ratios"]["sponsor_class_industry"]
    assert ratio["hazard_ratio_first_year"] == pytest.approx(3.0, rel=0.2)
    assert ratio["hazard_ratio_later"] == pytest.approx(1.0, rel=0.2)
    assert ratio["hazard_ratio_later"] < ratio["hazard_ratio"] < ratio["hazard_ratio_first_year"]
    assert ratio["schoenfeld_p"] < 0.001


def test_rows_without_follow_up_are_left_out() -> None:
    rows, time, event = simulated_rows(3_000, seed=14)
    time = time.copy()
    time[:25] = 0.0
    found = cox.analyze(dict(rows.features), time, event)
    assert found["rows_without_follow_up"] == 25
    assert found["rows"] == 3_000 - 25


def test_the_command_writes_the_report_for_the_later_development_origin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tracked_runs: list[Any],
) -> None:
    cfg = load_project_config()
    asked: list[dt.date] = []

    def load_rows(cfg: ProjectConfig, origin: dt.date, features_dir: Path, cohort_dir: Path) -> Any:
        asked.append(origin)
        rows, time, event = simulated_rows(4_000, seed=15)
        return dict(rows.features), time, event

    monkeypatch.setattr(cox, "load_rows", load_rows)
    doc, out = tmp_path / "cox_report.md", tmp_path / "cox.json"
    args = ["--doc", str(doc), "--out", str(out)]
    assert cox.main(args) == 0
    dev = [o.date for o in cfg.walk_forward.origins if o.role == "dev"]
    assert asked == [max(dev)]  # the later development origin, and no locked one
    text = doc.read_text(encoding="utf-8")
    assert "Research demo. Not medical advice. Not for patient decision-making." in text
    assert "ClinicalTrials.gov" in text
    assert "Data as of" in text
    assert "For interpretation only" in text
    assert "## Hazard of early stop" in text
    assert "## Hazard of completion" in text
    assert "—" not in text
    assert "\r" not in text
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["origin"] == max(dev).isoformat()
    assert saved["tracking"]["run_name"] == "cox-l0"
    assert [run["name"] for run in tracked_runs] == ["cox-l0"]
    logged = tracked_runs[0]
    assert logged["local"] is False
    assert logged["state"] == tracking.git_state()  # read by the command, handed to the store
    assert logged["artifacts"] == [str(out), str(doc)]
    assert logged["metrics"]["early_stop_events"] == saved["causes"]["early stop"]["events"]
    for term in saved["terms"]:
        assert term["label"] in text
    # A second run rewrites nothing, and --no-track logs nothing.
    assert cox.main([*args, "--no-track"]) == 0
    assert "up to date" in capsys.readouterr().out
    assert len(tracked_runs) == 1
    # --local-tracking reaches the store too, with the state read when the command started.
    started = {"git_commit": "started-here", "git_dirty": "yes"}
    monkeypatch.setattr(tracking, "git_state", lambda: dict(started))
    assert cox.main([*args, "--local-tracking"]) == 0
    assert (tracked_runs[-1]["local"], tracked_runs[-1]["state"]) == (True, started)


def test_a_tracking_failure_keeps_the_report_and_the_results_and_fails_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def load_rows(cfg: ProjectConfig, origin: dt.date, features_dir: Path, cohort_dir: Path) -> Any:
        rows, time, event = simulated_rows(4_000, seed=15)
        return dict(rows.features), time, event

    def refused(*args: object, **kwargs: object) -> dict[str, str]:
        raise tracking.TrackingError("the server answered 404")

    monkeypatch.setattr(cox, "load_rows", load_rows)
    monkeypatch.setattr(tracking, "log_run", refused)
    doc, out = tmp_path / "cox_report.md", tmp_path / "cox.json"
    assert cox.main(["--doc", str(doc), "--out", str(out)]) == tracking.FAILED_EXIT_CODE
    captured = capsys.readouterr()
    assert "not logged to MLflow (the server answered 404)" in captured.err
    assert "## Hazard of early stop" in doc.read_text(encoding="utf-8")
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["causes"]["early stop"]["events"] > 0
    assert "tracking" not in saved


# The design, value by value --------------------------------------------------------------------


def _hand_features(n: int = 2_000) -> dict[str, Any]:
    """Features with a known value in every row, for the rules of `covariates`."""
    rng = np.random.default_rng(31)
    rows, _, _ = simulated_rows(n, seed=31)
    features = dict(rows.features)
    # Sponsor classes: two large ones beside OTHER, and two under the 500-row threshold.
    features["sponsor_class"] = np.array(
        ["OTHER"] * 700 + ["INDUSTRY"] * 600 + ["NIH"] * 500 + ["FED"] * 120 + ["NETWORK"] * 80,
        dtype=object,
    )
    features["title_phase"] = np.array(
        ["EARLY_PHASE1", "PHASE1", "PHASE1_PHASE2", "PHASE2", "PHASE2_PHASE3", "PHASE3", "PHASE4",
         "NONE", None, "NOT_A_PHASE"] * (n // 10), dtype=object,
    )  # fmt: skip
    features["allocation"] = np.array(
        ["RANDOMIZED", "NON_RANDOMIZED", "NA", None] * (n // 4), dtype=object
    )
    features["status"] = np.array(
        ["RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION", "SUSPENDED"] * (n // 4),
        dtype=object,
    )
    features["minimum_age_years"] = np.array([18.0, 17.0, 0.0, np.nan, 65.0] * (n // 5))
    duration = rng.uniform(6.0, 60.0, n)
    duration[:100] = np.nan  # 5% without a planned duration
    duration[100:110] = -6.0  # a completion date before the start date
    duration[110:130] = 400.0  # more than 33 years
    features["planned_duration_months"] = duration
    enrollment = rng.integers(10, 1000, n).astype(np.float64)
    enrollment[:400] = np.nan  # 20% without a target
    features["enrollment_count"] = enrollment
    return features


def test_the_design_follows_its_rules_row_by_row() -> None:
    features = _hand_features()
    design, terms = cox.covariates(features)
    labels = {t.column: t.label for t in terms}
    # Classes with at least 500 rows get a column; the smaller ones share one.
    assert labels["sponsor_class_industry"] == "sponsor class INDUSTRY"
    assert "sponsor_class_nih" in design
    assert "sponsor_class_fed" not in design
    assert "sponsor_class_network" not in design
    assert design["sponsor_class_rest"].sum() == 120 + 80
    assert cox.MIN_LEVEL_ROWS == 500
    # A combined phase counts as its later phase; anything else is "no phase in the title".
    phase = features["title_phase"]
    for column, levels in (
        ("title_phase1", ("EARLY_PHASE1", "PHASE1")),
        ("title_phase2", ("PHASE1_PHASE2", "PHASE2")),
        ("title_phase3", ("PHASE2_PHASE3", "PHASE3")),
        ("title_phase4", ("PHASE4",)),
    ):
        assert design[column].to_numpy().tolist() == np.isin(phase, levels).astype(float).tolist()
    # Allocation: the registry's "not applicable" and a missing value share one column.
    assert labels["allocation_rest"] == "allocation not applicable or not given"
    assert design["allocation_rest"].sum() == len(design) // 2
    assert design["allocation_non_randomized"].sum() == len(design) // 4
    # Status: RECRUITING is the reference although it is not the most common level.
    reference = {t.column: t.reference for t in terms}
    assert reference["status_not_yet_recruiting"] == "RECRUITING"
    assert design["status_rest"].sum() == len(design) // 2
    # No minimum age given means no lower age limit: open to participants under 18.
    assert design["children_eligible"].to_numpy()[:5].tolist() == [0.0, 1.0, 1.0, 1.0, 0.0]
    # A number: a missing value takes the median of the others and gets a "not given" flag
    # when more than 1% are missing; the planned duration is held between 0 and 15 years.
    years = design["planned_years"].to_numpy()
    given = features["planned_duration_months"][130:] / 12.0
    assert years[130:] == pytest.approx(given)
    assert (years[100:110] == 0.0).all()
    assert (years[110:130] == 15.0).all()
    clipped = np.clip(features["planned_duration_months"][100:] / 12.0, 0.0, 15.0)
    assert years[:100] == pytest.approx(np.full(100, np.median(clipped)))
    assert design["planned_years_not_given"].to_numpy().tolist() == [1.0] * 100 + [0.0] * 1900
    assert labels["planned_years_not_given"] == "planned duration not given"
    doublings = np.log2(features["enrollment_count"] + 1.0)
    assert design["enrollment_log2"].to_numpy()[400:] == pytest.approx(doublings[400:])
    assert design["enrollment_log2"].to_numpy()[:400] == pytest.approx(
        np.full(400, np.median(doublings[400:]))
    )
    assert design["enrollment_log2_not_given"].sum() == 400
    # A number that is never missing gets no flag; a column without variation is dropped.
    assert "registration_year_not_given" not in design
    constant = dict(features)
    constant["healthy_volunteers"] = np.zeros(len(design))
    assert "healthy_volunteers" not in cox.covariates(constant)[0]


def _reference_statistic(
    x: np.ndarray, time: np.ndarray, event: np.ndarray, coef: np.ndarray, variance: np.ndarray
) -> np.ndarray:
    """The Schoenfeld-residual statistic, one event at a time: the trials at risk on the day
    of an event are all those whose follow-up ends on that day or later, and events of one
    day share the mean of their ranks."""
    risk = np.exp(x @ coef)
    events = np.flatnonzero(event)
    events = events[np.argsort(time[events], kind="stable")]
    residual = np.empty((len(events), x.shape[1]))
    for i, row in enumerate(events):
        at_risk = time >= time[row]
        residual[i] = x[row] - (risk[at_risk] @ x[at_risk]) / risk[at_risk].sum()
    days = time[events]
    position = np.arange(1, len(events) + 1, dtype=float)
    rank = np.array([position[days == d].mean() for d in days])
    scaled = len(events) * residual @ variance
    centered = rank - rank.mean()
    return np.asarray(
        (centered @ scaled) ** 2 / (len(events) * np.diag(variance) * (centered**2).sum())
    )


def test_the_schoenfeld_test_with_tied_days_matches_a_computation_event_by_event() -> None:
    """Follow-up is in whole days, so many trials end on the same day. They share one risk
    set and one rank, and the order of the rows cannot move the statistic."""
    rng = np.random.default_rng(22)
    n = 600
    x = np.column_stack([rng.normal(size=n), rng.random(n) < 0.5]).astype(float)
    raw = rng.exponential(1.0 / (0.05 * np.exp(0.5 * x[:, 0] + 0.8 * x[:, 1])))
    censor = rng.exponential(40.0, n)
    time = np.floor(np.minimum(raw, censor)) + 1.0  # a few dozen distinct days
    event = raw <= censor
    assert len(np.unique(time[event])) < event.sum() / 3  # heavily tied
    coef = np.array([0.45, 0.7])
    variance = np.array([[0.004, 0.0005], [0.0005, 0.012]])
    statistic, p_value = cox.schoenfeld_test(x, time, event, coef, variance)
    assert statistic == pytest.approx(_reference_statistic(x, time, event, coef, variance))
    assert ((p_value >= 0) & (p_value <= 1)).all()
    # The first covariate is sorted within each day: with ranks in row order the statistic
    # would follow that order. With one rank per day it does not move.
    order = np.lexsort((x[:, 0], time))
    again, _ = cox.schoenfeld_test(x[order], time[order], event[order], coef, variance)
    assert again == pytest.approx(statistic, rel=1e-9)
    back, _ = cox.schoenfeld_test(
        x[order[::-1]], time[order[::-1]], event[order[::-1]], coef, variance
    )
    assert back == pytest.approx(statistic, rel=1e-9)


def test_the_report_puts_every_number_in_its_own_column() -> None:
    cfg = load_project_config()
    ratio = {"hazard_ratio": 1.64, "ci_low": 1.56, "ci_high": 1.73, "p": 0.0004,
             "hazard_ratio_first_year": 3.1, "hazard_ratio_later": 1.31,
             "schoenfeld_statistic": 274.84, "schoenfeld_p": 0.0321}  # fmt: skip
    other = {"hazard_ratio": 0.9, "ci_low": 0.82, "ci_high": 1.0, "p": 0.041,
             "hazard_ratio_first_year": 0.88, "hazard_ratio_later": 0.93,
             "schoenfeld_statistic": 0.96, "schoenfeld_p": 0.325}  # fmt: skip
    result = {
        "rows": 117_742,
        "censored": 57_912,
        "terms": [
            {"column": "a", "label": "sponsor class INDUSTRY", "reference": "OTHER"},
            {"column": "b", "label": "planned duration, per year", "reference": ""},
        ],
        "causes": {
            "early stop": {"rows": 117_742, "events": 9_592, "events_first_year": 2_118,
                           "events_later": 7_474, "concordance": 0.6507,
                           "ratios": {"a": ratio, "b": other}},
            "completion": {"rows": 117_742, "events": 50_238, "events_first_year": 13_412,
                           "events_later": 36_826, "concordance": 0.7841,
                           "ratios": {"a": other, "b": ratio}},
        },
    }  # fmt: skip
    text = cox.document(cfg, dt.date(2017, 1, 1), result)
    stop, complete = text.split("## Hazard of early stop")[1].split("## Hazard of completion")
    assert (
        "9,592 trials reached this end among 117,742: 2,118 in the first year after "
        "registration and 7,474 later. Concordance on the same rows: 0.651"
    ) in stop
    assert "50,238 trials reached this end among 117,742: 13,412 in the first year" in complete
    assert (
        "| Characteristic | Compared with | Hazard ratio | 95% interval | p | Hazard ratio, "
        "first year | Hazard ratio, later | Schoenfeld statistic | Schoenfeld p |"
    ) in stop
    row = (
        "| sponsor class INDUSTRY | OTHER | 1.64 | 1.56 to 1.73 | <0.001 | 3.10 | 1.31 | 274.8 "
        "| 0.032 |"
    )
    assert row in stop
    assert row not in complete
    assert "| sponsor class INDUSTRY | OTHER | 0.90 | 0.82 to 1.00 | 0.041 |" in complete
    assert (
        "| planned duration, per year | (a number) | 0.90 | 0.82 to 1.00 | 0.041 | 0.88 | 0.93 "
        "| 1.0 | 0.325 |"
    ) in stop
    assert "the 117,742 interventional trials" in text
    assert "57,912 of them had reached neither end" in text
    assert "cohort as of 2017-01-01" in text
    # The wording stays on the side of association.
    assert "an association in registry records, not a cause" in text
    assert "raises the share" not in text
