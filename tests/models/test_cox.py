"""The cause-specific Cox models at registration (CLAUDE.md Section 9), on simulated rows
whose hazards are known."""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

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
    lifelines handles tied events another way (Efron), they stay close."""
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
    assert logged["artifacts"] == [str(out)]
    assert logged["metrics"]["early_stop_events"] == saved["causes"]["early stop"]["events"]
    for term in saved["terms"]:
        assert term["label"] in text
    # A second run rewrites nothing, and --no-track logs nothing.
    assert cox.main([*args, "--no-track"]) == 0
    assert "up to date" in capsys.readouterr().out
    assert len(tracked_runs) == 1
