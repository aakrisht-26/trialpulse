# 0077. Grading in a monthly workflow

- Date: 2026-11-10
- Status: **Accepted.** Decided by Aakrisht on 2026-11-10 (the Step 17 plan, docs/step17_plan.md, decision 2).

## Context

Section 14 asks for performance on matured predictions (AUC at 12 months for predictions issued at least 12 months ago) and partial-horizon AUC for recent ones; ADR 0039 keeps grades as aggregates and keeps one score file a month in Neon; ADR 0032 asks for the calibration slope to be tracked.

## Decision

1. `grade.yml`, a monthly GitHub workflow (and on dispatch), runs `trialpulse.monitor.grade --target neon`: it reads the score files, `live.outcome_events` and the run dates from Neon, and writes aggregates to `monitoring.graded_predictions` (source 'live'). It reads no dataset and imports no dataset module (ADR 0040).
2. **A prediction's outcome:** the first status change after its date to TERMINATED or WITHDRAWN (an early stop) or COMPLETED (a completion), a lapse exit as a censoring at its date, else censored at the latest data date.
3. **Metrics** are the evaluation harness's (IPCW AUC with censoring weights by sponsor class, ADR 0017; IPCW Brier; calibration slope and intercept; lift at 10%), per prediction date, horizon, landmark index (and all indexes) and group (all rows, flagged rows). A prediction is matured when the latest data date is at least its horizon after it; before that, its metrics are partial-horizon, at the follow-up so far.
4. A dispatch input `fail_on_purpose` raises after reading, to prove the failure issue (Step 17's acceptance).

## Alternatives

- **A step of the daily job:** adds a failure point to the job that serves users; grades move monthly anyway.
- **A laptop task:** the same inputs, but depends on the laptop.

## Consequences

- About 28 rows a month (under 1 MB a year).
- The first matured 12-month grade of a live prediction comes about November 2027.
