"""Static PNG figures for docs/eda.md, drawn with matplotlib.

One visual language for every figure:

- A fixed palette. Colors follow the entity, not its position in a chart: terminated is
  always blue, withdrawn orange, completed aqua, suspended violet; the 12-month horizon is
  blue and the 24-month horizon orange. Each color set was checked for separation under
  color-vision deficiency before use.
- One axis per chart, thin lines, hairline solid gridlines, a legend for two or more
  series, and labels on the marks only where they carry the story. Text is always ink
  colored; the mark beside it carries the identity.
- The font bundled with matplotlib and no file metadata, so the same numbers give the same
  bytes on every run.
"""

import textwrap
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import matplotlib
import numpy as np
import numpy.typing as npt
from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, PathPatch
from matplotlib.path import Path as MplPath
from matplotlib.ticker import FuncFormatter

FloatArray = npt.NDArray[np.float64]
Series = tuple[str, FloatArray, FloatArray, str]  # label, x, y, color

# The font bundled with matplotlib, so every machine draws the same glyphs. Figures are
# built on the Agg canvas directly, so no window system or pyplot state is involved.
matplotlib.rcParams["font.family"] = "DejaVu Sans"

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
AQUA = "#1baf7a"
YELLOW = "#eda100"
MAGENTA = "#e87ba4"
VIOLET = "#4a3aa7"
NEUTRAL = "#d5d4cc"
CATEGORICAL: tuple[str, ...] = (BLUE, ORANGE, AQUA, YELLOW, MAGENTA)

DPI = 150
WIDTH = 8.0  # inches
TEXT_LEFT = 0.04  # figure fraction where the title, subtitle, legend and source start
PLOT_LEFT = 0.085
LABEL_SIZE = 9.0
LINE_WIDTH = 1.6
WRAP = 108  # characters per subtitle line
# Vertical layout, in inches.
TITLE_TOP, SUBTITLE_TOP, SUBTITLE_LINE, LEGEND_ROW, PLOT_GAP = 0.18, 0.50, 0.19, 0.30, 0.14
BOTTOM, SOURCE_BOTTOM = 0.88, 0.10

DOT: dict[str, Any] = {"marker": "o", "markeredgecolor": SURFACE, "markeredgewidth": 1.2}
SMALL: dict[str, Any] = {"fontsize": LABEL_SIZE, "color": INK_SECONDARY}


def _percent(decimals: int = 0) -> FuncFormatter:
    return FuncFormatter(lambda value, _: f"{100 * value:.{decimals}f}%")


def _figure(
    title: str,
    subtitle: str,
    source: str,
    height: float,
    *,
    legend: bool,
    left: float = PLOT_LEFT,
    right: float = 0.78,
) -> tuple[Figure, Axes]:
    """A figure with its title, wrapped subtitle and source line, and one styled axis placed
    below them. The room for a legend row is kept only when the chart has one."""
    fig = Figure(figsize=(WIDTH, height), dpi=DPI, facecolor=SURFACE)
    FigureCanvasAgg(fig)
    lines = textwrap.wrap(subtitle, WRAP)
    used = SUBTITLE_TOP + SUBTITLE_LINE * len(lines) + (LEGEND_ROW if legend else 0.0) + PLOT_GAP
    ax = fig.add_axes((left, BOTTOM / height, right - left, 1 - (used + BOTTOM) / height))
    ax.set_facecolor(SURFACE)
    top: dict[str, Any] = {"va": "top", "ha": "left"}
    fig.text(TEXT_LEFT, 1 - TITLE_TOP / height, title, fontsize=13, fontweight="bold", color=INK,
             **top)  # fmt: skip
    fig.text(TEXT_LEFT, 1 - SUBTITLE_TOP / height, "\n".join(lines), fontsize=9.5,
             color=INK_SECONDARY, linespacing=1.35, **top)  # fmt: skip
    fig.text(TEXT_LEFT, SOURCE_BOTTOM / height, source, fontsize=7.5, color=INK_MUTED,
             va="bottom", ha="left")  # fmt: skip
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.spines["bottom"].set_linewidth(0.8)
    ax.tick_params(length=0, labelsize=LABEL_SIZE, labelcolor=INK_SECONDARY, pad=5)
    ax.set_axisbelow(True)
    return fig, ax


def _grid(ax: Axes, axis: Literal["x", "y"]) -> None:
    ax.grid(visible=True, axis=axis, color=GRIDLINE, linewidth=0.8, linestyle="-")


def _divider(ax: Axes, divider: tuple[float, str], top: float) -> None:
    """A hairline at an x position, with a short text beside its top."""
    ax.axvline(divider[0], color=BASELINE, linewidth=0.8, zorder=1)
    ax.annotate(divider[1], xy=(divider[0], top), xytext=(5, -4), textcoords="offset points",
                va="top", ha="left", fontsize=8.5, color=INK_MUTED)  # fmt: skip


def _x_label(ax: Axes, text: str) -> None:
    ax.set_xlabel(text, labelpad=6, **SMALL)


def _legend(fig: Figure, ax: Axes, handles: Sequence[Any], labels: Sequence[str]) -> None:
    """One row above the plot, starting at the text margin."""
    y = ax.get_position().y1 + 0.5 * PLOT_GAP / fig.get_figheight()
    legend = fig.legend(
        handles, labels, loc="lower left", bbox_to_anchor=(TEXT_LEFT, y), ncol=len(labels),
        frameon=False, fontsize=LABEL_SIZE, handlelength=1.3, handletextpad=0.5,
        columnspacing=1.4, borderaxespad=0.0, borderpad=0.0,
    )  # fmt: skip
    for text in legend.get_texts():
        text.set_color(INK_SECONDARY)


def _line_handle(color: str) -> Line2D:
    return Line2D([], [], color=color, linewidth=LINE_WIDTH, solid_capstyle="round")


def _dot_handle(color: str) -> Line2D:
    return Line2D([], [], color=color, markersize=7, linestyle="none", **DOT)


def _save(fig: Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=DPI, facecolor=SURFACE, metadata={"Software": None})


def spread(positions: Sequence[float], gap: float) -> list[float]:
    """Move positions apart until neighbors are at least `gap` apart, keeping their order
    and staying as close to where they started as that allows."""
    order = sorted(range(len(positions)), key=lambda i: positions[i])
    placed = [positions[i] for i in order]
    for _ in range(200):
        moved = False
        for a in range(len(placed) - 1):
            overlap = gap - (placed[a + 1] - placed[a])
            if overlap > 1e-6:
                placed[a] -= overlap / 2
                placed[a + 1] += overlap / 2
                moved = True
        if not moved:
            break
    out = [0.0] * len(positions)
    for rank, i in enumerate(order):
        out[i] = placed[rank]
    return out


def _side_labels(
    fig: Figure, ax: Axes, items: Sequence[tuple[float, float, str, str]], dots: bool = True
) -> None:
    """A label in ink color to the right of each (x, y), with a dot in the series color on
    the point when `dots` is set. Labels that would collide are moved apart vertically; the
    dot stays on the point."""
    fig.canvas.draw()
    pixels = [float(ax.transData.transform((x, y))[1]) for x, y, _, _ in items]
    gap = 1.3 * LABEL_SIZE * DPI / 72
    for (x, y, text, color), target, pixel in zip(items, spread(pixels, gap), pixels, strict=True):
        if dots:
            ax.plot([x], [y], color=color, markersize=7, clip_on=False, zorder=5, **DOT)
        ax.annotate(text, xy=(x, y), xytext=(8, (target - pixel) * 72 / DPI),
                    textcoords="offset points", va="center", ha="left",
                    annotation_clip=False, **SMALL)  # fmt: skip


def line_chart(
    path: Path,
    series: Sequence[Series],
    *,
    title: str,
    subtitle: str,
    source: str,
    end_labels: Sequence[str],
    x_label: str,
    x_ticks: Sequence[float],
    x_tick_labels: Sequence[str] | None = None,
    percent: bool = True,
    markers: bool = False,
    divider: tuple[float, str] | None = None,
    note: tuple[float, float, str] | None = None,
    right: float = 0.78,
    x_pad: float = 0.0,
) -> None:
    """Lines on one shared axis from zero, each named at its end. `divider` draws a hairline
    at an x position with a short text beside it (for example where the descriptive-only
    years begin), and `note` labels one point of the plot."""
    fig, ax = _figure(title, subtitle, source, 4.6, legend=len(series) > 1, right=right)
    _grid(ax, "y")
    top = 1.12 * max(float(np.max(y)) for _, _, y, _ in series)
    last_x = max(float(x[-1]) for _, x, _, _ in series)
    ax.set_ylim(0, top)
    ax.set_xlim(min(x_ticks) - x_pad, max(max(x_ticks), last_x) + x_pad)
    ax.set_xticks(list(x_ticks))
    if x_tick_labels is not None:
        ax.set_xticklabels(list(x_tick_labels))
    _x_label(ax, x_label)
    if percent:
        ax.yaxis.set_major_formatter(_percent())
    for _, x, y, color in series:
        ax.plot(x, y, color=color, linewidth=LINE_WIDTH, solid_capstyle="round",
                solid_joinstyle="round", marker="o" if markers else None, markersize=5,
                markeredgecolor=SURFACE, markeredgewidth=1.0, clip_on=False, zorder=3)  # fmt: skip
    if divider is not None:
        _divider(ax, divider, top)
    if note is not None:
        ax.annotate(note[2], xy=(note[0], note[1]), xytext=(8, 0), textcoords="offset points",
                    va="center", ha="left", fontsize=LABEL_SIZE, color=INK)  # fmt: skip
    ends = [
        (float(x[-1]), float(y[-1]), text, color)
        for (_, x, y, color), text in zip(series, end_labels, strict=True)
    ]
    _side_labels(fig, ax, ends)
    if len(series) > 1:
        _legend(fig, ax, [_line_handle(color) for *_, color in series], [s[0] for s in series])
    _save(fig, path)


def dot_ranges(
    path: Path,
    rows: Sequence[tuple[str, float, float]],
    names: tuple[str, str],
    colors: tuple[str, str],
    *,
    title: str,
    subtitle: str,
    source: str,
    x_label: str,
    reference: tuple[float, str] | None = None,
) -> None:
    """One row per category with two dots on a shared percent axis, joined by a hairline.
    The second value is labeled on the plot; the table beside the figure carries the rest."""
    lines = len(textwrap.wrap(subtitle, WRAP))
    height = SUBTITLE_TOP + SUBTITLE_LINE * lines + LEGEND_ROW + PLOT_GAP + BOTTOM
    height += 0.36 * len(rows) + 0.25
    left = TEXT_LEFT + (0.068 * max(len(label) for label, _, _ in rows) + 0.15) / WIDTH
    fig, ax = _figure(title, subtitle, source, height, legend=True, left=left, right=0.93)
    _grid(ax, "x")
    largest = max(max(a, b) for _, a, b in rows)
    ax.set_xlim(-0.09 * largest, 1.18 * largest)  # room for a value label left of a low dot
    ax.set_ylim(len(rows) - 0.4, -0.85)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([label for label, _, _ in rows], color=INK)
    ax.xaxis.set_major_formatter(_percent())
    _x_label(ax, x_label)
    ax.spines["bottom"].set_visible(False)
    if reference is not None:
        ax.axvline(reference[0], color=BASELINE, linewidth=0.8, zorder=1)
        ax.annotate(reference[1], xy=(reference[0], -0.85), xytext=(5, -3),
                    textcoords="offset points", va="top", ha="left", fontsize=8.5,
                    color=INK_MUTED)  # fmt: skip
    for i, (_, first, second) in enumerate(rows):
        ax.plot([first, second], [i, i], color=BASELINE, linewidth=1.2, zorder=2)
        for value, color in ((first, colors[0]), (second, colors[1])):
            ax.plot([value], [i], color=color, markersize=8, zorder=3, **DOT)
        side = 1 if second >= first else -1
        ax.annotate(f"{100 * second:.1f}%", xy=(second, i), xytext=(9 * side, 0),
                    textcoords="offset points", va="center", ha="left" if side > 0 else "right",
                    fontsize=LABEL_SIZE, color=INK)  # fmt: skip
    _legend(fig, ax, [_dot_handle(colors[0]), _dot_handle(colors[1])], list(names))
    _save(fig, path)


def stacked_states(
    path: Path,
    months: FloatArray,
    layers: Sequence[tuple[str, FloatArray, str]],
    *,
    title: str,
    subtitle: str,
    source: str,
    x_label: str,
) -> None:
    """Shares that add up to 1, stacked from the baseline with a surface-colored gap between
    layers. Each layer is named at the right edge with its final share."""
    fig, ax = _figure(title, subtitle, source, 4.6, legend=True, right=0.80)
    lower = np.zeros_like(months)
    labels: list[tuple[float, float, str, str]] = []
    for label, share, color in layers:
        upper = lower + share
        ax.fill_between(months, lower, upper, color=color, linewidth=0, zorder=2)
        ax.plot(months, upper, color=SURFACE, linewidth=1.4, zorder=3)
        middle = float(lower[-1] + share[-1] / 2)
        labels.append((float(months[-1]), middle, f"{label} {100 * share[-1]:.1f}%", color))
        lower = upper
    ax.set_xlim(float(months[0]), float(months[-1]))
    ax.set_ylim(0, 1)
    ax.set_xticks(list(np.arange(months[0], months[-1] + 1, 12)))
    ax.yaxis.set_major_formatter(_percent())
    _x_label(ax, x_label)
    _side_labels(fig, ax, labels, dots=False)
    handles = [Patch(facecolor=color, edgecolor="none") for *_, color in layers]
    _legend(fig, ax, handles, [label for label, *_ in layers])
    _save(fig, path)


def _rounded_bar(ax: Axes, x: float, height: float, width: float, color: str) -> None:
    """A bar that is square at the baseline and rounded at its data end."""
    (x0, y0), (x1, y1) = ax.transData.transform([(0, 0), (1, 1)])
    radius = 3.0 * DPI / 96  # pixels
    rx = min(radius / abs(x1 - x0), width / 2)
    ry = min(radius / abs(y1 - y0), height)
    left, right = x - width / 2, x + width / 2
    corners = [(left, height - ry), (left, height), (left + rx, height),
               (right - rx, height), (right, height), (right, height - ry)]  # fmt: skip
    line, curve = MplPath.LINETO, MplPath.CURVE3
    codes = [MplPath.MOVETO, line, curve, curve, line, curve, curve, line, MplPath.CLOSEPOLY]
    outline = MplPath([(left, 0), *corners, (right, 0), (left, 0)], codes)
    ax.add_patch(PathPatch(outline, facecolor=color, edgecolor="none", zorder=3))


def bars_by_year(
    path: Path,
    years: Sequence[int],
    shares: Sequence[float],
    *,
    title: str,
    subtitle: str,
    source: str,
    x_label: str,
    labeled: Sequence[int],
    divider: tuple[float, str] | None = None,
) -> None:
    """One share per year as thin bars from a zero baseline, with the values that carry the
    story (the `labeled` years) written above their bars. `divider` draws a hairline at an x
    position with a short text beside it, as in `line_chart`."""
    fig, ax = _figure(title, subtitle, source, 4.2, legend=False, right=0.95)
    _grid(ax, "y")
    ax.set_xlim(years[0] - 0.7, years[-1] + 0.7)
    ax.set_ylim(0, 1.12)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.yaxis.set_major_formatter(_percent())
    ax.set_xticks(list(years))
    ax.set_xticklabels([str(year) if year % 2 == 0 else "" for year in years])
    _x_label(ax, x_label)
    if divider is not None:
        _divider(ax, divider, 1.12)
    fig.canvas.draw()
    for year, share in zip(years, shares, strict=True):
        if share > 0:
            _rounded_bar(ax, year, share, 0.5, BLUE)
        if year in labeled:
            ax.annotate(f"{100 * share:.0f}%", xy=(year, share), xytext=(0, 4),
                        textcoords="offset points", va="bottom", ha="center",
                        fontsize=LABEL_SIZE, color=INK)  # fmt: skip
    _save(fig, path)
