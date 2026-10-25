# Model card: TrialPulse early-stop risk

Research demo. Not medical advice. Not for patient decision-making.

**Source: [ClinicalTrials.gov](https://clinicaltrials.gov).** Data as of 2026-09-25. TrialPulse normalizes the registry records and adds derived early-stop risk scores and drivers, which are not part of ClinicalTrials.gov (ADR 0008).

An elevated early-stop risk is an operational statement about a registry record. It says nothing about whether a treatment works. Every number on this card comes from [results.md](results.md) (the registered results of the locked test), [explanations.md](explanations.md) or the documents they cite; the data is described in [data_card.md](data_card.md).

## Model details

- **What it predicts:** for an open interventional trial on a prediction date, the probability that the trial stops early (terminated or withdrawn) within the next 12 and 24 months, with completion treated as a competing event (CLAUDE.md Section 6). The prediction dates of the evaluation are landmarks: registration and every 6 months after it, up to 3 years.
- **Model:** M4, the champion (ADR 0026). A discrete-time model: LightGBM, with a multiclass objective, predicts for each 6-month interval after the prediction date whether the trial continues, stops early or completes, and the probability of an early stop within a horizon follows from those interval hazards. It reads 54 features, all as of the prediction date: the trial's design, its amendment signals (date slips, enrollment changes, status, update activity), its sponsor's class and track record, the number of open interventional trials, and its time since registration ([features.md](features.md)). It reads no free text (ADR 0026).
- **Training:** one model per walk-forward origin, on the landmarks before the origin, with every outcome cut off at the origin (ADR 0016), and with the settings tuned on the development origins (`config/tuned/m4.json`, ADR 0025). At the 2018 and 2019 origins it trained on 567,423 and 653,603 landmark rows and grew 1,046 and 1,146 boosting rounds.
- **Explanations:** the five drivers of each prediction, from exact TreeSHAP values (ADR 0033, below).
- **Version:** the model and every result were fixed by the pre-registration, `docs/preregistration.md`, registered on 2026-10-21 (tag `prereg-v1`, commit `2ee8498`). The record of the locked results is commit `4991ea5`.
- **The served model** (Steps 13 to 16) will be trained the same way on all available data. This card describes the per-origin models evaluated in Step 11 and will be updated for it.

## Intended use

- Operational risk analytics for trial portfolios: clinical operations and portfolio teams at sponsors and contract research organizations who want to rank open trials by elevated early-stop risk, to decide which to review first.
- A research and portfolio demonstration of point-in-time, competing-risks prediction on registry metadata.

## Out-of-scope uses

- Medical advice, patient decisions, or choosing a trial to join.
- Judging whether a treatment works or is safe: the model reads registry metadata only and cannot anticipate a safety or efficacy finding.
- Judging a sponsor or an investigator: the sponsor's class and track record are features of a registry record, not an assessment of anyone.
- Any commercial use: the data is licensed for non-commercial use only.
- Trials outside the population: observational studies, and trials first posted before 2008.
- Reading a score as a certainty: a high score is an elevated early-stop risk, never a statement that a trial will stop.

## Evaluation

**Protocol.** Walk-forward evaluation (CLAUDE.md Section 6). The model, the hypotheses and every prediction with its acceptable range were registered before any test result existed, and the locked origins were evaluated once: 2018 and 2019 (the test) and 2020 (the COVID stress test, reported separately). The metrics weight each row by the inverse probability of censoring, estimated within the lead sponsor class (ADR 0017). Intervals are 95% cluster-bootstrap intervals over trials, 1,000 resamples.

**Headline** (pooled over the landmark indices):

| Metric | 2018 | 2019 | Registered prediction (range) |
| --- | --- | --- | --- |
| AUC, 12 months (primary) | 0.730 (0.719 to 0.741) | 0.723 (0.712 to 0.734) | 0.725 (0.700 to 0.750) |
| AUC, 24 months | 0.710 (0.702 to 0.718) | 0.696 (0.687 to 0.704) | 0.707 (0.689 to 0.725) |
| Brier score, 12 months | 0.0300 (0.0289 to 0.0313) | 0.0297 (0.0285 to 0.0309) | 0.031 (0.024 to 0.038) |
| Brier score, 24 months | 0.0567 (0.0551 to 0.0584) | 0.0583 (0.0566 to 0.0600) | 0.057 (0.046 to 0.069) |
| Lift at 10%, 12 months | 3.23 (3.06 to 3.45) | 3.06 (2.89 to 3.25) | 3.1 (2.6 to 3.7) |
| Lift at 10%, 24 months | 2.69 (2.55 to 2.81) | 2.56 (2.44 to 2.69) | 2.7 (2.3 to 3.1) |
| Calibration slope, 12 months | 0.855 | 0.803 | 0.89 (0.8 to 1.2) |
| Calibration slope, 24 months | 0.782 | 0.732 | 0.84 (0.8 to 1.2) |

Lift at 10% means that among the tenth of trials with the highest predicted risk, early stops within 12 months were about three times as common as overall. The Brier score mostly reflects how rare early stops are, not the model's skill (section 3 of the registration explains why).

**What held and what did not.** Both primary claims held: the AUC at 12 months on each test origin, and H2, the gain from the amendment signals growing with the time since registration (+0.0088 AUC per landmark index, +0.0060 to +0.0114). H5 held: at the COVID stress origin the AUC at 12 months was lower by 0.0334 (0.0198 to 0.0464). Three registered hypotheses were falsified, and they stay published:

- **H1, falsified.** At registration, M4 and the static classifier M1 rank trials about equally: the difference is -0.0027 (-0.0166 to +0.0114) at 12 months and -0.0013 (-0.0098 to +0.0075) at 24 months, where M4 was predicted to lead.
- **H3, falsified.** The model did not rank operational stops (accrual, business, funding, administrative, COVID-19) measurably better than scientific stops (safety, efficacy) over the two test origins: AUC 0.729 against 0.706, a difference of +0.0229 (-0.0025 to +0.0494). This card makes no claim that operational stops are more predictable.
- **H4, falsified.** The calibration slope at 24 months was below the registered range at both test origins (next section).

**The ladder** (AUC at 12 months, 2018 and 2019): M0, the sponsor class alone, 0.543 and 0.541; M2, the design, 0.663 and 0.653; M3, plus the amendment signals, 0.702 and 0.693; M4 0.730 and 0.723. M4 minus M3: +0.0290 (+0.0225 to +0.0351). The AUC at 12 months is lowest at registration (0.680 and 0.687) and between 0.698 and 0.743 at the later landmarks.

**Sensitivity analyses.** One censoring curve for all rows instead of one per sponsor class changes none of the AUCs above by more than 0.001. Counting the trials that lapsed into the registry's UNKNOWN status as early stops lowers the AUC at 12 months to 0.667 and 0.655 (ADR 0029): the model does not rank those trials as it ranks real early stops.

**The COVID stress test** (2020 origin): AUC 0.693 (0.683 to 0.704) at 12 months and 0.680 (0.672 to 0.688) at 24 months; calibration slopes 0.664 and 0.644.

## Calibration: a known limitation

The calibration slope at 24 months, 0.782 at 2018 and 0.732 at 2019, is below the pre-registered range of 0.8 to 1.2; at 12 months, 0.855 and 0.803, it is inside it. A slope below 1 means the predicted risks are spread too widely: the highest predictions are too high and the lowest too low. Use the scores to rank trials; read the absolute values, especially at 24 months, with that in mind ([results.md](results.md), the calibration figures).

**A trend whose cause is not established.** The champion's calibration slope fell at each later locked origin, at both horizons: at 12 months 0.855 (2018), 0.803 (2019) and 0.664 (2020); at 24 months 0.782, 0.732 and 0.644. The cause is not established: it could be drift in how trials are registered and updated, the COVID period, or something else. The monitoring of Step 17 must track the calibration slope of matured predictions over time.

**No recalibration is applied** (ADR 0032). A recalibration measured against a rule before the registration failed that rule (ADR 0027), so the registered model has none. Any recalibration from here on is a post-test product change: it is labeled as such, never mixed into the registered results, and judged by the live monitoring of Step 17.

## Explanations

Each prediction comes with its five drivers: the features whose SHAP values move the model's log-odds of an early stop against continuing the most, each marked as raising or lowering it (ADR 0033, [explanations.md](explanations.md)). The values are exact TreeSHAP values from LightGBM's own implementation, and they add up to the explained log-odds. That log-odds is not the served risk, which also depends on the chance of completing first, but the two rank the evaluation rows alike: Spearman's rank correlation 0.972 and 0.975 at 12 months and 0.930 and 0.934 at 24 months (2018 and 2019). The explanations are of the registered models: each model fitted again for them reproduces every score the locked run saved, with no difference.

Over the test origins, the features that move the 12-month log-odds most are the enrollment count (mean absolute SHAP value 0.192; higher values lower it), the sponsor's early-stop rate (0.186; higher values raise it), the planned duration (0.186; higher values lower it), the landmark index (0.170; later landmarks raise it) and the sponsor class (0.165). A driver says what moved this model's score, not why a trial stops.

Three example risk timelines, drawn from the test origins by a declared seeded rule (one trial that stopped early, one that completed, one still open), are in [explanations.md](explanations.md).

## Limitations

- **Registry metadata only.** The model sees what sponsors post on ClinicalTrials.gov. It cannot anticipate a safety or efficacy finding, a funding decision or a regulatory action that the record does not show, and it cannot see trials registered elsewhere.
- **Self-reported, US-centric data,** with estimated post dates before 2018 and records that go stale ([data_card.md](data_card.md), known biases).
- **Lapsed records.** Trials whose record lapsed into the registry's UNKNOWN status are censored, not counted as stopped (ADR 0014); if many of them in fact stopped, the scores understate early-stop risk (the sensitivity analysis above).
- **Drift.** The AUC was lower at the 2020 origin (H5) and the calibration slope fell over the locked origins. Registry rules and habits change, so the model needs the monitoring and retraining of Steps 14 and 17.
- **At registration** the model adds nothing measurable over a static classifier (H1); its gain comes later in a trial's life (H2).
- **Calibration** at 24 months is below the registered range (above).
- **The text of a record** (eligibility criteria, summary) is not used: on the development origins it lowered the AUC (ADR 0026), and on the test origins it made no measurable difference, +0.0001 (-0.0042 to +0.0045).
- **The example timelines** in [explanations.md](explanations.md) are three trials drawn by a declared rule, illustrations rather than results.

## Ethics

- **No patient data** exists in TrialPulse. Names and contact details of investigators and study contacts are never stored, logged or displayed, and a sponsor that is an individual has no stored name (ADRs 0012 and 0022). Documents show sponsors by class only.
- **Wording.** A public score about a named trial could be read as a verdict on the trial, its sponsor or its treatment. Every page therefore says "elevated early-stop risk", never "will fail", and carries the research-demo notice.
- **Sponsor features.** The model uses the sponsor's class and its own record of early stops (smoothed toward its class, ADR 0018), so trials of sponsors with many past early stops score higher. That is a statistical pattern in registry records, not a judgment of any sponsor, and it is listed among each prediction's drivers when it matters.
- **Not for patients.** The scores are operational analytics for portfolio teams, not guidance for anyone deciding whether to join a trial.
- **Non-commercial.** No monetization, paid feature or commercial use.

## License and citation

- Code: MIT ([LICENSE](../LICENSE)).
- Data: ClinicalTrials.gov records under the ClinicalTrials.gov terms and conditions, through the version-history dataset `brbk/clinical_trials_history` (CC BY-NC 4.0, non-commercial). Cite the dataset as [data_card.md](data_card.md) shows.
