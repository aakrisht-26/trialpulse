# 0035. A cheaper exact driver pass

- Date: 2026-10-30
- Status: **Proposed.** Built under rule 3 of Step 13 (a job past about an hour needs a cheaper plan first) and flagged for Aakrisht's review of Step 13. It replaces no decision of his: the three options put to him on 2026-10-27 (carry drivers forward, 12-month drivers only, the full pass on every run) stay open to him.
- Refines ADR 0033: how the SHAP values are computed, not what they are. No definition, target or stored field changes.

## Context

The five drivers stored with every served score (CLAUDE.md Section 13) need TreeSHAP for every scored trial. On the served champion (1,642 rounds of 3 classes, 4,926 trees) the pass of ADR 0033 took 51.5 ms a trial on a seeded sample of 5,000 open trials at the cutoff, an estimated 76 minutes for the 88,895 scored trials: past the one-hour line of Section 3.

More threads or processes did not help at the time: one pass over 4,000 real rows took between 52.7 s and 59.3 s on every mix tried, from 8 threads to 12 processes of 1 thread. That flat time was not a limit of the work or of the laptop's memory, as this ADR first said. The review found Windows holding these background processes on the four efficiency cores (per-CPU samples). A CPU spin of 8 processes ran at about 31 million counts a second, and at about 124 million when each process opted out of execution-speed throttling (`SetProcessInformation` with `ProcessPowerThrottling`), alternating twice. The same script, rerun later, took 12 s for the pass until the throttling set in and 54 s after it. Why Windows applies it at some moments and not at others is not established, and whether a run from a terminal in the foreground is affected is untested. The times in this ADR are as measured from this session, so they are probably throttled unless it says otherwise.

Two parts of that walk cannot change the result:

- The target is f_stop - f_continue, so the completion class's 1,642 trees are explained and then thrown away.
- SHAP values add up over trees, and a tree that never splits on the interval column gives the same values in every interval. Of the 3,284 stop and continue trees, only 609 split on it (273 of the stop class, 336 of the continue class), yet every tree is walked once per interval.

TreeSHAP walks the whole forest for every row, so its time follows the number of trees it touches, whatever the machine's state.

## Decision

1. `trialpulse.explain.drivers.split_trees` cuts the booster, through LightGBM's own text format, into single-output models: the stop and continue trees without the interval column (fixed) and those with it (moving). The completion class's trees are left out.
2. `explain_horizons` explains the fixed models once and the moving models once per interval, and adds them. The values are still LightGBM's `pred_contrib`, so ADR 0033's target, its additivity and every stored driver stay as they are.
3. Every call checks the sum against the full pass on its first 16 rows, in every interval it uses, and refuses a difference above 1e-9. A change in the model format or in the order of the classes therefore stops the run instead of corrupting the drivers.

This walks 25.9% of the leaves the full pass walks. Measured on the champion with a prototype of this rule, on 4,000 real rows: 52.5 s for all four passes, 13.1 ms a trial against 52.7 ms. The largest difference from the full pass on 300 rows was 1.69e-14. The full run at the cutoff then took 1,319 s and 1,266 s for the drivers of the 88,895 trials, about 22 minutes against the estimated 76, with a code review running on the laptop at the same time. In one machine state, the review measured 3.6 times between the two passes, so the saving is about 3.5 to 4 times.

## Alternatives

- **Carrying drivers forward** (explain a trial only when its history row is written). The first full run still takes the 76 minutes, and a stored driver can describe older features than the score beside it. It may still suit Step 14's daily job; that is a separate question, and this pass makes it less pressing.
- **12-month drivers only.** About half the time, but the 24-month score would have no drivers of its own, which narrows Section 13.
- **The full pass on every run,** as a declared exception to the one-hour rule. Nothing would be gained over this pass.
- **More threads or worker processes.** No gain while the throttling held (above). Without it they scale (about 350 rows a second on 8 processes against 80), but they keep all the work this pass removes.
- **Opting out of Windows' throttling instead.** One call at the start of a process. It speeds up every long job, not only this one: a full run at the cutoff with this pass and the opt-out took 304 s in all against about 1,450 s, with the same hashes, though two later runs took 485 s and 368 s with no opt-out, so the throttling comes and goes by itself. It depends on a behaviour of Windows whose trigger is not established, and it draws more power, so it is a separate question for Aakrisht. This pass does not depend on it.
- **Another TreeSHAP implementation** (the shap package, or our own). ADR 0033 rejected the package; our own would give up LightGBM's tested code.

## Consequences

- A full scoring run with drivers fits under the hour, so Step 13's run needs no exception.
- The code depends on LightGBM's text model format. `uv.lock` pins the version, a test checks the split on a small model, and the check of decision 3 guards every call on the real one.
- Step 12's explanations go through the same code, which is the test at scale. Regenerated from it, the four figures came out byte for byte, and `docs/explanations.md` differs in two cells only, the largest additivity gaps, which print rounding noise (3.4e-14 became 2.4e-14 and 2.5e-14 became 2.8e-14). Every importance, rank agreement and timeline over the 40,000 explained rows stayed the same. The document was regenerated, so the command reproduces it.
- ADR 0033's cost estimate of 6.5 ms per row and pass is superseded by the measurements here: the served champion is larger than the per-origin models, the drivers cover both horizons, and those times were probably throttled.
