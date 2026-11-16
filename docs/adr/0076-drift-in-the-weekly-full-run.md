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

## Amendment of 2026-11-17: long-tailed columns compared by position (decided by Aakrisht, option C)

**The cause.** Asked before relying on "0 of 52", a sensitivity check found a blind spot. Under a shift of half a standard deviation, 8 of the 22 numeric features did not flag, among them `enrollment_count` (shifted by 20,724, PSI 0.044), `n_versions` (by 13 versions, 0.005) and `planned_duration_months` (by 29 months, 0.091). Evidently's PSI (0.7.23, `legacy/calculations/stattests/utils.py`, `get_binned_data`) cuts a numeric column with more than 20 distinct values into equal-width bins over the joint range of both tables (`np.histogram_bin_edges(..., bins="sturges")`), though its docstring speaks of quantiles. On a long-tailed column a few extreme values stretch that range, almost every row falls in one bin, and a shift of the bulk is not seen. A column with at most 20 values is compared value by value, which sees shifts.

**Decision.** Before Evidently's PSI, each value of a numeric column with more than 20 distinct values in the reference is replaced by its position in the 2026-11-03 reference distribution: the reference's CDF, the share of its present values below the value, plus half the share of a point mass the value sits on (an exact value or an atom); inside a percentile bin the bin's share is spread evenly; below the reference's range 0, above it 1 (`drift.position`). Both tables are transformed (`compared_frames`): the reference's sample becomes nearly uniform on [0, 1], so Evidently's equal-width bins act as the reference's quantile bins. Columns with at most 20 values keep the exact-value handling; categorical columns are unchanged. The test stays Evidently's PSI at 0.1; the reference file is unchanged (positions come from the shares it already holds). The HTML report shows positions for those columns.

**The bar, on the step's own comparison** (`compared_frames`, then `evidently_drift`, as `drift_step` calls them; real rows: the 88,759 trials served on 2026-11-09, by Step 13's `score()` on parity 5a's warehouse, against the 2026-11-03 reference). Met in full:

| Case | Features flagged | Risks flagged | PSI |
| --- | --- | --- | --- |
| Synthetic (normal, age-like with point masses, long-tailed, categorical), another draw | 0 of 4 | | at most 0.0068 |
| Synthetic, shifted by half a standard deviation (the categorical one's shares moved) | 4 of 4 | | at least 0.1085 |
| Real, 2026-11-03 reference against 2026-11-09, unchanged | 0 of 52 | 0 of 2 | at most 0.0307 |
| Real, each of the 22 numeric features shifted by half its standard deviation | 22 of 22 numeric | 0 of 2 | at least 2.007 |
| Real, both risks shifted by half their standard deviation | 0 of 52 | 2 of 2 | at least 4.497 |

The daily growth of the sponsor counts, which exact matching had read as drift, stays below 0.002. PSI per column (flagged in bold):

| Column | Compared | Unchanged | Numeric features shifted | Risks shifted |
| --- | --- | --- | --- | --- |
| `allocation` | categories | 0.0000 | 0.0000 | 0.0000 |
| `intervention_model` | categories | 0.0000 | 0.0000 | 0.0000 |
| `primary_purpose` | categories | 0.0000 | 0.0000 | 0.0000 |
| `masking` | categories | 0.0000 | 0.0000 | 0.0000 |
| `healthy_volunteers` | categories | 0.0000 | 0.0000 | 0.0000 |
| `sex` | categories | 0.0000 | 0.0000 | 0.0000 |
| `minimum_age_years` | by position | 0.0003 | **8.6105** | 0.0003 |
| `maximum_age_years` | by position | 0.0007 | **4.0866** | 0.0007 |
| `enrollment_count` | by position | 0.0005 | **8.6980** | 0.0005 |
| `planned_duration_months` | by position | 0.0001 | **2.0070** | 0.0001 |
| `start_anticipated` | categories | 0.0004 | 0.0004 | 0.0004 |
| `registered_after_start` | categories | 0.0000 | 0.0000 | 0.0000 |
| `registration_lag_months` | by position | 0.0006 | **5.4352** | 0.0006 |
| `registration_year` | exact values | 0.0001 | **14.7255** | 0.0001 |
| `title_phase` | categories | 0.0000 | 0.0000 | 0.0000 |
| `n_interventions` | by position | 0.0020 | **31.7278** | 0.0020 |
| `n_intervention_types` | exact values | 0.0001 | **32.2379** | 0.0001 |
| `intervention_drug` | categories | 0.0000 | 0.0000 | 0.0000 |
| `intervention_procedure` | categories | 0.0000 | 0.0000 | 0.0000 |
| `intervention_other` | categories | 0.0000 | 0.0000 | 0.0000 |
| `intervention_biological` | categories | 0.0000 | 0.0000 | 0.0000 |
| `intervention_device` | categories | 0.0000 | 0.0000 | 0.0000 |
| `intervention_behavioral` | categories | 0.0000 | 0.0000 | 0.0000 |
| `intervention_radiation` | categories | 0.0000 | 0.0000 | 0.0000 |
| `intervention_dietary_supplement` | categories | 0.0000 | 0.0000 | 0.0000 |
| `intervention_diagnostic_test` | categories | 0.0000 | 0.0000 | 0.0000 |
| `intervention_genetic` | categories | 0.0000 | 0.0000 | 0.0000 |
| `intervention_combination_product` | categories | 0.0000 | 0.0000 | 0.0000 |
| `status` | categories | 0.0000 | 0.0000 | 0.0000 |
| `n_versions` | by position | 0.0002 | **8.9061** | 0.0002 |
| `n_versions_recent` | by position | 0.0001 | **12.9282** | 0.0001 |
| `months_since_last_update` | by position | 0.0307 | **2.7993** | 0.0307 |
| `primary_completion_slip_months` | by position | 0.0008 | **6.8729** | 0.0008 |
| `completion_slip_months` | by position | 0.0006 | **7.0576** | 0.0006 |
| `start_slip_months` | by position | 0.0002 | **6.7854** | 0.0002 |
| `enrollment_closed` | categories | 0.0000 | 0.0000 | 0.0000 |
| `enrollment_target_ratio` | by position | 0.0000 | **11.1835** | 0.0000 |
| `enrolled_to_first_target_ratio` | by position | 0.0032 | **4.5012** | 0.0032 |
| `ever_suspended` | categories | 0.0000 | 0.0000 | 0.0000 |
| `currently_suspended` | categories | 0.0000 | 0.0000 | 0.0000 |
| `start_overdue` | categories | 0.0002 | 0.0002 | 0.0002 |
| `primary_completion_overdue` | categories | 0.0002 | 0.0002 | 0.0002 |
| `primary_completion_reached` | categories | 0.0000 | 0.0000 | 0.0000 |
| `months_since_status_verified` | by position | 0.0060 | **2.4318** | 0.0060 |
| `sponsor_class` | categories | 0.0002 | 0.0002 | 0.0002 |
| `organization_class` | categories | 0.0001 | 0.0001 | 0.0001 |
| `sponsor_has_identity` | categories | 0.0000 | 0.0000 | 0.0000 |
| `sponsor_prior_registrations` | by position | 0.0014 | **4.7617** | 0.0014 |
| `sponsor_prior_ended` | by position | 0.0011 | **5.6346** | 0.0011 |
| `sponsor_prior_stopped` | by position | 0.0004 | **6.5413** | 0.0004 |
| `landmark_index` | exact values | 0.0003 | **14.7223** | 0.0003 |
| `months_since_t0` | exact values | 0.0003 | **14.7223** | 0.0003 |
| `risk_12m` | by position | 0.0001 | 0.0001 | **4.4967** |
| `risk_24m` | by position | 0.0000 | 0.0000 | **11.2200** |

| Synthetic column | Another draw | Shifted by half a standard deviation |
| --- | --- | --- |
| `normal` | 0.0068 | **0.2573** |
| `age` | 0.0067 | **1.8830** |
| `long_tail` | 0.0061 | **8.5892** |
| `kind` | 0.0003 | **0.1085** |

Rejected alternatives: PSI on the reference's own bins outside Evidently (flagged all 22 shifted features, but also 2 sponsor counts on unchanged data, since exact matching caught their daily growth); keeping the blind spot with a documented note.

**Realistic magnitudes (2026-11-17, asked by Aakrisht; a measurement, no pass bar).** The step's own comparison on the 88,759 trials served on 2026-11-09 against the 2026-11-03 reference, with one change at a time and then all at once:

| Case | PSI of the changed column | Flagged | Columns flagged in all |
| --- | --- | --- | --- |
| Unchanged | at most 0.0005 for these five | no | none |
| `enrollment_count` times 1.2 | 0.1683 | yes | 1 |
| `n_versions` plus 1 | 2.5207 | yes | 1 |
| `planned_duration_months` plus 3 | 0.2526 | yes | 1 |
| `risk_12m` times 1.2 | 0.0219 | no | none |
| `risk_24m` times 1.2 | 0.0247 | no | none |
| All four at once | 0.1683, 2.5207, 0.2526; risks 0.0219 and 0.0247 | the three features | 3 of 52 features |

Two readings. A rise of a fifth in every risk is a shift of calibration that keeps the ranking; PSI at 0.1 does not see it, and the grading of matured predictions (ADR 0077) and the gate's calibration slope (ADR 0080) are what watch it. And even the combined case flags 3 of 52 features (5.8%), under the alert's 20% share (ADR 0081): the alert is meant for broad shifts, while the drift table on the Model health page shows a single column's.
