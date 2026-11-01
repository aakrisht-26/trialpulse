# 0050. Reason counts through the cutoff, stored as aggregates

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 15 decision 5: option a, counts only).
- Refines CLAUDE.md Section 13 (`/v1/reasons/summary`, the "Why trials stop" page) and decision 10 of Step 14.

## Decision

1. `serving.reason_counts` holds counts of labeled early stops by month of the stop, reason and sponsor class, through the data cutoff. No per-trial label is stored in Neon.
2. A laptop command (`trialpulse.api.reasons`) computes them from the warehouse's `stop_reasons` table, the cohort's outcomes and the versions, and loads them (ADR 0040: aggregates may leave the laptop).
3. Early stops after the labels are counted, not labeled: the daily job keeps the count of early stops recorded after the labeled date, and the summary states both.
4. Labeling new stops on the laptop (option b) is a later improvement, not built now.
