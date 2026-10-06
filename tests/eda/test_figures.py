"""The chart primitives: label placement, and that each one draws a stable PNG."""

from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

from trialpulse.eda import figures as fg

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TEXT = {"title": "A title", "subtitle": "A subtitle. " * 14, "source": "Source: synthetic."}


def test_spread_leaves_positions_that_are_already_apart() -> None:
    assert fg.spread([10.0, 50.0, 90.0], gap=20) == [10.0, 50.0, 90.0]
    assert fg.spread([], gap=20) == []


def test_spread_separates_close_positions_and_keeps_their_order() -> None:
    positions = [100.0, 103.0, 40.0, 101.0]
    placed = fg.spread(positions, gap=20)
    ranked = sorted(range(4), key=lambda i: positions[i])
    assert ranked == sorted(range(4), key=lambda i: placed[i])
    ordered = sorted(placed)
    assert all(b - a >= 20 - 1e-6 for a, b in pairwise(ordered))
    assert placed[2] == 40.0  # far from the others, so it does not move
    assert np.mean(placed[:2] + placed[3:]) == pytest.approx(np.mean([100, 103, 101]), abs=1.0)


def _draw_all(out: Path) -> list[Path]:
    months = np.arange(0, 25, dtype=np.float64)
    rising = months / 100
    fg.line_chart(
        out / "lines.png",
        [("One", months, rising, fg.BLUE), ("Two", months, rising * 1.02, fg.ORANGE)],
        end_labels=["One 24.0%", "Two 24.5%"],
        x_label="Months",
        x_ticks=[0, 12, 24],
        divider=(12.0, "From here: descriptive only"),
        note=(6.0, 0.06, "A point"),
        markers=True,
        **TEXT,
    )
    fg.line_chart(
        out / "one_line.png",
        [("Only", months, rising, fg.BLUE)],
        end_labels=["Only"],
        x_label="Months",
        x_ticks=[0, 12, 24],
        x_tick_labels=["a", "b", "c"],
        percent=False,
        **TEXT,
    )
    fg.dot_ranges(
        out / "dots.png",
        [("A long category label", 0.02, 0.05), ("Short", 0.06, 0.03)],
        ("Before", "After"),
        (fg.INK_MUTED, fg.BLUE),
        x_label="Share",
        reference=(0.04, "All 4.0%"),
        **TEXT,
    )
    fg.stacked_states(
        out / "stack.png",
        months,
        [
            ("Low", rising, fg.BLUE),
            ("Thin", rising / 20, fg.ORANGE),
            ("Rest", 1 - 1.05 * rising, fg.NEUTRAL),
        ],
        x_label="Months",
        **TEXT,
    )
    fg.bars_by_year(
        out / "bars.png",
        [2015, 2016, 2017, 2018],
        [1.0, 1.0, 0.12, 0.0],
        x_label="Year",
        labeled=[2015, 2017, 2018],
        **TEXT,
    )
    return sorted(out.iterdir())


@pytest.mark.slow
def test_every_primitive_draws_the_same_png_twice(tmp_path: Path) -> None:
    first = _draw_all(tmp_path / "a")
    second = _draw_all(tmp_path / "b")
    assert [p.name for p in first] == [
        "bars.png",
        "dots.png",
        "lines.png",
        "one_line.png",
        "stack.png",
    ]
    for one, two in zip(first, second, strict=True):
        content = one.read_bytes()
        assert content.startswith(PNG_SIGNATURE), one.name
        assert len(content) > 5_000, one.name
        assert content == two.read_bytes(), one.name


def test_the_palette_is_the_validated_one() -> None:
    """The color sets were checked for separation under color-vision deficiency. Changing a
    value means running that check again, so the values are pinned here."""
    assert fg.CATEGORICAL == ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4")
    assert (fg.BLUE, fg.ORANGE, fg.AQUA, fg.VIOLET) == ("#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7")
    assert fg.SURFACE == "#fcfcfb"
