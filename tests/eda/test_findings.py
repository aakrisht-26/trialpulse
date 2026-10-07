"""The findings and their checks, on hand-written results.

`supporting()` holds numbers that support every finding. Each case in BROKEN changes one of
them so that one sentence would become untrue, and names the check that must then refuse
the finding. A case that is refused by some other check fails.
"""

import ast
import datetime as dt
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from trialpulse.eda import analysis as an
from trialpulse.eda import findings as findings_module
from trialpulse.eda.findings import BUILDERS, StaleFindingError, findings
from trialpulse.eda.results import (
    COVID_AFTER,
    COVID_BASELINE,
    COVID_SHOCK,
    STATE_MONTHS,
    Results,
)

# The BROKEN cases sit just past each threshold, so loosening a threshold also fails a test.
CITES = re.compile(r"\((Figures? \d|Table \d)")
REFUSAL = re.compile(
    r"the data no longer supports a finding in docs/eda\.md \((.+)\)\. Reread the numbers "
    r"and rewrite the finding in src/trialpulse/eda/findings\.py\."
)
SIGNAL_NUMBERS = {
    "ever_suspended": (0.239, 0.065),
    "not_yet_recruiting": (0.130, 0.059),
    "primary_completion_later": (0.080, 0.064),
    "target_cut": (0.066, 0.070),
    "target_raised": (0.051, 0.070),
    "enrollment_closed": (0.024, 0.069),
    "enrollment_short": (0.077, 0.011),
}


def _cif_rows(groups: dict[Any, tuple[Any, ...]], months: tuple[int, ...], **extra: Any) -> an.Rows:
    return [
        {"group": group, "trials": 1000, "early_stops": 100}
        | {f"cif_{m}m": value for m, value in zip(months, values, strict=True)}
        | {key: by_group[group] for key, by_group in extra.items() if group in by_group}
        for group, values in groups.items()
    ]


def _signal(key: str, with_signal: float, without: float) -> dict[str, Any]:
    return {
        "key": key,
        "signal": key,
        "among": an.EVERYONE,
        "with": 585,
        "without": 5000,
        "outside": 0,
        "cif_with": with_signal,
        "cif_without": without,
        "ratio": with_signal / without,
    }


def _post(year: int, share: float) -> dict[str, Any]:
    return {
        "year": year,
        "versions": 1000,
        "share_estimated": share,
        "median_days_to_post": 2.0,
        "p90_days_to_post": 5.0,
        "descriptive": year >= 2018,
    }


def _lag(
    date_type: str, is_first: bool, versions: int, median: float, p90: float
) -> dict[str, Any]:
    return {
        "date_type": date_type,
        "is_first": is_first,
        "versions": versions,
        "median_days": median,
        "p90_days": p90,
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


def _year(year: int, share: float, by_day: float) -> dict[str, Any]:
    return {
        "year": year,
        "share_after_start": share,
        "share_day_precision": by_day,
        "descriptive": year >= 2018,
    }


def supporting() -> Results:
    month = np.arange(STATE_MONTHS + 1, dtype=np.float64)
    terminated = 0.10 * month / STATE_MONTHS
    withdrawn = 0.042 * (1 - np.exp(-month / 20))
    covid_months = np.arange("2017-01", "2024-01", dtype="datetime64[M]").astype("datetime64[D]")
    suspended_rate = np.full(len(covid_months), 0.6)
    suspended_rate[covid_months == np.datetime64("2020-04-01")] = 10.6
    reg = an.Registrations(
        time=np.array([400.0]),
        event=np.array([1]),
        kind=np.array([1]),
        lapsed=np.array([False]),
        sponsor_class=np.array(["INDUSTRY"]),
        sponsor_group=np.array(["INDUSTRY"]),
        year=np.array([2008]),
        timing=np.array([an.PROSPECTIVE]),
        phase=np.array([an.NO_PHASE]),
    )
    sponsor = {
        "INDUSTRY": (0.035, 0.070, 0.094, 0.116),
        "OTHER": (0.013, 0.042, 0.072, 0.118),
        an.ALL: (0.020, 0.051, 0.077, 0.116),
    }
    years: dict[Any, tuple[Any, ...]] = {2008: (0.028, 0.057)}
    years |= {y: (0.018 + (y % 4) / 1000, 0.049 + (y % 3) / 1000) for y in range(2009, 2016)}
    years |= {2016: (0.019, None), 2017: (None, None), an.ALL: (0.020, 0.051)}
    timing = {
        an.PROSPECTIVE: (0.023, 0.059, 0.089, 0.131),
        an.LATE_WITHIN_YEAR: (0.017, 0.041, 0.065, 0.100),
        an.LATE_OVER_YEAR: (0.013, 0.030, 0.047, 0.073),
    }
    by_landmark = {0: (0.020, 0.050), 1: (0.029, 0.061), 2: (0.036, 0.067)}
    by_landmark |= dict.fromkeys((3, 4, 5, 6), (0.040, 0.071))
    lapse = [
        {"group": "INDUSTRY", "trials": 1000, "lapsed": 20, "lapse_60m": 0.023},
        {"group": "OTHER", "trials": 1000, "lapsed": 60, "lapse_60m": 0.079},
    ]
    slip = "primary_completion_later"
    return Results(
        cutoff=dt.date(2026, 9, 25),
        before=dt.date(2018, 1, 1),
        stamp="hand-written",
        snapshot="2026-09-22",
        landmark_rows=10,
        landmark_trials=5,
        final_landmark_rows=12,
        final_landmark_trials=6,
        horizons=(12, 24),
        spacing_months=6,
        reg=reg,
        sponsor=_cif_rows(sponsor, (12, 24, 36, 60)),
        sponsor_curves={},
        lapse=lapse,
        year=_cif_rows(years, (12, 24)),
        by_landmark=_cif_rows(by_landmark, (12, 24)),
        phase=[],
        states={
            "months": month,
            "terminated": terminated,
            "withdrawn": withdrawn,
            "early_stop": terminated + withdrawn,
            "completed": 0.74 * month / STATE_MONTHS,
            "naive_early_stop": (terminated + withdrawn) * (1 + 1.1 * month / STATE_MONTHS),
        },
        amendment_rows=5585,
        amendment_overall=0.067,
        amendments_outcome=[
            {"group": an.LATER_OUTCOMES[0][1], "trials": 100, slip: 0.231},
            {"group": an.LATER_OUTCOMES[1][1], "trials": 900, slip: 0.241},
        ],
        amendments_cif=[_signal(key, *numbers) for key, numbers in SIGNAL_NUMBERS.items()],
        registration_years=[
            _year(2008, 0.544, 0.0),
            _year(2016, 0.370, 0.0),
            _year(2017, 0.300, 0.609),
            _year(2019, 0.650, 0.72),
        ],
        timing=_cif_rows(
            timing,
            (12, 24, 36, 60),
            withdrawn={an.PROSPECTIVE: 0.028, an.LATE_WITHIN_YEAR: 0.009, an.LATE_OVER_YEAR: 0.004},
            terminated={
                an.PROSPECTIVE: 0.031,
                an.LATE_WITHIN_YEAR: 0.033,
                an.LATE_OVER_YEAR: 0.026,
            },
        ),
        post_dates=[_post(2015, 1.0), _post(2016, 1.0), _post(2017, 0.119), _post(2018, 0.0)],
        posting_lag=[
            _lag("ACTUAL", True, 15708, 3.0, 6.0),
            _lag("ACTUAL", False, 125365, 2.0, 5.0),
            _lag("ESTIMATED", True, 118463, 2.0, 5.0),
        ],
        covid={"months": covid_months, "suspended_per_1000": suspended_rate},
        covid_summary=[
            _period(COVID_BASELINE, 0.63, 1.94, 1.11),
            _period(COVID_SHOCK, 6.32, 2.03, 1.19),
            _period(COVID_AFTER, 0.89, 2.05, 1.27),
            _period("2022 to 2023", 0.71, 2.29, 1.32),
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


def _peak(res: Results, month: str, rate: float) -> dict[str, Any]:
    rates = np.full(len(res.covid["months"]), 0.6)
    rates[res.covid["months"] == np.datetime64(month)] = rate
    return {**res.covid, "suspended_per_1000": rates}


def _late_withdrawals(res: Results) -> dict[str, Any]:
    """Withdrawals that grow with time, where the supporting ones come early."""
    ratio = res.states["months"] / STATE_MONTHS
    withdrawn = 0.042 * ratio**2
    early_stop = res.states["terminated"] + withdrawn
    naive = early_stop * (1 + 1.1 * ratio)
    return _states(res, withdrawn=withdrawn, early_stop=early_stop, naive_early_stop=naive)


Change = Callable[[Results], Results]
# What is changed, and the claim (as given to `check`) that must refuse it.
BROKEN: dict[str, tuple[Change, str]] = {
    "early stops are common": (
        lambda r: replace(r, sponsor=_rows(r.sponsor, "group", an.ALL, cif_24m=0.101)),
        "early stops are uncommon",
    ),
    "industry is not sooner": (
        lambda r: replace(r, sponsor=_rows(r.sponsor, "group", "INDUSTRY", cif_12m=0.019)),
        "industry trials stop early sooner",
    ),
    "the classes stay apart": (
        lambda r: replace(r, sponsor=_rows(r.sponsor, "group", "OTHER", cif_60m=0.105)),
        "the two classes meet",
    ),
    "censoring is alike by class": (
        lambda r: replace(r, lapse=_rows(r.lapse, "group", "OTHER", lapse_60m=0.045)),
        "censored as UNKNOWN more often",
    ),
    "fewer than half complete": (
        lambda r: replace(r, states=_states(r, completed=0.67 * r.states["completed"])),
        "most trials complete",
    ),
    "the naive estimate is close": (
        lambda r: replace(r, states=_states(r, naive_early_stop=1.45 * r.states["early_stop"])),
        "treating completion as censoring overstates",
    ),
    "the overstatement does not grow": (
        lambda r: replace(r, states=_states(r, naive_early_stop=2 * r.states["early_stop"])),
        "the overstatement grows",
    ),
    "later landmarks are no riskier": (
        lambda r: replace(r, by_landmark=_rows(r.by_landmark, "group", 0, cif_12m=0.027)),
        "higher at later landmarks",
    ),
    "risk keeps rising": (
        lambda r: replace(r, by_landmark=_rows(r.by_landmark, "group", 6, cif_12m=0.051)),
        "risk is level",
    ),
    "withdrawals come late": (
        lambda r: replace(r, states=_late_withdrawals(r)),
        "withdrawals are a larger share",
    ),
    "no late landmark": (
        lambda r: replace(r, by_landmark=[x for x in r.by_landmark if x["group"] < 3]),
        "landmarks from 18 months on exist",
    ),
    "one registration year": (
        lambda r: replace(r, registration_years=[_year(2017, 0.3, 0.6), _year(2019, 0.6, 0.7)]),
        "several registration years exist",
    ),
    "two registration years followed": (
        lambda r: replace(r, year=[x for x in r.year if x["group"] in (2008, 2009, an.ALL)]),
        "several registration years exist",
    ),
    "a suspension marks nothing": (
        lambda r: replace(r, amendments_cif=_signals(r, "ever_suspended", 0.128, 0.065)),
        "a suspension on record marks higher risk",
    ),
    "waiting marks nothing": (
        lambda r: replace(r, amendments_cif=_signals(r, "not_yet_recruiting", 0.087, 0.059)),
        "still not yet recruiting marks higher risk",
    ),
    "a date slip separates as much as status": (
        lambda r: replace(r, amendments_cif=_signals(r, "primary_completion_later", 0.1166, 0.064)),
        "primary_completion_later separates less",
    ),
    "a target cut separates as much as status": (
        lambda r: replace(r, amendments_cif=_signals(r, "target_cut", 0.1275, 0.070)),
        "target_cut separates less",
    ),
    "a raised target protects strongly": (
        lambda r: replace(r, amendments_cif=_signals(r, "target_raised", 0.0384, 0.070)),
        "target_raised separates less",
    ),
    "slips are much more common before a stop": (
        lambda r: replace(
            r,
            amendments_outcome=_rows(
                r.amendments_outcome,
                "group",
                an.LATER_OUTCOMES[0][1],
                primary_completion_later=0.295,
            ),
        ),
        "slips are as common",
    ),
    "closed enrollment is no safer": (
        lambda r: replace(r, amendments_cif=_signals(r, "enrollment_closed", 0.047, 0.069)),
        "closed enrollment marks lower risk",
    ),
    "a shortfall marks nothing": (
        lambda r: replace(r, amendments_cif=_signals(r, "enrollment_short", 0.0215, 0.011)),
        "short of the target marks higher risk",
    ),
    "a signal cannot be compared": (
        lambda r: replace(
            r, amendments_cif=[s for s in r.amendments_cif if s["key"] != "enrollment_short"]
        ),
        "the signal enrollment_short can be compared",
    ),
    "the latest registrants carry more risk": (
        lambda r: replace(r, timing=_rows(r.timing, "group", an.LATE_OVER_YEAR, cif_24m=0.080)),
        "earlier registration carries more risk",
    ),
    "the middle group is the lowest": (
        lambda r: replace(r, timing=_rows(r.timing, "group", an.LATE_WITHIN_YEAR, cif_24m=0.020)),
        "earlier registration carries more risk",
    ),
    "the gap is not in withdrawals": (
        lambda r: replace(
            r, timing=_rows(r.timing, "group", an.LATE_WITHIN_YEAR, withdrawn=0.0145)
        ),
        "the gap is in withdrawals",
    ),
    "terminations differ": (
        lambda r: replace(r, timing=_rows(r.timing, "group", an.LATE_OVER_YEAR, terminated=0.0225)),
        "terminations are about as common",
    ),
    "late registration did not become less common": (
        lambda r: replace(
            r, registration_years=_rows(r.registration_years, "year", 2017, share_after_start=0.70)
        ),
        "late registration became less common",
    ),
    "start dates kept their precision": (
        lambda r: replace(
            r,
            registration_years=_rows(r.registration_years, "year", 2017, share_day_precision=0.24),
        ),
        "start dates gained day precision",
    ),
    "the 24-month rate drifts": (
        lambda r: replace(r, year=_rows(r.year, "group", 2012, cif_24m=0.0605)),
        "the 24-month rate is stable",
    ),
    "the 12-month rate drifts": (
        lambda r: replace(r, year=_rows(r.year, "group", 2011, cif_12m=0.0235)),
        "the 12-month rate is stable",
    ),
    "the first year is like the rest": (
        lambda r: replace(r, year=_rows(r.year, "group", 2008, cif_12m=0.0195)),
        "the first year is higher",
    ),
    "the first year does not register latest": (
        lambda r: replace(
            r, registration_years=_rows(r.registration_years, "year", 2016, share_after_start=0.60)
        ),
        "the first year registers latest",
    ),
    "post dates stay estimated": (
        lambda r: replace(r, post_dates=[_post(y, 1.0) for y in (2015, 2016, 2017)]),
        "post dates stop being estimated",
    ),
    "an estimated year is not complete": (
        lambda r: replace(r, post_dates=_rows(r.post_dates, "year", 2015, share_estimated=0.9)),
        "the estimated years come first",
    ),
    "no actual post date before 2018": (
        lambda r: replace(r, posting_lag=[x for x in r.posting_lag if x["date_type"] != "ACTUAL"]),
        "actual post dates exist",
    ),
    "too few actual post dates": (
        lambda r: replace(r, posting_lag=_rows(r.posting_lag, "versions", 15708, versions=999)),
        "enough actual post dates",
    ),
    "posting takes weeks": (
        lambda r: replace(r, posting_lag=_rows(r.posting_lag, "versions", 125365, p90_days=15.0)),
        "posting follows submission by days",
    ),
    "suspensions did not spike": (
        lambda r: replace(r, covid=_peak(r, "2020-04-01", 1.8)),
        "suspensions spiked",
    ),
    "the peak is in another year": (
        lambda r: replace(r, covid=_peak(r, "2022-06-01", 10.6)),
        "the suspension peak is in 2020",
    ),
    "terminations rose with the suspensions": (
        lambda r: replace(
            r, covid_summary=_rows(r.covid_summary, "period", COVID_SHOCK, terminated_per_1000=2.45)
        ),
        "terminated_per_1000 did not rise",
    ),
    "withdrawals rose with the suspensions": (
        lambda r: replace(
            r, covid_summary=_rows(r.covid_summary, "period", COVID_SHOCK, withdrawn_per_1000=1.40)
        ),
        "withdrawn_per_1000 did not rise",
    ),
}
# Sentences each finding must contain for the supporting numbers: every number in its
# place, so a swap of two numbers inside a sentence is caught.
EXPECTED: list[tuple[str, ...]] = [
    (
        "2.0% of trials stop early within 12 months of registration and 5.1% within 24.",
        "INDUSTRY trials reach 3.5% at 12 months against 1.3% for OTHER",
        "at 11.6% and 11.8% by 60 months (Figure 1, Table 1)",
        "7.9% within 60 months against 2.3% for INDUSTRY (Table 2)",
        "censoring is 3.4 times as common for OTHER sponsors",
    ),
    (
        "By 96 months 74.0% of trials have completed and 14.2% have stopped early.",
        "gives 29.7% instead of 14.2% at 96 months, and 6.9% instead of 5.4% at 24 months",
        "(Figures 4 and 5, Table 6)",
    ),
    (
        "The 12-month CIF is 2.0% at registration and between 4.0% and 4.0% from the 18-month",
        "(Table 4)",
        "are 60.3% of the early stops by 12 months and 29.4% by 96 months (Figure 4, Table 6)",
    ),
    (
        "23.9% with a suspension on record against 6.5% without, and 13.0% for trials still "
        "not yet recruiting against 5.9%",
        "moved later gives 8.0% against 6.4%, an enrollment target cut 6.6% against 7.0% and a "
        "target raised 5.1% against 7.0% (Figure 6, Table 8)",
        "later stopped (23.1%) as among trials that later completed (24.1%) (Table 7)",
        "the CIF is 2.4% against 6.9%, but 7.7% for the 585 trials",
        "against 1.1% for the rest (Table 8)",
    ),
    (
        "5.9% for trials registered in or before their start month, 4.1% for trials registered "
        "up to 1 year after it and 3.0% for trials registered later",
        "withdrawals (2.8%, 0.9% and 0.4% within 24 months)",
        "about as common (3.1%, 3.3% and 2.6%) (Table 10)",
        "falls from 54.4% in 2008 to 30.0% in 2017",
        "to the day for 60.9% of the trials registered in 2017 against 0.0% in 2016 "
        "(Figure 7, Table 9)",
    ),
    (
        "After 2008, the base rate is stable across registration years",
        "between 4.9% and 5.1% for trials registered from 2009 to 2015",
        "12-month CIF between 1.8% and 2.1% from 2009 to 2016",
        "2008 is higher at both horizons, 2.8% and 5.7% (Figure 2, Table 3)",
        "largest share of late registrations (Table 9)",
    ),
    (
        "Post dates are estimated through 2016",
        "Every version posted through 2016 has an ESTIMATED post date, and 11.9% of those "
        "posted in 2017 (Figure 8, Table 11)",
        "a median of 2 days (90th percentile 5) for 125,365 later versions and 3 days (90th "
        "percentile 6) for 15,708 first versions (Table 12)",
    ),
    (
        "Descriptive only: the first COVID months show as suspensions, not as stops",
        "averaged 0.63 per 1,000 trials per month in 2017 to 2019 and reached 10.6 in April "
        "2020 (Figure 9)",
        "Terminations averaged 1.94 per 1,000 in 2017 to 2019 and 2.03 in March to May 2020; "
        "withdrawals 1.11 and 1.19 (Table 13)",
    ),
]


def test_supported_findings_are_written_and_each_cites_its_evidence() -> None:
    found = findings(supporting())
    assert len(found) == len(BUILDERS)
    assert 5 <= len(found) <= 8  # Step 5: "End with 5 to 8 findings"
    for finding in found:
        assert CITES.search(finding.evidence), finding.title
        assert len(finding.implication) > 200, finding.title  # a real consequence, in sentences
        assert not finding.title.endswith(".")
        assert "—" not in finding.title + finding.evidence + finding.implication
    assert sum("Descriptive only" in f.title for f in found) == 1  # the COVID finding says so
    # Findings 6 and 8 point at findings 5 and 4 by number, which holds only in this order.
    assert "(finding 5)" in found[5].implication
    assert "Trials registered in or before" in found[4].title
    assert "(finding 4)" in found[7].implication
    assert "status and enrollment state" in found[3].title


@pytest.mark.parametrize("number", range(1, len(EXPECTED) + 1))
def test_every_number_in_a_finding_sits_in_its_place(number: int) -> None:
    finding = findings(supporting())[number - 1]
    text = f"{finding.title}. {finding.evidence} {finding.implication}"
    for sentence in EXPECTED[number - 1]:
        assert sentence in text, sentence


def test_the_numbers_in_a_finding_come_from_the_results() -> None:
    res = supporting()
    changed = replace(res, sponsor=_rows(res.sponsor, "group", "INDUSTRY", cif_12m=0.041))
    assert "reach 4.1% at 12 months against 1.3% for OTHER" in findings(changed)[0].evidence
    changed = replace(res, lapse=_rows(res.lapse, "group", "OTHER", lapse_60m=0.092))
    assert "censoring is 4.0 times as common" in findings(changed)[0].implication


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_a_finding_the_numbers_no_longer_support_is_refused_by_its_own_check(case: str) -> None:
    change, claim = BROKEN[case]
    assert claim in _refused_claim(change)


def _refused_claim(change: Change) -> str:
    """The claim a changed result is refused with."""
    with pytest.raises(StaleFindingError) as refused:
        findings(change(supporting()))
    found = REFUSAL.fullmatch(str(refused.value))
    assert found, str(refused.value)
    return found.group(1)


def test_every_check_has_a_case_that_breaks_it() -> None:
    """Every `check` call in findings.py is refused by at least one case above, so removing
    or loosening a check makes a test fail."""
    tree = ast.parse(Path(findings_module.__file__).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "check"
    ]
    assert len(calls) >= 35
    refused = {_refused_claim(change) for change, _ in BROKEN.values()}
    unbroken = []
    for call in calls:
        claim = call.args[1]
        parts = claim.values if isinstance(claim, ast.JoinedStr) else [claim]
        pattern = "".join(
            re.escape(part.value) if isinstance(part, ast.Constant) else ".+" for part in parts
        )
        if not any(re.fullmatch(pattern, text) for text in refused):
            unbroken.append(pattern)
    assert unbroken == []
