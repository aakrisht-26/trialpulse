# 0068. VACUUM FULL after a full run

- Date: 2026-11-07
- Status: **Accepted.** Decided by Aakrisht on 2026-11-07 (option 1 of the phase B Neon estimate), with the safeguard that it runs only inside the daily job, after a full run, while the job holds its lease.
- Refines ADR 0045 (the job's VACUUM after publication) and ADR 0060 (the renewable lease).

## Context

The phase B replay (four weeks of real daily runs through local Postgres) measured the tables as built. A full run rewrites every row of `serving.trial_scores_latest` (each row carries its score date), and every run that advances the sponsor pass rewrites the one row of `live.sponsor_file`. Plain VACUUM, which the job already runs after publication, makes the dead space reusable but never gives it back, so both tables settle at about twice their size:

| Table | Fresh at the cutoff | After four weeks | After VACUUM FULL |
| --- | --- | --- | --- |
| `serving.trial_scores_latest` | 39.7 MB | 79.2 MB | 39.6 MB |
| `live.sponsor_file` | 13.2 MB | 26.6 MB | 13.2 MB |

The 53.0 MB difference counts against Neon's 1 GB cap for the whole year.

## Decision

1. After a full run has published (scores, drivers, the score file, the data status, the display prune of ADR 0070), the daily job runs `VACUUM (FULL, ANALYZE)` on `serving.trial_scores_latest` and then `live.sponsor_file` (`store.compact`, the job's stage `compact`). A daily run that is not full compacts nothing.
2. **Under the lease.** The lease is renewed before each table (ADR 0060). A job that lost its lease raises before it compacts anything, and the run ends only after the compaction, so no other job can run while it does.
3. The connection is the job's own, in autocommit (VACUUM cannot run inside a transaction, and none is open: ADR 0058). `lock_timeout` is 60 seconds: VACUUM FULL waits that long for readers holding the table, then the run fails at stage `compact` and the issue opens. The publication is already committed, and a rerun publishes the same rows again and compacts.
4. The database size behind the retention alerts (ADR 0045) is read after the compaction.

## Alternatives

- **pg_repack or a table swap.** An extension the project does not use, or more code, for two tables this small.
- **Write only the rows that changed.** The score date on every row changes on every full run, so every row would still be written; dropping the per-row date changes the API's answers.
- **Accept the doubled size.** 53 MB of the year's budget, with no data behind it.
- **VACUUM FULL from a separate scheduled job.** It would need its own lease and could overlap a run; inside the job it cannot.

## Consequences

- During a full run, between its rewrite and its compaction, the two tables hold their old and new rows: about 53 MB more than the steady size for the minutes the run takes, still well under the cap.
- While VACUUM FULL rewrites a table (its time on Neon is not measured yet; the first full run there will record it in the run's timings), the API's reads of it wait; the API's cache (ADR 0049) answers most of them.
- Tests: a full run compacts both tables (each gets a new file) after publication, while its run row is still `running`; a run that is not full compacts nothing; a run that lost its lease compacts nothing.
