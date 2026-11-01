# 0054. Waking the API: retries the viewer can see

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 16 decision 3: option a, with no keep-alive workflow).
- Refines CLAUDE.md Section 13 ("It calls the API only and handles API cold starts gracefully") and ADR 0049.

## Context

Two waits can stack on a first visit. A Hugging Face Space that slept restarts its container: while it starts, a request fails to connect or gets a 502, 503 or 504 from the platform's proxy. Then, while Neon wakes, the API itself answers 503 with `Retry-After` and its notice (ADR 0049). The dashboard on Streamlit Community Cloud sleeps when idle too.

## Decision

1. **One API client for every page.** Each attempt has a 30-second timeout. Connection errors, timeouts and 502, 503 and 504 answers are retried, as is 429; a `Retry-After` header sets the wait (at least 1 and at most 60 seconds), otherwise the wait grows 5, 10, 20 then 30 seconds. All attempts of one request stop within about 3 minutes.
2. **The wait is visible.** While it waits, the page says why and when it retries: the API service is starting (it sleeps when idle), or the database is waking up, or too many requests. A 503 that carries the API's notice comes from the database; any other failure comes from the service.
3. **`/health` first.** The first request of a viewer's session goes to `/health`, which never touches the database (ADR 0049), so the service's start and the database's wake are told apart and waited for in turn.
4. **After the limit,** the page says in plain words that the API did not answer and offers a "Try again" button. It never shows a traceback, and the notice, the attribution and the disclaimer stay on the page.
5. **Answers are cached for 15 minutes** in the dashboard's process, so moving between pages does not call the API again; failures are never cached.
6. **No keep-alive workflow.** Nothing calls the API on a schedule.

## Alternatives

- **One attempt, then the message and a button:** the simplest client, but nearly every first visit after an idle period fails once.
- **The same client plus a scheduled workflow that calls `/health` so the Space never sleeps:** no Space cold start, but one more workflow to own, and it does nothing for the dashboard's own sleep.

## Consequences

- A first visit after a long idle period can take a minute or more, with the reason on screen.
- The dashboard is one client address for the API's rate limit (60 requests a minute, ADR 0049); the 15-minute cache keeps a demo's traffic well under it, and a 429 is waited out like a wake.
