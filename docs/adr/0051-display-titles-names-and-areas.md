# 0051. Titles, sponsor names and areas as display rows

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 15 decision 6: option a, with the size measured).
- Refines CLAUDE.md Section 13 and the Neon sizing of Step 14 (Part 3).

## Decision

1. `serving.trial_display` holds, for each trial with a wide row, its brief title, its keyed sponsor id, its sponsor class and its area codes; `serving.sponsors` holds the display names (ADR 0047). Both come from API v2 only, never from the dataset.
2. One bulk pull (`trialpulse.api.display`) fills them; the daily job keeps them current from each record it ingests, and removes the rows of trials that leave the live state.
3. Their size is measured from a real bulk pull into Postgres, and the Neon estimate is updated with the measured number (`docs/progress.md`, Step 15).
