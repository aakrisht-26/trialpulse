# 0016. Walk-forward training labels are built as of each origin

- Date: 2026-10-07
- Status: **Accepted** by Aakrisht on 2026-10-07 (Step 4 review, open question 1): "Rebuild walk-forward training labels as of each origin, so a training row's outcome, censoring, lapse resolution and reversal exclusion use only versions posted before the origin. Evaluation rows keep outcomes through the data cutoff."
- Refines CLAUDE.md Section 6 ("Walk-forward origins", "Reversals") and the Step 8 harness.

## Context

Section 6 says: "For origin T, training rows are landmarks with L before T, with every outcome administratively censored at T." Step 4 built the cohort once, at the data cutoff, and the harness truncated it at T: an event dated T or later became a censoring at T. That hides later events, but three things were still decided with hindsight:

- **The UNKNOWN rule (ADR 0014).** "A later version resolves a lapse." A trial that looked lapsed on T and reported again afterwards had training rows as if T had known it would report. A trial that lapsed after T was censored at an old verification date, although on T it was an open trial.
- **Reversals.** A trial whose terminal status was reversed after T was missing from training, although on T it was an ordinary completed or stopped trial.
- **The population.** "Interventional in at least one version" counted versions posted after T.

A model trained on T could not have had those rows, and production retraining (Step 17) will not have them either. The Step 4 review sized the difference for the 2016 origin; the build now reports it for every origin (see Evidence).

## Decision

1. **For each walk-forward origin T, the training rows come from the cohort built as of T:** only versions with a post date before T are known, and observation ends on T.
2. What follows from it:
   - an event counts only if its version was posted before T; otherwise the trial is censored on T;
   - a trial whose last known state is lapsed on T is censored at that state's status verified date, whether or not it reported later; a trial not yet lapsed on T is open on T;
   - a reversal excludes a trial only if it was posted before T;
   - the study type decides the population from the versions posted before T;
   - a person-period interval exists only if it ended on or before T (ADR 0015).
3. **Evaluation rows do not change:** landmarks with T <= L < T + 12 months from the final cohort, scored against outcomes observed through the data cutoff.
4. **The cohort build writes the training rows of every origin** (`data/cohort/training/origin_<date>/landmarks.parquet` and `person_period.parquet`), with the same rules and the same code as the final cohort (`CohortRules.as_of(T)`). The harness reads the file of its origin and refuses training rows that hold a landmark, an event or a censoring dated after the origin. There is no longer a step that truncates the final cohort.

## Evidence

Landmark rows as of each origin, against the final cohort's landmarks before T truncated at T (`docs/data_audit.md`, part 2):

| Origin T | Rows as of T | Final cohort, truncated at T | Only as of T | Only with hindsight | Same row, other label | Same label, other end date |
| --- | --- | --- | --- | --- | --- | --- |
| 2016-01-01 | 399,905 | 405,251 | 13,968 | 19,314 | 0 | 13,960 |
| 2017-01-01 | 480,046 | 481,985 | 16,232 | 18,171 | 0 | 17,097 |
| 2018-01-01 | 567,423 | 564,612 | 20,051 | 17,240 | 0 | 19,554 |
| 2019-01-01 | 653,603 | 651,168 | 22,429 | 19,994 | 0 | 23,593 |
| 2020-01-01 | 748,038 | 742,153 | 25,461 | 19,576 | 0 | 26,639 |

- The 2016 row equals the numbers an independent reviewer derived with separate SQL in the Step 4 review (399,905 against 405,251; 13,968; 19,314; 13,960).
- No event label differs on a row both versions hold: truncation at T already hid later events. What changes is which rows exist (about 8% of them at the 2016 origin) and when a censored row ends.
- `test_a_training_label_cannot_change_when_later_versions_are_rewritten`: every version posted on or after T is rewritten or dropped and later versions are added to every trial; the training rows of T are identical. The same rewrite changes the final cohort and the training rows of a later origin.
- On the real warehouse, for each of the five origins: every version posted on or after T was rewritten (another status, study type, sponsor class and dates) and one more version dated T was added to every trial; in a second run all those versions were dropped. For the 2016 origin that is 2,935,347 of 4,444,542 versions. The rebuilt training rows equal the files of the build in both runs, at every origin (0 landmark rows and 0 person-period rows differ). As a control, rewriting from 31 days before T changes them (2016 origin: 22,606 landmark rows only in the rebuilt set and 27,018 only in the file).
- M0 on the development origins with these labels: with one censoring curve, as before, the AUC is unchanged to four decimals at 12 months (0.5435 at the 2016 origin, 0.5514 at the 2017 origin), so the labels did not change how M0 ranks trials. The calibration slope at 12 months moves from 0.884 to 0.943 and from 1.090 to 1.125, and at 24 months from 1.217 to 1.422 and from 1.320 to 1.446: the fitted curves per sponsor class do change.

## Alternatives

- **Keep the hindsight labels and state the limitation** in the preregistration and the model card. Simpler, but the training set would not be reproducible from what was known at T, and the reported results would rest on rows no real retraining could have.
- **Rebuild only the UNKNOWN censoring as of T and keep the reversal exclusion with hindsight,** as Section 6 words it ("exclude the trial from training and evaluation"). A reversal posted after T is information from the future like any other.

## Consequences

- Section 6's reversal clause now reads: a trial with a reversal is excluded from evaluation, and from the training rows of every origin after the reversal was posted. Before that, it trains with the label it had.
- The cohort build takes about 15 seconds more and writes ten more Parquet files (about 2.8 million landmark rows and 7.7 million person-period rows over the five origins).
- Features are not affected: a feature at landmark L already uses only versions posted on or before L (Section 8). Step 7 joins its features to the training rows of each origin and to the evaluation rows.
- The discrete-time models (Step 10) train on the person-period file of their origin. `person_period.training_rows`, which truncated the final table, is removed.
- Tuning with early stopping "on the last training year" (Section 9) uses rows of the same as-of file.
- **The EDA reads the cohort as of 2018-01-01** (Aakrisht, 2026-10-07): its modeling-relevant sections use `training/origin_2018-01-01/`, for which the build also writes the outcomes table of each origin. Before, they read the final cohort cut at that date, which kept the same hindsight. Its descriptive sections keep the final cohort.
