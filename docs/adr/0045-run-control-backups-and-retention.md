# 0045. Run control, backups and retention of the live system

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 14 decisions 10 to 14, the defaults approved; backups as a weekly scheduled task on his laptop).
- Refines CLAUDE.md Section 12 (the daily job and its schedule) and Step 14's verify list.

## Decision

1. **Reason labels for new early stops (decision 10)** are deferred to Step 15. The `why_stopped` text stays in the API record; only labels are ever stored.
2. **Backups (decision 11):** a weekly scheduled task on Aakrisht's laptop pulls `live.study_versions` and the score history to `data/` (3 to 4 MB a week); never a public artifact. `serving.trial_score_history` gains `run_id`. Neon's own restore reaches back 6 hours only.
3. **Missed runs (decision 12):** `daily.yml` has a second cron, 3 hours after the first (00:30 UTC), that does nothing if the day already has a succeeded run. The idempotence test of Step 14 runs the job twice with that check bypassed.
4. **Run control (decision 13):**
   - **The date d** is the date of API v2's `dataTimestamp`, read once. Records posted after d wait. A Saturday or Sunday run does nothing.
   - **The pull** starts at the last stored watermark minus 3 days. The watermark is the earlier of the latest stored post date and d. A pull fails on zero records, on a count that differs from `totalCount`, and when more than 5 stored overlap rows come back neither with their date nor a later one (skipped on the first run).
   - **The gate before any write:** among overlap records with the same trial and post date as a stored row, the run stops if any one field turned NULL in more than 1% of them.
     Refined after the review of 2026-11-01 (finding 2, approved by Aakrisht): a record that cannot be converted is quarantined and the run goes on, and a quarantined record that matches a stored row counts toward the gate as having lost every field that row had.
   - **A record** that fails the contract is quarantined by its payload hash (unique), and the trial keeps its old state and score; the run then ends in failure after publishing. A NULL status, trial id or post date is refused the same way.
     A field of the wrong JSON type fails the contract: booleans accept only JSON true or false and counts only JSON integers (review finding 7).
   - **Scope** is computed, not remembered: the trials with no row in `serving.trial_scores_latest` or `serving.trials_not_scored`, or with another hash, uncapped landmark index or model, plus rows outside the population.
   - **Three stages, each its own transaction:** ingest, the sponsor pass, publication. Publication refuses when the pass is behind d minus 1.
   - **A full run** is due when the last succeeded full run is 7 or more days old.
   - **Writers:** one running job at a time, by a partial unique index on `monitoring.pipeline_runs`; a running row older than 120 minutes is stale and is marked failed by the next job.
   - **Time:** an alarm at 60 minutes, the job killed at 90.
   - The job refuses when `alembic_version` is not head; migrations run by hand.
   - The job connects to `NEON_DATABASE_URL` (the direct URL); the API's pooled read-only role is Step 15's.
   - VACUUM runs after publication, in a final step.
   - **Failure:** a separate job needs the daily job, runs on `always()` when it failed or was cancelled, has `issues: write` only, reuses one open issue and runs only on main.
5. **Retention (decision 14):** alerts at 600 and 700 MB of database size. At 600 MB, the laptop exports score history and `live.study_versions` older than 12 months to `data/`, confirms the export by row counts and digests, and only then deletes them from Neon. The delete is never automatic, because at the cap deletes fail too.
6. **Dispatch timing job (rule 3 of the Step 13 review):** a manually dispatched workflow loads the champion from MLflow, times predictions and the driver pass on 5,000 rows generated from the feature types (no dataset row), logs `lscpu`, and calls API v2's `/version` and one real page from the runner.
7. **Step 14's verify list** gains a local bootstrap command before the two `--target local` runs, and `gh secret list`.

## Alternatives

- **One cron, as Section 12 says:** a single failure loses a day.
- **Automatic deletes at the cap:** they fail there, and would delete without a confirmed export.

## Consequences

- The numbers above live in `config/project.yaml` under `live:`, not in code.
