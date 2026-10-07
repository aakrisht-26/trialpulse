"""The findings that close docs/eda.md, and the checks that keep them honest.

A finding states directions ("higher", "stable", "about as common") and draws a modeling
implication from them. Its numbers are formatted from the results, but its wording is
written by hand, so every direction a finding states is verified with `check` before the
report is written. If the data stops supporting a sentence (a new dataset revision, a
changed definition), the command refuses with the name of the claim, instead of printing
something untrue. A statement that cannot be checked from the results is not made.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from trialpulse.cli import RefusedError
from trialpulse.eda import analysis as an
from trialpulse.eda.refs import cite
from trialpulse.eda.results import (
    AMENDMENT_LANDMARK_MONTHS,
    COVID_BASELINE,
    COVID_SHOCK,
    DESCRIPTIVE,
    LAPSE_MONTHS,
    STATE_MONTHS,
    TABLE_MONTHS,
    Results,
    followed,
    groups,
    row,
)

LEVEL_FROM_INDEX = 3  # finding 3: risk over the next year is level from this landmark on
# The date and target edits of finding 4, which separate risk less than a trial's status.
EDITS: tuple[str, ...] = (
    "primary_completion_later",
    "completion_later",
    "primary_completion_earlier",
    "target_cut",
    "target_raised",
)


class StaleFindingError(RefusedError):
    """A finding's wording is no longer supported by the numbers."""


@dataclass(frozen=True)
class Finding:
    title: str
    evidence: str  # numbers, with the figures and tables that hold them
    implication: str  # what it means for the models


def check(holds: bool, claim: str) -> None:
    if not holds:
        raise StaleFindingError(
            f"the data no longer supports a finding in docs/eda.md ({claim}). Reread the "
            "numbers and rewrite the finding in src/trialpulse/eda/findings.py."
        )


def pct(value: float, digits: int = 1) -> str:
    return f"{100 * value:.{digits}f}%"


def _ratio(signal: dict[str, Any]) -> float:
    """A signal's risk ratio. It is undefined when no trial without the signal stopped
    early, which counts as an unbounded ratio."""
    return math.inf if signal["ratio"] is None else float(signal["ratio"])


def _separation(signal: dict[str, Any]) -> float:
    """How far a signal's risk ratio is from 1, in either direction (unbounded when one of
    the two groups has no early stop)."""
    ratio = _ratio(signal)
    return math.inf if ratio <= 0 or math.isinf(ratio) else abs(math.log(ratio))


def _sponsor_class(res: Results) -> Finding:
    cs, cl, far = f"cif_{res.short}m", f"cif_{res.long}m", f"cif_{TABLE_MONTHS[-1]}m"
    lapse = f"lapse_{LAPSE_MONTHS[-1]}m"
    everyone = row(res.sponsor, "group", an.ALL)
    industry, other = row(res.sponsor, "group", "INDUSTRY"), row(res.sponsor, "group", "OTHER")
    industry_lapse = row(res.lapse, "group", "INDUSTRY")[lapse]
    other_lapse = row(res.lapse, "group", "OTHER")[lapse]
    check(everyone[cl] < 0.10, "early stops are uncommon")
    check(industry[cs] > 1.5 * other[cs], "industry trials stop early sooner than OTHER")
    check(abs(industry[far] - other[far]) < 0.01, "the two classes meet by the last horizon")
    check(other_lapse > 2 * industry_lapse, "OTHER trials are censored as UNKNOWN more often")
    return Finding(
        "Early stops are uncommon, and sponsor classes differ in when they happen",
        f"{pct(everyone[cs])} of trials stop early within {res.short} months of registration "
        f"and {pct(everyone[cl])} within {res.long}. INDUSTRY trials reach {pct(industry[cs])} "
        f"at {res.short} months against {pct(other[cs])} for OTHER, and the two classes are at "
        f"{pct(industry[far])} and {pct(other[far])} by {TABLE_MONTHS[-1]} months "
        f"{cite('sponsor', 'sponsor')}. OTHER trials are also censored under the UNKNOWN rule "
        f"more often: {pct(other_lapse)} within {LAPSE_MONTHS[-1]} months against "
        f"{pct(industry_lapse)} for INDUSTRY {cite(tables='lapse')}.",
        "Sponsor class is a sound stratifier for M0. It enters the discrete-time models with "
        "the sponsor family (Section 8), where it should be free to interact with the interval "
        "index, because its effect fades with time since registration; the Cox analysis should "
        "expect the proportional-hazards check to fail for it. The comparison at long horizons "
        "assumes that censoring under the UNKNOWN rule is unrelated to the outcome, and that "
        f"censoring is {other_lapse / industry_lapse:.1f} times as common for OTHER sponsors. "
        "The Section 6 sensitivity analysis (UNKNOWN as an early stop) therefore matters most "
        "for that class, and the IPCW metrics estimate the censoring curve within each sponsor "
        "class instead of once for all trials (ADR 0017).",
    )


def _competing_risks(res: Results) -> Finding:
    end = STATE_MONTHS
    stop, naive, done = (res.state(k, end) for k in ("early_stop", "naive_early_stop", "completed"))
    stop_long, naive_long = (
        res.state("early_stop", res.long),
        res.state("naive_early_stop", res.long),
    )
    check(done > 0.5, "most trials complete")
    check(naive > 1.5 * stop, "treating completion as censoring overstates the risk")
    check(naive / stop > naive_long / stop_long, "the overstatement grows with the horizon")
    return Finding(
        "Completion is the common ending, and ignoring it overstates the risk",
        f"By {end} months {pct(done)} of trials have completed and {pct(stop)} have stopped "
        f"early. Treating completion as censoring gives {pct(naive)} instead of {pct(stop)} at "
        f"{end} months, and {pct(naive_long)} instead of {pct(stop_long)} at {res.long} months "
        f"{cite(('states', 'naive'), 'states')}.",
        "Targets, baselines and metrics must be competing-risks quantities (Aalen-Johansen "
        "CIFs, multinomial interval hazards, IPCW with completions as controls), as Sections 6 "
        "and 9 specify. A binary classifier that drops or censors completed trials would be "
        "miscalibrated upward, and more so at longer horizons.",
    )


def _time_since_registration(res: Results) -> Finding:
    cs, end = f"cif_{res.short}m", STATE_MONTHS
    first = res.by_landmark[0]
    later = [r for r in res.by_landmark if r["group"] >= LEVEL_FROM_INDEX]
    level_from = LEVEL_FROM_INDEX * res.spacing_months
    check(bool(later), f"landmarks from {level_from} months on exist")
    low, high = min(r[cs] for r in later), max(r[cs] for r in later)
    check(low > 1.5 * first[cs], "risk over the next year is higher at later landmarks")
    check(high - low < 0.01, f"risk is level from the {level_from}-month landmark on")
    early_share, late_share = res.withdrawn_share(res.short), res.withdrawn_share(end)
    check(early_share > late_share + 0.05, "withdrawals are a larger share of the first stops")
    return Finding(
        "Risk over the next year rises after registration and then levels off",
        f"The {res.short}-month CIF is {pct(first[cs])} at registration and between {pct(low)} "
        f"and {pct(high)} from the {level_from}-month landmark on {cite(tables='landmark')}. "
        f"Withdrawn trials are {pct(early_share)} of the early stops by {res.short} months and "
        f"{pct(late_share)} by {end} months {cite('states', 'states')}.",
        "The landmark index and the months since registration belong in the feature set "
        "(Section 8, time family). Metrics must be read per landmark index and not only "
        "pooled, because a model can score well pooled just by telling early landmarks from "
        "late ones.",
    )


def _amendments(res: Results) -> Finding:
    signal = {r["key"]: r for r in res.amendments_cif}
    for key in ("ever_suspended", "not_yet_recruiting", "primary_completion_later", "target_cut",
                "target_raised", "enrollment_closed", "enrollment_short"):  # fmt: skip
        check(key in signal, f"the signal {key} can be compared")
    suspended, waiting = signal["ever_suspended"], signal["not_yet_recruiting"]
    moved, cut, raised = (
        signal[k] for k in ("primary_completion_later", "target_cut", "target_raised")
    )
    closed, short = signal["enrollment_closed"], signal["enrollment_short"]
    stopped = row(res.amendments_outcome, "group", an.LATER_OUTCOMES[0][1])
    completed = row(res.amendments_outcome, "group", an.LATER_OUTCOMES[1][1])
    slip = "primary_completion_later"
    check(_ratio(suspended) > 2, "a suspension on record marks higher risk")
    check(_ratio(waiting) > 1.5, "still not yet recruiting marks higher risk")
    status = min(_separation(suspended), _separation(waiting))
    for key in EDITS:
        if key in signal:
            check(_separation(signal[key]) < 0.75 * status, f"{key} separates less than status")
    check(abs(stopped[slip] - completed[slip]) < 0.05, "slips are as common before a stop")
    check(_ratio(closed) < 0.67, "closed enrollment marks lower risk")
    check(_ratio(short) > 2, "enrollment short of the target marks higher risk")
    return Finding(
        f"At {AMENDMENT_LANDMARK_MONTHS} months, a trial's status and enrollment state separate "
        "risk more than date or target edits",
        f"The {res.long}-month CIF is {pct(suspended['cif_with'])} with a suspension on record "
        f"against {pct(suspended['cif_without'])} without, and {pct(waiting['cif_with'])} for "
        f"trials still not yet recruiting against {pct(waiting['cif_without'])}. Edits "
        f"separate less: a primary completion date moved later gives {pct(moved['cif_with'])} "
        f"against {pct(moved['cif_without'])}, an enrollment target cut {pct(cut['cif_with'])} "
        f"against {pct(cut['cif_without'])} and a target raised {pct(raised['cif_with'])} "
        f"against {pct(raised['cif_without'])} {cite('signals', 'signals_cif')}. A later "
        f"primary completion date is about as common among trials that later stopped "
        f"({pct(stopped[slip])}) as among trials that later completed ({pct(completed[slip])}) "
        f"{cite(tables='signals_outcome')}. Where enrollment has closed (the count is ACTUAL) "
        f"the CIF is {pct(closed['cif_with'])} against {pct(closed['cif_without'])}, but "
        f"{pct(short['cif_with'])} for the {short['with']:,} trials that enrolled 10% or more "
        f"below their first target against {pct(short['cif_without'])} for the rest "
        f"{cite(tables='signals_cif')}.",
        "The amendment family should carry status history (ever suspended, still not "
        "recruiting, start overdue) beside the date and enrollment changes. A yes or no slip "
        "flag is weak alone, so the features should keep the size and direction of each change "
        "and leave interactions to M4. An enrollment count is a target while its type is "
        "ESTIMATED and the number enrolled once it is ACTUAL, so the enrollment change ratio "
        "of Section 8 must be read together with the enrollment type: LightGBM can learn that, "
        "and the logistic model M3 needs the interaction written out. All of these read only "
        "versions posted on or before the landmark, which the future-perturbation test of "
        "Step 7 must confirm.",
    )


def _registration_timing(res: Results) -> Finding:
    cl = f"cif_{res.long}m"
    timing = {r["group"]: r for r in res.timing}
    on_time, within, over = (timing[k] for k in an.TIMING_ORDER[:3])
    years = [r for r in res.registration_years if not r["descriptive"]]
    share, day = "share_after_start", "share_day_precision"
    check(len(years) >= 2, "several registration years exist")
    first, previous, last = years[0], years[-2], years[-1]
    terminated = [r["terminated"] for r in (on_time, within, over)]
    check(on_time[cl] > within[cl] > over[cl], "earlier registration carries more risk")
    late_withdrawn = max(within["withdrawn"], over["withdrawn"])
    check(on_time["withdrawn"] > 2 * late_withdrawn, "the gap is in withdrawals")
    check(max(terminated) - min(terminated) < 0.01, "terminations are about as common")
    check(first[share] > last[share], "late registration became less common")
    check(last[day] > previous[day] + 0.25, "start dates gained day precision in the last year")
    return Finding(
        "Trials registered in or before their start month carry more early-stop risk",
        f"The {res.long}-month CIF is {pct(on_time[cl])} for trials registered in or before "
        f"their start month, {pct(within[cl])} for trials registered up to 1 year after it and "
        f"{pct(over[cl])} for trials registered later than that. The gap is in withdrawals "
        f"({pct(on_time['withdrawn'])}, {pct(within['withdrawn'])} and {pct(over['withdrawn'])} "
        f"within {res.long} months), while terminations are about as common "
        f"({pct(on_time['terminated'])}, {pct(within['terminated'])} and "
        f"{pct(over['terminated'])}) {cite(tables='timing_cif')}. The share registered after "
        f"the start month falls from {pct(first[share])} in {first['year']} to "
        f"{pct(last[share])} in {last['year']}, and start dates are given to the day for "
        f"{pct(last[day])} of the trials registered in {last['year']} against "
        f"{pct(previous[day])} in {previous['year']} {cite('timing', 'timing_year')}.",
        "Time zero is registration, so a trial registered late has usually passed the point "
        "where trials are withdrawn before enrolling. Retrospective registration and the lag "
        "are needed design features (Section 8): without them a model would read survivorship "
        "as low risk. The registry's start dates change from month to day precision just "
        "before the locked test years, so these features should be computed on one basis (the "
        "calendar month) or carry the precision with them; otherwise a change of precision "
        "would look like a change of behavior. Both the share and the precision are drift "
        "signals to monitor.",
    )


def _registration_year(res: Results) -> Finding:
    cs, cl = f"cif_{res.short}m", f"cif_{res.long}m"
    by_year = groups(res.year)
    first = by_year[0]
    short_rest, long_rest = followed(by_year[1:], cs), followed(by_year[1:], cl)
    years = [r for r in res.registration_years if not r["descriptive"]]
    share = "share_after_start"
    check(len(long_rest) >= 2 and first[cl] is not None, "several registration years exist")
    low, high = min(r[cl] for r in long_rest), max(r[cl] for r in long_rest)
    low_short, high_short = min(r[cs] for r in short_rest), max(r[cs] for r in short_rest)
    check(high - low < 0.01, f"the {res.long}-month rate is stable across registration years")
    check(high_short - low_short < 0.005, f"the {res.short}-month rate is stable across years")
    check(first[cs] > high_short and first[cl] > high, "the first year is higher at both horizons")
    check(years[0][share] == max(r[share] for r in years), "the first year registers latest")
    return Finding(
        f"After {first['group']}, the base rate is stable across registration years",
        f"The {res.long}-month CIF stays between {pct(low)} and {pct(high)} for trials "
        f"registered from {long_rest[0]['group']} to {long_rest[-1]['group']}, and the "
        f"{res.short}-month CIF between {pct(low_short)} and {pct(high_short)} from "
        f"{short_rest[0]['group']} to {short_rest[-1]['group']}. {first['group']} is higher at "
        f"both horizons, {pct(first[cs])} and {pct(first[cl])} {cite('year', 'year')}, although "
        f"it has the largest share of late registrations {cite(tables='timing_year')}.",
        "Training on earlier years to predict later ones, the walk-forward design, is "
        "reasonable for the overall level of risk. The first year after the 2007 registration "
        "mandate is different, and this report does not explain why: registration timing does "
        "not account for it, since a year with more late registrations would be expected to "
        "show a lower rate (finding 5). Registration year, or an indicator for that first "
        "year, stays a candidate feature for the Step 10 ablation, and calibration should be "
        "checked by registration year in Step 9.",
    )


def _post_dates(res: Results) -> Finding:
    modeling = [r for r in res.post_dates if not r["descriptive"]]
    estimated = [r for r in modeling if r["share_estimated"] == 1.0]
    mixed = [r for r in modeling if r["share_estimated"] < 1.0]
    check(bool(estimated) and bool(mixed), "post dates stop being estimated before 2018")
    check(modeling[: len(estimated)] == estimated, "the estimated years come first, in a row")
    lag = {r["is_first"]: r for r in res.posting_lag if r["date_type"] == "ACTUAL"}
    check(set(lag) == {True, False}, "actual post dates exist before 2018")
    first, later = lag[True], lag[False]
    check(min(first["versions"], later["versions"]) >= 1000, "enough actual post dates to judge")
    check(max(first["p90_days"], later["p90_days"]) <= 14, "posting follows submission by days")
    partial = ", and ".join(
        f"{pct(r['share_estimated'])} of those posted in {r['year']}" for r in mixed
    )
    return Finding(
        f"Post dates are estimated through {estimated[-1]['year']}, and the likely error is "
        "days, not months",
        f"Every version posted through {estimated[-1]['year']} has an ESTIMATED post date, and "
        f"{partial} {cite('post_dates', 'post_dates')}. Among versions posted before "
        f"{res.before.year} with an actual post date, posting follows submission by a median "
        f"of {later['median_days']:.0f} days (90th percentile {later['p90_days']:.0f}) for "
        f"{later['versions']:,} later versions and {first['median_days']:.0f} days (90th "
        f"percentile {first['p90_days']:.0f}) for {first['versions']:,} first versions "
        f"{cite(tables='posting_lag')}.",
        f"Point-in-time reconstruction through {estimated[-1]['year']} rests on estimated post "
        "dates. Where the registry recorded the post date it trails submission by days, so a "
        "few days is the likely error of an estimated one: small against landmarks 6 months "
        "apart, and no correction is needed. The date type changes just before the locked "
        "test years, so it would stand in for calendar time and must never be a feature. "
        "Features counted in days (days since the last update) may be distributed slightly "
        "differently before and after the change: a drift check for Step 17.",
    )


def _covid(res: Results) -> Finding:
    baseline = row(res.covid_summary, "period", COVID_BASELINE)
    shock = row(res.covid_summary, "period", COVID_SHOCK)
    peak_name, peak_rate, _ = res.covid_peak()
    check(peak_rate > 3 * baseline["suspended_per_1000"], "suspensions spiked")
    check(peak_name.endswith("2020"), "the suspension peak is in 2020")
    for key in ("terminated_per_1000", "withdrawn_per_1000"):
        check(shock[key] < 1.25 * baseline[key], f"{key} did not rise with the suspensions")
    return Finding(
        f"{DESCRIPTIVE.capitalize()}: the first COVID months show as suspensions, not as stops",
        f"New suspensions averaged {baseline['suspended_per_1000']:.2f} per 1,000 trials per "
        f"month in {COVID_BASELINE} and reached {peak_rate:.1f} in {peak_name} "
        f"{cite('covid')}. Terminations averaged {baseline['terminated_per_1000']:.2f} per "
        f"1,000 in {COVID_BASELINE} and {shock['terminated_per_1000']:.2f} in {COVID_SHOCK}; "
        f"withdrawals {baseline['withdrawn_per_1000']:.2f} and "
        f"{shock['withdrawn_per_1000']:.2f} {cite(tables='covid')}.",
        "Nothing here shapes a model (Section 10). Before 2018 a suspension on record marks "
        "elevated early-stop risk (finding 4). Whether that still held for the suspensions of "
        "2020 cannot be read from monthly rates, and it was not examined, because it would "
        "read outcomes of the locked stress-test period. It is a natural hypothesis for the "
        "Step 11 pre-registration, which should then say that these descriptive aggregates "
        "prompted it. Monitoring should track the suspension rate as a drift signal.",
    )


# The findings in report order. Findings 6 and 8 refer to findings 5 and 4 by number.
BUILDERS: tuple[Callable[[Results], Finding], ...] = (
    _sponsor_class,
    _competing_risks,
    _time_since_registration,
    _amendments,
    _registration_timing,
    _registration_year,
    _post_dates,
    _covid,
)


def findings(res: Results) -> list[Finding]:
    """The findings in report order. Raises StaleFindingError if one no longer holds."""
    return [build(res) for build in BUILDERS]
