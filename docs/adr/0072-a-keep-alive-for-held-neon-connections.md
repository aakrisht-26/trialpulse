# 0072. A keep-alive for held Neon connections

- Date: 2026-11-07
- Status: **Accepted.** Decided by Aakrisht on 2026-11-07 (option A of the phase B report on the day-0 check), with the rules below.
- Refines ADR 0058 (no transaction open while a job waits). Leaves ADR 0060 (the renewable lease) unchanged.

## Context

The first day-0 check against Neon failed before it compared anything: `AdminShutdown` ("terminating connection due to administrator command") on its first query after scoring. Neon's Free plan suspends a compute after 5 minutes without a query, cannot disable that or change its 5 minutes, and the suspend ends every open connection, inside a transaction or not. ADR 0058 removed open transactions while a job waits, which protects against `idle_in_transaction_session_timeout` only.

Measured on Neon on 2026-11-07: an autocommit connection that sent nothing for 330 s was ended (`AdminShutdown` at 330 s); one that sent `SELECT 1` every 60 s for 330 s still answered. The daily job's full runs took 360 to 395 s in the replay, most of it scoring and drivers between two statements on the job's connection, so a full run on Neon would lose its connection the same way.

The alternatives were a fresh connection after every long computation (B), which touches every stage boundary and the lease for no gain, and turning the suspend off (C), which the Free plan does not allow.

## Decision

1. **A keep-alive context manager** (`trialpulse.live.keepalive.keep_alive`) wraps every span in which a command holds an open connection while it computes, pulls or waits. While inside, a background thread sends `SELECT 1` on that same connection every `live.keepalive_seconds` (60, in `config/project.yaml`; at most 240, under Neon's 5 minutes).
2. **No transaction.** It may be entered only when the connection has no transaction open (its transaction status is idle); otherwise it refuses. It never pings inside a transaction: a ping that finds one stops with an error. The main thread does not use the connection inside the span.
3. **Failure.** If a ping fails, the thread stops and the error is raised when the span ends (`KeepAliveError`, its cause the database error), so the command fails through its existing path (the run's failure recorded on a fresh connection, ADR 0058). The thread always stops and is joined before the span returns, on success or error; an error of the span's own body takes precedence.
4. **The lease.** A ping does not renew the lease; renewal stays per stage (ADR 0060).
5. **The spans.** Every command that connects to Neon was audited for a held connection across a span that can exceed 60 s:

| Command | Stage | Span wrapped |
| --- | --- | --- |
| `live.daily` | ingest | The API v2 pull (`fetch`), which a catch-up after missed days can make long |
| `live.daily` | ingest | The ingest's computation: `engine.ingest`, the lapse exits and the display rows |
| `live.daily` | sponsors | The sponsor pass (`engine.advance`) |
| `live.daily`, `live.dayzero`, `live.replay_parity` | publish (`day_features`) | The features of the run's scope (`compute_live`) |
| `live.daily`, `live.dayzero`, `live.replay_parity` | publish (`score_day`) | The scores and drivers (`model.score`) |
| `live.daily` | publish (`publish_day`) | The publication, which writes on its own connection, and the month's score file |
| `live.retention` | each table | The Parquet file of the exported rows, its digest and its read-back check |
| `live.bootstrap` | seed | The whole stream and its sponsor pass. The per-batch lease renewal is replaced by a renewal before and after the stage, because the main thread may not use the connection inside the span and the sponsor pass at the end had no batch to renew on |
| `live.bootstrap` | hashes | The reason counts read from the dataset's files |

Not wrapped, with the reason:

- `live.bootstrap` scores stage: no connection is open yet. Its prepare stage (32.3 s on Neon) builds the COPY payloads with a cursor of the connection, so the main thread uses it; its write stage is one active COPY transaction.
- `live.reasons`, `live.statement`, `live.display`: each computes or pulls before it connects, then writes at once.
- `live.api_role`, `live.migrate`: a few statements, none waiting.
- `scoring.serving.publish` on its own connection: its read of the previous rows, the comparison and the writes follow each other within seconds.
- The API (`api.db`) is not a command: its pooled connections are made again when the compute wakes (ADR 0049).

## Alternatives

- **B. A fresh connection after every long computation.** Every stage boundary and the lease would change, for the same result.
- **C. Turn off the suspend.** Not possible on the Free plan.
- **Pings on a second connection.** They keep the compute awake, but the held connection could still be ended by a network idle timeout on the way; pinging the held connection itself keeps both.
- **Pings from the main thread between steps.** The long spans are single calls (a model's scoring, the API pull); they have no steps to ping between.

## Consequences

- The compute stays awake only while a command runs, so the pings add compute time only for the length of each run.
- psycopg serializes use of a connection, and the main thread does not use it inside a span, so the pings and the command never interleave on the wire.
- Tests (local Postgres, CI-safe) lower `idle_session_timeout` to 2 s, which ends a session quiet outside a transaction as Neon does. Without the keep-alive a 5-second quiet span loses its connection; with pings every 0.5 s it keeps it and then writes. Entering inside a transaction is refused; a ping that fails surfaces as `KeepAliveError` when the span ends and the thread is gone. The daily job's full run, with its pull, sponsor pass and scorer each quiet for 3 s, succeeds with the keep-alive and fails at ingest with `IdleSessionTimeout` without it; the day-0 check likewise.
- `live.migrate --from-env` reads NEON_DATABASE_URL, with the same refusal of a pooled URL, and never prints it; the hidden prompt stays the default. Neon's migration to 0006 on 2026-11-07 was applied through `migrate.upgrade` directly, before the option existed.
