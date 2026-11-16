# 0076. Drift in the weekly full run

- Date: 2026-11-10
- Status: **Accepted.** Decided by Aakrisht on 2026-11-10 (the Step 17 plan, docs/step17_plan.md, decision 1).

## Context

Section 14 asks for a weekly Evidently report on feature and score drift against the training reference. Neon holds no features: the daily job computes the features of the trials it scores and keeps only scores (ADR 0037), and only its weekly full run computes them for every open trial (ADR 0045). The dataset stays on the laptop (ADR 0040), so the reference can leave it only as an aggregate. Every Evidently release from 0.7 on requires plotly below 6, and the dashboard pins plotly 7.

## Decision

1. **Where:** a drift step at the end of every full run, only after publication has committed. A failure of the step is recorded in the run's details and opens or reuses a "Drift step failed" issue; it never fails the run, never undoes publication and never blocks the next day.
2. **The reference** is an aggregate made on the laptop, over the served champion's training rows (the final cohort's landmark rows): per column, the share missing and the shares of each category, of each value (a numeric column with at most 100 values), or of 100 percentile bins (a continuous column); the same for the served risks at the data cutoff. The columns are the 53 features M4 reads as the live path computes them; the 54th, `sponsor_stop_rate`, is computed after them from the sponsor class and the two sponsor counts, which are checked. It is committed as `config/drift_reference.json` (55 columns, about 55 KB, shares only).
3. **The test:** Evidently's data drift preset with the PSI method and its default threshold (0.1) on the 53 features and both risks, against a sample of 5,000 drawn without randomness from the reference's shares. With ten bins the sample's tails read as drift on unchanged data (PSI up to 0.59 on synthetic integers), so discrete columns keep exact values and continuous ones 100 bins (unchanged data: PSI at most 0.022). One row per column and week goes to `monitoring.drift`; the HTML report is a GitHub Actions artifact.
4. **Evidently's environment:** a `drift` dependency group that uv declares as conflicting with the `dashboard` group; the daily job installs it in place of the dashboard's, and CI runs the drift tests in that environment.

## Alternatives

- **A weekly laptop job:** needs the laptop on each week and recomputes what the full run already computed.
- **Weekly feature files on Neon:** 250 to 500 MB a year, past the 700 MB line.
- **A raw reference sample:** row-level data derived from the dataset would leave the laptop (ADR 0040).
- **Plotly below 6 for the dashboard:** a major-version change to a deployed page for a job's dependency.

## Consequences

- `monitoring.drift` grows by about 56 rows a week (under 0.3 MB a year).
- The first live drift summary comes with the first full run after this is deployed (a full run every 7 days, ADR 0045).

## Amendment of 2026-11-16: the reference at the seed date (decided by Aakrisht)

**Why.** Against the champion's training rows, the first local full run (2026-11-16) found 17 of 53 features drifted (32%) on its first day, above the 20% alert line of ADR 0081, so an "Alert: drift" issue would open on every full run from about 2026-11-21. That is structure, not drift: landmark rows of trials registered from 2008 to 2026 against the trials open today.

**Decision.** The reference is the features and risks of the 88,426 trials served at the seed date 2026-11-03: Step 13's `score()` on the rebootstrap's combined warehouse, the inputs of ADR 0074's reference check, its risks checked against that check's reference file (equal within 1e-12). It is the same kind of aggregate, made on the laptop (ADR 0040): shares only, committed as `config/drift_reference.json` (54 columns). The training-rows comparison is kept as the one-off measurement below, never as the alert's reference (`uv run python -m trialpulse.monitor.drift training-rows` writes it to `data/drift/`).

**The one-off measurement (training rows).** On the 88,759 trials served on 2026-11-09 (Step 13's `score()` on parity 5a's warehouse; the features equal the live path's, ADR 0038): 17 of 53 features at or above PSI 0.1 (32.1%), neither risk. The largest: `registration_year` 3.69, `open_interventional_trials` 8.35, `completion_slip_months` 0.325, `months_since_t0` and `landmark_index` 0.313, `primary_completion_slip_months` 0.309, `months_since_last_update` 0.305, `planned_duration_months` 0.283; the local full run's drift step gave the same 17.

**Two method changes made with it (during the unattended run, flagged for review).**
1. **Point masses kept exactly.** The first reference built at the seed date still flagged 7 of 53 features on the trials served six days later (`maximum_age_years` 0.272, `registration_lag_months` 0.171, `enrollment_target_ratio` 0.157, `minimum_age_years` 0.137, `enrollment_count` 0.125, `primary_completion_slip_months` 0.114, `open_interventional_trials` 18.4). Those columns hold most of their rows at round values (an age limit of 18 or 65, an enrollment of 60), which the sample spread inside their percentile bins, so Evidently's re-binning read a difference that is not in the data. A value holding at least 0.5% of a continuous column is now kept exactly (`atoms` and `atom_shares`); bins cover the rest. Alternative: computing PSI on the reference's own bins without Evidently.
2. **`open_interventional_trials` leaves the PSI columns,** like `sponsor_stop_rate`: it is one count shared by every trial on a date, so against any fixed reference date it drifts by construction. The step compares 52 features and both risks. Alternative: keep it as an always-drifted column (about 2 points of the alert's share).

**Result.** Against the new reference, the trials served on 2026-11-09: **0 of 52 features drifted** (largest PSI 0.044), neither risk.

**Sensitivity (2026-11-17, asked by Aakrisht before relying on the result): a blind spot, open for his decision.** The same comparison on shifted copies, against the same references: synthetic columns (a normal one, an age-like one with point masses, a categorical one) flag 0 of 3 on another draw and 3 of 3 under a shift of half a standard deviation. The features of the trials served on 2026-11-09 flag 0 of 52 as they are, and 14 of 52 with each of the 22 numeric features shifted by half its standard deviation (whole steps for counts); shifting the two risks by half theirs flags both (PSI 0.149 and 0.476). The 8 numeric features that do not flag even so are long-tailed: `enrollment_count` (shifted by 20,724, PSI 0.044), `n_versions` (by 13, 0.005), `n_versions_recent`, `planned_duration_months` (by 29 months, 0.091), `completion_slip_months`, `primary_completion_slip_months`, `enrollment_target_ratio` and `enrolled_to_first_target_ratio`. The cause is Evidently's PSI itself (0.7.23, `legacy/calculations/stattests/utils.py`, `get_binned_data`): a numeric column with more than 20 values is cut into equal-width bins (`np.histogram_bin_edges(..., bins="sturges")`) over the joint range, so a few extreme values put almost every row in one bin and a shift of the bulk is not seen. A prototype computing PSI on the reference's own bins flags all 22 shifted features, but also 2 sponsor counts on unchanged data (exact atoms catch the daily growth of large sponsors' counts). Not changed: the decision is Aakrisht's.
