# 0076. Drift in the weekly full run

- Date: 2026-11-10
- Status: **Accepted.** Decided by Aakrisht on 2026-11-10 (the Step 17 plan, docs/step17_plan.md, decision 1).

## Context

Section 14 asks for a weekly Evidently report on feature and score drift against the training reference. Neon holds no features: the daily job computes the 54 features of the trials it scores and keeps only scores (ADR 0037), and only its weekly full run computes them for every open trial (ADR 0045). The dataset stays on the laptop (ADR 0040), so the reference can leave it only as an aggregate. Every Evidently release from 0.7 on requires plotly below 6, and the dashboard pins plotly 7.

## Decision

1. **Where:** a drift step at the end of every full run, only after publication has committed. A failure of the step is recorded in the run's details and opens or reuses a "Drift step failed" issue; it never fails the run, never undoes publication and never blocks the next day.
2. **The reference** is an aggregate made on the laptop: per feature, the shares of ten quantile bins (numbers) or of each category (categories), and the share missing, over the served champion's training rows; the same for the served risks at the data cutoff. It is committed as `config/drift_reference.json`.
3. **The test:** Evidently's data drift preset with the PSI method and its default threshold (0.1) on every feature and both risks, against a fixed-seed sample of 5,000 drawn from the reference's bins. One row per column and week goes to `monitoring.drift`; the HTML report is a GitHub Actions artifact.
4. **Evidently's environment:** a `drift` dependency group that uv declares as conflicting with the `dashboard` group; the daily job installs it in place of the dashboard's, and CI runs the drift tests in that environment.

## Alternatives

- **A weekly laptop job:** needs the laptop on each week and recomputes what the full run already computed.
- **Weekly feature files on Neon:** 250 to 500 MB a year, past the 700 MB line.
- **A raw reference sample:** row-level data derived from the dataset would leave the laptop (ADR 0040).
- **Plotly below 6 for the dashboard:** a major-version change to a deployed page for a job's dependency.

## Consequences

- `monitoring.drift` grows by about 56 rows a week (under 0.3 MB a year).
- The first live drift summary comes with the first full run after this is deployed (a full run every 7 days, ADR 0045).
