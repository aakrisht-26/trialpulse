# 0011. Step 2: an automated version check replaces the manual check

- Date: 2026-09-30
- Status: Accepted (decided by Aakrisht on 2026-09-30). Changes a Step 2 GO criterion in CLAUDE.md Section 18.

## Context

Step 2 part e asked Aakrisht to compare version 0 of 10 trials in the dataset with the original version on each trial's Record History page, and required at least 9 of 10 to pass. The Record History page is fed by the same internal history API the dataset was built from. A person reading 10 pages therefore checks the same source as a script would, only on a smaller sample and more slowly.

## Decision

Part e becomes an automated check against the internal history endpoint (verification only, as CLAUDE.md Section 7 allows):

1. **Sample:** 50 cohort trials, drawn with the project seed from the cohort trials that have at least 2 versions, so that every trial contributes version 0 and one later version (100 versions in all).
2. **Versions:** for each trial, version 0 and one later version chosen at random with a seed. Cached change logs (from part f) are reused where they exist. Requests stay at 20 per minute or less, and only a projection of the compared fields is cached, never a raw record.
3. **Fields:** per version, the fields of the former checklist: submitted date, status, study type, first posted date, start date, primary completion date, enrollment count and type, and lead sponsor class. Dates are compared at the dataset's precision.
4. **GO criterion:** at least 95% field agreement across the 100 versions, with every mismatch labeled by its cause, as in part d. A mismatch with no known cause fails the criterion.
5. `docs/feasibility_manual_check.csv` stays as an optional human spot check. No criterion depends on it.
6. **Decision rule:** if every automated Step 2 criterion passes, Step 2 is recorded as GO in the feasibility report and the progress log. If any fails, no decision is stated.

## Alternatives

- **Keep the manual check.** Same source, a sample of 10 instead of 100 versions, and hours of manual work.
- **Check against API v2 only.** API v2 serves the current record only, so it cannot check version 0 or any earlier version.

## Consequences

- The Step 2 criterion "manual check passing at least 9 of 10" is replaced by the automated version check above.
- The internal history endpoint is used for up to 150 more requests (a change log and two versions per trial, fewer where cached), still for verification only and still at 20 per minute or less.
- Restricting the sample to trials with at least 2 versions checks the version history the project depends on; single-version trials are covered by part d's comparison of each trial's latest version.
