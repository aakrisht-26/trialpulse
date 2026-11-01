# 0061. Database errors the API tells apart

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (review of Steps 14 to 16, findings 7 and 8).
- Refines ADR 0049 (one retry for a waking compute, then 503).

## Context

The API retried every `OperationalError` and every error it took for a lost connection, then answered 503 "the database is waking up" with `Retry-After`. The review showed that a wrong password, and a role without SELECT on `serving.data_status`, both answered that 503 forever, with nothing in the log; on the dashboard, a misconfigured deployment looked exactly like a sleeping Neon. An error the API did not handle answered `text/plain` "Internal Server Error", without the notice, the attribution or the data date that every answer is meant to carry (ADR 0008).

## Decision

1. **Only connection failures and timeouts answer 503 "waking up"** (after the one retry of ADR 0049): no answer from the server (refused, reset, DNS, a connect timeout), SQLSTATE class 08, the server starting or shutting down (57P01 to 57P03), a statement timeout (57014), too many connections (53300), and every pooled connection busy past the pool's timeout.
2. **Every other database error answers at once with a JSON 500** carrying the notice, the attribution and the data date, saying it is a configuration error and not a wake-up, and is logged as one line with its SQLSTATE, its class and the path: never its message, which can hold a row. This covers a wrong password (28P01), a missing role or no `pg_hba.conf` entry (28000), a missing database (3D000), a missing grant (42501) and a missing table (42P01). A refusal at connect time carries no SQLSTATE in psycopg, so it is read from the server's `FATAL:` text, which Neon gives in English; transient texts (too many clients, starting up, shutting down) stay 503.
3. **A data date that cannot be read** follows the same rule: a refusal answers 500, not 503.
4. **Every unhandled error answers a JSON 500** with the notice, and is logged by the error's class and the path only.

## Alternatives

- **503 for everything, with the cause in a header:** the dashboard would still wait 3 minutes for an error that waiting cannot fix.
- **Log the error's message:** quicker to debug, but a message can hold a row.

## Consequences

- A misconfigured deployment shows a plain error on the dashboard at once (it does not retry a 500) and leaves a SQLSTATE in the Space's log.
- Tests: a wrong password, a missing grant on a score table (not retried: review fault R05), an unreadable data date, a missing table, an unhandled error, and a table of connect-time texts.
