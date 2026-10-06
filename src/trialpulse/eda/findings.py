"""The findings that close docs/eda.md, and the checks that keep them honest.

A finding states a direction ("higher", "stable", "levels off") and draws a modeling
implication from it. Its numbers are formatted from the results, but its wording is written
by hand, so each direction is verified with `check` before the report is written. If the
data stops supporting a sentence (a new dataset revision, a changed definition), the
command refuses with the name of the claim, instead of printing something untrue.
"""

from dataclasses import dataclass

from trialpulse.cli import RefusedError
from trialpulse.eda import analysis as an
from trialpulse.eda.results import (
    AMENDMENT_LANDMARK_MONTHS,
    COVID_AFTER,
    COVID_BASELINE,
    COVID_SHOCK,
    DESCRIPTIVE,
    STATE_MONTHS,
    TABLE_MONTHS,
    Results,
    groups,
    row,
)

LEVEL_FROM_INDEX = 3  # finding 3: risk over the next year is level from this landmark on


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


def _sponsor_class(res: Results) -> Finding:
    cs, cl, far = f"cif_{res.short}m", f"cif_{res.long}m", f"cif_{TABLE_MONTHS[-1]}m"
    everyone = row(res.sponsor, "group", an.ALL)
    industry, other = row(res.sponsor, "group", "INDUSTRY"), row(res.sponsor, "group", "OTHER")
    check(industry[cs] > 1.5 * other[cs], "industry trials stop early sooner than OTHER")
    check(industry[far] / other[far] < industry[cs] / other[cs], "the sponsor gap narrows")
    return Finding(
        "Early stops are uncommon, and sponsor classes differ in when they happen",
        f"{pct(everyone[cs])} of trials stop early within {res.short} months of registration "
        f"and {pct(everyone[cl])} within {res.long}. INDUSTRY trials reach {pct(industry[cs])} "
        f"at {res.short} months against {pct(other[cs])} for OTHER, yet the two classes are at "
        f"{pct(industry[far])} and {pct(other[far])} by {TABLE_MONTHS[-1]} months (Figure 1, "
        "Table 1).",
        "Sponsor class is a sound stratifier for M0 and a feature for every model, but its "
        "effect changes with time since registration. The discrete-time models need it to "
        "interact with the interval index (LightGBM does this by itself; the logistic models "
        "M2 and M3 need an explicit interaction term), and the Cox analysis should expect the "
        "proportional-hazards check to fail for sponsor class.",
    )


def _competing_risks(res: Results) -> Finding:
    end = STATE_MONTHS
    stop, naive, done = (res.state(k, end) for k in ("early_stop", "naive_early_stop", "completed"))
    check(naive > 1.5 * stop, "treating completion as censoring overstates the risk")
    check(done > 0.5, "most trials complete")
    return Finding(
        "Completion is the common ending, and ignoring it overstates the risk",
        f"By {end} months {pct(done)} of trials have completed and {pct(stop)} have stopped "
        f"early. Treating completion as censoring gives {pct(naive)} instead of {pct(stop)} at "
        f"{end} months, and {pct(res.state('naive_early_stop', res.long))} instead of "
        f"{pct(res.state('early_stop', res.long))} at {res.long} months (Figures 4 and 5, "
        "Table 5).",
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
    early_share = res.state("withdrawn", res.short) / res.state("early_stop", res.short)
    late_share = res.state("withdrawn", end) / res.state("early_stop", end)
    check(early_share > late_share, "withdrawals are a larger share of the first early stops")
    return Finding(
        "Risk over the next year rises after registration and then levels off",
        f"The {res.short}-month CIF is {pct(first[cs])} at registration and between {pct(low)} "
        f"and {pct(high)} from the {level_from}-month landmark on (Table 3). Withdrawn trials "
        f"are {pct(early_share, 0)} of early stops by {res.short} months and "
        f"{pct(late_share, 0)} by {end} months (Table 5).",
        "The landmark index and the months since registration belong in the feature set "
        "(Section 8, time family). Metrics must be read per landmark index and not only "
        "pooled, because a model can score well pooled just by telling early landmarks from "
        "late ones. Withdrawal before enrollment and termination after it are different "
        "processes, and the status at the landmark tells them apart.",
    )


def _amendments(res: Results) -> Finding:
    signal = {r["key"]: r for r in res.amendments_cif}
    suspended, waiting = signal["ever_suspended"], signal["not_yet_recruiting"]
    moved, cut = signal["primary_completion_later"], signal["enrollment_cut"]
    stopped = row(res.amendments_outcome, "group", an.LATER_OUTCOMES[0][1])
    completed = row(res.amendments_outcome, "group", an.LATER_OUTCOMES[1][1])
    slip = "primary_completion_later"
    check(suspended["ratio"] > 2 and waiting["ratio"] > 1.5, "status signals separate risk")
    check(moved["ratio"] < waiting["ratio"], "a date slip separates risk less than status")
    check(cut["ratio"] < waiting["ratio"], "an enrollment cut separates risk less than status")
    check(abs(stopped[slip] - completed[slip]) < 0.05, "slips are as common before a stop")
    return Finding(
        f"At {AMENDMENT_LANDMARK_MONTHS} months, status signals separate risk far more than "
        "date or enrollment edits",
        f"The {res.long}-month CIF is {pct(suspended['cif_with'])} with a suspension on record "
        f"against {pct(suspended['cif_without'])} without, and {pct(waiting['cif_with'])} for "
        f"trials still not yet recruiting against {pct(waiting['cif_without'])}. A primary "
        f"completion date moved later gives {pct(moved['cif_with'])} against "
        f"{pct(moved['cif_without'])}, and an enrollment target cut {pct(cut['cif_with'])} "
        f"against {pct(cut['cif_without'])} (Figure 6, Table 7). A later primary completion "
        f"date is about as common among trials that later stopped ({pct(stopped[slip])}) as "
        f"among trials that later completed ({pct(completed[slip])}) (Table 6).",
        "The amendment family should carry status history (ever suspended, still not "
        "recruiting, start overdue) beside the date and enrollment changes. A yes or no slip "
        "flag is weak alone, so the features should keep the size and direction of each change "
        "and leave interactions to M4. The gain of M3 over M2 should come mostly from status "
        "signals. All of them read only versions posted on or before the landmark, which the "
        "future-perturbation test of Step 7 must confirm.",
    )


def _registration_timing(res: Results) -> Finding:
    cl = f"cif_{res.long}m"
    timing = {r["group"]: r for r in res.timing}
    on_time, within, over = (timing[k] for k in an.TIMING_ORDER[:3])
    years = [r for r in res.registration_years if not r["descriptive"]]
    share = "share_after_start"
    check(on_time[cl] > within[cl] > over[cl], "prospective registration carries more risk")
    check(years[0][share] > years[-1][share], "late registration became less common")
    return Finding(
        "Trials registered before they start carry more early-stop risk",
        f"The {res.long}-month CIF is {pct(on_time[cl])} for trials registered on or before "
        f"their start date, {pct(within[cl])} for trials registered up to 1 year after it and "
        f"{pct(over[cl])} for trials registered later than that (Table 9). The share registered "
        f"after the start falls from {pct(years[0][share])} in {years[0]['year']} to "
        f"{pct(years[-1][share])} in {years[-1]['year']} (Figure 7, Table 8).",
        "Time zero is registration, so a trial registered late has already survived its "
        "start-up period and can no longer be withdrawn before enrolling. Retrospective "
        "registration and the lag in days are needed design features (Section 8): without "
        "them a model would read survivorship as low risk. Because the share changes over the "
        "years, it is also a slow source of drift to monitor.",
    )


def _registration_year(res: Results) -> Finding:
    cs, cl = f"cif_{res.short}m", f"cif_{res.long}m"
    by_year = groups(res.year)
    first, rest = by_year[0], by_year[1:]
    years = [r for r in res.registration_years if not r["descriptive"]]
    share = "share_after_start"
    check(len(rest) >= 2, "several registration years exist")
    low, high = min(r[cl] for r in rest), max(r[cl] for r in rest)
    check(high - low < 0.01, "the base rate is stable across registration years")
    check(first[cs] == max(r[cs] for r in by_year), "the first year has the highest early rate")
    check(years[0][share] == max(r[share] for r in years), "the first year registers latest")
    return Finding(
        f"The base rate is stable across registration years after {first['group']}",
        f"The {res.long}-month CIF stays between {pct(low)} and {pct(high)} for trials "
        f"registered from {rest[0]['group']} to {rest[-1]['group']}. {first['group']} has the "
        f"highest {res.short}-month CIF, {pct(first[cs])} (Figure 2, Table 2), and the largest "
        "share of late registrations (Table 8).",
        "Training on earlier years to predict later ones, the walk-forward design, is "
        "reasonable for the overall level of risk, and registration year should add little "
        "beyond the other features. The first year after the 2007 registration mandate differs "
        "through registration timing, which is a reason to keep the timing features and not a "
        "reason to drop that year.",
    )


def _post_dates(res: Results) -> Finding:
    estimated = [r for r in res.post_dates if r["share_estimated"] == 1.0]
    actual = [r for r in res.post_dates if r["share_estimated"] == 0.0]
    check(bool(estimated) and bool(actual), "post dates switch from estimated to actual")
    check(estimated[-1]["year"] < actual[0]["year"], "the switch happens once")
    check(actual[0]["year"] <= res.before.year, "the locked test years have actual post dates")
    worst = max(r["p90_days_to_post"] for r in actual)
    check(worst <= 14, "posting follows submission within days")
    return Finding(
        f"Post dates are estimated through {estimated[-1]['year']}, with an error of days and "
        "not months",
        f"Every version posted through {estimated[-1]['year']} has an ESTIMATED post date, and "
        f"none from {actual[0]['year']} on. Where post dates are actual, posting follows "
        f"submission by a median of {actual[0]['median_days_to_post']:.0f} days in "
        f"{actual[0]['year']} and by no more than {worst:.0f} days at the 90th percentile in "
        "any year (Figure 8, Table 10).",
        f"Point-in-time reconstruction through {estimated[-1]['year']} rests on estimated post "
        "dates. A few days of error is small against landmarks 6 months apart, so no "
        "correction is needed. The date type changes once, shortly before the locked test "
        "years begin, so it would stand in for calendar time and must never be a feature. "
        "Features counted in days (days since the last update) may be distributed slightly "
        "differently in the two periods: a drift check for Step 17.",
    )


def _covid(res: Results) -> Finding:
    baseline = row(res.covid_summary, "period", COVID_BASELINE)
    shock = row(res.covid_summary, "period", COVID_SHOCK)
    after = row(res.covid_summary, "period", COVID_AFTER)
    peak_name, peak_rate, _ = res.covid_peak()
    check(peak_rate > 3 * baseline["suspended_per_1000"], "suspensions spiked")
    check(peak_name.endswith("2020"), "the suspension peak is in 2020")
    for period in (shock, after):
        for key in ("terminated_per_1000", "withdrawn_per_1000"):
            check(period[key] < 1.5 * baseline[key], "stops did not spike with the suspensions")
    return Finding(
        f"{DESCRIPTIVE.capitalize()}: the COVID period shows as suspensions, not as stops",
        f"New suspensions averaged {baseline['suspended_per_1000']:.2f} per 1,000 trials per "
        f"month in {COVID_BASELINE} and reached {peak_rate:.1f} in {peak_name}. Terminations "
        f"averaged {baseline['terminated_per_1000']:.2f} per 1,000 in {COVID_BASELINE} and "
        f"{shock['terminated_per_1000']:.2f} in {COVID_SHOCK}; withdrawals "
        f"{baseline['withdrawn_per_1000']:.2f} and {shock['withdrawn_per_1000']:.2f} (Figure 9, "
        "Table 11).",
        "Nothing here shapes a model (Section 10). It is a reason to expect a specific "
        "weakness at the 2020 stress origin and to pre-register it in Step 11: before 2018 a "
        "suspension on record marks elevated early-stop risk (finding 4), while in 2020 "
        "suspensions rose several times over with no matching rise in stops, so the same "
        "signal carried less information. Monitoring should track the suspension rate as a "
        "drift signal.",
    )


def findings(res: Results) -> list[Finding]:
    """The findings in report order. Raises StaleFindingError if one no longer holds."""
    return [
        _sponsor_class(res),
        _competing_risks(res),
        _time_since_registration(res),
        _amendments(res),
        _registration_timing(res),
        _registration_year(res),
        _post_dates(res),
        _covid(res),
    ]
