"""The figures and tables of docs/eda.md, by name.

The document and the findings cite a figure or a table by its name here, and the number is
its position in these lists. Adding or moving one renumbers every citation at once, so a
sentence cannot keep pointing at an old number.
"""

FIGURES: dict[str, tuple[str, str]] = {
    "sponsor": (
        "eda_01_cif_by_sponsor_class.png",
        "Early-stop cumulative incidence by sponsor class",
    ),
    "year": (
        "eda_02_cif_by_registration_year.png",
        "Early-stop cumulative incidence by year of registration",
    ),
    "phase": (
        "eda_03_cif_by_phase_descriptive.png",
        "Early-stop cumulative incidence by phase group",
    ),
    "states": (
        "eda_04_states_since_registration.png",
        "Where trials are, by months since registration",
    ),
    "naive": (
        "eda_05_naive_vs_competing_risks.png",
        "Two estimates of the early-stop probability",
    ),
    "signals": (
        "eda_06_amendment_signals.png",
        "Early-stop cumulative incidence by what the record showed at the landmark",
    ),
    "timing": (
        "eda_07_registration_lag.png",
        "Share of trials registered after their start month",
    ),
    "post_dates": (
        "eda_08_estimated_post_dates.png",
        "Share of versions with an estimated post date",
    ),
    "covid": (
        "eda_09_covid_period_descriptive.png",
        "Monthly suspensions and stops, 2017 to 2023",
    ),
    "reasons": (
        "eda_10_reasons_by_year_descriptive.png",
        "Why trials stopped, by year of the stop",
    ),
}
TABLES: tuple[str, ...] = (
    "sponsor",
    "lapse",
    "year",
    "landmark",
    "phase",
    "states",
    "signals_outcome",
    "signals_cif",
    "timing_year",
    "timing_cif",
    "post_dates",
    "posting_lag",
    "covid",
    "reasons_year",
    "reasons_phase",
    "reasons_sponsor",
)
FIGURE_KEYS: tuple[str, ...] = tuple(FIGURES)
# The addendum of Step 6 exists only with the final reason labels; its figure and tables come
# last, so no other number depends on it.
REASON_FIGURES: tuple[str, ...] = ("reasons",)
REASON_TABLES: tuple[str, ...] = ("reasons_year", "reasons_phase", "reasons_sponsor")


def figure_keys(with_reasons: bool) -> tuple[str, ...]:
    """The figures the document holds, in order."""
    return FIGURE_KEYS if with_reasons else tuple(k for k in FIGURE_KEYS if k not in REASON_FIGURES)


def table_keys(with_reasons: bool) -> tuple[str, ...]:
    """The tables the document holds, in order."""
    return TABLES if with_reasons else tuple(k for k in TABLES if k not in REASON_TABLES)


def figure_number(key: str) -> int:
    return FIGURE_KEYS.index(key) + 1


def table_number(key: str) -> int:
    return TABLES.index(key) + 1


def cite(figures: tuple[str, ...] | str = (), tables: tuple[str, ...] | str = ()) -> str:
    """A citation such as "(Figures 4 and 5, Table 6)"."""

    def part(word: str, numbers: list[int]) -> str:
        if len(numbers) == 1:
            return f"{word} {numbers[0]}"
        return f"{word}s {', '.join(str(n) for n in numbers[:-1])} and {numbers[-1]}"

    figure_keys = (figures,) if isinstance(figures, str) else figures
    table_keys = (tables,) if isinstance(tables, str) else tables
    parts = []
    if figure_keys:
        parts.append(part("Figure", [figure_number(key) for key in figure_keys]))
    if table_keys:
        parts.append(part("Table", [table_number(key) for key in table_keys]))
    if not parts:
        raise ValueError("a citation needs at least one figure or table")
    return f"({', '.join(parts)})"
