# 0046. Areas from the current record, for display only

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 15 decision 1: option a, with two additions).
- Refines CLAUDE.md Section 13 (`/v1/areas/{area}/landscape`, the Landscape page). ADR 0007 is unchanged: no score reads an area.

## Context

There is no point-in-time therapeutic area (ADR 0007). API v2 returns no MeSH browse branches (0 of 88,504 scored trials). MeSH ancestor terms exist for 76.1% of scored trials, but NLM derives them from the current conditions, which changed after version 0 in 8% of sampled trials, and they have no history.

## Decision

1. A trial's areas are the top-level MeSH disease categories (tree C) and **Mental Disorders (F03)** found among the ancestor terms of its current record, through a fixed list of descriptors committed in the repository (`trialpulse.api.areas`). A trial without any listed ancestor is in **Not classified**.
2. **A trial counts in every area it belongs to.** Every landscape and area-list response says that area totals can exceed the number of trials.
3. Areas are display data: stored in `serving.trial_display` from API v2 only (ADR 0049), labeled in every response as coming from the current record, and never read by a feature or a score.
4. The landscape's competition figure is the number of open trials in the area today, labeled as descriptive.

## Alternatives

- **A versioned grouping** (title phase or sponsor class): point-in-time, but not a therapeutic area.
- **No area endpoint until an ADR 0007 classifier exists:** the Landscape page would have no data.

## Consequences

- The daily pull and the display pull request the ancestor terms.
- A trial's area can change with its conditions; the API shows today's.
