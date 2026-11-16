# Resume bullets (draft for review)

Draft of 2026-11-17 for a data science or machine learning engineering resume, to be reviewed in the planning chat. Every number is copied from docs/results.md or docs/progress.md; the comment under each bullet names the source line (line numbers as of commit `2bd45d2`). The positioning follows CLAUDE.md Section 2: operational early-stop risk of trial portfolios, never treatment efficacy.

- Built TrialPulse, an operational risk engine that estimates the probability that an open clinical trial on ClinicalTrials.gov stops early within 12 and 24 months, by rebuilding each trial point in time from 4,444,542 registry versions of 604,583 trials so that no feature reads information published after the prediction date.
  <!-- 4,444,542 and 604,583: docs/progress.md line 445 (Step 3, the warehouse). -->

- Modeled time to early stop as a competing risk against completion with a discrete-time LightGBM model over 6-month intervals and evaluated it with inverse-probability-of-censoring weights; on a pre-registered, locked test it reached a 12-month AUC of 0.730 (0.719 to 0.741) and 0.723 (0.712 to 0.734) with a lift at 10% of 3.23 and 3.06 on the 2018 and 2019 origins.
  <!-- docs/results.md lines 68 and 69 (M4, 2018 and 2019: AUC at 12 months with 95% cluster-bootstrap intervals, lift at 10%). -->

- Committed numeric hypotheses and a git-tagged test lock before any model scored the test years, then published the falsified ones, among them the expected gain over a static classifier at registration (M4 minus M1, AUC at 12 months: -0.0027 (-0.0166 to +0.0114)).
  <!-- docs/results.md line 20 (h1_delta_auc_12); line 50 lists every falsified prediction. -->

- Took the model to a live system: a daily GitHub Actions job pulls changed records from the ClinicalTrials.gov API v2, recomputes features with the same SQL as the offline pipeline and rescored 88,667 trials on its first full run into Postgres, served by a read-only FastAPI service; the live risks matched an offline recomputation to within 1.4e-17.
  <!-- 88,667: docs/progress.md line 2691 (go-live, run 3). 1.4e-17: docs/progress.md line 2659 (parity layer 5a, risk_24m). -->

- Added monitoring with matured-outcome grading, a replay of 78 weekly prediction dates scored by a model fitted as of 2023-01-01 (12-month AUC mean 0.7506, as monitoring evidence), a drift check validated on deliberate shifts (22 of 22 shifted numeric features flagged, 0 of 52 on unchanged data), and a promotion gate that waits for matured outcomes instead of grading a challenger on data it was trained on.
  <!-- 0.7506: docs/progress.md line 2528 (the replay's grades). 22 of 22 and 0 of 52: docs/progress.md line 2586 (drift, option C). -->
