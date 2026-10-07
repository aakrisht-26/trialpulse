# 0017. IPCW censoring weights are estimated by sponsor class

- Date: 2026-10-07
- Status: **Accepted** by Aakrisht on 2026-10-07 (Step 5 review, open question 1): "estimate the IPCW censoring weights separately by sponsor class as the primary method, and keep the single curve as the sensitivity check." The rule for small classes (decision 2) and the tie convention (decision 5) were **accepted on 2026-10-07** in his review of the follow-up: "Decide the small-class groups once per origin on all evaluation rows and reuse them at every landmark index. The 200-row threshold is approved", and "events first at tied times in the censoring curve".
- Changes CLAUDE.md Section 6 ("Metrics") and the Step 8 harness. Replaces the note logged for Step 11 in the Step 8 review ("a sensitivity check that estimates the censoring weights separately by sponsor class").

## Context

Section 6 says: "Rows censored before the horizon are handled with IPCW, using a Kaplan-Meier estimate of the censoring distribution." One curve for all rows is right only if censoring is unrelated to the outcome.

Within the 12- and 24-month horizons of the evaluation rows, censoring comes from the UNKNOWN rule (ADR 0014): the data cutoff is years after the development and test origins. The EDA (`docs/eda.md`, Tables 1 and 2) shows that this censoring depends on the sponsor class, and that the sponsor class predicts the outcome:

| Lead sponsor class | Censored under the UNKNOWN rule within 60 months | Early stop within 12 months |
| --- | --- | --- |
| INDUSTRY | 2.3% | 3.5% |
| OTHER | 7.2% | 1.3% |
| NIH | 2.4% | 1.4% |
| Remaining classes | 9.2% | 1.1% |

The table is from the cohort as of 2018-01-01, which the EDA has read since 2026-10-07. When this ADR was written the EDA read the final cohort cut at that date, and the first column was 2.3%, 7.9%, 1.0% and 10.5%.

With one curve, the rows of a heavily censored class get too little weight, and every weighted metric leans toward the classes that report reliably.

## Decision

1. **Primary method: one censoring curve per lead sponsor class at the landmark.** The Kaplan-Meier estimate G of the censoring distribution is fitted separately within each class (the `stratum` column of the landmark rows), and each row is weighted by the curve of its own class. This applies to every IPCW metric of Section 6: AUC, Brier score, lift, and the calibration slope and intercept. In the cluster bootstrap, each resample fits its own curves.
2. **Small classes share one curve, and the groups are decided once per origin.** A class with fewer than 200 rows among all the evaluation rows of an origin joins one pooled group with the other small classes. The number is `evaluation.censoring_min_rows` in `config/project.yaml`. The same groups are used when all landmark indices are scored together and at each landmark index, so a class never changes scheme between slices. The curves are fitted on the rows of the slice being scored. The groups are fixed before bootstrapping, so a class cannot change group between resamples either.
3. **Sensitivity check: one curve for all rows.** The same metrics with a single censoring curve are reported beside the primary ones (`single_censoring_curve` in the results), as point estimates.
4. The calibration table by risk decile is unchanged: it compares predictions with Aalen-Johansen within each decile and uses no weights.
5. **At a tied time, events come before censorings.** A row that stopped or completed on day t was observed through day t, so it is not at risk of a censoring on day t: the risk set of that censoring leaves it out. Under this convention the weights of the rows scored at one horizon add up to the number of rows.

## Evidence

- `test_the_single_curve_is_biased_when_censoring_depends_on_the_class`: two synthetic classes, one that stops early often and is censored heavily, one that rarely stops and is never censored. The share stopped early by the horizon is known from the uncensored times. By-class weights recover it within 0.006; the single curve underestimates it by more than 0.03.
- On the constructed pattern of the Step 8 tests, the weights are worked out by hand for two groups and for the single curve.
- M0 on the development origins: with labels as of each origin, the AUC at 12 months is 0.5439 by class against 0.5435 with one curve (2016 origin) and 0.5519 against 0.5514 (2017 origin); at 24 months 0.5345 against 0.5338 and 0.5348 against 0.5341. The Brier score at 12 months is 0.03248 against 0.03250 and 0.03094 against 0.03097. The differences are small for M0, whose predictions are constant within a sponsor class.
- Groups on the development origins: INDUSTRY, OTHER, NIH, FED, NETWORK and OTHER_GOV keep their own curve at every landmark index; INDIV and UNKNOWN are pooled (92 rows at the 2016 origin, 71 at the 2017 origin). At landmark index 6 of the 2016 origin the curves are fitted on 1,092 INDUSTRY, 3,947 OTHER, 125 NIH, 102 FED, 63 NETWORK, 87 OTHER_GOV and 13 pooled rows.
- Decisions 2 and 5 together, against the rule as first built (groups decided on each slice, ties with events at risk): M0's values for all landmark indices together are unchanged to five decimals (AUC at 12 months 0.543942 and 0.551912 at both), and the AUC at one landmark index moves by at most 0.0003.
- `test_at_a_tied_time_events_come_before_censorings` works decision 5 out by hand, and `test_the_weights_at_one_horizon_add_up_to_the_rows` checks the sum on whole-week times with many ties. The censoring curve is compared with lifelines on times where each censoring is moved half a day later.

## Small classes: the options considered

Decision 2 was first built with the groups decided on the slice being scored (option a below). An independent review of that version (2026-10-07) confirmed the weights against a separate implementation and found two limits of it, measured on the development origins:

- **What shares the pooled curve.** At one landmark index, NIH, FED and NETWORK are under 200 rows at every index, and OTHER_GOV from index 3 (2016 origin) and index 4 (2017 origin). OTHER_GOV is the most heavily censored class (11.4% of its rows censored within 24 months at the 2016 origin) and NIH is almost never censored (2 of 1,068 rows). Inside the pooled group the censoring rate then differs by a factor of about 10, which is the dependence this ADR sets out to remove. Example, 2017 origin, index 4, 24 months: the pooled curve gives G = 0.934, where OTHER_GOV on its own curve at index 3 has G = 0.859.
- **The scheme changes between slices.** OTHER_GOV has its own curve at index 0 to 2 (2016) and 0 to 3 (2017) and is pooled afterwards, so the series by landmark index mixes two schemes. With all landmark indices together, the pooled group is INDIV plus UNKNOWN with 92 and 71 rows, itself below the minimum.

The effect on M0 is small. Deciding the groups once on all the evaluation rows of an origin and reusing them at each landmark index (option b) moves an AUC at one landmark index by at most 0.0003, a Brier score by at most 0.00003 and a calibration slope by at most 0.014, against the rule as built.

| Option | What it does | For | Against |
| --- | --- | --- | --- |
| a. Keep the rule as built | Groups decided on the slice being scored | No curve is fitted on fewer than 200 rows, except the pooled group itself | The two limits above |
| b. Groups decided once per origin | Groups from all the evaluation rows of the origin, reused at each landmark index; each slice still fits its own curves | A class never changes scheme between landmark indices; OTHER_GOV, NIH, FED and NETWORK keep their own curve everywhere | Curves on 100 to 200 rows at the late landmarks, and a pooled remainder of 6 to 16 rows at one index (0.1% of the rows) |
| c. Another minimum | For example 100 rows | Fewer classes pooled | Any number is arbitrary; both limits stay, at other landmarks |

Aakrisht chose option b on 2026-10-07, with the minimum of 200 rows. The code implements it.

## Alternatives

- **One curve as the primary method and curves by class as the sensitivity check,** as logged in the Step 8 review. The dependence of censoring on sponsor class is measured, so the method that allows for it should be the one reported first.
- **A regression model for censoring** (for example Cox with several covariates). It would allow for more than one covariate, at the price of a second fitted model inside every metric and every bootstrap resample. Sponsor class is where the EDA found the dependence.
- **Counting an UNKNOWN censoring as an early stop.** That is the sensitivity analysis Section 6 already requires for Step 11. It answers a different question (what if those trials in fact stopped), and stays.

## Consequences

- Evaluation rows must carry `stratum`, the lead sponsor class at the landmark; the harness refuses rows without it.
- The weights are still right only if censoring is unrelated to the outcome within a sponsor class. Dependence on anything else is not handled, and the single-curve values show how much the choice matters.
- Models that train with IPCW weights (M1 in Step 9) should use curves by sponsor class as well, fitted on their own training rows. To be settled in Step 9.
- The graded performance of live predictions (Step 17) uses the same weights.
- **Ties.** The censoring curve of Step 8 kept a row that has an event on day t at risk of a censoring on day t, and its weights missed the row count by about 2 in 100,000 (0.99998 n on the 2016 development rows). Decision 5 replaces that. Hand case: times 2, 2, 4, 6 with a stop, a censoring, a completion and a stop, horizon 5: the weights are now 1, 0, 1.5, 1.5 (they were 1, 0, 1.333, 1.333).
- With option b a curve can be fitted on few rows at a late landmark (87 OTHER_GOV rows and 13 pooled rows at landmark index 6 of the 2016 origin). A row is always weighted by a curve fitted on rows that include it, so no weight can be undefined; the price is noise in a small share of the weights.
- Each bootstrap resample now fits several curves instead of one, which adds to the run time (M0 on the development origins: under 10 minutes for 1,000 resamples, in Claude's shell).
