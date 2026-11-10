"""The dashboard's Plotly figures. Each takes the API's JSON as it came."""

import datetime as dt
from collections import defaultdict
from typing import Any

import plotly.graph_objects as go

BEFORE_GO_LIVE = "Before the first served score: amendments only"
REASON_ORDER = ("accrual", "business", "funding", "administrative", "covid19", "safety",
                "efficacy", "other")  # fmt: skip
REASON_NAMES = {
    "accrual": "Accrual (enrollment)",
    "business": "Business decision",
    "funding": "Funding",
    "administrative": "Administrative",
    "covid19": "COVID-19",
    "safety": "Safety",
    "efficacy": "Efficacy",
    "other": "Other",
}
OPERATIONAL = frozenset({"accrual", "business", "funding", "administrative", "covid19"})


def _date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def timeline(points: list[dict[str, Any]], markers: list[dict[str, Any]],
             scored_now: bool) -> go.Figure:  # fmt: skip
    """Every served point at 12 and 24 months, the amendment markers on the same date axis,
    and the span before the first point shaded: the first point is the trial's score at the
    data cutoff, or its first live score for a trial registered later (ADR 0043).
    Flagged points have their own marker (ADR 0055)."""
    fig = go.Figure()
    days = [_date(p["as_of_date"]) for p in points]
    label = "" if scored_now else " (earlier scores)"
    for horizon, dash in (("12", "solid"), ("24", "dot")):
        fig.add_trace(go.Scatter(
            x=days, y=[p[f"risk_{horizon}m"] for p in points], mode="lines+markers",
            name=f"Early-stop risk, {horizon} months{label}", line={"dash": dash, "shape": "hv"},
            hovertemplate="%{x|%Y-%m-%d}: %{y:.1%}<extra></extra>",
        ))  # fmt: skip
    flagged = [p for p in points if p["flagged"]]
    if flagged:
        fig.add_trace(go.Scatter(
            x=[_date(p["as_of_date"]) for p in flagged] * 2,
            y=[p["risk_12m"] for p in flagged] + [p["risk_24m"] for p in flagged],
            mode="markers", name="Outside the validated range",
            marker={"symbol": "diamond-open", "size": 14, "line": {"width": 2}},
            hovertemplate="%{x|%Y-%m-%d}: flagged, may overstate the risk<extra></extra>",
        ))  # fmt: skip
    if markers:
        top = max([p["risk_24m"] for p in points] + [0.05])
        fig.add_trace(go.Scatter(
            x=[_date(m["post_date"]) for m in markers], y=[top * 1.08] * len(markers),
            mode="markers", name="Registry update", marker={"symbol": "triangle-down", "size": 11},
            text=["Changed: " + ", ".join(m["changed"]) for m in markers],
            hovertemplate="%{x|%Y-%m-%d}<br>%{text}<extra></extra>",
        ))  # fmt: skip
    marker_days = [_date(m["post_date"]) for m in markers]
    if days or marker_days:  # the shaded span ends at the first served point
        first = min(days) if days else max(marker_days) + dt.timedelta(days=30)
        start = min([*marker_days, first]) - dt.timedelta(days=60)
        fig.add_vrect(x0=start, x1=first, fillcolor="grey", opacity=0.12, line_width=0,
                      annotation_text=BEFORE_GO_LIVE,
                      annotation_position="top left")  # fmt: skip
    fig.update_layout(
        yaxis={"title": "Early-stop risk", "tickformat": ".0%", "rangemode": "tozero"},
        xaxis={"title": "Date"}, legend={"orientation": "h", "y": -0.25},
        margin={"l": 10, "r": 10, "t": 30, "b": 10}, height=380,
    )  # fmt: skip
    return fig


def areas(rows: list[dict[str, Any]]) -> go.Figure:
    """Open trials per area today (descriptive; ADR 0046)."""
    ordered = sorted(rows, key=lambda a: a["open_trials"])
    fig = go.Figure(go.Bar(
        x=[a["open_trials"] for a in ordered], y=[a["name"] for a in ordered], orientation="h",
        hovertemplate="%{y}: %{x:,} open trials<extra></extra>",
    ))  # fmt: skip
    fig.update_layout(xaxis={"title": "Open trials (a trial counts in every area it belongs to)"},
                      margin={"l": 10, "r": 10, "t": 10, "b": 10},
                      height=max(320, 22 * len(rows)))  # fmt: skip
    return fig


def reasons(counts: list[dict[str, Any]], by_year: bool) -> go.Figure:
    """Labeled early stops per period and reason, stacked (descriptive; ADR 0057)."""
    table: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for c in counts:
        period = c["month"][:4] if by_year else c["month"][:7]
        table[c["reason"]][period] += int(c["n"])
    periods = sorted({p for r in table.values() for p in r})
    fig = go.Figure()
    for reason in [r for r in REASON_ORDER if r in table] + sorted(set(table) - set(REASON_ORDER)):
        fig.add_trace(go.Bar(
            x=periods, y=[table[reason].get(p, 0) for p in periods],
            name=REASON_NAMES.get(reason, reason),
            hovertemplate="%{x}: %{y:,}<extra>" + REASON_NAMES.get(reason, reason) + "</extra>",
        ))  # fmt: skip
    fig.update_layout(barmode="stack", yaxis={"title": "Labeled early stops"},
                      xaxis={"title": "Year the stop became public" if by_year else "Month"},
                      legend={"orientation": "h", "y": -0.25},
                      margin={"l": 10, "r": 10, "t": 10, "b": 10}, height=420)  # fmt: skip
    return fig


SOURCE_NAMES = {
    "replay": "Replay (dataset; model fitted as of 2023-01-01)",
    "live": "Live (served champion)",
}


def graded(grades: list[dict[str, Any]], metric: str, label: str,
           band: tuple[float, float] | None = None) -> go.Figure:  # fmt: skip
    """One metric of the graded predictions over the prediction dates, replay and live apart
    (ADRs 0078 and 0079); a partial-horizon live grade is drawn hollow."""
    fig = go.Figure()
    if band is not None:
        fig.add_hrect(y0=band[0], y1=band[1], line_width=0, fillcolor="rgba(0,0,0,0.06)",
                      annotation_text="Gate range", annotation_position="top left")  # fmt: skip
    for source in ("replay", "live"):
        rows = [g for g in grades if g["source"] == source and g.get(metric) is not None]
        if not rows:
            continue
        fig.add_trace(go.Scatter(
            x=[_date(g["prediction_date"]) for g in rows], y=[g[metric] for g in rows],
            mode="lines+markers" if source == "replay" else "markers",
            name=SOURCE_NAMES[source],
            marker={"symbol": ["circle" if g["matured"] else "circle-open" for g in rows],
                    "size": 8},
            hovertemplate="%{x}: %{y:.3f}<extra>" + SOURCE_NAMES[source] + "</extra>",
        ))  # fmt: skip
    fig.update_layout(yaxis={"title": label}, xaxis={"title": "Prediction date"},
                      legend={"orientation": "h", "y": -0.3},
                      margin={"l": 10, "r": 10, "t": 10, "b": 10}, height=340)  # fmt: skip
    return fig
