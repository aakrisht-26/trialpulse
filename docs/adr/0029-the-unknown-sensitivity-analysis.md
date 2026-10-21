# 0029. The UNKNOWN sensitivity analysis

- Date: 2026-10-21
- Status: **Accepted.** Approved by Aakrisht in the review of the Step 10 follow-up and the pre-registration draft on 2026-10-21 ("Section 5.1: the UNKNOWN sensitivity analysis is approved as proposed").
- Refines CLAUDE.md Section 6 ("A sensitivity analysis treats UNKNOWN as an early stop") as amended by ADR 0014.

## Context

ADR 0014 censors a trial whose last state is lapsed at the data cutoff at that state's status verified date. Section 6 asks for a sensitivity analysis that treats UNKNOWN as an early stop. ADR 0014's consequences note that it cannot be done by relabeling the landmark table: 35,938 eligible trials are censored at or before registration and have no landmark row, and the landmarks between a trial's last verification and its lapse have none either. The analysis therefore needs a cohort of its own, and its method has to be fixed before any locked origin is read.

## Decision

1. **A cohort variant** (`uv run python -m trialpulse.cohort.build --unknown-as-stop`, folder `data/cohort_unknown_as_stop/`): a trial whose last state is lapsed at the data cutoff has an early stop at its lapse date (the first day the registry's rule holds for that state, recorded in `outcomes.parquet`) instead of a censoring at its verification. Nothing else changes, so the landmark rules of Step 4 add the rows the main cohort leaves out. A lapse that a later version resolves stays a lapse that censors nothing.
2. **Features for the added rows** come from the same code (`uv run python -m trialpulse.features.build --variant unknown-as-stop`, folder `data/features_unknown_as_stop/`).
3. **The champion is trained exactly as in the main run**: the variant's training cohorts are built with the main rules, and the build refuses to finish unless they equal the main build's, file for file. The model scores the variant's evaluation rows (`uv run python -m trialpulse.eval.walkforward --model m4 --origins <year> --unlock-test --variant unknown-as-stop`).
4. **Reported per test origin (2018 and 2019)** beside the main results: AUC, Brier score, lift at 10% and calibration slope at 12 and 24 months. No hypothesis rests on it.

## Alternatives

- **Train on the variant's labels as well.** It answers a different question: how well a model of "stop or lapse" ranks trials, not how the registered model's numbers move if lapsed trials are counted as stops.
- **Relabel the existing landmark rows only.** It leaves out the rows the main cohort never had, which are the rows the analysis is about.

## Consequences

- The cohort build, the feature build and the harness each have a variant switch, writing to folders of their own; the main files, `docs/data_audit.md` and `docs/features.md` are never touched by them.
- `docs/results.md` (Step 11) has a section for the analysis, generated with the rest.
- Added after the review of 2026-10-21 (how, not what): the harness reads the variant's training rows and their features from the main folders, so the model is the main run's by construction; the variant cohort is built beside its folder and moved into place only after its training files match, the feature build refuses to cross between main and variant, and `docs/results.md` refuses a variant result that read other inputs.
