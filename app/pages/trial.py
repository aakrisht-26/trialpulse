"""Trial detail: one trial's latest early-stop risk, its drivers, and its timeline with the
registry updates (ADR 0052). A trial without a score shows its reason and no risk."""

import streamlit as st
from dashboard import charts, ui

DRIVER_NOTE = (
    "A driver says what moved the model's score for this trial, not why a trial stops. Each "
    "one raises or lowers the model's odds of an early stop against continuing, averaged over "
    "the horizon's 6-month intervals; those odds rank trials like the served risk, though not "
    "exactly (ADR 0033)."
)


def main(page: ui.Page) -> None:
    nct_id = st.text_input(
        "Registry id (NCT number)", value=st.query_params.get("nct", ""), key="nct_input",
        max_chars=20, placeholder="NCT01234567",
    ).strip().upper()  # fmt: skip
    if not nct_id:
        st.info("Enter a registry id, or open a trial from the Portfolio or Landscape page.")
        return
    if not ui.NCT.match(nct_id):
        st.error("A registry id is NCT followed by 8 digits, for example NCT01234567.")
        return
    st.query_params["nct"] = nct_id
    risk = page.get(f"/v1/trials/{nct_id}/risk", allow=(404,))
    if risk.status == 404:
        st.info(
            f"TrialPulse serves no trial with the id {nct_id}: it serves open interventional "
            f"trials first posted from 2008 on. [See {nct_id} on ClinicalTrials.gov]"
            f"({ui.registry_link(nct_id)})."
        )
        return
    trial = risk.body
    st.subheader(trial["brief_title"] or nct_id)
    st.markdown(
        f"[{nct_id} on ClinicalTrials.gov]({ui.registry_link(nct_id)}) · Sponsor: "
        f"{ui.sponsor_label(trial['sponsor_name'], trial['sponsor_class'])}"
    )
    if trial["areas"]:
        st.caption("Areas (from the current record, display only): " + ", ".join(trial["areas"]))

    score = trial["score"]
    if score is not None:
        cols = st.columns(2)
        cols[0].metric("Early-stop risk within 12 months", ui.percent(score["risk_12m"]))
        cols[1].metric("Early-stop risk within 24 months", ui.percent(score["risk_24m"]))
        st.caption(f"Scored on {score['as_of_date']} by {score['model_version']}.")
        if score["flagged"]:
            st.markdown(ui.FLAG_BADGE)
            st.caption(score["flag_note"])
        st.markdown("**Top drivers**")
        st.caption(DRIVER_NOTE)
        tabs = st.tabs(["12 months", "24 months"])
        for tab, horizon in zip(tabs, ("12", "24"), strict=True):
            rows = [{"Feature": d["feature"], "Value": d["value"],
                     "Effect": f"{d['direction']} the odds of an early stop",
                     "Contribution (log-odds)": d["contribution"]}
                    for d in score["drivers"].get(horizon, [])]  # fmt: skip
            tab.dataframe(rows, hide_index=True)
    else:
        missing = trial["not_scored"]
        st.markdown(":grey-badge[:material/block: No current score]")
        st.markdown(f"**Reason:** {missing['reason_text']} (code `{missing['reason']}`)")
        st.caption(f"Recorded on {missing['as_of_date']}. No risk and no drivers are shown.")

    st.markdown("**Timeline**")
    timeline = page.get(f"/v1/trials/{nct_id}/timeline", allow=(404,))
    if timeline.status == 404:
        st.info("This trial has no served points and no registry updates to show yet.")
        return
    line = timeline.body
    if score is None and line["points"]:
        st.caption("Earlier scores: this trial has no current score.")
    st.plotly_chart(charts.timeline(line["points"], line["markers"], score is not None),
                    key="timeline")  # fmt: skip
    st.caption(line["note"])


ui.run(main, "Trial detail")
