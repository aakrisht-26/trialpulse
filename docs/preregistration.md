# Pre-registration

Research demo. Not medical advice. Not for patient decision-making.

An elevated early-stop risk is an operational statement about a registry record; it says nothing about whether a treatment works.

Registered: 2026-10-21

**Every exposure to the locked period so far.** These show numbers, outcomes or texts from 2018 on:

1. **The development evaluation itself, by design** (Section 6, ADR 0016). The evaluation rows of the 2016 and 2017 origins have landmarks before 2018-01-01, but they are scored against outcomes through the data cutoff. At the 2017 origin, 1,390 of the 2,605 early stops within 12 months (53%) and 3,838 of the 5,053 within 24 months (76%, the last on 2019-12-23) are dated 2018 or 2019; at the 2016 origin, 1,134 of the 4,760 within 24 months (24%) and none within 12. The champion, its features and its tuned settings were chosen on these outcomes, and every development number in this file comes from them. No landmark on or after 2018-01-01 was scored.
2. **The Step 4 sanity table** (`docs/data_audit.md`, part 2): the Aalen-Johansen early-stop curve at 12 and 24 months by sponsor class, from landmark 0 before 2018-01-01 but with outcomes followed to the data cutoff.
3. **The data audits and the feasibility report**: `docs/data_audit.md` part 2 as a whole (the cohort funnel, the landmark rows by index with their later outcomes, the outcomes by first-post year 2008 to 2026, the UNKNOWN counts at the cutoff, the person-period rows, and the training cohorts as of every origin, 2018 to 2020 included), part 1 (status reversals over all years), the Step 2 cohort counts of `docs/feasibility_report.md` (latest status by first-post year), and the UNKNOWN-rule evidence of ADR 0014 (registry UNKNOWN labels at the data cutoff).
4. **The descriptive COVID aggregates** of `docs/eda.md` section 7: monthly rates of suspensions, terminations and withdrawals from 2017 to 2023 (see H5).
5. **The reasons addendum** of `docs/eda.md` section 9 (Tables 14 to 16, Figure 10): the early stops and their reason shares by year of the stop, 2008 to 2026, and pooled over all years by phase group and sponsor class.
6. **The descriptive rows from 2018 on** of `docs/eda.md` Tables 9 and 11 (registration timing and estimated post dates by year; not outcomes).
7. **A check in Step 5** that compared the EDA tables with outcomes followed to the data cutoff instead of censored at 2018-01-01 (cells moved by at most 0.4 points).
8. **The stop reasons of Step 6**: the gold set and the 10,000-text LLM sample are reason texts of trials that stopped in 2000 to 2026, stratified by status and stop year, and the distilled classifier is trained on them, by design (Section 11).
9. **Checks of the dataset against the registry** (Step 2 parts d and e, the Step 3 parity sample): records compared field by field, not outcome rates.

The models, their features and their tuned settings were chosen on the development origins (item 1), whose landmarks all precede 2018-01-01 (Section 10). None of items 2 to 9 was used to choose a model, a feature or a setting of the early-stop models, with one qualification: the evidence of ADR 0014 (item 3) fixed details of the rule that decides when a record is lapsed, a cohort definition that reads no early-stop outcome. The reason classifier is a model of reason texts, not of early stops. H5 came from item 4, as disclosed under H5.

## 1. What is registered

- **The primary claims:** the champion's AUC at 12 months on the 2018 and on the 2019 origin (section 3), and H2. Everything else is secondary.
- **The model:** M4 as ADR 0026 defines it, the discrete-time LightGBM on every feature family but text (54 features), with the settings committed in `config/tuned/m4.json` (ADR 0025). No recalibration: the candidate of ADR 0027 was measured on the development origins against its rule, which was committed before the measurement, and failed it (the 24-month calibration intercept at landmark 0 moved away from 0 at both origins, +0.053 to +0.055 and +0.176 to +0.235, and the 12-month AUC fell by 0.00125 at the 2017 origin, more than the 0.001 the rule allows).
- **The comparisons:** M0, M1, M2 and M3 as in Steps 9 and 10, for the hypotheses below. The ablation rung `m4-text` may be run as a declared secondary comparison (a paired difference against M4); no hypothesis rests on it.
- **The origins:** 2018-01-01 and 2019-01-01, the locked test (primary). 2020-01-01, the COVID stress test, reported separately and used for H5 only.
- **The rows, the outcomes and the metrics:** Section 6 of CLAUDE.md with its amendments: training labels as of each origin (ADR 0016), IPCW censoring weights by sponsor class (ADR 0017, with one curve for all rows as the sensitivity check), the AUC, Brier score, calibration slope and intercept, and lift at 10%, with 95% cluster-bootstrap intervals over trials, 1,000 resamples. Differences between models are paired, and a trial with evaluation rows at several origins is one cluster (ADR 0025).
- **The cause-specific results** are the H3 analysis; no cause-specific model is fitted (ADR 0028).
- **The UNKNOWN sensitivity analysis** (ADR 0029): a cohort variant in which a trial whose last state is lapsed at the data cutoff has an early stop at its lapse date instead of a censoring, holding the rows the main cohort leaves out, with features from the same code. The champion, trained exactly as in the main run, scores its evaluation rows; AUC, Brier score, lift at 10% and calibration slope at 12 and 24 months are reported per test origin beside the main results. Development evidence, from a dry run of the code on the development origins (runs `m4-2016-unknown-as-stop` and `m4-2017-unknown-as-stop`): AUC at 12 months 0.712 and 0.696, at 24 months 0.611 and 0.595, and calibration slope at 24 months 0.453 and 0.398 (2016 and 2017), against 0.720 and 0.729, 0.706 and 0.707, and 0.824 and 0.851 in the main run. No hypothesis rests on it.
- **The reason labels** (H3): the final distilled classifier of Step 6, trained on the full 10,000-text sample, applied to every early stop. They live in the uncommitted `data/` folder, so the registration records their SHA-256 on two lines, each made of the file name, the word `sha256:` and the 64-character hash, as in a line `distilled.joblib sha256:` followed directly by the hash and a line `early_stop_reasons.parquet sha256:` followed directly by the hash (the H3 job also accepts spaces or backticks between the parts, and refuses two different hashes for one file); the H3 job refuses to run if either differs and records both in its result file.
  - distilled.joblib sha256:b8ccf2750b6edb10a350aa34dc46817c4d5e98aac9286015ad0486928a1ccbea
  - early_stop_reasons.parquet sha256:dc40e718e7bd33b7d88466c05f55db49342e253ec8fe5968c8f00c9454588410
- **The code:** the commit tagged `prereg-v1`. Every job of Step 11 runs with HEAD at that commit and a clean tree, and every result file records that commit and the tag object (ADR 0004); `docs/results.md` is generated only from such files (`trialpulse.eval.results` refuses anything else). If a defect forces a code change after the unlock, every job is run again from the new commit, and the change and its reason are reported in `docs/results.md`.
- **A prediction is falsified** when its observed value falls outside its acceptable range, or when the condition of its hypothesis fails. Falsified predictions are reported in `docs/results.md`, not hidden. The predictions and ranges are also given in a machine-readable block at the end of this file, which the generator of `docs/results.md` reads from the tagged commit.

## 2. Hypotheses

Each hypothesis states what is measured, what the development origins showed, the predicted value, the acceptable range with its arithmetic or labeled a judgment, and an empty results block. Secondary statistics are reported without a prediction and do not decide a hypothesis.

### H1. At registration, M4 and M1 differ by less than 0.02 in AUC at 12 months, with M4 ahead at 24 months

This replaces "M4 beats M1 at L0".

- **Statistic:** the paired difference in AUC, M4 minus M1, at landmark 0, over the 2018 and 2019 origins together (the mean of the two origins' differences), at 12 and at 24 months, with one paired cluster bootstrap that resamples trials once across both origins and scores both models on each resample.
- **Holds if:** the absolute difference at 12 months is less than 0.02, and the difference at 24 months is above 0 (unrounded point estimates). The paired intervals are reported beside them.
- **Development evidence:** the same statistic over both development origins, 1,000 resamples: at 12 months +0.0071 (-0.0092 to +0.0227); by origin +0.0144 (2016) and -0.0001 (2017); at 24 months +0.0086 (-0.0014 to +0.0181); by origin +0.0099 (2016) and +0.0073 (2017). (Source: `data/results/compare/dev/m4__m1.json`, M4 as ADR 0026 defines it.) Both intervals include 0, which is why the condition is stated on the point estimates.
- **Predicted:** +0.007 at 12 months and +0.009 at 24 months (the development estimates, rounded).
- **Acceptable range:** at 12 months -0.02 to +0.02, the hypothesis's own band. At 24 months above 0 (the hypothesis's own condition) up to +0.03: the prediction plus the half-width of the development interval plus an allowance for drift, 0.009 + 0.010 + 0.010 = 0.029, rounded up; the allowance is a judgment.
- **Results:**

### H2. The gain from the amendment signals grows with the landmark index

- **Statistic:** the paired difference in AUC, M3 minus M2, at each landmark index 0 to 6, over the 2018 and 2019 origins together, at 12 months; the slope of a least-squares line through the seven differences against the landmark index, with a paired cluster-bootstrap interval (each resample scores both models at every landmark index, each index with the weights of its own rows). Secondary: the same at 24 months, and the mean difference at landmarks 4 to 6 minus the mean at landmarks 0 and 1.
- **Holds if:** the slope at 12 months is above 0 and its interval excludes 0.
- **Development evidence** (1,000 resamples; `data/results/compare/dev/m3__m2.json`, run `compare-m3-vs-m2` from commit `008b42d`): at 12 months the differences were +0.021, +0.048, +0.050, +0.038, +0.045, +0.054 and +0.065 at landmarks 0 to 6; the slope was **+0.0049 per landmark index (+0.0021 to +0.0076)**, and the late-minus-early contrast +0.020 (+0.007 to +0.032). At 24 months: +0.014 rising to +0.070, a slope of +0.0074 (+0.0052 to +0.0096) and a contrast of +0.031 (+0.021 to +0.041). Both slope intervals exclude 0.
- **Predicted:** a slope of +0.005 per landmark index at 12 months (the development estimate, rounded).
- **Acceptable range:** above 0 (the hypothesis's own condition) up to +0.012: the prediction plus the half-width of the development interval plus an allowance for drift, 0.005 + 0.003 + 0.004 = 0.012; the allowance is a judgment.
- **Results:**

### H3. Operational stops are more predictable than scientific stops

- **Statistic:** the champion's scores are evaluated twice at each origin and horizon, on the rows of all landmark indices. The labels and the censoring weights are those of the main metric, estimated on all evaluation rows of the origin with the censoring groups by sponsor class (ADR 0017); only then are rows left out. Cases are the early stops of one kind within the horizon (operational: accrual, business, funding, administrative, covid19; scientific: safety, efficacy); controls are the rows with no early stop by the horizon; the rows with an early stop of the other kind, of reason "other", or without a reason text within the horizon are left out of that score. The statistic is the paired difference (operational minus scientific) in IPCW AUC at 12 months, over the 2018 and 2019 origins together (the mean of the two origins' differences), with a paired cluster-bootstrap interval (trials resampled once across both origins, the weights and both scores computed again on each resample). These are the cause-specific results of Step 11 (ADR 0028).
- **Holds if:** the difference is above 0 and its interval excludes 0.
- **Errors in the reason labels push the difference toward zero:** an operational stop labeled scientific, or the reverse, moves a case into the other score, so each score holds some cases of the other kind and the two AUCs move toward each other. This holds as long as the classifier's errors do not depend on the risk score; the classifier reads only the reason text, which the risk model never sees. Errors into or out of the label other move a score toward the AUC of the stops labeled other instead, and on the test texts they are the larger share (8 of the 40 texts predicted scientific, 17 of the 204 predicted operational). They too push toward zero when the stops labeled other score like the operational ones, as on the development origins: AUC 0.730 and 0.731 at 12 months for the stops labeled other, against 0.729 and 0.738 for the operational ones (2016 and 2017). The final classifier agrees with the reference labels on 76.7% of the 300 test texts (macro-F1 0.769, `docs/results/test_distilled.json`). The reason text is the one each record showed when it was pulled (API v2, 2026-09-23), which can differ from the text posted with the first terminal version.
- **Development evidence, final labels:** the statistic over both development origins with the final reason labels (the distilled classifier trained on the full sample; model file SHA-256 `b8ccf2750b6e`), 1,000 resamples (`data/results/hypotheses/h3-dev.json`, run `h3-dev` from commit `b645bfd`): at 12 months **+0.086 (+0.054 to +0.117)**, at 24 months +0.071 (+0.046 to +0.097); each origin with its own paired interval in the table. Early stops of reason "other" (273 to 722 per origin and horizon) and without a reason text (290 to 821) are left out.

| Origin | Horizon | Operational stops | AUC, operational | Scientific stops | AUC, scientific | Difference |
| --- | --- | --- | --- | --- | --- | --- |
| 2016 | 12 months | 1,556 | 0.729 (0.716 to 0.743) | 195 | 0.636 (0.592 to 0.677) | +0.094 (+0.050 to +0.140) |
| 2017 | 12 months | 1,689 | 0.738 (0.725 to 0.751) | 240 | 0.659 (0.623 to 0.696) | +0.079 (+0.037 to +0.118) |
| 2016 and 2017 (mean) | 12 months |  | 0.734 (0.724 to 0.743) |  | 0.648 (0.618 to 0.677) | +0.086 (+0.054 to +0.117) |
| 2016 | 24 months | 2,978 | 0.719 (0.709 to 0.730) | 397 | 0.625 (0.590 to 0.657) | +0.094 (+0.059 to +0.131) |
| 2017 | 24 months | 3,368 | 0.713 (0.703 to 0.724) | 446 | 0.665 (0.637 to 0.692) | +0.048 (+0.019 to +0.078) |
| 2016 and 2017 (mean) | 24 months |  | 0.716 (0.708 to 0.725) |  | 0.645 (0.621 to 0.669) | +0.071 (+0.046 to +0.097) |

Scientific stops are few (195 to 446), so their AUC is the less precise of the two.
- **Predicted:** +0.09 at 12 months (the development estimate, rounded).
- **Acceptable range:** +0.03 to +0.15: the prediction plus or minus the half-width of the development interval and an allowance for drift and for differences in the labels, 0.09 plus or minus (0.031 + 0.02), rounded outward; the allowance is a judgment.
- **Results:**

Registry metadata cannot anticipate a safety or efficacy finding (Section 2), which is why this hypothesis is stated and why the headline claims are about operational stops.

### H4. The champion's calibration slope lies in [0.8, 1.2]

- **Statistic:** the pooled calibration slope of the champion at 12 and at 24 months, at each of the 2018 and 2019 origins (four values).
- **Holds if:** all four lie between 0.8 and 1.2, both included.
- **Development evidence:** M4's pooled slopes were 0.863 and 0.925 at 12 months and 0.824 and 0.851 at 24 months (2016 and 2017). The 24-month values are the nearer to the lower bound. The recalibration of ADR 0027 moved them toward 1 on the development origins (to 0.958 and 0.927) but failed its rule on the landmark 0 intercept and, at the 2017 origin, on the AUC, so it is not used.
- **Predicted:** 0.89 at 12 months and 0.84 at 24 months (the means of the development origins).
- **Acceptable range:** the hypothesis's own, 0.8 to 1.2 for each of the four values; of the four, the 24-month ones are the likelier to fall below 0.8.
- **Results:**

### H5. The 2020 origin lowers the AUC

**Disclosure: this hypothesis came from descriptive aggregates** (`docs/eda.md`, section 7, labeled descriptive only; item 4 of the exposures above). They are rates by calendar month from 2017 to 2023, which include the test and stress-test years, and they include monthly rates of terminations and withdrawals: new suspensions rose from 0.63 per 1,000 trials per month in 2017 to 2019 to 6.32 in March to May 2020 (10.6 in April), while terminations (1.94 and 2.03 per 1,000) and withdrawals (1.11 and 1.19) barely moved. The amendment family reads a suspension as a sign of elevated early-stop risk (finding 4 of the EDA, before 2018); a pause ordered by a pandemic is likely a weaker sign. No model was scored on a locked origin, and whether the suspensions of 2020 were followed by early stops at trial level was not examined.

- **Statistic:** the champion's pooled AUC at 12 months at the 2020 origin minus the mean of its pooled AUC at 12 months at the 2018 and 2019 origins, each origin with the weights of the main metric. A trial can have evaluation rows at all three origins, so the interval of the difference comes from one cluster bootstrap that resamples trials once across the 2018, 2019 and 2020 origins and computes both sides on each resample (ADR 0025).
- **Holds if:** the difference is below 0.
- **Development evidence:** none; the development origins precede 2020.
- **Predicted:** -0.025, a judgment: no development origin went through a shock like 2020.
- **Acceptable range:** -0.08 to -0.005, a judgment. The upper end is the smallest fall read as a fall; the lower end is about three times the gain of M4 over M3 on the development origins (+0.028), beyond which the drop would say more about the evaluation rows of 2020 than about the suspensions.
- **Results:**

## 3. The champion's numbers on the 2018 and 2019 origins

Predicted for each of the two origins, from the development results of the champion (M4 without the text families; `data/results/walkforward/m4_2016.json` and `m4_2017.json`). The champion has no recalibration (ADR 0027 was not adopted). Each origin's pooled point estimate (all landmark indices) is held against the range; a value outside it at either origin falsifies that prediction. The AUC at 12 months on each origin is a primary claim.

| Metric (pooled, all landmark indices) | 2016 (development) | 2017 (development) | Predicted for 2018 and for 2019 | Acceptable range |
| --- | --- | --- | --- | --- |
| **AUC, 12 months** | 0.720 (0.708 to 0.731) | 0.729 (0.718 to 0.740) | 0.725 | 0.700 to 0.750 |
| AUC, 24 months | 0.706 (0.697 to 0.715) | 0.707 (0.698 to 0.716) | 0.707 | 0.689 to 0.725 |
| Brier score, 12 months | 0.0316 (0.0303 to 0.0329) | 0.0301 (0.0289 to 0.0313) | 0.031 | 0.024 to 0.038 |
| Brier score, 24 months | 0.0577 (0.0559 to 0.0595) | 0.0568 (0.0551 to 0.0586) | 0.057 | 0.046 to 0.069 |
| Lift at 10%, 12 months | 3.02 (2.84 to 3.21) | 3.24 (3.05 to 3.43) | 3.1 | 2.6 to 3.7 |
| Lift at 10%, 24 months | 2.68 (2.55 to 2.82) | 2.69 (2.56 to 2.82) | 2.7 | 2.3 to 3.1 |

**How the predictions are derived:**

- **AUC.** The prediction is the mean of the two development origins (0.7245 at 12 months and 0.7065 at 24, rounded to 0.725 and 0.707). No trend is extrapolated: a later origin has more training rows, which helped from 2016 to 2017 (+0.010 at 12 months), but it also has more drift, and the 2017 Final Rule changed what sponsors post. The acceptable range is the rounded prediction plus or minus a half-width built from three parts: the half-width of one origin's bootstrap interval, half the difference between the two development origins, and 0.009 for drift to a later origin (a judgment: no development result measures drift beyond one year). At 12 months that is 0.011 + 0.005 + 0.009 = 0.025; at 24 months 0.009 + 0.000 + 0.009 = 0.018.
- **Brier score.** It moves with the base rate more than with the model: its range mostly tests the base rate, and the model's skill is the factor of about 0.97 against p(1 - p), where a score that knew only the base rate would have a factor of 1. On the development origins the factor was 0.972 and 0.971 at 12 months and 0.967 and 0.963 at 24, where p is the observed share of early stops within the horizon: 3.36% and 3.20% at 12 months, 6.38% and 6.29% at 24 (Aalen-Johansen, from the calibration tables of the result files). The prediction takes the mean of the two rates, 3.28% and 6.33%, with the factors 0.97 and 0.965, which gives 0.031 and 0.057. The acceptable range takes p from the lower development rate less a fifth to the higher plus a fifth (a judgment, as the base rate drifts by year): 2.56% to 4.04% at 12 months, which gives 0.024 to 0.038, and 5.03% to 7.65% at 24 months, which gives 0.046 to 0.069 (rounded outward).
- **Lift at 10%.** The mean of the development origins (3.13 at 12 months, 2.69 at 24), rounded to one decimal; the range is the union of their two intervals widened by 0.2 either way (2.64 to 3.63 at 12 months, 2.35 to 3.02 at 24), rounded outward. The widening is a judgment.

## 4. Run plan for Step 11

One job per origin and per comparison pair, all from the commit tagged `prereg-v1` with a clean tree, each logged to the project's MLflow server. Times are scaled from the latest development runs of the same job by the number of training rows (480,046 at the 2017 origin against 567,423, 653,603 and 748,038 at the locked origins, 18% to 56% more; ADR 0016) or, for comparisons, from the per-pair runs of 2026-10-20 and 2026-10-21. On this laptop the same job varies by up to two to three times, so a job estimated above 40 minutes can pass an hour; under the Step 11 run rule such jobs run without a separate proposal, and they are named here.

**Before any locked origin is read:**

1. Step 6 finished: the 10,000-text sample labeled, the final distilled classifier trained, and the reasons table of every early stop rebuilt with it.
2. The development evidence of H3 computed with the final labels, and this file updated with it.
3. The code for the locked run, tested on synthetic data: the comparisons over the test origins behind the test lock with the trend of H2 (`trialpulse.eval.compare --origins test`), H3 and H5 (`trialpulse.eval.hypotheses`), the hash check of the reason labels, the UNKNOWN cohort variant and the features of its added rows (`--unknown-as-stop` and `--variant`), and the generator of `docs/results.md` with its check that every result file names the tagged commit (`trialpulse.eval.results`).
4. Aakrisht's review of this file; then the registration commit (the status line and the draft note replaced by a `Registered: YYYY-MM-DD` line, the two hash lines of section 1 added, nothing else changed) and the annotated tag `prereg-v1` on it, pushed (ADRs 0004 and 0009).

**The locked run** (estimated minutes per job). The models, one origin per job:

| Job | 2018 | 2019 | 2020 (stress) |
| --- | --- | --- | --- |
| M0 | 7 | 8 | 9 |
| M1 | 3 | 3 | 3 |
| M2 | 9 | 10 | 12 |
| M3 | 10 | 11 | 13 |
| M4 (the champion) | 35 | 41 | 46 |
| `m4-text` (declared secondary, optional) | 48 | 55 | 63 |

Each is `uv run python -m trialpulse.eval.walkforward --model MODEL --origins YEAR --unlock-test`, with the model and the year of its row and column.

| Comparison or analysis job | Command (`uv run python -m trialpulse.` ...) | Estimated minutes |
| --- | --- | --- |
| M4 against M1 at landmark 0 (H1) | `eval.compare --pairs m4:m1 --origins test --unlock-test` | 3 |
| M3 against M2 at every landmark index, with the slope (H2) | `eval.compare --pairs m3:m2 --origins test --unlock-test` | 16 |
| Operational against scientific stops (H3) | `eval.hypotheses h3 --origins test --unlock-test` | 5 |
| M4 against M3 | `eval.compare --pairs m4:m3 --origins test --unlock-test` | 10 |
| M4 against M0 | `eval.compare --pairs m4:m0 --origins test --unlock-test` | 6 |
| 2020 against 2018 and 2019 (H5) | `eval.hypotheses h5 --unlock-test` | 10 |
| `m4-text` against M4 (declared secondary, optional) | `eval.compare --pairs m4-text:m4 --origins test --unlock-test` | 12 |
| The UNKNOWN cohort variant | `cohort.build --unknown-as-stop` | 2 |
| Its features | `features.build --variant unknown-as-stop` | 15 |
| The UNKNOWN sensitivity analysis, 2018 | `eval.walkforward --model m4 --origins 2018 --unlock-test --variant unknown-as-stop` | 40 |
| The UNKNOWN sensitivity analysis, 2019 | `eval.walkforward --model m4 --origins 2019 --unlock-test --variant unknown-as-stop` | 46 |
| `docs/results.md` from the result files | `eval.results` | 5 |

In all, about 6.3 hours without `m4-text` (378 minutes, 26 jobs: 15 model runs, 6 comparisons and hypotheses, 4 jobs of the UNKNOWN analysis and the document) and about 9.3 hours with it (556 minutes, 30 jobs). Measured on 2026-10-20 and 2026-10-21, the development runs of the same jobs took 24 and 30 minutes for M4 (2016 and 2017), 32 and 14 minutes for `m4-text`, 1 to 12 minutes for each comparison over both origins, and 4 minutes for H3.

**After the run:** the Results block of each hypothesis is filled in a new commit, with every prediction and range above unchanged; `docs/results.md` cites the commit tagged `prereg-v1` and that results commit, and is regenerated by `uv run python -m trialpulse.eval.results`.

## 5. The predictions, machine-readable

The generator of `docs/results.md` reads this block from the tagged commit. Each entry is a prediction above with its acceptable range (both ends included unless `low_exclusive`).

```json predictions
{
 "predictions": [
  {
   "id": "h1_delta_auc_12",
   "statement": "H1: M4 minus M1, AUC at landmark 0, 12 months",
   "predicted": 0.007,
   "low": -0.02,
   "high": 0.02
  },
  {
   "id": "h1_delta_auc_24",
   "statement": "H1: M4 minus M1, AUC at landmark 0, 24 months",
   "predicted": 0.009,
   "low": 0.0,
   "high": 0.03,
   "low_exclusive": true
  },
  {
   "id": "h2_slope_12",
   "statement": "H2: slope of M3 minus M2 per landmark index, 12 months",
   "predicted": 0.005,
   "low": 0.0,
   "high": 0.012,
   "low_exclusive": true
  },
  {
   "id": "h3_difference_12",
   "statement": "H3: operational minus scientific AUC, 12 months",
   "predicted": 0.09,
   "low": 0.03,
   "high": 0.15
  },
  {
   "id": "h5_difference",
   "statement": "H5: AUC at 2020 minus the mean of 2018 and 2019, 12 months",
   "predicted": -0.025,
   "low": -0.08,
   "high": -0.005
  },
  {
   "id": "h4_slope_12_2018",
   "statement": "H4: calibration slope, 12 months, 2018",
   "predicted": 0.89,
   "low": 0.8,
   "high": 1.2
  },
  {
   "id": "h4_slope_24_2018",
   "statement": "H4: calibration slope, 24 months, 2018",
   "predicted": 0.84,
   "low": 0.8,
   "high": 1.2
  },
  {
   "id": "auc_12_2018",
   "statement": "AUC, 12 months, 2018",
   "predicted": 0.725,
   "low": 0.7,
   "high": 0.75
  },
  {
   "id": "auc_24_2018",
   "statement": "AUC, 24 months, 2018",
   "predicted": 0.707,
   "low": 0.689,
   "high": 0.725
  },
  {
   "id": "brier_12_2018",
   "statement": "Brier score, 12 months, 2018",
   "predicted": 0.031,
   "low": 0.024,
   "high": 0.038
  },
  {
   "id": "brier_24_2018",
   "statement": "Brier score, 24 months, 2018",
   "predicted": 0.057,
   "low": 0.046,
   "high": 0.069
  },
  {
   "id": "lift_12_2018",
   "statement": "Lift at 10%, 12 months, 2018",
   "predicted": 3.1,
   "low": 2.6,
   "high": 3.7
  },
  {
   "id": "lift_24_2018",
   "statement": "Lift at 10%, 24 months, 2018",
   "predicted": 2.7,
   "low": 2.3,
   "high": 3.1
  },
  {
   "id": "h4_slope_12_2019",
   "statement": "H4: calibration slope, 12 months, 2019",
   "predicted": 0.89,
   "low": 0.8,
   "high": 1.2
  },
  {
   "id": "h4_slope_24_2019",
   "statement": "H4: calibration slope, 24 months, 2019",
   "predicted": 0.84,
   "low": 0.8,
   "high": 1.2
  },
  {
   "id": "auc_12_2019",
   "statement": "AUC, 12 months, 2019",
   "predicted": 0.725,
   "low": 0.7,
   "high": 0.75
  },
  {
   "id": "auc_24_2019",
   "statement": "AUC, 24 months, 2019",
   "predicted": 0.707,
   "low": 0.689,
   "high": 0.725
  },
  {
   "id": "brier_12_2019",
   "statement": "Brier score, 12 months, 2019",
   "predicted": 0.031,
   "low": 0.024,
   "high": 0.038
  },
  {
   "id": "brier_24_2019",
   "statement": "Brier score, 24 months, 2019",
   "predicted": 0.057,
   "low": 0.046,
   "high": 0.069
  },
  {
   "id": "lift_12_2019",
   "statement": "Lift at 10%, 12 months, 2019",
   "predicted": 3.1,
   "low": 2.6,
   "high": 3.7
  },
  {
   "id": "lift_24_2019",
   "statement": "Lift at 10%, 24 months, 2019",
   "predicted": 2.7,
   "low": 2.3,
   "high": 3.1
  }
 ]
}
```
