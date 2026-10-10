# 0025. Discrete-time models: how they are fitted, tuned and compared

- Date: 2026-10-10
- Status: **Proposed.** Built in Step 10 without asking, and waiting for Aakrisht's review. Decision 6 (the paired bootstrap) is his instruction from the review of Step 9 and is recorded here with how it was built.
- Refines CLAUDE.md Section 9 (the discrete-time models, tuning, the selection rule) and Section 6 (how the discrete-time formulation is used for training). No locked definition changes: the intervals, the outcomes, the horizons, the origins, the evaluation rows and the metrics are those of Section 6 and ADRs 0015 to 0017.

## Context

Section 9 names the models (M2 and M3 multinomial logistic regressions, M4 LightGBM), the tuning budget (Optuna, 40 trials per model, development origins only, fixed seeds, LightGBM early stopping on the last training year) and the selection rule (best mean AUC at 12 months over the development origins; ties by the Brier score, then by the calibration slope closest to 1). It leaves open how a discrete-time model is trained on an origin, what a tuning trial is scored on, how 40 trials fit on a laptop, and how two models are told apart. ADR 0024 fixes which features each rung has.

## Decision

1. **Training rows.** A model expands the landmark rows of the cohort as of the origin (ADR 0016) into person-period rows with the expansion of the cohort build (`cohort.person_period.expand`), the origin being the end of observation (ADR 0015). The features are the landmark's; the interval number is a feature. The rows carry no censoring weights: a row exists only for an interval a trial was followed through, so a trial censored under the lapse rule contributes the intervals before its censoring and none after.
2. **The linear models (M2, M3).** A number is put on a signed log scale, centered and scaled, with the median and a "not given" column for a missing value; a category is one column per level of the training rows; a flag is 0 or 1 with a "not given" column where it is ever missing; the landmark index is a category and the interval is one column per interval. Every one of these is fitted on the origin's training rows (Section 6). The one setting is the strength of the L2 penalty.
3. **The trees of M4.** The number of boosting rounds is chosen by early stopping on the last training year, read for person-period rows as the intervals that ended in the 12 months before the origin: a model fitted on the intervals that ended earlier is stopped on them, and the final model runs the best number of rounds on every row. The patience is 50 rounds within at most 2,000.
4. **Tuning.** A trial is one setting of a model. For each development origin the model is fitted with it on a seeded tenth of that origin's training trials (every landmark row of a drawn trial) and scored on all of the origin's evaluation rows. The objective is the selection rule of Section 9 applied to settings: the mean IPCW AUC at 12 months over the development origins and all landmark indices, ties by the Brier score and then the slope. The first trial is the default setting. The chosen settings are written to `config/tuned/<model>.json` and committed; every later run reads them, the locked test of Step 11 included, and nothing is tuned there. The ablation rungs of M4 run with M4's settings.
5. **Who can be the champion.** The selection rule compares models over all landmark indices, so its candidates are the models that score every landmark index: M0, M2, M3 and M4. M1 is a model of landmark 0 and is compared there.
6. **Differences between models** come with a paired cluster bootstrap (`trialpulse.eval.compare`): trials are resampled once and both models are scored on each resample. The number reported is the mean of the difference over the development origins; a trial with evaluation rows at both origins is one cluster, so the origins are resampled together. Two models are compared only on rows both scored with the same follow-up, event and censoring group.
7. **LightGBM settings that are not tuned:** the multiclass objective with three classes (continue, early stop, completion), row subsampling at every round, deterministic mode, row-wise histograms and 8 threads.

## Evidence

- `tests/models/test_discrete_time.py`: without covariates the discrete-time CIF matches Aalen-Johansen at the interval boundaries (the required test of Section 9: within 0.004 at 6, 12, 18 and 24 months on 40,000 simulated trials with staggered entry); the person-period rows are those of the cohort build; what each learner is handed (rows, labels, no weights, the validation intervals, the settings); the linear design value by value.
- `tests/models/test_tuning.py`: whole trials are sampled, the search stays in its ranges, the first trial is the default, settings are ranked by the declared rule, and only development origins are prepared.
- `tests/eval/test_compare.py`: a model against itself differs by exactly nothing; the paired interval of two models that agree on most rows is less than a quarter as wide as the interval of one of them; files that do not hold the same rows are refused.
- Timings on the 2016 origin, a quarter of its training trials: the linear fit takes 20 seconds with a pool of linear-algebra threads and 3 seconds on one thread, with the same coefficients; one trial of M4 takes about 100 seconds per origin, so 40 trials on a quarter of the trials of two origins would take about two and a half hours. On a tenth they take under an hour. M4 gives identical predictions with 4 and with 8 threads.

## Alternatives

- **Tune on the training rows only** (for example the log loss on the last training year). It keeps the evaluation rows of the development origins out of the tuning. Section 9 says tuning is on the development origins and defines the selection rule there, which is what those origins are for; the locked origins of 2018 to 2020 are what keeps the result honest.
- **A larger tuning sample with fewer trials, or the 40 trials split over several jobs.** The budget of 40 is in Section 9, and the working agreements allow subsampling trials to stay under an hour. A split study would not be reproducible from one command.
- **Censoring weights on person-period rows.** Needed only if censoring depended on the outcome within an interval given the features. The by-class and by-year calibration tables are where that would show.
- **An interval of the difference from two separate intervals.** The two AUCs move together from resample to resample, so separate intervals overstate the uncertainty of the difference several times over.

## Consequences

- A setting is tuned on a tenth of the trials and used on all of them. The number of trees is chosen again on the full rows by early stopping; the penalty of the linear models and the leaf sizes of the trees are not rescaled, which makes the full fits a little less regularized, relative to their data, than the tuned ones.
- Forty trials scored on the evaluation rows of the development origins make the development results of the tuned models slightly optimistic. Step 11's locked origins are untouched by any of it.
- If accepted, CLAUDE.md gains through the Amendments: "ADR 0025, discrete-time models: person-period rows from the cohort as of the origin, without censoring weights; M4's trees by early stopping on the intervals that ended in the last 12 months; a tuning trial is a setting fitted on a tenth of the training trials and scored by the selection rule of Section 9 on the development origins, and the chosen settings are committed in config/tuned/; the champion is chosen among the models that score every landmark index; differences between models use a paired cluster bootstrap."
