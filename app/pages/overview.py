"""Overview: what TrialPulse is, what each page shows, and how many trials are served."""

import streamlit as st
from dashboard import ui


def main(page: ui.Page) -> None:
    st.markdown(
        "TrialPulse estimates, for open interventional trials on ClinicalTrials.gov, the "
        "probability of an early stop (terminated or withdrawn) within 12 and 24 months, from "
        "registry metadata only, and updates it as sponsors amend their records. It is built "
        "for clinical operations and portfolio teams: a high score means elevated early-stop "
        "risk for operational planning. It is not about whether a treatment works, and it "
        "holds no patient data."
    )
    model = page.get("/v1/model")
    body = model.body
    cols = st.columns(3)
    cols[0].metric("Trials with a score", f"{body['scored_trials']:,}")
    cols[1].metric("Trials without a score",
                   f"{sum(body['not_scored'].values()):,}")  # fmt: skip
    share = body["flagged_share"]
    cols[2].metric("Scores outside the validated range",
                   "none" if share is None else f"{share:.0%}")  # fmt: skip
    st.markdown(
        "- **Portfolio**: a sponsor's open trials ranked by early-stop risk.\n"
        "- **Trial detail**: one trial's risk, its drivers and its timeline with registry "
        "updates.\n"
        "- **Landscape**: open trials and risk by therapeutic area (descriptive).\n"
        "- **Why trials stop**: the reasons sponsors gave for early stops (descriptive).\n"
        "- **Model health**: data freshness, the served model and its registered results."
    )


ui.run(main, "TrialPulse")
