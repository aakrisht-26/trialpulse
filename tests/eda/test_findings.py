"""The findings and their checks, on hand-written results.

`supporting()` holds numbers that support every finding. Each case in BROKEN changes one
of them so that one sentence would become untrue, and the finding must then be refused.
"""

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import numpy as np
import pytest

from trialpulse.eda import analysis as an
from trialpulse.eda.findings import StaleFindingError, findings
from trialpulse.eda.results import (
    COVID_AFTER,
    COVID_BASELINE,
    COVID_SHOCK,
    STATE_MONTHS,
    Results,
)

CITES = re.compile(r"\((Figure|Figures|Table) \d")


def _cif_rows(groups: dict[Any, tuple[float, ...]], months: tuple[int, ...]) -> an.Rows:
    return [
        {"group": group, "trials": 1000, "early_stops": 100}
        | {f"cif_{m}m": value for m, value in zip(months, values, strict=True)}
        for group, values in groups.items()
    ]


def _signal(key: str, with_signal: float, without: float) -> dict[str, Any]:
    return {
        "key": key,
        "signal": key,
        "with": 500,
        "without": 5000,
        "unknown": 0,
        "cif_with": with_signal,
        "cif_without": without,
        "ratio": with_signal / without,
    }


def _post(year: int, share: float, median: float = 2, p90: float = 5) -> dict[str, Any]:
    return {
        "year": year,
        "versions": 1000,
        "share_estimated": share,
        "median_days_to_post": median,
        "p90_days_to_post": p90,
        "descriptive": year >= 2018,
    }


def _period(name: str, suspended: float, terminated: float, withdrawn: float) -> dict[str, Any]:
    return {
        "period": name,
        "months": 3,
        "open_trials": 60000.0,
        "suspended_per_1000": suspended,
        "terminated_per_1000": terminated,
        "withdrawn_per_1000": withdrawn,
    }


def supporting() -> Results:
    month = np.arange(STATE_MONTHS + 1, dtype=np.float64)
    terminated = 0.10 * month / STATE_MONTHS
    withdrawn = 0.05 * (1 - np.exp(-month / 20))
    completed = 0.78 * month / STATE_MONTHS
    covid_months = np.arange("2017-01", "2024-01", dtype="datetime64[M]").astype("datetime64[D]")
    suspended_rate = np.full(len(covid_months), 0.6)
    suspended_rate[covid_months == np.datetime64("2020-04-01")] = 10.7
    reg = an.Registrations(
        time=np.array([400.0]),
        event=np.array([1]),
        kind=np.array([1]),
        sponsor_class=np.array(["INDUSTRY"]),
        sponsor_group=np.array(["INDUSTRY"]),
        year=np.array([2008]),
        timing=np.array([an.PROSPECTIVE]),
        phase=np.array([an.NO_PHASE]),
    )
    sponsor = {
        "INDUSTRY": (0.035, 0.070, 0.094, 0.118),
        "OTHER": (0.014, 0.042, 0.072, 0.117),
        an.ALL: (0.020, 0.050, 0.078, 0.116),
    }
    years = {2008: (0.028, 0.057)} | {y: (0.019, 0.049 + (y % 3) / 1000) for y in range(2009, 2018)}
    timing = {
        an.PROSPECTIVE: (0.023, 0.059, 0.090, 0.132),
        an.LATE_WITHIN_YEAR: (0.016, 0.040, 0.064, 0.096),
        an.LATE_OVER_YEAR: (0.012, 0.030, 0.046, 0.072),
    }
    by_landmark = {0: (0.020, 0.050), 1: (0.029, 0.061), 2: (0.036, 0.068)}
    by_landmark |= dict.fromkeys((3, 4, 5, 6), (0.040, 0.071))
    registration_years = [
        {"year": 2008, "share_after_start": 0.54, "descriptive": False},
        {"year": 2012, "share_after_start": 0.42, "descriptive": False},
        {"year": 2017, "share_after_start": 0.385, "descriptive": False},
        {"year": 2019, "share_after_start": 0.60, "descriptive": True},
    ]
    outcome = [
        {"group": an.LATER_OUTCOMES[0][1], "trials": 100, "primary_completion_later": 0.229},
        {"group": an.LATER_OUTCOMES[1][1], "trials": 900, "primary_completion_later": 0.230},
    ]
    return Results(
        cutoff=dt.date(2026, 9, 25),
        before=dt.date(2018, 1, 1),
        stamp="hand-written",
        snapshot="2026-09-22",
        landmark_rows=10,
        landmark_trials=5,
        horizons=(12, 24),
        spacing_months=6,
        reg=reg,
        sponsor=_cif_rows(sponsor, (12, 24, 36, 60)),
        sponsor_curves={},
        year=_cif_rows(years | {an.ALL: (0.020, 0.050)}, (12, 24)),
        by_landmark=_cif_rows(by_landmark, (12, 24)),
        phase=[],
        states={
            "months": month,
            "terminated": terminated,
            "withdrawn": withdrawn,
            "early_stop": terminated + withdrawn,
            "completed": completed,
            "open": 1 - terminated - withdrawn - completed,
            "naive_early_stop": 2.4 * (terminated + withdrawn),
        },
        amendment_rows=5500,
        amendment_overall=0.068,
        amendments_outcome=outcome,
        amendments_cif=[
            _signal("ever_suspended", 0.242, 0.066),
            _signal("not_yet_recruiting", 0.129, 0.059),
            _signal("primary_completion_later", 0.082, 0.064),
            _signal("enrollment_cut", 0.068, 0.068),
        ],
        registration_years=registration_years,
        timing=_cif_rows(timing, (12, 24, 36, 60)),
        post_dates=[
            _post(2015, 1.0, 1, 4),
            _post(2016, 1.0, 1, 4),
            _post(2017, 0.119),
            _post(2018, 0.0),
            _post(2019, 0.0, 2, 8),
        ],
        covid={"months": covid_months, "suspended_per_1000": suspended_rate},
        covid_summary=[
            _period(COVID_BASELINE, 0.65, 1.94, 1.11),
            _period(COVID_SHOCK, 6.46, 2.03, 1.19),
            _period(COVID_AFTER, 0.93, 2.05, 1.27),
            _period("2022 to 2023", 0.73, 2.29, 1.32),
        ],
    )


def _rows(rows: an.Rows, key: str, value: Any, **changes: Any) -> an.Rows:
    """A copy of `rows` with one row changed."""
    assert any(row[key] == value for row in rows)
    return [row | changes if row[key] == value else dict(row) for row in rows]


def _states(res: Results, **changes: Any) -> dict[str, Any]:
    return {**res.states, **changes}


def _signals(res: Results, key: str, with_signal: float, without: float) -> an.Rows:
    return [
        _signal(key, with_signal, without) if r["key"] == key else r for r in res.amendments_cif
    ]


Change = Callable[[Results], Results]
BROKEN: dict[str, Change] = {
    "industry no longer stops early sooner": lambda r: replace(
        r, sponsor=_rows(r.sponsor, "group", "INDUSTRY", cif_12m=0.015)
    ),
    "the sponsor gap no longer narrows": lambda r: replace(
        r, sponsor=_rows(r.sponsor, "group", "OTHER", cif_60m=0.030)
    ),
    "ignoring completion no longer overstates": lambda r: replace(
        r, states=_states(r, naive_early_stop=1.1 * r.states["early_stop"])
    ),
    "fewer than half of trials complete": lambda r: replace(
        r, states=_states(r, completed=0.4 * r.states["completed"])
    ),
    "later landmarks are no riskier": lambda r: replace(
        r, by_landmark=_rows(r.by_landmark, "group", 0, cif_12m=0.039)
    ),
    "risk keeps rising at late landmarks": lambda r: replace(
        r, by_landmark=_rows(r.by_landmark, "group", 6, cif_12m=0.065)
    ),
    "withdrawals come late, not early": lambda r: replace(
        r, states=_states(r, withdrawn=0.05 * (r.states["months"] / STATE_MONTHS) ** 2)
    ),
    "a suspension no longer marks risk": lambda r: replace(
        r, amendments_cif=_signals(r, "ever_suspended", 0.07, 0.066)
    ),
    "a date slip separates more than status": lambda r: replace(
        r, amendments_cif=_signals(r, "primary_completion_later", 0.30, 0.064)
    ),
    "an enrollment cut separates more than status": lambda r: replace(
        r, amendments_cif=_signals(r, "enrollment_cut", 0.30, 0.068)
    ),
    "slips are much more common before a stop": lambda r: replace(
        r,
        amendments_outcome=_rows(
            r.amendments_outcome, "group", an.LATER_OUTCOMES[0][1], primary_completion_later=0.45
        ),
    ),
    "late registration carries more risk": lambda r: replace(
        r, timing=_rows(r.timing, "group", an.LATE_OVER_YEAR, cif_24m=0.080)
    ),
    "late registration did not become less common": lambda r: replace(
        r, registration_years=_rows(r.registration_years, "year", 2017, share_after_start=0.70)
    ),
    "the base rate drifts across years": lambda r: replace(
        r, year=_rows(r.year, "group", 2015, cif_24m=0.075)
    ),
    "the first year does not stand out": lambda r: replace(
        r, year=_rows(r.year, "group", 2013, cif_12m=0.035)
    ),
    "the first year does not register latest": lambda r: replace(
        r, registration_years=_rows(r.registration_years, "year", 2012, share_after_start=0.60)
    ),
    "post dates never become actual": lambda r: replace(
        r, post_dates=[_post(y, 1.0) for y in (2016, 2017, 2018, 2019)]
    ),
    "the test years still have estimated dates": lambda r: replace(
        r, post_dates=[_post(2018, 1.0), _post(2019, 0.5), _post(2020, 0.0)]
    ),
    "posting takes weeks": lambda r: replace(
        r, post_dates=_rows(r.post_dates, "year", 2019, p90_days_to_post=40)
    ),
    "suspensions did not spike": lambda r: replace(
        r, covid={**r.covid, "suspended_per_1000": np.full(len(r.covid["months"]), 0.6)}
    ),
    "terminations spiked with the suspensions": lambda r: replace(
        r, covid_summary=_rows(r.covid_summary, "period", COVID_SHOCK, terminated_per_1000=4.0)
    ),
    "withdrawals rose afterwards": lambda r: replace(
        r, covid_summary=_rows(r.covid_summary, "period", COVID_AFTER, withdrawn_per_1000=2.5)
    ),
}


def test_supported_findings_are_written_and_each_cites_its_evidence() -> None:
    found = findings(supporting())
    assert 5 <= len(found) <= 8  # Step 5: "End with 5 to 8 findings"
    for finding in found:
        assert CITES.search(finding.evidence), finding.title
        assert finding.implication
        assert not finding.title.endswith(".")
    assert sum("Descriptive only" in f.title for f in found) == 1  # the COVID finding says so


def test_the_numbers_in_a_finding_come_from_the_results() -> None:
    first = findings(supporting())[0]
    assert "3.5% at 12 months against 1.4% for OTHER" in first.evidence
    changed = replace(
        supporting(), sponsor=_rows(supporting().sponsor, "group", "INDUSTRY", cif_12m=0.041)
    )
    assert "4.1% at 12 months against 1.4% for OTHER" in findings(changed)[0].evidence


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_a_finding_the_numbers_no_longer_support_is_refused(case: str) -> None:
    with pytest.raises(StaleFindingError, match="no longer supports a finding"):
        findings(BROKEN[case](supporting()))


def test_the_covid_peak_must_fall_in_2020() -> None:
    res = supporting()
    rate = np.full(len(res.covid["months"]), 0.6)
    rate[res.covid["months"] == np.datetime64("2022-06-01")] = 10.7
    with pytest.raises(StaleFindingError, match="peak is in 2020"):
        findings(replace(res, covid={**res.covid, "suspended_per_1000": rate}))
