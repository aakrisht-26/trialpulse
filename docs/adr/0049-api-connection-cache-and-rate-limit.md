# 0049. The API's connection to Neon, its cache and its rate limit

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 15 decision 4: option a, with three additions).
- Refines CLAUDE.md Sections 13 and 17 (a new secret, `API_DATABASE_URL`), and decision 13 of Step 14.

## Context

Neon's free compute suspends when idle; a keep-alive every 5 minutes or less would use the 100 CU-hours by day 16.7 (Step 14, Part 3). The API runs as one container on Hugging Face Spaces behind its proxy.

## Decision

1. **Connection.** `API_DATABASE_URL` is Neon's pooled URL for a role created by SQL (`scripts/api_role.sql`) that may read the `serving` schema only. SQLAlchemy's pool with pre-ping and a 15-second connect timeout; a request that finds Neon waking is retried once; if Neon still does not answer, 503 with `Retry-After` and the notice.
2. **Prepared statements.** psycopg prepares a statement automatically after five executions, which a transaction-mode pooler may not carry between connections. Neon's documentation could not be read from the cloud session, so automatic preparing is turned off (`prepare_threshold=None`): correct whether or not the pooler supports it, at a negligible cost. Confirming Neon's behavior is left to the laptop.
3. **Cache.** Answers are cached in memory, keyed by the data-as-of date and the request. **The data-as-of date is read from Neon at most once every 15 minutes, never per request**, so a cached answer never wakes the compute.
4. **Rate limit.** Per client address, taken from the `X-Forwarded-For` entry that Spaces' proxy appends (the last one), not the proxy's own address; 429 with `Retry-After` and the notice above the limit.
5. **`/health` never touches Neon.**

## Alternatives

- **The direct URL with a held connection:** may keep the compute awake.
- **The serving tables loaded into memory once a day:** fast, but about 2 GB a month of transfer and stale for minutes after each run.
