"""Model health: data freshness, the served model with its registered results, and today's
scope. Drift and graded performance arrive with the monitoring stage, Step 17 (ADR 0053)."""

from typing import Any

import streamlit as st
from dashboard import ui

STALE_AFTER_DAYS = 3  # CLAUDE.md Section 14: watermark lag at most 3 days
METRICS = (("auc", "AUC"), ("brier", "Brier score"), ("lift", "Lift at 10%"),
           ("calibration_slope", "Calibration slope"))  # fmt: skip
REASONS = {"reversal": "Status reversal", "beyond_120_months": "Registered 120+ months ago",
           "band_not_validated": "Age band not validated"}  # fmt: skip


def _interval(m: dict[str, Any]) -> str:
    text = f"{m['value']:.4f}"
    if "low" in m and "high" in m:
        text += f" ({m['low']:.4f} to {m['high']:.4f})"
    return text


def main(page: ui.Page) -> None:
    answer = page.get("/v1/model")
    info = answer.body

    st.header("Freshness")
    as_of = answer.data_as_of
    if as_of is None:
        st.warning("The data-as-of date is not known: no daily run has loaded data yet.")
    else:
        age = (ui.today() - as_of).days
        st.metric("Data as of", as_of.isoformat(), f"{age} days old", delta_color="off")
        if age > STALE_AFTER_DAYS:
            st.warning(
                f"The data is {age} days old, more than the {STALE_AFTER_DAYS} days the daily "
                "job allows. Scores may not reflect the latest registry updates."
            )
        else:
            st.success(f"Within the {STALE_AFTER_DAYS}-day freshness target.")

    st.header("Served model")
    statement = info["statement"]
    st.markdown(f"Model version: `{info['model_version'] or 'not recorded'}`")
    if statement is None:
        st.info("The model statement is not loaded yet.")
    else:
        served = statement["served_model"]
        st.markdown(served["description"])
        st.caption(f"Training data through {served['training_data_cutoff']}.")
        results = statement["registered_results"]
        st.subheader("Registered results of the locked test")
        st.caption(
            "From the per-origin models of the locked test (docs/results.md), not from the "
            f"served fit. {results['intervals']}."
        )
        rows = []
        for origin, metrics in sorted(results["origins"].items()):
            for key, label in METRICS:
                for horizon in ("12", "24"):
                    m = metrics.get(f"{key}_{horizon}")
                    if m is not None:
                        rows.append(
                            {
                                "Origin": origin,
                                "Metric": label,
                                "Horizon": f"{horizon} months",
                                "Value": _interval(m),
                            }
                        )
        st.dataframe(rows, hide_index=True)
        covid = results["covid_stress_test"]
        st.caption(f"COVID stress test: {covid['description']}: {_interval(covid)}.")
        st.subheader("Falsified hypotheses")
        for h in statement["falsified_hypotheses"]:
            st.markdown(f"- **{h['id']}**, {h['statement']}: {h['result']}")
        st.subheader("Known limitations")
        for item in statement["known_limitations"]:
            st.markdown(f"- {item}")

    st.header("Today's scope")
    cols = st.columns(3)
    cols[0].metric("Trials with a score", f"{info['scored_trials']:,}")
    share = info["flagged_share"]
    cols[1].metric("Outside the validated range", "none" if share is None else f"{share:.1%}")
    cols[2].metric("Trials without a score", f"{sum(info['not_scored'].values()):,}")
    st.caption(ui.FLAG_CAPTION)
    if info["not_scored"]:
        st.dataframe([{"Reason": REASONS.get(k, k), "Trials": v}
                      for k, v in sorted(info["not_scored"].items())],
                     hide_index=True)  # fmt: skip

    st.header("Drift")
    st.info(
        "Not available yet. Weekly reports of feature and score drift against the training "
        "data arrive with the monitoring stage of the project (Step 17).",
        icon=":material/pending:",
    )
    st.header("Graded performance")
    st.info(
        "Not available yet. Grading arrives with the monitoring stage (Step 17), and the first "
        "12-month predictions mature one year after go-live.",
        icon=":material/pending:",
    )


ui.run(main, "Model health")
