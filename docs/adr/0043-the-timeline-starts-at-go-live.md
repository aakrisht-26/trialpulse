# 0043. The risk timeline starts at go-live, with amendment markers before it

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 14 decision 8).
- Refines CLAUDE.md Section 13 (the trial-detail timeline) and Step 16's acceptance wording.

## Context

Under ADR 0037 no version row from before go-live is stored, so on day one the timeline of `/v1/trials/{nct_id}/timeline` would have one point per trial. Scores at past landmarks would be in-sample for the served champion, since no out-of-sample model covers recent landmarks.

## Decision

1. **The risk line starts at go-live.** No back-scores, in-sample or otherwise.
2. **Amendment markers** are stored for the trials in `live.trial_state`, as one row per trial with two arrays (`serving.amendment_markers`): the post dates of the versions where a field a served feature reads changed, and for each a code of what changed (bits: 1 status, 2 dates, 4 enrollment, 8 design, 16 sponsor, 32 title phase or intervention types). The bootstrap fills them from the dataset; each live version adds one.
3. A version that changed only fields no feature reads is not a marker.

## Alternatives

- **Leave the timeline empty before go-live:** less useful to a portfolio team and nothing saved that matters (12 MB).
- **Markers as rows:** 23 MB against 12.
- **Scores at past landmarks:** in-sample, and would have to be labeled so.

## Consequences

- 12 MB in Neon (estimated). The markers are the closest stored item to the version history; they are dates and codes only, no field values (ADR 0040).
- Step 16's acceptance says the risk line starts at go-live.
