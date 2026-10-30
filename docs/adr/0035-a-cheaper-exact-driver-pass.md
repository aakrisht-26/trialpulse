# 0035. A cheaper exact driver pass

- Date: 2026-10-30
- Status: **Proposed.** Built under rule 3 of Step 13 (a job past about an hour needs a cheaper plan first) and flagged for Aakrisht's review of Step 13. It replaces no decision of his: the three options put to him on 2026-10-27 (carry drivers forward, 12-month drivers only, the full pass on every run) stay open to him.
- Refines ADR 0033: how the SHAP values are computed, not what they are. No definition, target or stored field changes.

## Context

The five drivers stored with every served score (CLAUDE.md Section 13) need TreeSHAP for every scored trial. On the served champion (1,642 rounds of 3 classes, 4,926 trees) the pass of ADR 0033 took 51.5 ms a trial on a seeded sample of 5,000 open trials at the cutoff, about 76 minutes for the 88,895 scored trials: past the one-hour line of Section 3.

More threads or processes do not help. One pass over 4,000 real rows took 52.7 s on 8 threads, 52.7 s on 2 processes of 4 threads, 54.0 s on 8 processes of 1 thread and 53.0 s on 12 processes of 1 thread. The limit is the laptop's memory, not its cores or any setting: a pure CPU load reached 7.75 times one process on 12 processes, while random reads over a 128 MB array stopped at 2.3 times on 4 processes and fell after that. TreeSHAP walks the whole forest for every row, so its time follows the number of trees it touches.

Two parts of that walk cannot change the result:

- The target is f_stop - f_continue, so the completion class's 1,642 trees are explained and then thrown away.
- SHAP values add up over trees, and a tree that never splits on the interval column gives the same values in every interval. Of the 3,284 stop and continue trees, only 609 split on it (273 of the stop class, 336 of the continue class), yet every tree is walked once per interval.

## Decision

1. `trialpulse.explain.drivers.split_trees` cuts the booster, through LightGBM's own text format, into single-output models: the stop and continue trees without the interval column (fixed) and those with it (moving). The completion class's trees are left out.
2. `explain_horizons` explains the fixed models once and the moving models once per interval, and adds them. The values are still LightGBM's `pred_contrib`, so ADR 0033's target, its additivity and every stored driver stay as they are.
3. Every call checks the sum against the full pass on its first 16 rows, in every interval it uses, and refuses a difference above 1e-9. A change in the model format or in the order of the classes therefore stops the run instead of corrupting the drivers.

Measured on the champion with a prototype of this rule, on 4,000 real rows: 52.5 s for all four passes, 13.1 ms a trial against 52.7 ms, so about 19 minutes for the 88,895 trials against about 78. The largest difference from the full pass on 300 rows was 1.69e-14.

## Alternatives

- **Carrying drivers forward** (explain a trial only when its history row is written). The first full run still takes the 76 minutes, and a stored driver can describe older features than the score beside it. It may still suit Step 14's daily job; that is a separate question, and this pass makes it less pressing.
- **12-month drivers only.** About half the time, but the 24-month score would have no drivers of its own, which narrows Section 13.
- **The full pass on every run,** as a declared exception to the one-hour rule. Nothing would be gained over this pass.
- **Worker processes or more threads.** Measured above: no gain on this laptop.
- **Another TreeSHAP implementation** (the shap package, or our own). ADR 0033 rejected the package; our own would give up LightGBM's tested code.

## Consequences

- A full scoring run with drivers fits under the hour, so Step 13's run needs no exception.
- The code depends on LightGBM's text model format. `uv.lock` pins the version, a test checks the split on a small model, and the check of decision 3 guards every call on the real one.
- Step 12's explanations go through the same code. Regenerating `docs/explanations.md` from it is the test at scale: the document must come out byte for byte.
- ADR 0033's cost estimate of 6.5 ms a row is superseded by the measurements here: the served champion is larger than the per-origin models, and the drivers cover both horizons.
