"""The nine figures of docs/eda.md: what each one shows, drawn with the primitives in
`figures.py`. File names and captions are in `refs.py`.

A title that states a direction ("industry trials stop early sooner") repeats a finding.
The findings are checked before anything is drawn (`report.generate`), so a title cannot
outlive the numbers behind it. A figure that shows dates from 2018 on says "descriptive
only" on the figure itself.
"""

from pathlib import Path

import numpy as np

from trialpulse.eda import analysis as an
from trialpulse.eda import figures as fg
from trialpulse.eda.refs import FIGURES
from trialpulse.eda.results import (
    AMENDMENT_LANDMARK_MONTHS,
    COVID_END,
    COVID_START,
    CURVE_MONTHS,
    DESCRIPTIVE,
    PHASE_LABEL,
    STATE_MONTHS,
    TABLE_MONTHS,
    Results,
    followed,
    groups,
)

DISCLAIMER = "Research demo. Not medical advice. Not for patient decision-making."
SPONSOR_LEGEND = {"OTHER": "OTHER (mostly universities and hospitals)"}
# Colors follow the entity in every figure.
STATE_LAYERS = (
    ("Terminated", "terminated", fg.BLUE),
    ("Withdrawn", "withdrawn", fg.ORANGE),
    ("Completed", "completed", fg.AQUA),
    ("Still open", "open", fg.NEUTRAL),
)
COVID_COLORS = {"suspended": fg.VIOLET, "terminated": fg.BLUE, "withdrawn": fg.ORANGE}


def pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def draw(res: Results, out_dir: Path) -> list[Path]:
    paths = {key: out_dir / name for key, (name, _) in FIGURES.items()}
    source = (
        f"Source: ClinicalTrials.gov registry records, data as of {res.cutoff.isoformat()}. "
        f"{DISCLAIMER}"
    )
    before = res.before.isoformat()
    scope = (
        f"Trials open at registration, first posted {res.first_year} to "
        f"{res.last_modeling_day.year}; outcomes observed before {before}"
    )
    short, long_, far = res.short, res.long, TABLE_MONTHS[-1]
    since = "Months since registration"
    from_year = (res.before.year - 0.5, f"From {res.before.year}: {DESCRIPTIVE}")

    curves = list(res.sponsor_curves.items())
    fg.line_chart(
        paths["sponsor"],
        [
            (SPONSOR_LEGEND.get(name, name), months, cif, color)
            for (name, (months, cif)), color in zip(curves, fg.CATEGORICAL, strict=False)
        ],
        title=f"Industry trials stop early sooner; OTHER sponsors are level by {far} months",
        subtitle=f"Cumulative incidence of early stop by lead sponsor class. {scope}.",
        source=source,
        end_labels=[f"{name.split(' ')[0]} {pct(float(cif[-1]))}" for name, (_, cif) in curves],
        x_label=since,
        x_ticks=list(range(0, CURVE_MONTHS + 1, 12)),
    )

    by_year = groups(res.year)
    year_series = []
    for horizon, color in ((short, fg.BLUE), (long_, fg.ORANGE)):
        shown = followed(by_year, f"cif_{horizon}m")
        year_series.append(
            (
                f"Within {horizon} months",
                np.array([row["group"] for row in shown], dtype=np.float64),
                np.array([row[f"cif_{horizon}m"] for row in shown], dtype=np.float64),
                color,
                f"{horizon} months {pct(shown[-1][f'cif_{horizon}m'])}",
            )
        )
    all_years = [float(row["group"]) for row in by_year]
    fg.line_chart(
        paths["year"],
        [series[:4] for series in year_series],
        title=f"After {res.first_year}, the early-stop rate is steady across registration years",
        subtitle=(
            f"Cumulative incidence of early stop by year of registration. {scope}. A year is "
            "shown only where all of its trials could be followed for the whole horizon."
        ),
        source=source,
        end_labels=[series[4] for series in year_series],
        x_label="Year of registration",
        x_ticks=all_years,
        x_tick_labels=[str(int(year)) for year in all_years],
        markers=True,
        right=0.80,
        x_pad=0.3,
    )

    phases = [row for row in res.phase if row["group"] in an.PHASE_ORDER]
    fg.dot_ranges(
        paths["phase"],
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
    fg.stacked_states(
        paths["states"],
        months,
        [(label, res.states[key], color) for label, key, color in STATE_LAYERS],
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
        paths["naive"],
        [(label, months, res.states[key], color) for label, key, _, color in estimates],
        title="Ignoring completion overstates the early-stop risk",
        subtitle=f"Two estimates of the probability of an early stop. {scope}.",
        source=source,
        end_labels=[f"{end} {pct(res.state(key, STATE_MONTHS))}" for _, key, end, _ in estimates],
        x_label=since,
        x_ticks=list(range(0, STATE_MONTHS + 1, 24)),
    )

    fg.dot_ranges(
        paths["signals"],
        [(row["signal"], row["cif_without"], row["cif_with"]) for row in res.amendments_cif],
        ("Without the signal", "With the signal"),
        (fg.INK_MUTED, fg.BLUE),
        title=f"What the record shows at {AMENDMENT_LANDMARK_MONTHS} months, and what follows",
        subtitle=(
            f"Cumulative incidence of early stop in the {long_} months after the "
            f"{AMENDMENT_LANDMARK_MONTHS}-month landmark, with and without each signal, among "
            f"the trials the signal applies to. Landmarks and outcomes before {before}."
        ),
        source=source,
        x_label=f"Cumulative incidence of early stop within {long_} months of the landmark",
        reference=(res.amendment_overall, f"All trials {pct(res.amendment_overall)}"),
    )

    lag = [row for row in res.registration_years if not row["descriptive"]]
    lag_years = np.array([row["year"] for row in lag], dtype=np.float64)
    lag_series = (
        ("Registered after the start month", "share_after_start", fg.BLUE),
        ("More than 1 year after the start month", "share_over_a_year_late", fg.ORANGE),
    )
    fg.line_chart(
        paths["timing"],
        [
            (label, lag_years, np.array([row[key] for row in lag], dtype=np.float64), color)
            for label, key, color in lag_series
        ],
        title="Registration after the start month, by year of registration",
        subtitle=(
            "Share of trials registered after the calendar month of their start date. Trials "
            f"open at registration, first posted {lag[0]['year']} to {lag[-1]['year']}."
        ),
        source=source,
        end_labels=[pct(lag[-1][key]) for _, key, _ in lag_series],
        x_label="Year of registration",
        x_ticks=[float(year) for year in lag_years],
        x_tick_labels=[str(int(year)) for year in lag_years],
        markers=True,
        right=0.90,
        x_pad=0.3,
    )

    post_years = [int(row["year"]) for row in res.post_dates]
    shares = [float(row["share_estimated"]) for row in res.post_dates]
    estimated = [y for y, s in zip(post_years, shares, strict=True) if s == 1.0]
    partial = [y for y, s in zip(post_years, shares, strict=True) if 0.0 < s < 1.0]
    switch = (
        f"Post dates are estimated through {estimated[-1]}"
        if estimated and estimated == post_years[: len(estimated)]
        else "Share of versions with an estimated post date"
    )
    fg.bars_by_year(
        paths["post_dates"],
        post_years,
        shares,
        title=switch,
        subtitle=(
            "Share of cohort-trial versions whose post date (the version clock) is ESTIMATED, "
            "by year of posting."
        ),
        source=source,
        x_label="Year of posting",
        labeled=[post_years[0], *partial, post_years[-1]],
        divider=from_year if post_years[-1] >= res.before.year else None,
    )

    covid_x = 1970 + res.covid["months"].astype("datetime64[M]").astype(np.int64) / 12
    peak_name, peak_rate, peak_at = res.covid_peak()
    tick_years = list(range(COVID_START.year, COVID_END.year + 2))
    fg.line_chart(
        paths["covid"],
        [
            (label, covid_x, res.covid[f"{key}_per_1000"], COVID_COLORS[key])
            for key, label in an.COVID_SERIES
        ],
        title="The first COVID months show as suspensions, not as stops",
        subtitle=(
            f"{DESCRIPTIVE.capitalize()}, not used for modeling. Events posted in the month "
            "per 1,000 cohort trials under follow-up."
        ),
        source=source,
        end_labels=[label for _, label in an.COVID_SERIES],
        x_label="Month of posting",
        x_ticks=tick_years,
        x_tick_labels=[str(year) for year in tick_years],
        percent=False,
        note=(float(covid_x[peak_at]), peak_rate, f"{peak_name}: {peak_rate:.1f}"),
        right=0.84,
    )
    return [paths[key] for key in FIGURES]
