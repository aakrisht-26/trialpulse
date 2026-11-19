# Interview guide: TrialPulse

For placement interviews. Plain English, every claim backed by the repository's documents; every number on the numbers sheet at the end is copied from the file and line it cites (line numbers as of 2026-11-17). Positioning (CLAUDE.md Section 2): operational early-stop risk of trial portfolios, never whether a treatment works; research demo, not medical advice.

## The 2-minute pitch

Clinical operations teams at sponsors and contract research organizations run hundreds of trials, and some of them stop early: they fail to recruit, lose funding, or the sponsor changes course. TrialPulse estimates, for every open interventional trial on ClinicalTrials.gov, the probability that it stops early within 12 and 24 months, and names the five features that moved each score, so a portfolio team knows which trials to look at first.

The hard part is not the classifier. A registry record rewrites itself after a trial stops, so a naive model learns from the future. I rebuilt every trial point in time from its full version history, about 4.4 million versions of about 600,000 trials, so that every feature at a prediction date reads only what was public then. Most trials have not ended yet, and a trial can end by completing or by stopping, so I treated it as a competing-risks survival problem with a discrete-time model and evaluated it with censoring weights.

I pre-registered numeric hypotheses and locked the test years behind a git tag before any model scored them. On the locked 2018 and 2019 test origins the champion reached a 12-month AUC of 0.730 and 0.723 with a lift at 10% of about 3. Three hypotheses were falsified, and I published them.

Then I made it live: a daily GitHub Actions job pulls changed records from the official API, recomputes features with the same SQL as the offline pipeline, rescores about 89,000 open trials into Postgres, and a read-only FastAPI service and a Streamlit dashboard read only those tables. Monitoring grades predictions as outcomes mature, watches drift, and a promotion gate refuses to promote a retrained model until a fair test window exists.

## The 10-minute technical walkthrough

1. **The problem and its users (1 minute).** Operational risk analytics: which open trials have elevated early-stop risk. Registry metadata only, no patient data. Scientific stops (safety, efficacy) are outcomes metadata cannot anticipate, so claims are about operational risk, and the hypothesis that operational stops are easier to rank was tested and falsified.

2. **Point-in-time reconstruction (2 minutes).** Each version of a record becomes known at its last-update post date. The state of a trial at time t is its latest version posted by t. Features read only versions posted on or before the prediction date, including other trials' versions (the competition count, the sponsor's track record). A leakage test alters every later version of sampled trials and checks that no feature moves. Sponsor track records use only outcomes before the landmark, smoothed toward the class rate.

3. **Landmarks and competing risks (2 minutes).** Predictions are made at registration and every 6 months after it (landmarks 0 to 6). A discrete-time model predicts, per 6-month interval, continue, complete or stop; the cumulative incidence of an early stop is built from those interval hazards. A trial still open at the data cutoff is censored, not counted as a success, and completion removes the chance of an early stop. Evaluation uses IPCW: inverse-probability-of-censoring weights from a Kaplan-Meier estimate of the censoring distribution, by sponsor class.

4. **The locked test (2 minutes).** Walk-forward origins: development on 2016 and 2017, locked test on 2018 and 2019, a COVID stress test on 2020. The hypotheses, with predicted values and acceptable ranges, were committed and tagged `prereg-v1` before evaluation; the harness refuses the locked origins without the published tag. Both primary claims held; H1 (M4 against a static classifier at registration), H3 (operational against scientific stops) and H4 (calibration slopes in 0.8 to 1.2) were falsified and published. The 24-month calibration is a known limitation, and no recalibration was adopted after the test.

5. **The live loop and parity (2 minutes).** The daily job keeps compact state in Neon, turns API records into the same canonical rows, runs one shared SQL select to produce the 54 features on both paths, and rescores the trials that changed. Parity is tested in layers: synthetic histories in CI, a replay on the laptop, the cutoff reproduced, a census of unchanged trials, and the live rows themselves scored again offline (equal within 1e-12; the only differences were 15 risks at the last bit, from Windows against Linux floating point).

6. **Monitoring and the honest gate (1 minute).** A monthly workflow grades served predictions (matured or partial-horizon); a replay of 2023 and 2024, scored by a model fitted as of 2023-01-01, gives monitoring history; a weekly drift step compares features and risks with the trials served on 2026-11-03, and was checked on deliberate shifts before being relied on; alerts open GitHub issues. The quarterly retrain trains a challenger but promotes nothing until 12-month outcomes have matured after the champion's training data, about November 2027, and the comparison then uses a challenger that has not seen the window it is judged on.

## The 26 most likely questions

1. **Why is an AUC around 0.72 useful?** Early stops are rare (a few percent within 12 months), so ranking is what a portfolio team needs: the top tenth of predicted risk held about three times the average early-stop rate (lift at 10% of 3.23 and 3.06 on the locked origins). It is a triage tool for operations, not a decision rule, and the documents say so.

2. **Why not a random survival forest?** The library forests do not handle competing risks; a comparison that ignores completion as a competing event would be biased, so they were excluded rather than compared unfairly (CLAUDE.md Section 9).

3. **What leaks, and how did you prevent it?** The current record: after a stop, status, dates and enrollment are rewritten. Prevention: every feature from versions posted by the landmark only, outcomes from the version history, training labels as of each walk-forward origin, transforms fitted on training rows only, a future-perturbation leakage test, and a test lock.

4. **Why discrete time instead of a Cox model?** It handles two event types directly, works with a gradient-boosted learner, and its cumulative incidence matches Aalen-Johansen without covariates (a required unit test). Cox was fitted for interpretation only.

5. **What is IPCW and why do you need it?** Trials censored before the horizon have unknown outcomes; dropping them biases the metric. Each row with a known status at the horizon is weighted by the inverse probability of remaining uncensored until then.

6. **What was falsified?** H1: at registration M4 did not beat the static classifier (-0.0027 at 12 months). H3: operational stops were not ranked better than scientific ones. H4: calibration slopes did not all land in 0.8 to 1.2. All three are published as falsified.

7. **What would you do differently?** Fix more of the evaluation details before building models (several definitions needed ADRs mid-project), plan the live system's storage and parity from the start, and wait for CI before every push (four pushes went red during Step 17).

8. **How do you know the live system computes the same features as training?** One shared SQL select produces the features on both paths, and five parity layers test it, the last on the live rows themselves.

9. **What does the calibration problem mean for users?** At 24 months predicted risks are spread too widely, so the scores rank well but overstate the extremes. The dashboard presents risk with that limitation stated; recalibration would be a post-test product change judged by live monitoring.

10. **Why pre-register an ML project?** To make the test honest: predictions with ranges fixed before seeing the test years, so a result cannot be tuned after the fact, and failures are reported.

11. **How is drift monitored, and could it miss something?** Population stability index per feature against a reference; a sensitivity check found that Evidently bins long-tailed columns by width and missed shifts, so those columns are compared by their position in the reference distribution. It still does not see a uniform 20% rise in risks, which is a calibration shift that grading watches.

12. **Why does the promotion gate wait a year?** A fair comparison needs matured 12-month outcomes of predictions made after the champion's training data; before that, any window favours the model trained on it.

13. **Why is the replay's AUC (about 0.75) above the test's?** It grades every open trial at its true landmark index, older trials included, with features on the scoring date, in 2023 and 2024; at landmark 0 alone it is close to the registered numbers. It is monitoring evidence, not a registered result.

14. **How did you handle trials that reverse a terminal status?** Excluded from training and evaluation and counted in the data audit, as the locked definitions say.

15. **What about trials marked UNKNOWN?** The registry's own lapse rule is applied point in time; a lapsed trial is censored at its last verification, and a sensitivity analysis treats such lapses as early stops.

16. **How did you deal with personal data?** Investigator and contact modules are dropped at ingestion; individual sponsors get no stored name; sponsor identity on the live side is a keyed hash.

17. **Why DuckDB and Parquet?** One laptop, no GPU: columnar files and an embedded engine handle millions of versions in minutes and keep every intermediate reproducible.

18. **How is the dataset license respected?** It is CC-BY-NC-4.0 and gated: it stays on the laptop, only aggregates, models and named summaries leave, and the project is non-commercial.

19. **How do you explain a single score?** Exact TreeSHAP values from LightGBM's own contributions; the top five drivers are stored with each score, with a note that a driver says what moved the model, not why a trial stops.

20. **What is the most important feature?** Amendment signals matter more as a trial ages (the gain from them grows with the landmark index, H2); the drivers vary by trial.

21. **How did you choose the champion?** A rule declared in advance: best mean development AUC at 12 months, ties broken by Brier score, then calibration slope.

22. **What breaks in production, and how would you know?** A failed daily run opens a GitHub issue; alerts cover data lag, quarantine, drift and matured AUC; backups are verified by restore; a second daily cron covers GitHub's late starts.

23. **How do you test a system that depends on a live API?** Recorded responses only; a guard fails any test that opens a real network connection.

24. **What is the biggest limitation?** Registry metadata cannot anticipate scientific stops, the data are self-reported and US-centric, and live outcomes take a year to mature.

25. **What did you learn?** That most of the work in a credible model is deciding what is known when, and that publishing what failed makes the rest believable.

26. **You built an LLM memo and then removed the LLM. Why?** The roadmap asked for a per-trial memo written by an LLM, with a check that every number and date came from the stored values. The first version passed that check on every memo and still failed review: four of 20 memos related true facts wrongly, such as a first posting called a status change. A number check cannot catch that. Each fix moved a statement from the model to a template, and each audit (40 findings, then 20, with no high ones left) narrowed the model further, until the check allowed exactly one sentence per driver: the sentence a template writes. The model then added nothing but a secret, a daily token limit and an outside dependency, so I retired it, recorded the deviation from the roadmap in a decision record, and kept the check as the template's test. The lesson: an LLM earns its place when the output needs judgment the data does not hold, and this memo needs exact statements about data the system already has.

## Numbers sheet

| Number | What | Source |
| --- | --- | --- |
| 0.730 (0.719 to 0.741) | M4, AUC at 12 months, 2018 origin | docs/results.md line 68 |
| 0.723 (0.712 to 0.734) | M4, AUC at 12 months, 2019 origin | docs/results.md line 69 |
| 0.710 (0.702 to 0.718); 0.696 (0.687 to 0.704) | M4, AUC at 24 months, 2018 and 2019 | docs/results.md lines 68 and 69 |
| 0.0300 (0.0289 to 0.0313); 0.0297 (0.0285 to 0.0309) | Brier score at 12 months, 2018 and 2019 | docs/results.md lines 68 and 69 |
| 3.23 (3.06 to 3.45); 3.06 (2.89 to 3.25) | Lift at 10%, 12 months, 2018 and 2019 | docs/results.md lines 68 and 69 |
| 0.855 and 0.782; 0.803 and 0.732 | Calibration slope, 12 and 24 months, 2018 and 2019 | docs/results.md lines 68 and 69 |
| -0.0027 (-0.0166 to +0.0114) | H1: M4 minus M1, AUC at landmark 0, 12 months (falsified) | docs/results.md line 20 |
| +0.0088 (+0.0060 to +0.0114) | H2: slope of M3 minus M2 per landmark index | docs/results.md line 22 |
| +0.0229 (-0.0025 to +0.0494) | H3: operational minus scientific AUC (falsified) | docs/results.md line 23 |
| -0.0334 (-0.0464 to -0.0198) | H5: AUC at 2020 minus the mean of 2018 and 2019 | docs/results.md line 24 |
| 4,444,542 versions of 604,583 trials | The warehouse | docs/progress.md line 445 |
| 330,121 trials; 1,448,969 landmark rows | The cohort | docs/progress.md line 545 |
| 88,667 rescored | The first full live run, 2026-11-08 | docs/progress.md line 2691 |
| 1.4e-17 | Largest live risk difference against the offline recomputation | docs/progress.md line 2659 |
| 0.7506 (0.7356 to 0.7583 over the dates) | Replay, 12-month AUC mean over 78 dates (monitoring) | docs/progress.md line 2528 |
| 22 of 22; 0 of 52 | Drift check: shifted numeric features flagged; unchanged features flagged | docs/progress.md line 2586 |
| 263.2 MB | Neon's size on 2026-11-17 (line at 700 MB) | docs/progress.md line 2582 |
| 0.842; 0.769 | Reason labeling: LLM and distilled classifier macro-F1 on the reference test labels | docs/progress.md line 87 |
