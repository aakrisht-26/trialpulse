# 0023. M1 trains on registrations whose horizon has passed

- Date: 2026-10-10
- Status: **Proposed.** Built in Step 9 without asking, and waiting for Aakrisht's review. Decision 2 (the censoring weights by sponsor class) is his own instruction from the review of Step 7 and is recorded here for completeness.
- Refines CLAUDE.md Section 9 (M1, "static IPCW LightGBM binary classifier at L0 only, one per horizon") and how M1 reads the training rows of Section 6. No locked definition changes: the training rows of an origin, their labels (ADR 0016), the evaluation rows and the metrics are untouched. This is ADR 0015 applied to a horizon instead of an interval.

## Context

Section 6 gives the training rows of origin T: the landmarks before T, with every outcome censored at T. Section 9 makes M1 a binary classifier on the landmark 0 rows, weighted for censoring. Read literally, M1 trains on every landmark 0 row before T: a row is a case if the trial stopped early within the horizon, a control if it did not, and a row censored before the horizon has no label and passes its weight to the labeled rows (IPCW).

For the registrations of the last H months before T, that label exists only if the trial ended before T. A trial that was still open on T is censored before its horizon. So among the newest registrations, the rows with a label are the ones that stopped early or completed within months, and the share of early stops among them is two and a half to six times the share among the older rows:

| Origin, horizon | Labeled rows whose horizon had passed (early stops) | Labeled rows whose horizon had not passed (early stops) |
| --- | --- | --- |
| 2016, 12 months | 80,828 (1,703, 2.1%) | 941 (107, 11.4%) |
| 2016, 24 months | 63,980 (3,440, 5.4%) | 4,403 (603, 13.7%) |
| 2017, 12 months | 96,536 (1,984, 2.1%) | 1,029 (135, 13.1%) |
| 2017, 24 months | 78,143 (4,144, 5.3%) | 4,632 (630, 13.6%) |

The weights do what they promise on average: the weighted share of early stops among all training rows is 2.03% under the literal reading and 2.08% as built (2016, 12 months). They cannot do it for a model that sees when a trial was registered. This censoring is administrative: the censoring time of an open trial is T minus its first-post date, so it is a function of the registration date, and the model reads the registration year, the number of open trials and other features that grow with calendar time. Given the registration date, a recent registration that did not end has probability zero of being observed at its horizon, and no weight repairs a probability of zero. The model learns that recent means ended, and every evaluation row is more recent than every training row.

Measured on the development origins, at landmark 0, with the same settings and the same number of trees for both (point estimates, the harness's metrics):

| Origin, horizon | Observed early-stop rate | Mean predicted, as built | Mean predicted, literal reading | AUC, as built | AUC, literal reading |
| --- | --- | --- | --- | --- | --- |
| 2016, 12 months | 1.91% | 1.74% | 24.85% | 0.677 | 0.599 |
| 2016, 24 months | 4.86% | 4.91% | 32.45% | 0.668 | 0.595 |
| 2017, 12 months | 1.96% | 1.81% | 27.85% | 0.700 | 0.624 |
| 2017, 24 months | 4.81% | 4.88% | 37.19% | 0.669 | 0.586 |

About 1% of the 12-month rows and 6% of the 24-month rows are enough to do this, because they are exactly the rows that look most like the trials M1 is asked about.

## Decision

1. **For a horizon of H months, M1 trains on the landmark 0 rows first posted more than H months before the origin** (L + H before T; an outcome dated on the origin is not known on the origin, ADR 0015, so a registration exactly H months before T is left out too). A later registration is not a training row for that horizon, whatever happened to it.
2. **Censoring weights by sponsor class.** Among those rows, a row can still be censored before the horizon under the lapse rule (ADR 0014). It gets weight 0, and the labeled rows are weighted by the inverse probability of staying uncensored, from Kaplan-Meier censoring curves within each lead sponsor class, fitted on these training rows, with the pooling threshold of ADR 0017. Training and evaluation then make one assumption about censoring.
3. **The number of trees comes from the last training year that has labels:** the latest 12 months of those registrations (first posted from T minus H minus 12 months to T minus H). A model fitted on the earlier registrations chooses the number of boosting rounds by early stopping on that year; the final model runs that many rounds on every labeled row. Section 9 says "early stopping on the last training year"; the calendar year before T has no usable labels, for the reason above.
4. **Fixed settings in Step 9** (`trialpulse.models.static_clf.PARAMS`: learning rate 0.05, 31 leaves, at least 100 rows per leaf, feature and row subsampling 0.8, L2 penalty 1, fixed seed, deterministic). Tuning belongs to Step 10 (Section 9).
5. **M1 is scored on the landmark 0 rows of an origin's evaluation rows.** The censoring groups are decided on all evaluation rows of the origin, before the landmark filter, so a row carries the same weight under M1 as under M0, and the two can be compared row for row at landmark 0.

## Evidence

- The two tables above (the second from a one-off run on the development origins with 20 resamples; the variant is `StaticClassifier` with decision 1 switched off and the tree counts of the built model).
- `tests/models/test_static_clf.py`: `test_only_registrations_whose_horizon_has_passed_are_trained_on`, `test_the_rounds_come_from_the_last_training_year`, `test_a_row_censored_before_the_horizon_has_no_say` (labels and weights equal those of the evaluation metrics), `test_m1_separates_trials_that_stop_from_trials_that_do_not` (on simulated trials posted after the origin the mean predicted risk equals the observed rate within 3 points; the literal reading overpredicted them by 40%), `test_m1_runs_through_the_walk_forward_harness` (trained before the origin and scored after it, end to end).
- As built, on the development origins: calibration slopes of 0.906 and 1.120 at 12 months and 1.002 and 0.967 at 24 months, intercepts within 0.09 of 0 (docs/results_dev.md).

## Alternatives

- **The literal reading** (every landmark 0 row before T, with censoring weights). Measured above: mean predicted risk of 25% to 37% against an observed 2% to 5%.
- **The literal reading without the calendar features** (registration year, the number of open trials). It closes the visible channel only. Every feature that drifts with calendar time (the sponsor's earlier registrations, the text components, the registry's own conventions) reopens it, and M1 would no longer read all feature families, which is its role as the comparison for M4 at landmark 0.
- **Censoring weights by registration period.** The right repair for censoring that depends on a covariate is to estimate the censoring curve given that covariate. Here the curve given the registration date is a step from 1 to 0 at T minus the registration date, so the weight of a recent control is one over zero. The rows that would carry the information do not exist.

## Consequences

- M1 at 24 months trains on 66,996 of the 99,828 landmark 0 rows of origin 2016, and its newest training registration is two years old on the origin (one year at 12 months). That is the price of a classifier with a fixed horizon, and one reason for the discrete-time models of Step 10: an interval is 6 months long, so they lose the last 6 months only (ADR 0015).
- Step 11's hypothesis "M4 beats M1 at L0" compares M4 with this M1.
- If accepted, CLAUDE.md gains through the Amendments: "ADR 0023, M1's training rows: for a horizon of H months, M1 trains on the landmark 0 rows first posted more than H months before the origin, weighted for lapse censoring by sponsor class on those rows; early stopping uses the latest 12 months of them."
