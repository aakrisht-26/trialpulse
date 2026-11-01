# 0053. The Model health page before the monitoring tables are filled

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 16 decision 2: option a).
- Refines CLAUDE.md Sections 13 and 14 and Step 16 ("Model health: freshness, drift, graded performance"), with ADRs 0039, 0048 and 0049.

## Context

Before Step 17 the API serves the data-as-of date (in every response), the model statement (ADR 0048), today's flagged share and the counts of trials without a score. Run history, drift and graded performance live in the `monitoring` schema, which the API's role cannot read (ADR 0049), and grading is built in Step 17 (ADR 0039).

## Decision

The page shows what exists and says plainly what does not exist yet:

1. **Freshness:** the data-as-of date and its age in days, with a warning when it is older than 3 days (Section 14's watermark lag).
2. **The served model:** its version, its description from the statement (a new fit with no metric of its own), the registered results of the locked test with their 95% intervals, the COVID stress test, the falsified hypotheses and the known limitations.
3. **Today's scope:** the number of served scores, the flagged share and the trials without a score by reason.
4. **Drift and graded performance** are sections that state they arrive with the monitoring stage (Step 17), and that the first 12-month predictions mature one year after go-live.

## Alternatives

- **Hide the page until Step 17:** nothing unfinished on show, but the roadmap's five pages are not all there.
- **A serving-side run summary and an endpoint for it now:** real freshness from the job itself, but it widens Step 16 into the API and the daily job, and Step 17 needs such a summary anyway.

## Consequences

- Two sections are placeholders until Step 17, which fills them through the API.
- Freshness is the data-as-of date, not the time of the last run; a run that fails leaves the date where it was, so the warning still fires after 3 days.
