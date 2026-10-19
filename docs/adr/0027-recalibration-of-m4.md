# 0027. A recalibration of M4, and the rule it must pass

- Date: 2026-10-19
- Status: **Proposed, with its rule fixed before the measurement.** Aakrisht agreed in the review of Step 10 (2026-10-19) to recommendation (c) of the Step 10 report: "Before measuring the recalibration, commit the rule it must pass; then measure it on the development origins only, and adopt it only if it passes." This file is that commit. The measurement comes after it, and its result is added below without changing anything above the line "Result".
- Would change CLAUDE.md Section 9 (M4's predictions) only if the measurement passes the rule.

## Context

On the development origins of Step 10 the boosted model was overconfident at the top (pooled calibration slopes 0.82 to 0.93) and its calibration intercept ran along the age of a record: too low at landmark 0 and too high at landmark 6 (for example +0.112 at landmark 0 and -0.083 at landmark 6 at 12 months, origin 2016). M4 is now the model without the text families (ADR 0026); its pooled slopes on those origins were 0.863 and 0.925 at 12 months and 0.824 and 0.851 at 24.

## The candidate

1. **Fitted per origin, on that origin's training data only** (a fitted transform of Section 6). The data are the person-period rows of the last training year: the intervals that ended in the 12 months before the origin, which M4's early stopping already holds out.
2. **The model that did not see that year** gives the predictions it is fitted on: the model the early stopping fits on the earlier intervals, at its chosen number of rounds.
3. **The form:** for each of the two hazards of an interval (early stop, completion), its log odds against continuing get a slope, and an intercept per landmark index:
   - log odds of an early stop = a_stop[k] + b_stop × (the model's log odds of an early stop);
   - log odds of a completion = a_complete[k] + b_complete × (the model's log odds of a completion);
   - k is the landmark index (0 to 6).
   The 16 parameters are fitted by maximum likelihood of the three outcomes of each person-period row, without weights (ADR 0025).
4. **It is applied** to the hazards of the final model (the one fitted on every training row) before the cumulative incidence is formed.

## The rule

The candidate is adopted, through an amendment of this ADR, only if **every** condition holds at **both** development origins (2016-01-01 and 2017-01-01). The metrics are the harness's, on all evaluation rows of the origin, with censoring weights by sponsor class (ADR 0017), as point estimates:

1. The pooled calibration slope at 24 months is closer to 1: |slope − 1| is smaller with the recalibration than without it.
2. At 24 months, the absolute calibration intercept is smaller with the recalibration than without it, at landmark index 0 and at landmark index 6, each.
3. The Brier score at 12 months, pooled, is not larger with the recalibration than without it.
4. The AUC at 12 months, pooled, is not lower by more than 0.001. (Not in the Step 10 recommendation; added here so that a gain in calibration is never bought with ranking, the quantity the selection rule reads.)

If any condition fails at either origin, M4 stays as it is, and Step 11 evaluates it without a recalibration. Nothing is tuned: the form above is the only candidate, and it is measured once.

## What is measured, and where

`uv run python -m trialpulse.models.recalibration --origins dev` fits M4 on each development origin, fits the candidate as above, scores the origin's evaluation rows with and without it, writes the four conditions and their numbers to `data/results/recalibration/dev.json`, and logs the run. No locked origin is read.

## Alternatives

- **Isotonic regression of the cumulative incidence at each horizon.** Not monotone across horizons by construction, and it needs outcomes at the horizon, which the last training year does not have for most rows (12 and 24 months after a landmark of that year lie after the origin).
- **A slope and one intercept per hazard.** It cannot follow the gradient along the landmark index that the Step 10 tables show.
- **Fitting on the evaluation rows of the development origins.** It would measure the candidate on the rows it was fitted on.

## Result

(Added after the measurement; nothing above this line changes.)
