# 0006. Phase: keep current-record phase out of the main model, use the phase stated in the versioned title

- Date: 2026-09-23
- Status: **Proposed**. For Aakrisht to accept after Step 2 part b has run. It applies only if part b shows that the core config has no per-version phase column.

## Context

CLAUDE.md Section 8 rule 2 allows a current-record-only field as a feature only if it changed after version 0 in under 3% of sampled trials. The Step 2 stability audit (part f: 150 seeded cohort trials, all 973 versions) found:

- phases changed after version 0 in 6 of 150 trials, 4.0% (Wilson 95% interval 1.8% to 8.5%);
- phase group (early, mid, late, post-approval, N/A) changed in 5 of 150, 3.3% (1.4% to 7.6%).

Both fail the rule. Part b, which says whether the core config has a per-version phase column, is blocked on Hugging Face access. The dataset card describes `core` as scalar protocol-section columns, while phases is a list in the registry, so a per-version phase column is not expected (unverified). The card also lists titles among the core columns ("The remaining ~70 columns cover titles, brief/detailed descriptions, eligibility criteria ..."), so the official title has version history. Sponsors often state the phase in it (for example "A Phase 2, Randomized ...").

Phase matters to the model plan: it is a design feature, M0 stratifies by phase group when phase is allowed, and CLAUDE.md already names sponsor class as M0's fallback.

**Evidence from the cached part g pull:** none. The pull requested no title field. The cohort Parquet file has no title column, and none of the 856 cached API v2 pages contains `officialTitle` or `briefTitle`. As instructed, nothing was pulled again. The share of cohort trials whose title states a phase, and its agreement with the current phase field, will be measured from the dataset in Step 7.

## Decision (proposed)

1. If part b confirms there is no per-version phase column, **current-record phase stays out of the main model**. It is not a feature of M1 to M4, and M0 stratifies by sponsor class. The 3% rule holds as written.
2. Add a **"phase stated in the title"** feature, parsed from the official title (falling back to the brief title) as of the landmark. Titles are versioned, so it is point-in-time.
   - A deterministic parser recognizes Arabic and Roman numerals and combined phases (for example "Phase 1/2", "Phase I/II", "Early Phase 1").
   - It maps to the same groups as the audit (early, mid, late, post-approval), plus "not stated".
   - The parser has unit tests. Step 7 reports its coverage and its agreement with the current phase field.
3. Current-record phase appears only in a **sensitivity analysis**, labeled as using a field that fails the stability rule, reported separately and never in headline results.
4. If part b finds a per-version phase column, this ADR is withdrawn and that column is used directly.

## Alternatives

- **Use current-record phase anyway.** It breaks the 3% rule the project set for itself. A phase that is changed later can leak information about how the trial went.
- **Drop phase entirely.** Leak-free, but it throws away a strong, well-known predictor that the title often states.
- **Point-in-time phase from AACT snapshots** (`docs/fallback_options.md`, option 1). Leak-free at snapshot resolution, but only from 2017, and it needs the AACT pipeline.

## Consequences

- CLAUDE.md Section 8, design family: "phase (if allowed)" becomes "phase stated in the title". Section 9: M0 uses its documented fallback (sponsor class).
- The feature registry records the new feature as versioned, with the title fields as its source.
- Trials whose titles do not state a phase (most N/A trials, and some others) fall into "not stated", which is itself informative and is kept as a category.
