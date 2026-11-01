# 0044. Sponsor identity in Neon by a keyed hash, and who gets a display name

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 14 decision 9).
- Refines ADRs 0012, 0018 and 0022 for the live system, and CLAUDE.md Section 17 (a new secret, `SPONSOR_ID_KEY`).
- Decision 2 (display names) is replaced by ADR 0059, an allowlist.

## Context

The sponsor pass of ADR 0018 needs equality of sponsor keys only. A plain hash of a name can be reversed by hashing candidate names, which matters for sponsors who may be people: 4,786 keys on 8,893 scored trials are two or three plain words (an upper bound that includes brand names), and ADR 0022 says the true number of people among them is unknown.

## Decision

1. **The sponsor id** in Neon and in the sponsor file is `HMAC-SHA256(SPONSOR_ID_KEY, sponsor_key)` in hex, computed only for a key with an identity (`contracts.sponsor.has_identity`); otherwise it is NULL. The key lives only in `.env` and in GitHub secrets. A run without it refuses.
2. **Display names.** A sponsor gets a stored and shown display name unless its class is OTHER, UNKNOWN or AMBIG and its name is two or three plain words with no organization word. A plain word is letters only (an inner hyphen or apostrophe allowed). Individuals under ADRs 0012 and 0022 never get one. Industry, NIH, FED, OTHER_GOV and NETWORK sponsors keep their names and stay searchable.
3. Display names are written by the live path only from API v2 (Step 15 adds the table), never from the dataset.
4. **Ties.** Events of one day that the pass orders by sponsor id sort by the HMAC text on the live path, so parity layer 2 runs the offline pass on the same ids.

## Alternatives

- **A plain SHA-256 of the key:** reversible by a dictionary of names.
- **Names in Neon:** needed by nothing but display.

## Consequences

- Rotating `SPONSOR_ID_KEY` means recomputing every id in the sponsor file, `live.trial_state` and `live.study_versions` in one transaction; it is not planned.
- The export adapter (ADR 0037) gives the offline code HMAC ids in `sponsor_key`, so the laptop computes the same record.
