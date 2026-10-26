# 0033. Explanations of the champion and the example timelines

- Date: 2026-10-25
- Status: **Proposed** (Step 12), for Aakrisht's review.
- Refines CLAUDE.md Section 13 ("SHAP for M4. The top 5 drivers, with direction, are stored with every served prediction") and Step 12.

## Context

M4 is a multiclass LightGBM model of each 6-month interval after a landmark (continue, stop early, complete). The number it serves is the cumulative incidence of an early stop at 12 or 24 months, computed from the hazards of two or four intervals with completion as a competing event. TreeSHAP splits each class's raw score in each interval exactly into one value per feature, but no additive split of the trees reaches the cumulative incidence: it is a nonlinear function of three class scores in several intervals. Step 13 has to store five drivers with every served prediction, for every open trial, each week, so the method has to be cheap as well as exact.

## Decision

1. **What is explained.** The log-odds of an early stop against continuing, f_stop - f_continue, averaged over the intervals of the horizon. Its SHAP values are the stop class's values less the continue class's, averaged over the intervals; they add up exactly to the target. They come from LightGBM's own `pred_contrib`, the TreeSHAP algorithm that the shap package's TreeExplainer runs on these trees, so the shap package is not added. The interval column is set by the horizon and kept apart; it is never listed as a driver. The explanations document states how closely the target and the served risk rank the same rows (Spearman's rank correlation).
2. **The models.** The champion of each test origin, fitted again exactly as the locked run fitted it, and used only if it reproduces the scores the locked run saved for every evaluation row of its origin to 1e-12 (a score that is not a number is refused). It is cached in `data/explain/` and checked again on every run, and each timeline point is explained only by the model of its own origin, which must give that point its saved scores.
3. **Importance.** The mean absolute SHAP value of each feature over a seeded sample of 20,000 evaluation rows of each test origin (the project's seed), at 12 and 24 months. For a number feature, a direction, from the 12-month explanation of the first test origin's sample (2018): the sign of Spearman's correlation between its value and its SHAP value when it is at least 0.1 in size, otherwise "mixed". The agreement with the served risk is measured on the same samples.
4. **The example timelines.** Eligible trials have evaluation rows at both test origins and an outcome that is an early stop, a completion, or still open at the data cutoff (a trial censored by the lapse rule is not eligible). Within each kind the registry ids are sorted and one is chosen with numpy's generator seeded by the project's seed, in the order stopped, completed, open. A timeline shows the scores the locked run saved at the trial's test landmarks, the five drivers of the 12-month log-odds at each (from the model of the point's origin), the registry updates that changed the status, start, primary completion or enrollment, and the outcome. The first-post date comes from the record itself (a landmark clamps the day to the month's length, so it cannot be worked back from one). The sponsor appears by class only; no name or contact is read.
5. **One command** (`uv run python -m trialpulse.explain.report --unlock-test`) checks the test lock first, then writes `docs/explanations.md` and its figures. It logs nothing to MLflow: it evaluates no metric, and the models are the locked run's.

## Alternatives

- **Model-agnostic SHAP on the cumulative incidence itself** (KernelSHAP or the permutation explainer, with a background sample). Its values are in risk units and add up to the served number, but each row costs thousands of model evaluations (at least two per feature for each sampled ordering of the 54 features, each over a background sample), where TreeSHAP needs one pass per interval of the horizon (two for 12 months, four for 24). Measured on the 2018 champion (3,138 trees) on this laptop, one TreeSHAP pass takes about 6.5 ms per row, 223 times a plain prediction; thousands of predictions per row would take longer still and would be too slow for Step 13 to store drivers for every open trial each week (an estimate from the number of evaluations, not measured). The values also depend on the background sample.
- **TreeSHAP on the stop class's raw score alone.** A softmax class score means nothing without the others; the difference from the continue class is the log-odds the scores actually define.
- **A linearization of the cumulative incidence around each prediction.** Approximate, and its values no longer add up.
- **The shap package.** The same numbers on these trees, with a new dependency (numba and llvmlite) and nothing gained.

## Consequences

- Step 13 stores the five drivers of each served prediction from `trialpulse.explain.drivers.top_drivers`, on the same target, and the dashboard describes a driver as raising or lowering the model's odds of an early stop against continuing. At 6.5 ms per row and pass, the four interval passes of the 91,194 trials open at the data cutoff take about 40 minutes on this laptop if the 12-month drivers reuse the first two passes; Step 13 should compute drivers only for the trials it rescores and measure the cost on the runner before the weekly job is enabled.
- A feature that raises both the stop and the completion hazards can raise the target while it lowers the served risk. The rank agreement in `docs/explanations.md` says how much the two differ over the seeded sample of evaluation rows explained per origin.
- The timelines are illustrations, not results: three trials, drawn by a declared rule.
