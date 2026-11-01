# 0047. Sponsors are found through their display names only

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 15 decision 2: option a).
- Refines CLAUDE.md Section 13 (`/v1/sponsors/{name}/portfolio`) and ADR 0044.

## Context

Neon holds a keyed sponsor id (ADR 0044). A sponsor of class OTHER, UNKNOWN or AMBIG whose name is two or three plain words with no organization word gets no display name, because it may be a person.

## Decision

1. `serving.sponsors` holds, for each sponsor with a display name, its keyed id, its display name, a search key (the name normalized as a sponsor key) and its class. The API matches a queried name, normalized the same way, against the search keys. It never holds `SPONSOR_ID_KEY`.
2. A sponsor without a display name cannot be found by name. Its lookup answers 404 with **exactly the body** of a name that does not exist, so the API never confirms that a person sponsors trials.
3. In every other response, a trial of such a sponsor shows its sponsor class and a null sponsor name.
4. `/v1/sponsors?prefix=` lists display names that start with a prefix (at least 3 characters).
5. Aliases (ADR 0018) are not followed in v1: a portfolio holds the trials whose current sponsor is that sponsor.

## Alternatives

- **The API computes ids from any name with the key:** finds every sponsor, but confirms a person's sponsorship to anyone who tries the name, and puts the key in a second place.
- **Opaque ids in the path:** stable URLs, but changes the roadmap's route and still cannot reach hidden sponsors.

## Consequences

- A renamed sponsor is found under its current name only.
