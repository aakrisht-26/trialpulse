# 0052. The Trial detail page before go-live and for a trial without a score

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 16 decision 1: option a, with two additions).
- Refines CLAUDE.md Sections 13 and 18 (Step 16, "Trial detail: risk timeline with amendment markers, top drivers") and ADRs 0033 and 0043.

## Context

The risk line starts at go-live (ADR 0043): after the bootstrap a trial has one served point, its score at the data cutoff, and the amendment markers posted before go-live. The line grows only when a score moves by 0.01 or more, the record changes, a landmark passes or the model changes. A trial without a score has a reason code (reversal, beyond_120_months, band_not_validated) and may have earlier points, for example a trial that crossed 120 months after go-live.

## Decision

1. **One layout for every trial.** A score card shows the latest early-stop risk at 12 and 24 months, the date it was scored, the flag when there is one (ADR 0055) and the five drivers of each horizon. One chart draws every served point on a date axis, with the amendment markers on the same axis; the span before the first point is shaded and captioned that the risk line starts at go-live.
2. **A trial without a score** shows its reason in words and its code, no risk number and no drivers. Its earlier points stay on the chart, labeled as earlier scores with no current score.
3. **The registry id links to the trial's page on ClinicalTrials.gov** (`https://clinicaltrials.gov/study/{nct_id}`) wherever a trial is listed or shown.
4. **The drivers carry ADR 0033's note:** a driver says what moved the model's score for this trial, not why a trial stops. Each driver is described as raising or lowering the model's odds of an early stop against continuing, averaged over the horizon's intervals, a quantity that ranks trials like the served risk, though not exactly.

## Alternatives

- **No chart until a trial has two points**, with a table of markers before that: no sparse chart, but two layouts to build and test, and most trials would show no chart for weeks after go-live.
- **A trial without a score shows only its reason:** simplest, but it hides real history.

## Consequences

- A chart with a single point looks sparse until scores move; the shaded span and its caption say why.
- The page shows no back-scores before go-live, so it never implies history the system does not have.
