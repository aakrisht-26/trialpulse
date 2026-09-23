# 0007. Competition: use the versioned count of all open interventional trials

- Date: 2026-09-23
- Status: **Proposed**. For Aakrisht to accept after Step 2 part b has run.

## Context

CLAUDE.md Section 8 defines the competition family as the number of open interventional trials sharing at least one therapeutic area (MeSH browse branch, if allowed), overall and within the same phase group. Step 2 found that the therapeutic area cannot be measured point-in-time from the planned sources:

- API v2 returns no MeSH browse branches: 0 of 50 full records checked, and 0 of 420,073 cohort trials in the part g pull.
- API v2 does return NLM's MeSH condition terms (77.5% of cohort trials) and their ancestors (76.0%). But NLM derives them from the current conditions, and conditions changed after version 0 in 8.0% of sampled trials (part f). The version snapshots carry no derived section, so these terms have no history to audit.
- The phase-group split fails the stability rule too (3.3%, see ADR 0006).

## Decision (proposed)

The competition family is the **count of all open interventional trials at the landmark**, computed from the status history of every trial. It uses only versioned data: a trial counts as open at L if its latest version on or before L has an open status. The therapeutic-area and phase-group splits are dropped from v1.

**Possible later improvement, not built now:** a classifier that maps the versioned title and brief summary, as of L, to MeSH-based therapeutic areas using the MeSH vocabulary. That would give point-in-time area counts without relying on NLM's current-record assignment. It needs NLM's MeSH files (a new data source, so its own ADR), a labeled check set and its own leakage tests.

## Alternatives

- **Current MeSH ancestor terms from API v2.** Available for about three quarters of trials, but derived from current conditions (8.0% of which changed), so it risks leakage and cannot be audited.
- **Map the conditions to MeSH tree branches with NLM's MeSH files.** A new data source, and it inherits the instability of the conditions.
- **Point-in-time MeSH terms from AACT snapshots** (`docs/fallback_options.md`, option 1). Leak-free at snapshot resolution, but only from 2017, and it needs the AACT pipeline.

## Consequences

- CLAUDE.md Section 8, competition family, changes to the single versioned count.
- An overall count mostly tracks registry growth over calendar time, so it may add little beyond the registration year and landmark date. The Step 10 ablation shows whether it earns its place.
- The Landscape dashboard page (Section 13) cannot show risk by therapeutic area until an area source exists. It would show the overall open-trial count instead.
