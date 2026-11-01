# 0057. The descriptive pages: Why trials stop and Landscape

- Date: 2026-11-01
- Status: **Accepted.** Rules set by Aakrisht on 2026-11-01 with the Step 16 decisions.
- Refines CLAUDE.md Section 13 (the Landscape and Why trials stop pages) with ADRs 0028, 0046 and 0050.

## Context

The served score is all-cause: no cause-specific model was fitted (ADR 0028), and H3, that operational stops are ranked better than scientific ones, was falsified. Areas come from the current record's MeSH terms and never feed a score, and a trial counts in every area it belongs to (ADR 0046).

## Decision

1. **Why trials stop is descriptive.** It shows the labeled reasons of early stops by month and sponsor class through the labels' date, and the number of later stops that are counted but not labeled (ADR 0050). It calls no score endpoint, shows no risk number, and says that the scores do not predict a reason. Nothing on it suggests otherwise.
2. **Landscape shows the API's own statements** that areas come from the current record and never feed a score, and that area totals can exceed the number of trials, beside the counts they qualify.

## Alternatives

None was put forward: these are rules for the pages, set with the decisions.

## Consequences

- A test holds each page to its rule: the API's area note appears on the Landscape page, and the Why trials stop page calls only the reasons endpoint and states that the scores do not predict a reason.
