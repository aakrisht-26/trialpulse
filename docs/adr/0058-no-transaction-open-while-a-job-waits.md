# 0058. No transaction is open while a live job waits or computes

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (review of Steps 14 to 16, finding 1, option a).
- Refines ADR 0045 (run control: three transactions) and the retention step of ADR 0045.

## Context

Neon sets `idle_in_transaction_session_timeout` to 300000 ms: a session that stays idle inside an open transaction for 5 minutes is ended (Neon's compatibility page, read on 2026-11-01). As built in Step 14, the daily job opened a transaction with its first read of a stage and kept it open while it waited on API v2, converted records, advanced the sponsor pass and scored with drivers. The Step 14 estimate of a full run's drivers on a runner is 14 to 23 minutes, and the first catch-up run after the bootstrap converts months of records at about 26 ms each. The review reproduced the result on local Postgres with the limit scaled down: every run failed at publication, the run row stayed `running` because the failure was written on the connection that had been ended, no score file or data-as-of date was written, and the next run was again a full run, so the job could never succeed. Retention held a locked transaction open while it wrote and read back its Parquet file.

## Decision

1. **The daily job's connection runs in autocommit.** Each read is its own statement, and every write sits in a short explicit transaction: the lease, the ingest writes, the sponsor file, the score file with the data status, the run's end. The API pull, the conversion, the ingest decisions, the sponsor pass, the features, the scores and the drivers run with no transaction open. `serving.publish` keeps its own single transaction, which only reads the previous history and writes.
2. **A failure is recorded on a fresh connection**, since the run's own connection may be what failed. If that also fails, the stale lease releases the run as before.
3. **Retention** selects, writes and verifies its export with no transaction open, then deletes in one short transaction that returns the deleted rows: they must be the exported rows, value for value, or the transaction is rolled back.
4. **Reads and the writes that follow them are separate transactions.** The lease keeps every other writer out between them (ADR 0045, strengthened by ADR 0060).
5. **Regression tests** lower `idle_in_transaction_session_timeout` to 1 second on every connection the job opens (through the connection string) and slow the pull, the sponsor pass, the scorer and the retention export past it: the review's two runs now both succeed, and a run whose connection is ended records its failure.

## Alternatives

- **Raise the limit for the job's role** (`ALTER ROLE ... SET idle_in_transaction_session_timeout`): one statement, but long idle transactions stay open on a small shared compute, and a missed setting on a new role brings the failure back.
- **Commit before each long step but keep one connection in transaction mode:** correct only as long as no read slips in before a long step; autocommit makes the rule hold by construction.

## Consequences

- A stage's reads are no longer one snapshot. Under the lease no other job writes the live tables, and the API role cannot write, so the reads see the same data.
- The bootstrap command (not yet built) must follow the same rule: stream in chunks and keep every transaction short (recorded in docs/progress.md).
