"""Why trials stop: the reasons sponsors gave for early stops, by month and sponsor class.
Descriptive only: it calls no score endpoint and shows no risk (ADR 0057)."""

import datetime as dt

import streamlit as st
from dashboard import charts, ui

DESCRIPTIVE = (
    "This page describes the free-text reasons sponsors gave when trials stopped early, as "
    "labeled by a text classifier, counted by the month the stop became public. It is "
    "descriptive: TrialPulse's scores are all-cause and do not predict the reason a trial "
    "stops. In the registered test, operational stops were not ranked better than scientific "
    "ones (hypothesis H3, falsified)."
)


def main(page: ui.Page) -> None:
    summary = page.get("/v1/reasons/summary").body
    counts = summary["counts"]
    if not counts:
        st.info("No reason counts are loaded yet.")
        return
    classes = sorted({c["sponsor_class"] for c in counts})
    chosen = st.multiselect("Sponsor class", classes, default=classes, key="classes")
    by_year = st.segmented_control("Period", ["Year", "Month"], default="Year",
                                   key="period") != "Month"  # fmt: skip
    shown = [c for c in counts if c["sponsor_class"] in chosen]
    st.plotly_chart(charts.reasons(shown, by_year), key="reasons")
    totals: dict[str, int] = {}
    for c in shown:
        totals[c["reason"]] = totals.get(c["reason"], 0) + int(c["n"])
    all_stops = sum(totals.values())
    st.dataframe(
        [{"Reason": charts.REASON_NAMES.get(r, r),
          "Kind": "operational" if r in charts.OPERATIONAL
          else "scientific" if r in ("safety", "efficacy") else "other",
          "Early stops": n, "Share": f"{n / all_stops:.1%}" if all_stops else ""}
         for r, n in sorted(totals.items(), key=lambda kv: -kv[1])],
        hide_index=True,
    )  # fmt: skip
    through = summary["labeled_through"]
    after = summary["early_stops_after_labels"]
    if through:
        when = dt.date.fromisoformat(through).isoformat()
        st.caption(
            f"Labeled through {when}."
            + (f" {after:,} early stops recorded after that date are counted, not labeled."
               if after else "")
        )  # fmt: skip
    st.caption(summary["note"])


ui.run(main, "Why trials stop", DESCRIPTIVE)
