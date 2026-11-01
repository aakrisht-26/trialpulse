# 0041. One later read of the gap before go-live

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 14 decision 6: Gap (b)). His access to `anamnesis-data/clinical_trials_history` is approved.
- Adds a second, one-time offline source to CLAUDE.md Section 7 item 1, for the bootstrap only. Refines Section 12 ("Bootstrap").

## Context

The pinned revision ends on 2026-09-25 and go-live is planned for about 2026-12-01. API v2 cannot show the versions of that gap that were superseded, posted on one day with others, or never passed review: at an analog of the 67 days, 17,748 versions would be hidden and 4,932 scored rows would differ. `n_versions` and the version-0 values would stay wrong for good.

## Decision

1. **One read, once, on the laptop, before go-live,** of the `core` rows (and the `interventions` rows) posted after the pinned cutoff, from the dataset's new location `anamnesis-data/clinical_trials_history`, at a pinned commit hash (tags can move).
2. **The real API pull from the cutoff runs first** and is kept on the laptop, written to no live table. The later read is then compared with it as parity layer 5b (ADR 0038): what the API really hid, and the versions it never showed.
3. **The bootstrap seeds state at the release of that read**, minus a tail of 1 to 7 days that the first live run's overlap covers.
4. **The new layout is read through an adapter** that maps it to the pinned columns. The card lists breaking changes: interventions keyed by `ord` with repeated pairs as separate rows, an empty post date for versions that failed review, a new column, rich text now Markdown. Every one is handled by the adapter or refused, never silently passed.
5. Fallback: Gap (a), accept and record with a flag per trial, if the read fails.

## Alternatives

- **Gap (a), accept and record:** no new read, but the flag covers 5 to 7 times the rows affected and `n_versions` stays wrong for good.
- **Gap (c), a newer full pin:** ties the evidence of Steps 3 to 13 to the old pin and still leaves a tail.

## Consequences

- About 150 lines of laptop code, built with the bootstrap, not in the cloud session.
- The gap read is the last dataset read before go-live; any later one needs its own ADR.
