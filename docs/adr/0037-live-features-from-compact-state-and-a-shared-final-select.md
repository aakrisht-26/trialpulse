# 0037. Live features from compact state and a shared final SELECT

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 14 decisions 1 and 4: Option 3, compact rows with the export adapter).
- Refines CLAUDE.md Section 12 (what the live tables hold) and Step 14. Replaces the live tables of Step 3 (Alembic revision 0001) with compact ones. No locked definition, feature or result changes.

## Context

API v2 returns the latest record of a changed trial and nothing else, while 8 of the 54 served features need values only the version history holds: five version-0 values, the number of versions, whether the trial was ever suspended and the post dates of the last 185 days. The sponsor track record needs every trial's sponsor history, and the competition count needs every trial's current state. Three options were measured in the Step 14 options report (docs/progress.md, Step 14, Part 1).

## Decision

1. **Neon holds compact state, not versions from the dataset.**
   - `live.trial_state`: one wide row per trial that can still be scored or counted (every trial whose latest state is not terminal, of any study type and first-post date, plus open trials with a status reversal). It holds the 26 latest-record fields the served features read, the 8 dataset-only values (`first_*` for the five version-0 values, `n_versions`, `ever_suspended`, `recent_post_dates`), the first day the state is lapsed (`lapse_from`, ADR 0014) and a `reversal` flag.
   - The sponsor file (`live.sponsor_file`, one row): the thin data of every trial (last post date, current and registration sponsor ids, ended code, ever-terminal flag) and the full resumable state of the sponsor pass of ADR 0018, with the last day it reached and the last run it consumed.
   - `live.study_versions`: every API v2 record appended after go-live, every study type, as a **compact row**: the latest-record fields of the wide row (title phase in place of the titles, the intervention types in place of the interventions, the keyed sponsor id of ADR 0044 in place of the name and key). No text is stored.
2. **Identity of a record.** A compact row carries a `feature_hash`: SHA-256 over its compact fields, with the submitted status (ADR 0014) in place of the registry's UNKNOWN label (guidance item 10). Only a difference in a field a served feature reads changes it (ADR 0042, condition 4). The serving tables' `content_hash` is this hash on both paths from the bootstrap on.
3. **One final SELECT for both paths.** The SELECT that turns feature inputs into the 54 features (and the eligibility statistics) is `trialpulse.features.build.feature_select_sql`. The offline build calls it on a view of its at-landmark rows joined with the sponsor record, the competition count, the title phase and the intervention types; the live path calls it on a relation built from the wide rows as of the scoring date. `build_states` and `build_at_landmark` are unchanged. The title phase is resolved (official title, else brief title) by one rule with a Python form at ingest and a SQL form offline, held equal by a test.
4. **The sponsor pass is resumable.** `track_records` runs through `SponsorPass`, whose state can be written to bytes and read back. Offline it runs in one go as before; live it applies each run's versions, writes its file and answers the day's queries from the file. Events of a day are ordered by kind, then by the sponsor ids' and trial ids' text, so a tie sorts the same on both paths when both use the same ids.
5. **A run, in order.** Read the API's `dataTimestamp` as d; pull the delta; update state and commit; advance the sponsor pass through d minus 1 and commit its file; load the wide rows, the file and the open count into DuckDB; build one row per trial as of d; run the shared final SELECT; score and compute drivers with the champion; publish.
6. **Compact serving rows** (decision 4). `serving.trial_scores_latest` and `serving.trial_score_history` hold the risks and, for the latest row, the drivers as arrays of feature number, value and contribution to 3 decimals (the sign is the direction). The API expands them on read (Step 15).
7. **The export adapter** turns compact rows back into what the offline feature code reads (canonical version rows, a title text that states the stored phase, and intervention types per version), so layer 5a of ADR 0038 and the laptop retrain of ADR 0040 can run unchanged offline code on them. Without it, the joins would silently blank 14 of the 54 features; a test runs the offline build on exported rows and compares.

## Alternatives

- **Option 1, version rows in Neon and the offline SQL on the runner.** Keeps the most offline code and gets amendment markers free, but sits at or past the 700 MB line at month 12 and puts 27% of the dataset's row count on a runner every week.
- **Option 2, compact state expanded into the offline tables.** Same storage, but changes about eight places of approved code, and its blank per-day rows gave silent NULL features when state ran ahead of the date.
- **No dataset-derived values in Neon.** Moves the median risk by 0.006 at 12 months and 0.013 at 24 and keeps only two thirds of the top decile.
- **Full rows for `live.study_versions`.** 694 to 724 MB at month 12, past the 30% headroom line at the top of the range.

## Consequences

- About 512 to 534 MB in Neon after 12 months (Step 14 options report, Part 3).
- State accumulation, title phase at ingest, intervention pairs, reversal from state, the open count and event derivation are new code; Parity C (ADR 0038) is what protects them.
- The offline build's output must not change with the refactor: a test holds the feature table of the synthetic warehouse to a digest taken before it, and the laptop's feature build must report "Identical to the previous build".
