# Pre-registration

Research demo. Not medical advice. Not for patient decision-making.

An elevated early-stop risk is an operational statement about a registry record; it says nothing about whether a treatment works.

Status: draft, not yet registered.

**Draft for Aakrisht's review, written on 2026-10-20 from the development origins only (2016-01-01 and 2017-01-01).** It has no registration date, so the test lock cannot accept it (ADR 0004). It becomes the registration when, after review and after Step 6 is finished, a commit replaces the status line and this note with a `Registered:` line and removes section 5, and the annotated tag `prereg-v1` is created on that commit and pushed (ADRs 0004 and 0009). No locked origin has been evaluated: no model has been scored on 2018-01-01, 2019-01-01 or 2020-01-01. The only inputs from the locked period are the descriptive aggregates disclosed under H5 and the counts of training rows by origin (ADR 0016), used here for run times only.

## 1. What is registered

- **The model:** M4 as ADR 0026 defines it, the discrete-time LightGBM on every feature family but text (54 features), with the settings committed in `config/tuned/m4.json` (ADR 0025). No recalibration: the candidate of ADR 0027 was measured on the development origins against its rule, which was committed before the measurement, and failed it (the 24-month calibration intercept at landmark 0 moved away from 0 at both origins, +0.053 to +0.055 and +0.176 to +0.235, and the 12-month AUC fell by 0.00125 at the 2017 origin, more than the 0.001 the rule allows).
- **The comparisons:** M0, M1, M2 and M3 as in Steps 9 and 10, for the hypotheses below. The ablation rung `m4-text` may be run as a declared secondary comparison (a paired difference against M4); no hypothesis rests on it.
- **The origins:** 2018-01-01 and 2019-01-01, the locked test (primary). 2020-01-01, the COVID stress test, reported separately and used for H5 only.
- **The rows, the outcomes and the metrics:** Section 6 of CLAUDE.md with its amendments: training labels as of each origin (ADR 0016), IPCW censoring weights by sponsor class (ADR 0017, with one curve for all rows as the sensitivity check), the AUC, Brier score, calibration slope and intercept, and lift at 10%, with 95% cluster-bootstrap intervals over trials, 1,000 resamples. Differences between models are paired, and a trial with evaluation rows at several origins is one cluster (ADR 0025).
- **The reason labels** (H3): the final distilled classifier of Step 6, trained on the full 10,000-text sample, applied to every early stop. At registration this file records the SHA-256 of the distilled model file (`data/nlp/models/distilled.joblib`) and of the reasons table (`data/nlp/reasons/early_stop_reasons.parquet`); both live in the uncommitted `data/` folder, so the H3 job refuses to run if either differs and records both hashes in its result file. The development evidence of H3 below uses the provisional classifier of 2026-09-26 and is computed again with the final one before registration.
- **The code:** the commit tagged `prereg-v1`. Every job of Step 11 runs with HEAD at that commit and a clean tree, and every result file records that commit and the tag object (ADR 0004). If a defect forces a code change after the unlock, every job is run again from the new commit, and the change and its reason are reported in `docs/results.md`.
- **A prediction is falsified** when its observed value falls outside its acceptable range, or when the condition of its hypothesis fails. Falsified predictions are reported in `docs/results.md`, not hidden.

## 2. Hypotheses

Each hypothesis states what is measured, what the development origins showed, the predicted value, the acceptable range, and an empty results block. Secondary statistics are reported without a prediction and do not decide a hypothesis.

### H1. At registration, M4 and M1 differ by less than 0.02 in AUC at 12 months, with M4 ahead at 24 months

This replaces "M4 beats M1 at L0".

- **Statistic:** the paired difference in AUC, M4 minus M1, at landmark 0, over the 2018 and 2019 origins together (the mean of the two origins' differences), at 12 and at 24 months, with one paired cluster bootstrap that resamples trials once across both origins and scores both models on each resample.
- **Holds if:** the absolute difference at 12 months is less than 0.02, and the difference at 24 months is above 0 (unrounded point estimates). The paired intervals are reported beside them.
- **Development evidence:** the same statistic over both development origins, 1,000 resamples: at 12 months +0.0071 (-0.0092 to +0.0227); by origin +0.0144 (2016) and -0.0001 (2017); at 24 months +0.0086 (-0.0014 to +0.0181); by origin +0.0099 (2016) and +0.0073 (2017). (Source: `data/results/compare/dev/m4__m1.json`, M4 as ADR 0026 defines it.) Both intervals include 0, which is why the condition is stated on the point estimates.
- **Predicted:** +0.007 at 12 months, acceptable range -0.02 to +0.02; +0.009 at 24 months, acceptable range above 0 up to +0.03.
- **Results:**

### H2. The gain from the amendment signals grows with the landmark index

- **Statistic:** the paired difference in AUC, M3 minus M2, at each landmark index 0 to 6, over the 2018 and 2019 origins together, at 12 months; the slope of a least-squares line through the seven differences against the landmark index, with a paired cluster-bootstrap interval (each resample scores both models at every landmark index). Secondary: the same at 24 months, and the mean difference at landmarks 4 to 6 minus the mean at landmarks 0 and 1.
- **Holds if:** the slope at 12 months is above 0 and its interval excludes 0.
- **Development evidence:** at 12 months the differences were +0.021, +0.048, +0.050, +0.038, +0.045, +0.054 and +0.065 at landmarks 0 to 6: a slope of +0.0049 per landmark index, and +0.020 between the late and the early landmarks. At 24 months: +0.014 rising to +0.070, a slope of +0.0074 and a contrast of +0.031. (Source: `data/results/compare/dev/m3__m2.json`; the interval of the slope was not computed on the development origins, because the statistic is new.)
- **Predicted:** a slope of +0.005 per landmark index at 12 months, acceptable range above 0 up to +0.012.
- **Results:**

### H3. Operational stops are more predictable than scientific stops

- **Statistic:** the champion's scores are evaluated twice at each origin and horizon, on the rows of all landmark indices. Cases are the early stops of one kind within the horizon (operational: accrual, business, funding, administrative, covid19; scientific: safety, efficacy); controls are the rows with no early stop by the horizon; rows with an early stop of the other kind, of reason "other", or without a reason text within the horizon are left out of that score. Censoring weights by sponsor class, from the rows that remain. The statistic is the paired difference (operational minus scientific) in IPCW AUC at 12 months, over the 2018 and 2019 origins together, with a paired cluster-bootstrap interval (trials resampled once across both origins, both scores computed on each resample).
- **Holds if:** the difference is above 0 and its interval excludes 0.
- **Development evidence, provisional labels:** M4's scores of the development origins (identical, row for row, to those of the runs from commit `af27a17`), the provisional classifier of 2026-09-26 (trained on 1,375 LLM labels), 200 resamples. Early stops of reason "other" (282 to 719 per origin and horizon) and without a reason text (290 to 821) are left out.

| Origin | Horizon | Operational stops | AUC, operational | Scientific stops | AUC, scientific | Difference (95% interval) |
| --- | --- | --- | --- | --- | --- | --- |
| 2016 | 12 months | 1,565 | 0.734 | 177 | 0.642 | +0.091 (+0.048 to +0.138) |
| 2016 | 24 months | 3,019 | 0.720 | 354 | 0.630 | +0.090 (+0.053 to +0.127) |
| 2017 | 12 months | 1,721 | 0.739 | 207 | 0.688 | +0.051 (+0.006 to +0.095) |
| 2017 | 24 months | 3,428 | 0.713 | 389 | 0.685 | +0.028 (-0.005 to +0.059) |

Scientific stops are few (177 to 389), so their AUC is the less precise of the two.

- **Predicted:** +0.07 at 12 months (the mean of the two development origins), acceptable range +0.02 to +0.13. Computed again with the final labels before registration, and the prediction and range set from that computation.
- **Results:**

Registry metadata cannot anticipate a safety or efficacy finding (Section 2), which is why this hypothesis is stated and why the headline claims are about operational stops.

### H4. The champion's calibration slope lies in [0.8, 1.2]

- **Statistic:** the pooled calibration slope of the champion at 12 and at 24 months, at each of the 2018 and 2019 origins (four values).
- **Holds if:** all four lie between 0.8 and 1.2, both included.
- **Development evidence:** M4's pooled slopes were 0.863 and 0.925 at 12 months and 0.824 and 0.851 at 24 months (2016 and 2017). The 24-month values are the nearer to the lower bound. The recalibration of ADR 0027 moved them toward 1 on the development origins (to 0.958 and 0.927) but failed its rule on the landmark 0 intercept and, at the 2017 origin, on the AUC, so it is not used.
- **Predicted:** 0.89 at 12 months and 0.84 at 24 months (the means of the development origins). The acceptable range is the hypothesis's own, 0.8 to 1.2; of the four values, the 24-month ones are the likelier to fall below 0.8.
- **Results:**

### H5. The 2020 origin lowers the AUC

**Disclosure: this hypothesis came from descriptive aggregates** (`docs/eda.md`, section 7, labeled descriptive only). They are rates by calendar month from 2017 to 2023, which include the test and stress-test years, and they include monthly rates of terminations and withdrawals: new suspensions rose from 0.63 per 1,000 trials per month in 2017 to 2019 to 6.32 in March to May 2020 (10.6 in April), while terminations (1.94 and 2.03 per 1,000) and withdrawals (1.11 and 1.19) barely moved. The amendment family reads a suspension as a sign of elevated early-stop risk (finding 4 of the EDA, before 2018); a pause ordered by a pandemic is likely a weaker sign. No model was scored on a locked origin, and whether the suspensions of 2020 were followed by early stops at trial level was not examined.

- **Statistic:** the champion's pooled AUC at 12 months at the 2020 origin minus the mean of its pooled AUC at 12 months at the 2018 and 2019 origins. A trial can have evaluation rows at all three origins, so the interval of the difference comes from one cluster bootstrap that resamples trials once across the 2018, 2019 and 2020 origins and computes both sides on each resample (ADR 0025).
- **Holds if:** the difference is below 0.
- **Development evidence:** none; the development origins precede 2020.
- **Predicted:** -0.025, acceptable range -0.08 to -0.005.
- **Results:**

## 3. The champion's numbers on the 2018 and 2019 origins

Predicted for each of the two origins, from the development results of the champion (M4 without the text families; `data/results/walkforward/m4_2016.json` and `m4_2017.json`). The champion has no recalibration (ADR 0027 was not adopted). Each origin's pooled point estimate (all landmark indices) is held against the range; a value outside it at either origin falsifies that prediction.

| Metric (pooled, all landmark indices) | 2016 (development) | 2017 (development) | Predicted for 2018 and for 2019 | Acceptable range |
| --- | --- | --- | --- | --- |
| AUC, 12 months | 0.720 (0.708 to 0.731) | 0.729 (0.718 to 0.740) | 0.725 | 0.700 to 0.750 |
| AUC, 24 months | 0.706 (0.697 to 0.715) | 0.707 (0.698 to 0.716) | 0.707 | 0.689 to 0.725 |
| Brier score, 12 months | 0.0316 (0.0303 to 0.0329) | 0.0301 (0.0289 to 0.0313) | 0.031 | 0.024 to 0.038 |
| Brier score, 24 months | 0.0577 (0.0559 to 0.0595) | 0.0568 (0.0551 to 0.0586) | 0.057 | 0.046 to 0.069 |
| Lift at 10%, 12 months | 3.02 (2.84 to 3.21) | 3.24 (3.05 to 3.43) | 3.1 | 2.6 to 3.7 |
| Lift at 10%, 24 months | 2.68 (2.55 to 2.82) | 2.69 (2.56 to 2.82) | 2.7 | 2.3 to 3.1 |

**How the predictions are derived:**

- **AUC.** The prediction is the mean of the two development origins (0.7245 at 12 months and 0.7065 at 24, rounded to 0.725 and 0.707). No trend is extrapolated: a later origin has more training rows, which helped from 2016 to 2017 (+0.010 at 12 months), but it also has more drift, and the 2017 Final Rule changed what sponsors post. The acceptable range is the rounded prediction plus or minus a half-width built from three parts: the half-width of one origin's bootstrap interval, half the difference between the two development origins, and 0.009 for drift to a later origin (a judgment: no development result measures drift beyond one year). At 12 months that is 0.011 + 0.005 + 0.009 = 0.025; at 24 months 0.009 + 0.000 + 0.009 = 0.018.
- **Brier score.** It moves with the base rate more than with the model. On the development origins it was 0.97 times p(1 - p) at 12 months (0.972 and 0.971) and 0.965 times p(1 - p) at 24 months (0.967 and 0.963), where p is the observed share of early stops within the horizon: 3.36% and 3.20% at 12 months, 6.38% and 6.29% at 24 (Aalen-Johansen, from the calibration tables of the result files). The prediction takes the mean of the two rates, 3.28% and 6.33%, which gives 0.031 and 0.057. The acceptable range takes p from the lower development rate less a fifth to the higher plus a fifth (a judgment, as the base rate drifts by year): 2.56% to 4.04% at 12 months, which gives 0.024 to 0.038, and 5.03% to 7.65% at 24 months, which gives 0.046 to 0.069 (rounded outward).
- **Lift at 10%.** The mean of the development origins (3.13 at 12 months, 2.69 at 24), rounded to one decimal; the range is the union of their two intervals widened by 0.2 either way (2.64 to 3.63 at 12 months, 2.35 to 3.02 at 24), rounded outward.

## 4. Run plan for Step 11

One job per origin and per comparison pair, all from one clean commit recorded in every result file, each logged to the project's MLflow server. Times are scaled from the latest development runs of the same job by the number of training rows (480,046 at the 2017 origin against 567,423, 653,603 and 748,038 at the locked origins, 18% to 56% more; ADR 0016) or, for comparisons, from the per-pair runs of 2026-10-20. On this laptop the same job varies by up to two to three times, so a job estimated above 40 minutes can pass an hour; under the Step 11 run rule such jobs run without a separate proposal, and they are named here.

**Before any locked origin is read:**

1. Step 6 finished: the 10,000-text sample labeled, the final distilled classifier trained, and the reasons table of every early stop rebuilt with it (about 30 minutes once the sample is complete).
2. The development evidence of H3 computed again with the final labels, and this file updated with it (about 5 minutes).
3. Code for the locked run, tested on synthetic data (development work, not a run job): the comparisons and the H2, H3 and H5 statistics over the test origins behind the test lock; the hash check of the reason labels; the UNKNOWN cohort variant and the features of its added rows (section 5); the generator of `docs/results.md`; and a check that every result file of the run names one commit and the tag.
4. The decisions of section 5 made, and Aakrisht's review of this file; then the registration commit (the status line and the draft note replaced by a `Registered: YYYY-MM-DD` line, the SHA-256 hashes of the reason labels (section 1) added, section 5 removed, nothing else changed) and the annotated tag `prereg-v1` on it, pushed (ADRs 0004 and 0009).

**The locked run** (`--unlock-test`; estimated minutes per job):

| Job | 2018 | 2019 | 2020 (stress) |
| --- | --- | --- | --- |
| M0 | 7 | 8 | 9 |
| M1 | 3 | 3 | 3 |
| M2 | 9 | 10 | 12 |
| M3 | 10 | 11 | 13 |
| M4 (the champion) | 35 | 41 | 46 |
| `m4-text` (declared secondary, optional) | 48 | 55 | 63 |

| Comparison or analysis job (over 2018 and 2019 unless named) | Estimated minutes |
| --- | --- |
| M4 against M1 at landmark 0 (H1) | 3 |
| M3 against M2 at every landmark index, with the slope (H2) | 12 |
| Operational against scientific stops (H3) | 20 |
| M4 against M3 | 12 |
| M4 against M0 | 8 |
| 2020 against 2018 and 2019 (H5) | 15 |
| `m4-text` against M4 (declared secondary, optional) | 12 |
| The UNKNOWN cohort variant and its features (once) | 15 |
| The UNKNOWN sensitivity analysis, 2018 | 40 |
| The UNKNOWN sensitivity analysis, 2019 | 46 |
| Generating `docs/results.md` from the result files | 5 |

In all, about 6.5 hours without `m4-text` (396 minutes, 25 jobs) and about 9.5 hours with it (574 minutes, 29 jobs). Measured on 2026-10-20, the development runs of the same jobs took 24 and 30 minutes for M4 (2016 and 2017), 32 and 14 minutes for `m4-text`, and 1 to 8 minutes for each comparison pair over both origins.

**After the run:** the Results block of each hypothesis is filled in a new commit, with every prediction and range above unchanged; `docs/results.md` cites the commit tagged `prereg-v1` and that results commit, and names the one command that regenerates it from the result files.

## 5. Open decisions before registration

This section is for Aakrisht's review and is removed at registration; each decision, once made, is written into sections 1 to 4 (and, where it changes a locked definition, into an ADR).

1. **The UNKNOWN sensitivity analysis** (Section 6 of CLAUDE.md, "treats UNKNOWN as an early stop"; ADR 0014). Proposed: a cohort variant in which a trial whose last state is lapsed at the data cutoff has an early stop at its lapse date (recorded in `data/cohort/outcomes.parquet`) instead of a censoring, and which holds the landmark rows the main cohort leaves out (the trials censored at or before registration, and the landmarks between a trial's last verification and its lapse), with features from the same code. The champion, trained exactly as in the main run (training labels unchanged), scores the variant's evaluation rows; the AUC, Brier score, lift at 10% and calibration slope at 12 and 24 months are reported per origin (2018 and 2019) beside the main results. No hypothesis rests on it. The alternative is to train on the variant's labels as well, which asks a different question (a model of stop or lapse).
2. **The secondary target** (cause-specific CIFs for operational and scientific stops, Section 6). No model of Steps 9 and 10 predicts a cause-specific CIF. Options: (a) fit a cause-specific version of M4 (four outcomes per interval: continue, complete, operational stop, scientific stop) on the development origins after Step 6, then register it as a secondary model, about 35 to 46 minutes per locked origin; (b) report the H3 analysis as the cause-specific results of Step 11 and fit no cause-specific model, which changes a locked definition and needs an ADR.
