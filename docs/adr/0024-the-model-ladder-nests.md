# 0024. The model ladder nests: the sponsor class is in M2 and M3

- Date: 2026-10-10
- Status: **Accepted.** Decided by Aakrisht in the review of Steps 4 and 5 on 2026-10-07 ("Step 10: sponsor class enters M2 and M3, so that each rung of the ladder contains the one before", recorded as guidance item 8 with an ADR promised for Step 10) and repeated in the review of Step 9 on 2026-10-10 ("Sponsor class goes into M2 and M3 so the ladder nests (M0 sponsor class, M2 plus design, M3 plus amendment signals, M4 plus everything else). Record it as the ADR promised in Step 5."). Decisions 2 and 3 are how Claude built it; they are flagged in the Step 10 report.
- Changes CLAUDE.md Section 9 (the model ladder) and names the rungs of the Step 10 ablation.

## Context

Section 9 gives each rung of the ladder one question:

- M0: one Aalen-Johansen curve per lead sponsor class (ADR 0006: phase is not versioned). Is there any signal beyond base rates?
- M2: discrete-time multinomial logistic regression, "design features only".
- M3: M2 plus the amendment signals. Do dynamic signals help?
- M4: discrete-time LightGBM with all feature families.

The sponsor class belongs to the sponsor family of Section 8, so M2 and M3 as written do not know what M0 knows. "M2 against M0" would then mix a gain (the design family) with a loss (the sponsor class), and no difference between two rungs would answer its question. The sponsor class is also the strongest single field of the registry record for this outcome (EDA, Table 1; the Cox models of Step 9).

## Decision

1. **The sponsor class is a feature of M2 and M3.** The ladder is: M0, the sponsor class; M2, the sponsor class and the design family; M3, M2 and the amendment signals; M4, every feature family.
2. **Every discrete-time rung also has the interval number and the time family** (the landmark index; the months since registration repeat it and are left out of the linear models). They say where in a trial's life a row sits, which a model of person-period rows pooled over landmarks needs. M0 does not have the landmark index: it pools every landmark in one curve per class. So "M2 minus M0" is the gain of the design family and of knowing the landmark index together, on top of the change from a curve to a regression. The report says so where it reads that difference.
3. **The ablation of M4 uses the same nesting, with M4's learner and settings**: `m4-design` (the sponsor class, the time family and the design family), `m4-amendments` (plus the amendment signals), `m4-sponsor` (plus the rest of the sponsor family and competition), then M4 itself (plus the text families). "Design only" in Step 10's build list is therefore read as the first of these rungs. The gain of a family is then not mixed with a change of learner, and `m4-design` and `m4-amendments` have the features of M2 and M3: the difference between M2 and `m4-design` is what trees add to the same features.

## Evidence

- `tests/models/test_discrete_time.py::test_each_rung_of_the_ladder_holds_the_one_below`: the feature sets nest, M4's set is every main feature of the registry, the difference between M3 and M2 is exactly the amendment family, and the ablation rungs nest up to M4.
- `docs/results_dev.md`: the ladder, the paired differences between neighboring rungs, and the ablation.

## Alternatives

- **Section 9 as written** (M2 on the design family only). Each difference between rungs would have two causes of opposite sign.
- **One model per sponsor class** for M2 and M3. It keeps "design only" in each model, but the small classes (a few hundred rows) cannot carry a model, and M4 would not nest.

## Consequences

- CLAUDE.md gains through the Amendments: "ADR 0024, the ladder nests: the sponsor class is a feature of M2 and M3, so each rung holds the one below (M0 sponsor class; M2 plus design; M3 plus amendment signals; M4 every family). Every discrete-time rung also has the interval and the landmark index. The ablation of Step 10 adds the same families one at a time to M4's learner."
- Step 11's hypothesis "the gain from amendment signals grows with landmark index" is the difference M3 minus M2 by landmark index, which `trialpulse.eval.compare` reports with a paired interval.
