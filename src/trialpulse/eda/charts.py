"""The nine figures of docs/eda.md: what each one shows, drawn with the primitives in
`figures.py`. File names and captions are fixed here, and the document cites them by number.

A title that states a direction ("industry trials stop early sooner") repeats a finding.
The findings are checked before anything is drawn (`report.generate`), so a title cannot
outlive the numbers behind it.
"""

from pathlib import Path

import numpy as np

from trialpulse.eda import analysis as an
from trialpulse.eda import figures as fg
from trialpulse.eda.results import (
    AMENDMENT_LANDMARK_MONTHS,
    COVID_END,
    COVID_START,
    CURVE_MONTHS,
    DESCRIPTIVE,
    PHASE_LABEL,
    STATE_MONTHS,
    Results,
    groups,
)

FIGURES: dict[int, tuple[str, str]] = {
    1: ("eda_01_cif_by_sponsor_class.png", "Early-stop cumulative incidence by sponsor class"),
    2: (
        "eda_02_cif_by_registration_year.png",
        "Early-stop cumulative incidence by year of registration",
    ),
    3: ("eda_03_cif_by_phase_descriptive.png", "Early-stop cumulative incidence by phase group"),
    4: ("eda_04_states_since_registration.png", "Where trials are, by months since registration"),
    5: ("eda_05_naive_vs_competing_risks.png", "Two estimates of the early-stop probability"),
    6: ("eda_06_amendment_signals.png", "Early-stop cumulative incidence by amendment signal"),
    7: ("eda_07_registration_lag.png", "Share of trials registered after their start date"),
    8: ("eda_08_estimated_post_dates.png", "Share of versions with an estimated post date"),
    9: ("eda_09_covid_period_descriptive.png", "Monthly suspensions and stops, 2017 to 2023"),
}
SPONSOR_LEGEND = {"OTHER": "OTHER (mostly universities and hospitals)"}


def pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def draw(res: Results, out_dir: Path) -> list[Path]:
    paths = {number: out_dir / name for number, (name, _) in FIGURES.items()}
    source = (
        f"Source: ClinicalTrials.gov registry records, data as of {res.cutoff.isoformat()}. "
        "Research demo, not medical advice."
    )
    scope = (
        f"Trials open at registration, first posted {res.first_year} to "
        f"{res.last_modeling_day.year}"
    )
    short, long_ = res.short, res.long
    since = "Months since registration"

    curves = list(res.sponsor_curves.items())
    fg.line_chart(
        paths[1],
        [
            (SPONSOR_LEGEND.get(name, name), months, cif, color)
            for (name, (months, cif)), color in zip(curves, fg.CATEGORICAL, strict=False)
        ],
        title="Industry trials stop early sooner; other sponsors catch up",
        subtitle=f"Cumulative incidence of early stop by lead sponsor class. {scope}.",
        source=source,
        end_labels=[f"{name.split(' ')[0]} {pct(float(cif[-1]))}" for name, (_, cif) in curves],
        x_label=since,
        x_ticks=list(range(0, CURVE_MONTHS + 1, 12)),
    )

    by_year = groups(res.year)
    years = np.array([row["group"] for row in by_year], dtype=np.float64)
    horizon_colors = ((short, fg.BLUE), (long_, fg.ORANGE))
    fg.line_chart(
        paths[2],
        [
            (f"Within {m} months", years, np.array([row[f"cif_{m}m"] for row in by_year]), color)
            for m, color in horizon_colors
        ],
        title=f"After {res.first_year}, the early-stop rate is steady across registration years",
        subtitle=f"Cumulative incidence of early stop by year of registration. {scope}.",
        source=source,
        end_labels=[f"{m} months {pct(by_year[-1][f'cif_{m}m'])}" for m, _ in horizon_colors],
        x_label="Year of registration",
        x_ticks=[float(y) for y in years],
        x_tick_labels=[str(int(y)) for y in years],
        markers=True,
        right=0.80,
        x_pad=0.3,
    )

    phases = [row for row in res.phase if row["group"] in an.PHASE_ORDER]
    fg.dot_ranges(
        paths[3],
        [(row["group"], row[f"cif_{short}m"], row[f"cif_{long_}m"]) for row in phases],
        (f"Within {short} months", f"Within {long_} months"),
        (fg.BLUE, fg.ORANGE),
        title="Early-stop incidence by phase group",
        subtitle=(
            f"{PHASE_LABEL}: the phase is the current-record phase (snapshot of "
            f"{res.snapshot}), not the phase known at registration. {scope}."
        ),
        source=source,
        x_label="Cumulative incidence of early stop since registration",
    )

    months = res.states["months"]
    layers = (("Terminated", "terminated", fg.BLUE), ("Withdrawn", "withdrawn", fg.ORANGE),
              ("Completed", "completed", fg.AQUA), ("Still open", "open", fg.NEUTRAL))  # fmt: skip
    fg.stacked_states(
        paths[4],
        months,
        [(label, res.states[key], color) for label, key, color in layers],
        title="Most trials complete; completion competes with stopping early",
        subtitle=f"Share of trials in each state by months since registration. {scope}.",
        source=source,
        x_label=since,
    )

    estimates = (
        ("Competing-risks estimate (Aalen-Johansen)", "early_stop", "Competing risks", fg.BLUE),
        ("Completion treated as censoring (1 minus Kaplan-Meier)", "naive_early_stop",
         "As censoring", fg.ORANGE),
    )  # fmt: skip
    fg.line_chart(
        paths[5],
        [(label, months, res.states[key], color) for label, key, _, color in estimates],
        title="Ignoring completion overstates the early-stop risk",
        subtitle=f"Two estimates of the probability of an early stop. {scope}.",
        source=source,
        end_labels=[f"{end} {pct(res.state(key, STATE_MONTHS))}" for _, key, end, _ in estimates],
        x_label=since,
        x_ticks=list(range(0, STATE_MONTHS + 1, 24)),
    )

    fg.dot_ranges(
        paths[6],
        [(row["signal"], row["cif_without"], row["cif_with"]) for row in res.amendments_cif],
        ("Without the signal", "With the signal"),
        (fg.INK_MUTED, fg.BLUE),
        title=f"What the record shows at {AMENDMENT_LANDMARK_MONTHS} months, and what follows",
        subtitle=(
            f"Cumulative incidence of early stop in the {long_} months after the "
            f"{AMENDMENT_LANDMARK_MONTHS}-month landmark, for trials with and without each "
            f"signal at the landmark. Landmarks before {res.before.isoformat()}."
        ),
        source=source,
        x_label=f"Cumulative incidence of early stop within {long_} months of the landmark",
        reference=(res.amendment_overall, f"All trials {pct(res.amendment_overall)}"),
    )

    lag = res.registration_years
    lag_years = np.array([row["year"] for row in lag], dtype=np.float64)
    lag_series = (
        ("Registered after the start date", "share_after_start", fg.BLUE),
        ("More than 1 year after the start date", "share_over_a_year_late", fg.ORANGE),
    )
    even = [y for y in lag_years if int(y) % 2 == 0]
    fg.line_chart(
        paths[7],
        [
            (label, lag_years, np.array([row[key] for row in lag], dtype=np.float64), color)
            for label, key, color in lag_series
        ],
        title="Registration after the start date, by year of registration",
        subtitle=(
            "Share of trials registered after their start date, by year of registration. "
            f"Trials open at registration; the last year runs to {res.cutoff.isoformat()}."
        ),
        source=source,
        end_labels=[pct(lag[-1][key]) for _, key, _ in lag_series],
        x_label="Year of registration",
        x_ticks=even,
        x_tick_labels=[str(int(y)) for y in even],
        markers=True,
        divider=(res.before.year - 0.5, f"From {res.before.year}: {DESCRIPTIVE}"),
        right=0.90,
        x_pad=0.3,
    )

    post_years = [int(row["year"]) for row in res.post_dates]
    shares = [float(row["share_estimated"]) for row in res.post_dates]
    changing = [y for y, s in zip(post_years, shares, strict=True) if 0.001 < s < 0.999]
    estimated = [y for y, s in zip(post_years, shares, strict=True) if s >= 0.999]
    actual = [y for y, s in zip(post_years, shares, strict=True) if s <= 0.001]
    switch = (
        f"Post dates are estimated through {estimated[-1]} and actual from {actual[0]}"
        if estimated and actual and estimated[-1] < actual[0]
        else "Share of versions with an estimated post date"
    )
    fg.bars_by_year(
        paths[8],
        post_years,
        shares,
        title=switch,
        subtitle=(
            "Share of cohort-trial versions whose post date (the version clock) is ESTIMATED, "
            "by year of posting."
        ),
        source=source,
        x_label="Year of posting",
        labeled=[post_years[0], *changing, post_years[-1]],
    )

    covid_x = 1970 + res.covid["months"].astype("datetime64[M]").astype(np.int64) / 12
    peak_name, peak_rate, peak_at = res.covid_peak()
    covid_colors = (fg.VIOLET, fg.BLUE, fg.ORANGE)
    tick_years = list(range(COVID_START.year, COVID_END.year + 2))
    fg.line_chart(
        paths[9],
        [
            (label, covid_x, res.covid[f"{key}_per_1000"], color)
            for (key, label), color in zip(an.COVID_SERIES, covid_colors, strict=True)
        ],
        title="The COVID period shows as suspensions, not as stops",
        subtitle=(
            f"{DESCRIPTIVE.capitalize()}, not used for modeling. Events posted in the month "
            "per 1,000 cohort trials under follow-up."
        ),
        source=source,
        end_labels=[label for _, label in an.COVID_SERIES],
        x_label="Month of posting",
        x_ticks=tick_years,
        x_tick_labels=[str(y) for y in tick_years],
        percent=False,
        note=(float(covid_x[peak_at]), peak_rate, f"{peak_name}: {peak_rate:.1f}"),
        right=0.84,
    )
    return [paths[number] for number in sorted(paths)]
