# 0060. A renewable lease for the live jobs

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (review of Steps 14 to 16, finding 5).
- Refines ADR 0045 (the lease and the stale lease). Migration 0006.

## Context

ADR 0045's lease is one running row in `monitoring.pipeline_runs`, released as stale when it is older than `live.lease_stale_minutes` (120) by its start time. The review showed that a job still running past 120 minutes is taken over: a second job starts, and the first one's finish then overwrites the takeover's `stale lease` with `succeeded`. On a runner the 90-minute kill prevents it; on the laptop (the bootstrap, retention, a long catch-up run of the daily job) nothing does.

## Decision

1. **A job renews its lease at every stage** (`store.renew`): a new column `renewed_at` is set to now on the job's running row. A running row is stale when its last renewal, not its start, is older than the stale window.
2. **Every write renews first, in its own transaction**: the ingest writes, the sponsor file, publication (inside `serving.publish`'s transaction), the score file and data status, and retention's delete. The renewal's row lock holds off a takeover until the transaction ends, so no write lands after one.
3. **A job finishes only if it still holds its lease**: `store.finish_run` updates only a running row and raises `LeaseLostError` otherwise.
4. **A job that lost its lease stops without writing**: it records no failure (the job that took over owns the rows now) and skips the closing VACUUM. Its exit is a failure in one line, as for any failure (ADR 0040).

## Alternatives

- **A longer stale window:** delays the takeover of a job that truly died, and a long enough job still overlaps.
- **Advisory locks:** a session lock dies with its connection, but the job deliberately opens several connections (ADR 0058) and would need to hold one open for the whole run.

## Consequences

- A long job is never taken over while it makes progress; a job that stalls longer than the window between two stages is.
- Tests: a run taken over while it scores publishes nothing and writes no data date or score file; a takeover during the pull or the sponsor pass is found by that stage's write; retention taken over deletes nothing; a row started 3 hours ago but renewed recently is not stale.
