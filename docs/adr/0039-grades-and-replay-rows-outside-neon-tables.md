# 0039. Grades as aggregates in Neon, row-level files outside its tables

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 14 decision 3: Grades 1, monthly files).
- Changes CLAUDE.md Section 12 (step 5 of the daily job, and `monitoring.graded_predictions`) and Step 17 (where replay rows live).

## Context

Row-level grades do not fit the Neon free plan: replay rows are 14.22M rows and 2,341 MB row by row (584 MB compact), and live grades row by row would add 1,541 MB in 12 months. The Model health page needs aggregates only, and the promotion gate needs features at past dates, which Neon does not hold, so it runs on the laptop (ADR 0040).

## Decision

1. **`monitoring.graded_predictions` holds aggregates:** per prediction date, horizon, landmark index and group (all rows, flagged rows), the counts of rows, early stops, completions and censored rows, the sums the metrics need, and the metrics themselves once matured. Section 12's per-row table is replaced.
2. **One score file per month in Neon** (`monitoring.score_files`): a compressed Parquet of the month's last full run, holding the trial, the prediction date, the risks at 12 and 24 months to 4 decimals, the sponsor class, the landmark index, the flag and the model version. Rounding to 4 decimals moves AUC by less than 0.00001 and the calibration slope by at most 0.00004.
3. **Replay rows stay on the laptop** in `data/`, and so do any row-level grades a Step 17 job computes there.
4. **Step 14 grades nothing.** The first 12-month predictions mature a year after go-live and grading is built in Step 17; Section 12's fifth daily step is deferred there. From the first month, Step 14 records what grading needs: the score files, lapse exits and status changes in `live.outcome_events`, and in `monitoring.pipeline_runs` the watermark, `dataTimestamp`, records fetched, versions added, records quarantined, trials rescored and stage durations.

## Alternatives

- **Rebuilding grades from the change-only history:** its 0.01 tolerance is half the median 12-month score and can move AUC by up to 0.0098; never good enough for the gate.
- **MLflow artifacts on DagsHub:** dataset-derived rows on a public service.
- **A second Neon project:** more moving parts for a problem this solves.
- **Weekly files:** 25 MB more a year at 4 decimals; Section 14 needs no weekly grade date.

## Consequences

- About 0.67 MB of aggregates and 7.4 MB of score files a year in Neon.
- The promotion gate cannot read Neon's score files for a challenger; it is a laptop job whose result alone reaches `monitoring.promotions` (ADR 0040).
