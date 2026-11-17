# Resume bullets

Bullets chosen by Aakrisht on 2026-11-17 for a data science or machine learning engineering resume. Each number is rounded on purpose from the source line cited in the comment under its bullet (line numbers as of 2026-11-17). The positioning follows CLAUDE.md Section 2: operational early-stop risk of trial portfolios, never treatment efficacy.

**Once the dashboard is deployed,** the third bullet gains "and a Streamlit dashboard" (served by a FastAPI service and a Streamlit dashboard).

- Built TrialPulse, an end-to-end risk engine that estimates each open clinical trial's probability of stopping early within 12 and 24 months, rebuilding trials point in time from 4.4M registry versions of 604K trials to prevent data leakage.
  <!-- 4.4M from 4,444,542 versions and 604K from 604,583 trials: docs/progress.md line 445 (Step 3, the warehouse). 604,583 rounds to 605K; 604K is the number cut to its thousands. -->

- Modeled early stopping as a competing risk with a discrete-time LightGBM model and IPCW evaluation. On a pre-registered, locked test (hypotheses git-tagged before scoring, falsified ones published), it reached a 12-month AUC of 0.72 to 0.73 and a 3.1 to 3.2x lift in the top 10% of risk.
  <!-- AUC at 12 months 0.730 (2018) and 0.723 (2019), lift at 10% 3.23 (2018) and 3.06 (2019): docs/results.md lines 68 and 69 (M4). -->

- Shipped it as a live system: a daily GitHub Actions pipeline ingests ClinicalTrials.gov API updates into Postgres and rescores about 89,000 trials, served by a FastAPI service, with layered parity tests holding live scores to the offline pipeline.
  <!-- about 89,000 from 88,667 rescored on the first full run: docs/progress.md line 2691 (go-live, run 3). -->

- Added production monitoring: monthly grading of matured predictions, a 78-week leak-free replay (12-month AUC 0.75), PSI drift checks validated on injected shifts, and a promotion gate that never grades a challenger on its own training data.
  <!-- 78 weekly dates: docs/progress.md line 2527 (every Friday from 2023-01-06 to 2024-06-28); no label or feature after a prediction date: line 2569. 0.75 from the 12-month AUC mean 0.7506: docs/progress.md line 2528. Monthly grading: .github/workflows/grade.yml line 9. -->

- Optional: Labeled 10,000 stop-reason texts with an LLM (macro-F1 0.84 against adjudicated reference labels) and distilled them into a TF-IDF and logistic regression classifier (macro-F1 0.77) used in production.
  <!-- 10,000 texts: docs/reason_labeling.md line 66. 0.84 from the LLM's macro-F1 0.842: docs/reason_labeling.md line 9. 0.77 from the distilled classifier's 0.769, the only reason model used in production: docs/reason_labeling.md line 11. -->
