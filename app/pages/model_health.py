"""Model health: data freshness, the served model with its registered results, and today's
scope (ADR 0053); and Step 17's monitoring: graded performance, drift and the promotion gate
(ADRs 0076 to 0080)."""

from typing import Any

import streamlit as st
from dashboard import charts, ui

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

    graded_performance(page)
    drift(page)
    promotions(page)


def graded_performance(page: ui.Page) -> None:
    st.header("Graded performance")
    perf = page.get("/v1/monitoring/performance").body
    st.caption(perf["note"])
    grades = perf["grades"]
    if not grades:
        st.info("No graded predictions yet.", icon=":material/pending:")
        return
    choice = st.radio("Horizon", ["12 months", "24 months"], horizontal=True, key="horizon")
    months = 12 if str(choice).startswith("12") else 24
    rows = [g for g in grades if g["horizon_months"] == months]
    st.plotly_chart(charts.graded(rows, "auc", f"AUC at {months} months"), key="graded_auc")
    band = (0.8, 1.2) if months == 12 else None  # the gate reads the 12-month slope (ADR 0080)
    st.plotly_chart(charts.graded(rows, "calibration_slope", "Calibration slope", band),
                    key="graded_slope")  # fmt: skip
    if months == 24:
        st.caption("The 24-month slope is tracked as a known limitation (ADR 0032), not gated.")
    live = [g for g in rows if g["source"] == "live"]
    if not live:
        st.caption("No live prediction graded yet.")
    elif not any(g["matured"] for g in live):
        st.caption(
            f"{len(live)} live prediction dates graded on their follow-up so far (hollow "
            "markers); the first live 12-month horizon passes about one year after go-live."
        )


def drift(page: ui.Page) -> None:
    st.header("Drift")
    answer = page.get("/v1/monitoring/drift").body
    st.caption(answer["note"])
    if answer["week_date"] is None:
        st.info("No drift summary yet: the first comes with the daily job's next weekly full "
                "run.", icon=":material/pending:")  # fmt: skip
        return
    share = answer["features_drifted_share"]
    cols = st.columns(2)
    cols[0].metric("Week", answer["week_date"])
    cols[1].metric("Features drifted", "none" if share is None else f"{share:.0%}")
    st.dataframe(
        [{"Column": c["column_name"], "Kind": c["kind"], "PSI": c["statistic"],
          "Threshold": c["threshold"], "Drifted": "yes" if c["drifted"] else ""}
         for c in answer["columns"]],
        hide_index=True,
    )  # fmt: skip


def promotions(page: ui.Page) -> None:
    st.header("Retraining and promotion")
    gate = page.get("/v1/monitoring/promotions").body
    st.caption(gate["note"])
    if not gate["decisions"]:
        st.info("No retraining has run yet.", icon=":material/pending:")
        return
    st.dataframe(
        [{"Decided": d["decided_at"][:10], "Challenger": d["challenger_version"],
          "Promoted": "yes" if d["promoted"] else "no", "Reason": d["reason"],
          "Calibration slope, 12 months": d["challenger_calibration_slope"]}
         for d in gate["decisions"]],
        hide_index=True,
    )  # fmt: skip


ui.run(main, "Model health")
