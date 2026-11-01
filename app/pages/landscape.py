"""Landscape: open trials and early-stop risk by therapeutic area, today. Descriptive: areas
come from the current record and never feed a score (ADRs 0046 and 0057)."""

import streamlit as st
from dashboard import charts, ui

REASONS = {"reversal": "Status reversal", "beyond_120_months": "Registered 120+ months ago",
           "band_not_validated": "Age band not validated"}  # fmt: skip


def main(page: ui.Page) -> None:
    listing = page.get("/v1/areas").body
    st.info(listing["note"], icon=":material/info:")
    st.caption(f"{listing['open_trials']:,} distinct open trials in all.")
    st.plotly_chart(charts.areas(listing["areas"]), key="areas")
    names = {a["name"]: a["slug"] for a in listing["areas"]}
    chosen = st.selectbox("Area", list(names), key="area")
    slug = names[str(chosen)]
    area = page.get(f"/v1/areas/{slug}/landscape").body
    st.subheader(area["name"])
    st.caption(area["note"])
    cols = st.columns(4)
    cols[0].metric("Open trials", f"{area['open_trials']:,}")
    cols[1].metric("With a score", f"{area['scored']:,}")
    cols[2].metric("Outside the validated range", f"{area['flagged']:,}")
    cols[3].metric("Without a score", f"{sum(area['not_scored'].values()):,}")
    cols = st.columns(4)
    cols[0].metric("Mean risk, 12 months", ui.percent(area["mean_risk_12m"]) or "none")
    cols[1].metric("Median risk, 12 months", ui.percent(area["median_risk_12m"]) or "none")
    cols[2].metric("Mean risk, 24 months", ui.percent(area["mean_risk_24m"]) or "none")
    cols[3].metric("Median risk, 24 months", ui.percent(area["median_risk_24m"]) or "none")
    if area["not_scored"]:
        st.caption(
            "Without a score: "
            + ", ".join(f"{REASONS.get(k, k)} {v:,}" for k, v in sorted(area["not_scored"].items()))
        )
    st.markdown("**Highest early-stop risk at 12 months (top 20)**")
    ui.show_trials(area["top_trials"], key="landscape")


ui.run(
    main,
    "Landscape",
    "How many trials are open in each therapeutic area today, and their early-stop risk. "
    "Many open trials in one area is a sign of competition for the same participants.",
)
