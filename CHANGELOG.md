# Changelog

One entry per roadmap step (CLAUDE.md, Section 18), newest first. Dates are the dates a step was approved or, for the live system, went live. The evidence for each entry is in [docs/progress.md](docs/progress.md), and every decision has an ADR in [docs/adr/](docs/adr/).

## Stretch item 2: the per-trial risk memo (built 2026-11-17, memo_v4 2026-11-19)

- A memo for the top 100 trials by 12-month risk and the trials whose risk moved, built after each daily run by `memo.yml` from a payload of stored values only, and stored only if a deterministic fabrication check passes. It stays off the API (`api.memo_public` false) until the owner's review (ADR 0084).
- memo_v1 to memo_v3: a language model on Groq wrote the memo, then only one sentence per driver, under a check that ended up allowing only the text a template would write (three reviews and audits, in docs/progress.md).
- memo_v4: the language model is retired; a template builds every paragraph, the check tests the template at every generation, and no workflow reads `GROQ_API_KEY` (ADR 0085). This deviates from the roadmap's "written by an LLM", and the ADR says why.
- The Trial detail timeline names a trial's first posting "First posted" instead of a status change (ADR 0043, amended 2026-11-18; migration 0009).
- memo_v5: the daily job stores the inputs of each served sponsor rate (migration 0010), so the memo says exactly how the rate was made; one name per marker group ("recruitment status", "dates or status verification") in the memo and the API; review files are never overwritten (ADR 0085, amended 2026-11-19).
- memo_v6: the rate sentence says what its ended trials count, every sponsor class reads "the class rate for <its sponsors>", the change list names code 32 "interventions or phase named in the title" in the memo and the API, and a change without an earlier version says there is nothing to compare it with (ADR 0085, amended 2026-11-21).
- The memos are public since 2026-11-21: the light audit of the memo_v6 memos confirmed 0 high and 0 medium findings; the dashboard check covers the memo section, and the README shows it.

## Step 18: Documentation and polish (approved 2026-11-17, in progress)

- README brought up to date with the live system, Step 17's monitoring and the current limitations; this changelog.
- The dashboard's public link in the README (https://trialpulse-demo.streamlit.app).
- The README opens with an at-a-glance summary (badges, positioning, headline results, live links, what makes this different), and docs/related_work.md compares TrialPulse's setup with four published studies of trial termination (2026-11-22).
- The drift alert also fires when one of the 5 most important features drifts (ADR 0081, amended 2026-11-17).
- The 7 packages pip-audit flagged upgraded (streamlit to 1.67.0 with pillow 13); no known vulnerability left.

## Step 17: Monitoring, retraining and replay (approved 2026-11-10, verified 2026-11-17)

- Grading of served predictions as aggregates, with IPCW AUC, Brier score, calibration and lift per prediction date, matured or partial-horizon; a monthly GitHub workflow, `grade.yml` (ADRs 0077 and 0079).
- A replay of every Friday from 2023-01-06 to 2024-06-28, scored by M4 fitted as of 2023-01-01 and graded against matured outcomes; monitoring evidence, never a registered result (ADR 0078).
- A drift step at the end of each weekly full run: Evidently's PSI on 52 features and both risks against the trials served on 2026-11-03, long-tailed columns compared by their position in that reference (ADR 0076).
- The quarterly retrain on the laptop and a promotion gate that waits for matured outcomes after the champion's training data; its comparison uses a challenger blind to the window it is judged on (ADR 0080).
- Alerts as GitHub issues, one per kind (ADR 0081); three read-only monitoring endpoints and the Model health page's new sections.
- A guard that fails any test making a real network call; error output with addresses masked; a backup task that retries daily and opens an issue on failure.

## Step 16: Dashboard (approved 2026-11-01, live 2026-11-17)

- Deployed on Streamlit Community Cloud on 2026-11-17 at https://trialpulse-demo.streamlit.app; every page checked against the deployed API by `scripts/check_dashboard.py` (7 of 7 pages passed).

- Five Streamlit pages that read only the API: Portfolio, Trial detail, Landscape, Why trials stop and Model health (ADRs 0052 to 0057).
- One client that waits out a sleeping API and says why, accepts only the API's own answers, and caches them for 15 minutes (ADRs 0054 and 0063).
- Tests of every page against stubs recorded from the real API (ADR 0056).

## Step 15: API service (approved 2026-11-01, live 2026-11-08)

- A read-only FastAPI service over the serving tables, with Pydantic response models and the notice on every answer (ADRs 0046 to 0051).
- Deployed on Render's free web service, with a per-client rate limit keyed by the client address Cloudflare reports (ADR 0065).
- A connection that tells a waking database from a real error, and a database role that reads `serving` only (ADRs 0049, 0061 and 0064).

## Step 14: Live ingestion and daily workflow (decided 2026-11-01, live 2026-11-08)

- The daily job on GitHub Actions: pulls changed records from ClinicalTrials.gov API v2, keeps compact state in Neon, computes the same features through the same SQL as the offline path and rescores the affected trials (ADRs 0037 and 0045).
- Serving parity in five layers, from synthetic histories to the live rows scored again offline (ADRs 0038 and 0042).
- The gap between the dataset's cutoff and go-live read once from a later release of the dataset, and the live state rebootstrapped from it (ADRs 0041, 0073 to 0075).
- Run control: a renewable lease, a keep-alive, short transactions, retention and backups (ADRs 0058, 0060, 0068 to 0072).

## Step 13: Scoring pipeline and model registry (approved 2026-10-31)

- `score(as_of_date)` for every open trial on any date, between landmarks and beyond landmark 6 with a flag for trials outside the validated range (ADR 0034).
- The served champion, M4 fitted on all data, registered on DagsHub with the alias `champion`; exact TreeSHAP drivers stored with each score (ADRs 0033 and 0035).

## Step 12: Explainability, model card, data card (approved 2026-10-26)

- Global importance and per-prediction drivers for M4, three example risk timelines, the model card and the data card (ADR 0033).

## Step 11: Pre-registration, locked test and results (approved 2026-10-25)

- Hypotheses with numeric predictions committed and tagged (`prereg-v1`) before the locked origins were evaluated (ADRs 0004 and 0009).
- Results: both primary claims held (AUC at 12 months 0.730 and 0.723); H1, H3 and H4 falsified and published (ADRs 0028 to 0032).

## Step 10: Discrete-time models and tuning (approved 2026-10-19)

- M2, M3 and M4 as discrete-time competing-risk models, Optuna tuning on the development origins, an ablation by feature family; the champion is M4 without the text families (ADRs 0024 to 0026).

## Step 9: Baselines and Cox analysis (approved 2026-10-10)

- M0 (Aalen-Johansen by sponsor class) and M1 (a static IPCW classifier at registration); a cause-specific Cox model for interpretation; runs logged to MLflow on DagsHub (ADR 0023).

## Step 8: Evaluation harness and test lock (approved 2026-09-23)

- IPCW AUC and Brier score, calibration, lift, cluster bootstrap, the walk-forward runner and the test lock, built and tested on synthetic data before any model was evaluated (ADRs 0016 and 0017).

## Step 7: Point-in-time features (approved 2026-10-10)

- The feature registry and families (design, amendment signals, sponsor, competition, text), leakage tests, and docs/features.md generated from the registry (ADRs 0018 to 0022).

## Step 6: Why trials stop (finished 2026-10-21)

- A taxonomy and labeling guide, reference labels from an adjudicated model panel, LLM labels for a stratified sample and a distilled TF-IDF classifier for every early stop (ADR 0010).

## Step 5: Exploratory data analysis (approved 2026-10-07)

- Early-stop cumulative incidence by phase, sponsor class and year, amendment behavior, registration lag and the COVID-era shift, regenerated by one command.

## Step 4: Cohort, outcomes and landmarks (approved 2026-10-07)

- The population, events with reversals excluded, the registry's UNKNOWN replaced by its own rule, landmark rows and person-period rows, and training labels as of each origin (ADRs 0013 to 0016).

## Step 3: Warehouse, contracts and live schemas (approved 2026-09-30)

- The DuckDB warehouse, one canonical version schema for the dataset and API v2, Alembic migrations for the live, serving and monitoring schemas (ADR 0012).

## Step 2: Feasibility spike (GO on 2026-09-30)

- The version-history dataset profiled, checked against API v2 and the registry's own history; every automated criterion met (ADRs 0006, 0007 and 0011).

## Step 1: Repo skeleton, tooling, CI (approved 2026-09-23)

- The uv project, configuration loading, pre-commit, CI, local Postgres in Docker, and the first ADRs (ADRs 0001 to 0005).
