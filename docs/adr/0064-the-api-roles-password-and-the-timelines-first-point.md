# 0064. The API role's password, and the timeline's first point

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (review of Steps 14 to 16, finding 12).
- Refines ADR 0049 (the API's read-only role) and the caption wording of ADRs 0043 and 0052.

## Context

`trialpulse.live.api_role` asked for the role's password and checked only that it had 16 characters, while Neon refuses a role made by SQL whose password has less than 60 bits of entropy. ADR 0052's caption said "the risk line starts at go-live", but the bootstrap regenerates every live trial's score at the data cutoff (ADR 0040, item 2), and that score is each trial's first point, dated before go-live.

## Decision

1. **The command makes the password itself**: 32 random bytes from Python's `secrets` (256 bits; at least 128 asked), URL-safe, never typed and never stored by the command. It prints `API_DATABASE_URL` once (with Neon's pooled host for `--target neon`) to paste into `.env` and the Space's secrets. A second run makes a new password: it is how the password is rotated.
2. **The timeline's caption says what the first point is**: the trial's score at the data cutoff, regenerated when the live system started (or its first live score, for a trial registered later), with no scores before it. ADR 0043's rule is unchanged: no back-scores.
3. ADR 0049 names the command (`src/trialpulse/live/api_role.py`) instead of a SQL file that does not exist.

## Consequences

- The printed URL holds a password: it is shown once, in the laptop's terminal, and nowhere else.
- The dashboard's stubs were recorded again from the API; only the timeline's note changed.
