# 0026. The champion leaves out the text families

- Date: 2026-10-19
- Status: **Accepted.** Decided by Aakrisht in the review of Step 10 on 2026-10-19: "M4 from here on is the full model without the text families; the text families stay in the report as an ablation and leave the production pipeline. The declared selection rule then picks the new M4." Decisions 3 to 5 are how Claude built it; they are flagged in the report of the follow-up.
- Changes CLAUDE.md Section 9 (M4 is no longer "all feature families"), Section 8 (the text families are computed for an ablation only), Section 13 (the drivers shown with a prediction are named features), ADR 0019 (where the text components are used) and ADR 0024 (the rungs of the ablation).

## Context

On the development origins of Step 10 the ablation of M4 added the text families (64 TF-IDF and SVD components and 5 eligibility statistics, ADR 0019) last. With them, M4's mean AUC at 12 months over all landmark indices was 0.7190; without them 0.7245. The paired difference was -0.0055 (-0.0101 to -0.0011) at 12 months and -0.0004 (-0.0043 to +0.0033) at 24. The text families made M4's fit about three times as long, and one text component was the third feature by gain: a driver that a user cannot read (Section 13 shows the top 5 drivers with every prediction).

## Decision

1. **M4 is the discrete-time LightGBM on every feature family but text:** the sponsor class, the time family, the design family, the amendment signals, the sponsor record and competition (54 features). It is the champion candidate of Section 9, and the selection rule chooses among M0, M2, M3 and M4 as before.
2. **The text families are an ablation rung, `m4-text`** (M4 plus the text families), reported beside the ladder and never a candidate. They leave the production pipeline: the scoring of Step 13 and the live path of Step 14 compute no text feature. The offline feature build keeps computing them, so that the ablation can be reproduced.
3. **The ablation of M4** is `m4-design`, `m4-amendments`, M4, `m4-text`. The rung that Step 10 called `m4-sponsor` is the new M4.
4. **M4 keeps the settings tuned in Step 10** (`config/tuned/m4.json`), chosen for the same learner on every family; `m4-text` runs with them too. Step 10's `m4-sponsor` ran with these settings, so the development numbers of the new M4 are those numbers.
5. **Every result is named by what it is.** The development runs of M4 and of `m4-text` are made again under the new names (the code is deterministic, so they reproduce Step 10's numbers of `m4-sponsor` and of the old M4), and the paired comparisons that involve M4 are made again. The result files of Step 10 under the old names are kept aside, not deleted.
6. **M1 is unchanged.** It is the static comparison of Section 9 and ADR 0023, with every family; it is not part of the production pipeline.

## Alternatives

- **Keep M4 as Section 9 wrote it.** On the development origins it ranks trials worse, costs three times the fit and shows text components as drivers.
- **Tune the new M4 again** (40 trials, about an hour). The settings were chosen for this learner; a second tuning would spend the budget of Section 9 a second time on the same development rows and make M4's development numbers a little more optimistic. Not done; it can be asked for.
- **Drop the text families from the feature build.** The ablation could then not be reproduced, and the finding that text did not help would rest on a run nobody can repeat.

## Consequences

- CLAUDE.md gains through the Amendments: "ADR 0026, the champion leaves out the text families: M4 is the discrete-time LightGBM on every feature family but text (54 features) and the champion candidate; the text families are the ablation rung m4-text and leave the production pipeline (Steps 13 and 14 compute no text feature); M4 keeps the settings tuned in Step 10; M1 is unchanged."
- The SHAP drivers of Step 12 and the API of Step 15 show named features only.
- Step 11 evaluates M4 as defined here; `m4-text` may be run there as a declared secondary comparison.
