# 0063. The dashboard accepts only the API's answers

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (review of Steps 14 to 16, finding 9).
- Refines ADR 0054 (waking the API).

## Context

The dashboard's client returned every answer that was not a wait (anything but 429, 502, 503 and 504) as it came, and the pages cached 200 and 404 answers for 15 minutes for every viewer. The review sent a 200 HTML page, as a platform may answer while a Space starts: the trial page failed with `KeyError: 'brief_title'`, and the page kept failing after the real API answered, because the bad answer was cached. What Hugging Face serves while a Space starts is not documented.

## Decision

1. **Every answer of the API carries its notice**, so a 200 or 404 whose body is not a JSON object with the notice is not the API's. The client never returns it, so no page reads it and nothing caches it (the two statuses the pages cache are the two it checks).
2. **Such a page is waited out like the service starting** (5, 10, 20, then 30 seconds, within the same 3-minute budget), with its own message: the address answered with a page that is not the API, which may be starting.
3. **When the budget runs out**, the page says the address kept answering with a page that is not the TrialPulse API, and that the dashboard's API address is wrong if it lasts; it keeps its frame and its "Try again" button.

## Alternatives

- **Show an error at once:** a Space that serves its own page while starting would then fail where waiting would succeed.
- **Retry without a separate message:** a wrong `API_URL` would look like a cold start for 3 minutes, which the review also flagged.

## Consequences

- A platform page or a wrong address never reaches a page's code, and nothing of it is kept.
- Tests: the client never returns an HTML 200, a JSON 200 without the notice, a list or an HTML 404, and gives up with its own message; every page waits out two platform pages, and a trial page that only got platform pages gives up cleanly and shows the trial once the API answers.
