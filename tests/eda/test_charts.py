"""What each figure is given to draw, on the synthetic registry.

The chart primitives are replaced by recorders, so these tests see which series, values,
colors and labels reach each figure: a swapped pair of dots, a label on the wrong line or a
missing descriptive-only mark would show here, where a comparison of PNG bytes cannot say
what is wrong.
"""

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from trialpulse.cohort.audit import MODELING_EDA_BEFORE
from trialpulse.config import load_project_config
from trialpulse.eda import analysis as an
from trialpulse.eda import charts
from trialpulse.eda import figures as fg
from trialpulse.eda.refs import FIGURES
from trialpulse.eda.results import (
    DESCRIPTIVE,
    PHASE_LABEL,
    STATE_MONTHS,
    Results,
    compute,
    followed,
    groups,
)

from .conftest import MODELING_YEARS, World

DISCLAIMER = "Research demo. Not medical advice. Not for patient decision-making."
PRIMITIVES = ("line_chart", "dot_ranges", "stacked_states", "bars_by_year")


@pytest.fixture(scope="module")
def results(world: World) -> Results:
    return compute(world.sources, load_project_config(), MODELING_EDA_BEFORE)


@pytest.fixture
def drawn(
    results: Results, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, dict[str, Any]]:
    """The arguments each figure was drawn with, by figure name."""
    calls: list[dict[str, Any]] = []
    for primitive in PRIMITIVES:

        def record(path: Path, *args: Any, _primitive: str = primitive, **kwargs: Any) -> None:
            calls.append({"primitive": _primitive, "path": path, "args": args, **kwargs})

        monkeypatch.setattr(charts.fg, primitive, record)
    paths = charts.draw(results, tmp_path)
    assert paths == [tmp_path / name for name, _ in FIGURES.values()]
    assert [call["path"] for call in calls] == paths  # one figure per file, in report order
    return dict(zip(FIGURES, calls, strict=True))


def test_every_figure_carries_the_source_and_the_full_disclaimer(drawn: dict[str, dict]) -> None:
    for name, call in drawn.items():
        assert call["source"].endswith(DISCLAIMER), name
        assert "ClinicalTrials.gov" in call["source"], name
        assert "data as of 2026-09-25" in call["source"], name
        assert call["title"], name
        assert "—" not in call["title"] + call["subtitle"], name


def test_the_figures_of_later_dates_say_descriptive_only(drawn: dict[str, dict]) -> None:
    assert drawn["phase"]["subtitle"].startswith(PHASE_LABEL)
    assert drawn["covid"]["subtitle"].startswith(
        f"{DESCRIPTIVE.capitalize()}, not used for modeling"
    )
    position, text = drawn["post_dates"]["divider"]
    assert (position, text) == (2017.5, "From 2018: descriptive only")
    # The other figures show nothing dated 2018 or later.
    assert max(drawn["timing"]["args"][0][0][1]) == 2017
    assert max(max(series[1]) for series in drawn["year"]["args"][0]) <= 2016
    for name in ("sponsor", "year", "states", "naive", "signals"):
        assert "before 2018-01-01" in drawn[name]["subtitle"], name


def test_the_sponsor_figure_labels_each_line_with_its_own_class(
    drawn: dict[str, dict], results: Results
) -> None:
    call = drawn["sponsor"]
    series = call["args"][0]
    assert [label.split(" ")[0] for label, *_ in series] == [
        "INDUSTRY",
        "OTHER",
        "NIH",
        "Remaining",
    ]
    assert [color for *_, color in series] == list(fg.CATEGORICAL[:4])
    for (label, _, cif, _), end, name in zip(
        series, call["end_labels"], results.sponsor_curves, strict=True
    ):
        assert label.startswith(name.split(" ")[0])
        assert end == f"{name.split(' ')[0]} {100 * cif[-1]:.1f}%"
        assert np.array_equal(cif, results.sponsor_curves[name][1])


def test_the_year_figure_shows_only_the_years_followed_for_the_whole_horizon(
    drawn: dict[str, dict], results: Results
) -> None:
    short, long_ = drawn["year"]["args"][0]
    assert (short[0], short[3]) == ("Within 12 months", fg.BLUE)
    assert (long_[0], long_[3]) == ("Within 24 months", fg.ORANGE)
    assert short[1].tolist() == [2014, 2015, 2016]
    assert long_[1].tolist() == [2014, 2015]
    by_year = {row["group"]: row for row in groups(results.year)}
    assert short[2].tolist() == [by_year[y]["cif_12m"] for y in (2014, 2015, 2016)]
    assert long_[2].tolist() == [by_year[y]["cif_24m"] for y in (2014, 2015)]
    assert drawn["year"]["end_labels"] == [
        f"12 months {100 * short[2][-1]:.1f}%",
        f"24 months {100 * long_[2][-1]:.1f}%",
    ]
    assert [row["group"] for row in followed(groups(results.year), "cif_24m")] == [2014, 2015]


def test_the_phase_and_signal_figures_put_each_value_under_its_own_name(
    drawn: dict[str, dict], results: Results
) -> None:
    rows, names, colors = drawn["phase"]["args"]
    assert names == ("Within 12 months", "Within 24 months")
    assert colors == (fg.BLUE, fg.ORANGE)
    phases = [r for r in results.phase if r["group"] in an.PHASE_ORDER]
    assert rows == [(r["group"], r["cif_12m"], r["cif_24m"]) for r in phases]

    rows, names, colors = drawn["signals"]["args"]
    assert names == ("Without the signal", "With the signal")
    assert colors == (fg.INK_MUTED, fg.BLUE)
    assert rows == [(r["signal"], r["cif_without"], r["cif_with"]) for r in results.amendments_cif]
    assert any(without != with_signal for _, without, with_signal in rows)
    value, label = drawn["signals"]["reference"]
    assert value == results.amendment_overall
    assert label == f"All trials {100 * value:.1f}%"


def test_the_state_figures_keep_one_color_per_state(
    drawn: dict[str, dict], results: Results
) -> None:
    months, layers = drawn["states"]["args"]
    assert months is results.states["months"]
    assert [(label, color) for label, _, color in layers] == [
        ("Terminated", fg.BLUE), ("Withdrawn", fg.ORANGE), ("Completed", fg.AQUA),
        ("Still open", fg.NEUTRAL),
    ]  # fmt: skip
    for (_, share, _), key in zip(
        layers, ("terminated", "withdrawn", "completed", "open"), strict=True
    ):
        assert share is results.states[key]

    competing, naive = drawn["naive"]["args"][0]
    assert competing[2] is results.states["early_stop"]
    assert naive[2] is results.states["naive_early_stop"]
    assert competing[0].startswith("Competing-risks estimate")
    assert naive[0].startswith("Completion treated as censoring")
    assert drawn["naive"]["end_labels"] == [
        f"Competing risks {100 * results.state('early_stop', STATE_MONTHS):.1f}%",
        f"As censoring {100 * results.state('naive_early_stop', STATE_MONTHS):.1f}%",
    ]


def test_the_timing_figure_shows_modeling_years_only(
    drawn: dict[str, dict], results: Results
) -> None:
    late, very_late = drawn["timing"]["args"][0]
    assert late[1].tolist() == list(MODELING_YEARS)
    modeling = [r for r in results.registration_years if not r["descriptive"]]
    assert late[2].tolist() == [r["share_after_start"] for r in modeling]
    assert very_late[2].tolist() == [r["share_over_a_year_late"] for r in modeling]
    assert late[0] == "Registered after the start month"
    assert very_late[0] == "More than 1 year after the start month"
    assert drawn["timing"]["end_labels"] == [
        f"{100 * modeling[-1]['share_after_start']:.1f}%",
        f"{100 * modeling[-1]['share_over_a_year_late']:.1f}%",
    ]
    assert drawn["timing"].get("divider") is None


def test_the_post_date_and_covid_figures(drawn: dict[str, dict], results: Results) -> None:
    years, shares = drawn["post_dates"]["args"]
    assert years == [r["year"] for r in results.post_dates]
    assert shares == [r["share_estimated"] for r in results.post_dates]
    assert drawn["post_dates"]["title"] == "Post dates are estimated through 2016"
    assert drawn["post_dates"]["labeled"][0] == years[0]

    series = drawn["covid"]["args"][0]
    assert [(label, color) for label, _, _, color in series] == [
        ("Suspended", fg.VIOLET), ("Terminated", fg.BLUE), ("Withdrawn", fg.ORANGE),
    ]  # fmt: skip
    assert drawn["covid"]["end_labels"] == [label for label, *_ in series]
    for (_, _, rate, _), key in zip(series, ("suspended", "terminated", "withdrawn"), strict=True):
        assert rate is results.covid[f"{key}_per_1000"]
    assert drawn["covid"]["percent"] is False
    name, rate, at = results.covid_peak()
    x, y, text = drawn["covid"]["note"]
    assert (name, y, text) == ("April 2020", rate, f"April 2020: {rate:.1f}")
    assert x == pytest.approx(2020 + 3 / 12)
    assert series[0][1][at] == x
