# 0078. The replay scores with a model of its own origin

- Date: 2026-11-10
- Status: **Accepted.** Decided by Aakrisht on 2026-11-10 (the Step 17 plan, docs/step17_plan.md, decision 3).

## Context

Step 17's replay runs the scoring and grading code over 2023-01-01 to 2024-06-30 at weekly cadence, from the dataset, to give the Model health page a monitoring history with matured outcomes. The served champion was fitted on all data through 2026-09-25, including the outcomes of 2023 and 2024, so a replay scored by it would be graded on its own training data. With drivers, 78 weekly scorings take about 8 hours.

## Decision

1. **The replay model** is M4 with the tuned settings (config/tuned), fitted as of origin 2023-01-01 exactly as the walk-forward origins were: training rows from the cohort as of the origin (ADR 0016), sponsor priors fitted on those rows, early stopping as ADR 0025. Features of landmark rows the feature build did not hold are computed by the feature build's own code.
2. It is logged to MLflow as a replay model and never registered as champion or challenger.
3. **The dates:** every Friday from 2023-01-06 to 2024-06-28 (78 dates), scored with Step 13's `score()` as of each date, without drivers, in quarterly chunks (one command per quarter, resumable by date, each under an hour). The scores stay in `data/replay/` (ADR 0039); only aggregates leave.
4. **Outcomes** from the final cohort (through the pinned cutoff, 2026-09-25): every 12- and 24-month horizon of the replay has matured by then.
5. The replay is monitoring evidence of the champion's method, labeled "replay" everywhere, never a registered result (ADR 0032).

## Alternatives

- **The served champion:** graded on its own training outcomes.
- **The daily job's code through local Postgres, with drivers:** about 8 hours.
- **A monthly cadence:** 18 points.

## Consequences

- The replay model is a sixth fit of M4 at a new origin; its numbers are never compared with the registered results as if they were results.

## Measured (2026-11-16 and 2026-11-17)

The replay model, fitted as of 2023-01-01: 239,921 trials, 1,052,557 landmark rows, 3,027,077 intervals, 1,536 rounds. All 78 Fridays from 2023-01-06 to 2024-06-28 scored. Grades (1,560 rows; trials with a status reversal, and trials the final cohort had already ended or censored by the prediction date, left out, after the review of 2026-11-17): 12-month AUC mean 0.7506 (0.7356 to 0.7583 over the dates), calibration slope 0.869; 24-month AUC mean 0.7363 (0.7290 to 0.7405), slope 0.842; flagged trials 0.7441 at 12 months. Pooled over the dates with a 95% cluster bootstrap by trial: 0.7507 (0.7441 to 0.7561) for all trials, 0.7439 (0.7322 to 0.7548) flagged, 0.7532 (0.7457 to 0.7597) unflagged; 0.7116 at landmark index 0 (unflagged). The replay sits above the registered 0.72 to 0.73 because it grades every open trial at its true landmark index, older trials included, with features on the scoring date, in 2023 and 2024; at index 0 alone it is close to the registered numbers. Monitoring evidence, never a registered result. The grades were written to Neon on 2026-11-17.
