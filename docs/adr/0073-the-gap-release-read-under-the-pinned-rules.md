# 0073. The gap release read under the pinned rules

- Date: 2026-11-08
- Status: **Accepted.** Decided by Aakrisht on 2026-11-08, on the three decisions the gap read of 2026-11-07 stopped at (docs/progress.md, "Step 14, the gap read of ADR 0041").
- Amends ADR 0067 (the canonical rule for intervention types). Refines ADR 0041 (decision 4, the adapter).

## Context

The gap read of ADR 0041 pinned `anamnesis-data/clinical_trials_history` at tag `v2026.11.04` (commit `2b7c8b0e4c000df4bc438d64e148220f92c0e6d1`) and built the adapter of its decision 4. The full schema comparison then found what the card's list of breaking changes does not hold, because that list starts at `v2026.09.28` and the pinned revision is `v2026.09.26`:

1. **Eight columns.** Two new columns no TrialPulse code reads (`status_verified_date_precision`, `document_no_sap`); the merge flag `is_obsolete` with `obsolete_by` (106 versions of 31 trials, which the pinned revision kept unflagged); six columns typed INTEGER in the pinned files because they held no value there, now with their real types, of which only `unposted_responsible_party` holds values (64,094).
2. **Failed-review versions.** The new release marks 24,002 versions of 13,524 trials as having failed the registry's review: never public, with an empty post date and the date of a reset notice. The pinned revision held 22,797 of them as ordinary versions dated at that reset notice (all 22,797 equal to the new `reset_notice_post_date`, from 2020-01-14 to 2026-09-25). So the warehouse, the cohort, the features of Steps 7 to 13 and the bootstrap on Neon all counted them as public versions posted on that date.
3. **Interventions without a name.** The new release's interventions config keeps entries without a name (412); the pinned config held none (0 of 9,401,447 rows). ADR 0067's rule kept a pair without a name on both paths, so the API v2 path counted such entries while the pinned data could not.

## Decision

1. **The eight columns: the handling as built.** The two unread new columns and the merge flag are dropped; the six retyped columns are NULL of the pinned type, as they were pinned. The merged registrations are kept, as the pinned revision kept them, and counted: 106 versions of 31 trials in the new release (2 posted after the cutoff); 104 versions of 29 trials are in the pinned warehouse, with 29 outcome rows, 80 landmark rows and 6 evaluation rows of the test origins.
2. **Failed-review versions keep the pinned rule** on the pinned rows and on the gap rows alike: a version with an empty post date and a reset notice is kept, dated at its reset notice, and counted. A version with neither date, or a public version with a reset notice, is still refused. This keeps one version clock across the pinned rows, the gap rows and the state the champion was trained and checked on. It is a known limitation, measured below and stated in the data card.
3. **Interventions without a name are left out on both paths.** `distinct_intervention_types` and its SQL twin in `warehouse/build.py` (`intervention_lists_sql`) leave out a pair whose name is missing or empty, as the pinned config never held one. The census compares raw pairs without such entries (`census.api_pairs`) and counts the records that list one.

### What the failed-review versions touch (measured read-only on 2026-11-08)

On the pinned warehouse (schema 4) and the cohort of Step 4, for the 22,797 failed-review versions it holds (12,904 trials):

| Measure | Count |
| --- | --- |
| Status differs from the trial's previous version | 2,928 (2,920 by the submitted status of ADR 0014) |
| Terminal status | 20,525: 15,275 COMPLETED, 5,250 TERMINATED, 0 WITHDRAWN |
| ... of which the previous version was not in that status | 2,822: 1,810 COMPLETED, 1,012 TERMINATED |
| The trial's version 0, or its first version | 0 |
| The trial's last pinned version | 1,284 |
| Cohort outcomes dated by a failed-review version | 615 of the 47,772 early stops and 1,751 of the 335,253 completions; for 615 and 1,748 of them no public version of that day carries the status, so the card's rule would move the date |
| Landmark rows of these trials | 66,147 of 1,448,969 |
| ... whose landmark is on or after the trial's first failed review (the only rows whose features can read one) | 298 |

Evaluation rows of the test origins (landmarks in the 12 months after the origin):

| Origin | Rows | Rows of these trials (trials) | Rows on or after the first failed review | Rows whose outcome is dated by a failed review: early stops (within 24 months, within 12) | completions (within 24, within 12) |
| --- | --- | --- | --- | --- | --- |
| 2018-01-01 | 86,556 | 7,540 (4,400) | 0 | 332 (14, 0) | 955 (58, 0) |
| 2019-01-01 | 90,985 | 10,860 (6,095) | 0 | 508 (73, 16) | 1,455 (253, 65) |
| 2020-01-01 | 94,052 | 11,979 (7,034) | 23 | 652 (176, 59) | 1,669 (436, 172) |

So the features of the test origins are almost untouched (23 rows), and the effect is on outcome dates: an outcome a failed-review version dates is dated at the reset notice, earlier than the next public version that shows it. Within the 12-month horizon that is 0, 81 and 231 rows of the three test origins.

**The registered results do not change.** docs/results.md, docs/preregistration.md and the served model stay as they are; Step 17's retrain revisits this rule under its own ADR.

### Interventions without a name (checked on 2026-11-08)

- The pinned interventions config has 0 entries without a name (0 missing, 0 empty, 0 blank of 9,401,447 rows), so the warehouse, the cohort and the features are unchanged and nothing is rebuilt.
- The gap's API pull (28,750 records) has 1 record with an entry without a name.
- Neon holds no intervention type from API v2 yet: one run (the bootstrap), 0 rows in `live.study_versions`, and no `live.trial_state` row posted after the cutoff (checked in a read-only session).
- The census of ADR 0042, rerun under the new rule on API v2 data of 2026-11-06 (data/census/census_2026-11-06.json), passes: 144,627 unchanged trials, 0 failing differences, 0 condition-4 breaks. 2 of its records list an intervention without a name, both unchanged trials, and neither now differs in intervention types. The census of 2026-11-04 had counted 2 intervention-type differences as "registry: the interventions changed"; that cause is now absent, so those were these entries without a name rather than registry changes. No new difference and no bug appears.

## Alternatives

- **Follow the card for failed reviews (leave them out).** The version clock would match what the public saw, but it would split from the data every registered number rests on: `n_versions` and outcome dates would move for up to 12,904 trials, and a state seeded from pinned plus gap rows would mix two rules unless the pinned rows were read again too. That belongs to a retrain (Step 17), not to the bootstrap.
- **Keep nameless interventions on the API path (ADR 0067 as it was).** The live path would count interventions the offline path never could, a skew in `n_interventions` and the type flags that parity would have to carry as an exception.
- **Gap (a) of ADR 0041** instead of approving the eight columns: no new read, but the flag would cover far more rows and `n_versions` would stay wrong for good.

## Consequences

- ADR 0067's sentence "a pair without a type is left out" now reads "a pair without a type or without a name (missing or empty) is left out", on both paths. The content hash of an API v2 record that lists such an entry changes; no stored hash does, since Neon holds no API v2 row.
- The gap rows carry the 445 failed reviews whose notice came after the cutoff, dated at their notice, as the pinned rows carry theirs.
- After go-live, API v2 never shows a failed-review version, so the live path cannot count one; that skew between the offline rule and the live feed exists already and is reported by parity layer 5b.
- The data card's known limitations state the failed-review rule with the numbers above.
