# 0043. The risk timeline starts at go-live, with amendment markers before it

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 14 decision 8). **Amended 2026-11-18** (below): a marker on the trial's first-post date is named the first posting, never a change.
- Refines CLAUDE.md Section 13 (the trial-detail timeline) and Step 16's acceptance wording.

## Context

Under ADR 0037 no version row from before go-live is stored, so on day one the timeline of `/v1/trials/{nct_id}/timeline` would have one point per trial. Scores at past landmarks would be in-sample for the served champion, since no out-of-sample model covers recent landmarks.

## Decision

1. **The risk line starts at go-live.** No back-scores, in-sample or otherwise.
2. **Amendment markers** are stored for the trials in `live.trial_state`, as one row per trial with two arrays (`serving.amendment_markers`): the post dates of the versions where a field a served feature reads changed, and for each a code of what changed (bits: 1 status, 2 dates, 4 enrollment, 8 design, 16 sponsor, 32 title phase or intervention types). The bootstrap fills them from the dataset; each live version adds one.
3. A version that changed only fields no feature reads is not a marker.

## Amendment of 2026-11-18: the first posting

Decided by Aakrisht on 2026-11-18 (memo_v3, ADR 0084), after the audit of the memo_v2 memos (data/memo_review/2026-11-18-audit.md).

### What the audit found

A trial's first version has the code 1 (`live.contract.marker_code` of a first version), the status bit, although nothing changed: it is the first posting. The API named it "status", so the Trial detail page has shown every trial's first posting as "Changed: status" since Step 16, and 9 of the 20 reviewed memos listed it as a status change (the registry's first-post dates of NCT07153796 and NCT06660810, 2025-09-04 and 2024-10-28, are the dates of those trials' oldest markers). The code of the dates bit also covers the status verified date, which the name "dates" hid.

### Decision

1. **When markers are read** (by the API's timeline and by the memo's payload), a marker is named "first posted", and left out of every count of changes, only when its post date equals the trial's first-post date from the live state (`live.trial_state.study_first_post_date`). Otherwise it is shown as stored, so trimmed markers (whose oldest is not the first posting), a first version posted on another day, or a trial without a live-state row keep their codes. No stored marker changes: no Neon data change.
2. **The API reads the first-post date through a view**, `serving.trial_first_posts` (migration 0009: `nct_id` and `first_post_date` from `live.trial_state`), so its role keeps reading the `serving` schema only, as ADR 0049 requires and ADR 0079's views do. The daily job's expected head is 0009.
3. **One table of words** (`trialpulse.markers`, which imports nothing from the project, so the API's image may use it) names the codes for both the API and the memo: 1 status, 2 "dates or status verification" (the code cannot tell a schedule date from a re-verification), 4 enrollment, 8 design, 16 sponsor, 32 "intervention types or title phase". The memo names the dates bit "dates" or "status verified" where two versions held since go-live show which changed (ADR 0084, memo_v3). **Amended 2026-11-19 (ADR 0085, memo_v5):** code 1 is "recruitment status", and the dates bit is "dates or status verification" everywhere, in the memo as on the timeline. **Amended 2026-11-21 (ADR 0085, memo_v6):** code 32 is "interventions or phase named in the title", the label's own words.
4. **The timeline's markers** gain `first_posted` (true for the first posting, whose `changed` is empty; null when the live state holds no first-post date for the trial, as for an ended trial, whose markers then show their stored groups), and its note says so. The Trial detail page shows the first posting as "First posted".

### Consequences

- Every trial in the live state whose oldest stored marker falls on its first-post date shows it as the first posting on the API, the page and in memos; the others keep their stored codes. An ended trial leaves the live state, so the API can no longer name its first posting (`first_posted` null); the dashboard asks for its timeline only while it is served.
- The view adds no storage. The API's answers change shape (a new field, new names), so the dashboard's recorded stubs were recorded again (ADR 0056).

### Alternatives of the amendment

- **Name the oldest stored marker the first posting by its position alone:** trimmed markers would rename a real status change.
- **A code of its own for a first version, with the stored codes rewritten on Neon once:** a data change for what a read can already tell.
- **Drop the first marker from the timeline and the memos:** hides when the trial was registered.
- **A first-post date column in `serving.trial_display`, written by the daily job:** a table change and a new write path, where a view reads the state that already holds the date.
- **The memo only for now:** the page would keep calling the first posting a status change.

## Alternatives

- **Leave the timeline empty before go-live:** less useful to a portfolio team and nothing saved that matters (12 MB).
- **Markers as rows:** 23 MB against 12.
- **Scores at past landmarks:** in-sample, and would have to be labeled so.

## Consequences

- 12 MB in Neon (estimated). The markers are the closest stored item to the version history; they are dates and codes only, no field values (ADR 0040).
- Step 16's acceptance says the risk line starts at go-live.
