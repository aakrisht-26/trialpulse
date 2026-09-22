# CLAUDE.md: TrialPulse, a Dynamic Clinical Trial Risk Engine

## 1. What this project is

TrialPulse estimates, for every open interventional trial registered on ClinicalTrials.gov, the probability that it stops early (terminated or withdrawn) within the next 12 and 24 months, explains the main drivers, and updates the estimate every day as sponsors amend their records.

Three things make it more than a classifier:

1. **Point-in-time correctness.** The current version of a registry record leaks the outcome: after a trial stops, its status, dates and enrollment get rewritten. TrialPulse rebuilds every trial exactly as it looked on the prediction date, using the trial's full version history.
2. **Survival framing.** Most open trials have not ended yet (censoring), and a trial can end two ways (completion competes with early stop). TrialPulse uses competing-risks methods instead of pretending unfinished trials succeeded.
3. **A live loop.** A daily job pulls updated records from the official ClinicalTrials.gov API v2, rescores trials, grades old predictions as real outcomes arrive, monitors drift, and retrains behind a promotion gate.

This is Aakrisht's flagship portfolio project. Every choice should reflect industry practice, not notebook shortcuts. The build is also a learning exercise: briefly explain the "why" behind non-obvious choices as you go.

## 2. Positioning rules (non-negotiable)

- This is operational risk analytics for trial portfolios. The users are clinical operations and portfolio teams at sponsors and CROs.
- Nothing may be framed as predicting whether a treatment works, as medical advice, or as guidance for patients choosing trials. The project uses registry metadata only; no individual patient data exists anywhere in it.
- UI and docs say "elevated early-stop risk", never "will fail". Every public page shows: "Research demo. Not medical advice. Not for patient decision-making."
- Headline claims target what is validated. Safety and efficacy stops are scientific outcomes that registry metadata cannot anticipate; the project says so plainly and focuses its claims on operational stops.
- Non-commercial, always. The version-history dataset is CC-BY-NC-4.0. No monetization, paid features or commercial use.
- Privacy: API v2 records include names and contact details of investigators and study contacts. Never store, log or display them. Drop those modules at ingestion.

## 3. Working agreements

- Work ONE roadmap step at a time. Never start the next step without explicit approval from Aakrisht.
- At the start of every session: read this file, then docs/progress.md, and state which step you are on before doing anything else.
- After completing a step: summarize what was built, list every file touched, give the exact verify commands, update docs/progress.md, then STOP and wait for review.
- When a decision has meaningful tradeoffs, present 2 to 3 options with a recommendation and ask. Do not silently choose. Record every accepted decision as an ADR in docs/adr/ (NNNN-short-title.md with context, decision, alternatives, consequences).
- Never weaken a test, threshold, metric definition or acceptance criterion to make something pass. If blocked, stop and report.
- The locked definitions in Section 6 change only through an ADR that Aakrisht approves.
- Small conventional commits (feat:, fix:, test:, docs:, chore:, refactor:) per logical change, and at minimum one commit at the end of every step. The commit history is part of the portfolio, so messages stay professional and specific.
- Never commit secrets or data files. data/ is gitignored. The gated dataset must never be pushed anywhere.
- Adding a dependency outside the locked stack (Section 15) requires a one-line justification and approval.
- Interview notes: at the end of every step, append to docs/interview_notes.md: what was built, the key decision and why, the rejected alternative and why not, and one likely interview question with a short answer.

Environment and verification:

- Aakrisht works on Windows with PowerShell, Docker Desktop and uv. Every verify command must run in PowerShell, one command per line, using `uv run` for Python. No bash-only syntax.
- Your shell may not behave like his machine (this caused a real bug in the previous project). Never claim a verify step passed on his machine; he runs the verify commands and reports back.
- Assume no GPU and a mid-range laptop. Prefer DuckDB and Parquet, process in chunks, and cache expensive intermediates. If any job would take more than about 1 hour, stop and propose a cheaper plan first. Tuning may subsample trials (grouped by trial) to respect this; final fits use all rows.

Writing style (README, docs, comments, docstrings, commit messages, UI text):

- No em dashes anywhere. Use commas, colons, parentheses or separate sentences.
- Plain, precise English. No hype words.

Definition of done for every step: ruff, ruff format, mypy and pytest all clean; new logic has tests; docs/progress.md and docs/interview_notes.md updated; work committed.

## 4. How Aakrisht reviews each step

Three checks on every step report:

1. Were the acceptance criteria actually met, with evidence?
2. Were any decisions made unilaterally, and are they flagged with reasons?
3. Is anything blocked, skipped or quietly deferred?

Write every step report so these three answers are easy to find.

## 5. Domain primer

Trial vocabulary:

- **ClinicalTrials.gov:** the registry run by the US National Library of Medicine. Sponsors self-report and update records; every update creates a new version, and all versions are kept.
- **Interventional study:** participants are assigned to interventions (drug, biologic, device, behavior). Observational studies are out of scope.
- **Phases:** Early Phase 1 (tiny exploratory doses), Phase 1 (safety and dosing, often healthy volunteers), Phase 2 (first efficacy signal plus safety), Phase 3 (large confirmatory, often multi-country), Phase 4 (after approval), N/A (trials without phases, such as many device or behavioral trials). Combined labels like Phase 1/Phase 2 exist.
- **Arms and allocation:** arms are groups receiving different interventions. Allocation is randomized or non-randomized. The intervention model is single group, parallel, crossover, factorial or sequential.
- **Masking (blinding):** none (open label), single, double, triple or quadruple.
- **Primary purpose:** treatment, prevention, diagnostic, supportive care, screening, health services research, basic science, other.
- **Enrollment:** a target count with type ESTIMATED while the trial runs, switched to ACTUAL when enrollment ends.
- **Statuses:** open ones are NOT_YET_RECRUITING, RECRUITING, ENROLLING_BY_INVITATION, ACTIVE_NOT_RECRUITING (enrollment closed, participants still followed) and SUSPENDED (paused, may resume). Terminal ones are COMPLETED, TERMINATED (stopped early after enrolling) and WITHDRAWN (stopped before enrolling anyone). UNKNOWN means the record passed its completion date without a status verification in the past 2 years.
- **Why stopped:** a free-text reason, required for TERMINATED, WITHDRAWN and SUSPENDED.
- **Key dates:** first posted (registration becomes public), start, primary completion (last participant's primary outcome measured), completion, last update posted, status verified.
- **Sponsor classes:** INDUSTRY, NIH, FED, OTHER_GOV, NETWORK, INDIV, OTHER (mostly universities and hospitals), UNKNOWN. The history dataset also uses AMBIG.
- **Conditions and MeSH:** free-text conditions plus NLM-assigned MeSH terms, grouped into browse branches (therapeutic areas).
- **Eligibility criteria:** free-text inclusion and exclusion criteria plus structured age, sex and healthy-volunteer fields.
- **Regulation:** the 2007 FDAAA law made registration mandatory for many trials, and the 2017 Final Rule tightened reporting. Registry behavior changes over time, which is a real source of drift.

Methods vocabulary:

- **Censoring:** the outcome is not observed yet (for example, the trial is still open at the data cutoff). Censored trials still carry information up to their censoring time.
- **Competing risks:** a trial ends by completion or by early stop; completing removes the chance of an early stop.
- **Cumulative incidence function (CIF):** the probability of an early stop by time t when a competing event exists. Aalen-Johansen is its nonparametric estimator.
- **Landmarking:** making predictions at fixed checkpoints in a trial's life, using only the information available at each checkpoint.
- **Discrete-time hazard model:** split follow-up into intervals and predict, per interval, whether the trial stops, completes or continues.
- **IPCW:** inverse probability of censoring weighting, which keeps evaluation unbiased when some outcomes are not yet known.

## 6. Locked definitions

- **Population:** study_type = INTERVENTIONAL and study_first_post_date on or after 2008-01-01.
- **Time zero (t0):** study_first_post_date. Registration, not start, because withdrawals happen before a trial starts.
- **Version clock:** a version becomes known at its last_update_post_date. For live data, the lastUpdatePostDate observed through API v2.
- **State at time t:** the latest version whose effective time is on or before t.
- **Event of interest (early stop):** the first version whose status is TERMINATED or WITHDRAWN. Event time is that version's effective time, which is when the public could see it.
- **Competing event:** the first version whose status is COMPLETED.
- **Reversals:** if a terminal status is later replaced by an open status, exclude the trial from training and evaluation, and count it in the data audit.
- **UNKNOWN:** censored at the status_verified_date recorded in the first version showing UNKNOWN (fallback: that version's effective time). A sensitivity analysis treats UNKNOWN as an early stop.
- **Data cutoff:** the maximum last_update_post_date in the pinned dataset revision. Open trials are censored there.
- **Landmarks:** L_k = t0 + 6k months for k = 0 to 6, kept only if the trial is open and uncensored at L_k.
- **Horizons:** 12 and 24 months after the landmark.
- **Primary target:** the CIF of early stop at L + 12 and L + 24 months, given the information available at L.
- **Secondary target:** cause-specific CIFs for operational stops (reasons: accrual, business, funding, administrative, covid19) and scientific stops (safety, efficacy), using the reason classifier from Step 6.
- **Discrete-time formulation:** four 6-month intervals after each landmark (j = 1 to 4). Features are frozen at L, and j is itself a feature. Per interval, the outcome is continue, complete or stop. The sequence ends at the first stop or completion. An interval censored before its end is dropped and ends the sequence.
- **Walk-forward origins:** T in {2016-01-01, 2017-01-01, 2018-01-01, 2019-01-01, 2020-01-01}. For origin T, training rows are landmarks with L before T, with every outcome administratively censored at T. Evaluation rows are landmarks with T <= L < T + 12 months, scored against outcomes observed through the data cutoff.
- **Origin roles:** 2016 and 2017 are for development and tuning. 2018 and 2019 are the locked test. 2020 is the locked COVID stress test, reported separately.
- **Fitted transforms** (PCA, encoders, scalers, smoothing priors) are fit on each origin's training rows only.

Metrics (reported per horizon, per landmark index, and pooled):

- **Primary:** IPCW time-dependent AUC for early stop at the horizon. Cases stopped early within the horizon. Controls had no early stop by the horizon (completions included). Rows censored before the horizon are handled with IPCW, using a Kaplan-Meier estimate of the censoring distribution.
- **Secondary:** IPCW Brier score at the horizon; calibration (predicted CIF vs Aalen-Johansen observed CIF by risk decile, plus calibration slope and intercept); lift at 10% (early-stop rate in the top 10% of predicted risk divided by the overall rate).
- **Uncertainty:** 95% cluster-bootstrap confidence intervals, resampling trials, 1,000 resamples.

## 7. Data sources and licensing

1. **Version history:** Hugging Face dataset `brbk/clinical_trials_history`, config `core`. One row per (nct_id, nct_version); about 4.3M rows across about 583K trials, roughly 1 GB of Parquet.
   - Gated: Aakrisht must accept the terms on Hugging Face and provide HF_TOKEN.
   - License CC-BY-NC-4.0: non-commercial use only. Cite it in the README and data card. Never redistribute the raw files.
   - The dataset refreshes weekly with Git tags (v2026.MM.DD). Pin one revision in config/project.yaml and log it with every run.
   - Semantics: last_update_post_date is when that version became public (the version clock). study_first_post_date is constant across versions. Post dates before about 2018 are often ESTIMATED (roughly the submit date plus one day).
   - The core config holds single-value protocol fields only. List fields (phases, conditions, interventions, arms, locations) may be missing; Step 2 confirms. The dataset card lists planned configs (locations, interventions, version_history); if they are published later, adopting them requires an ADR.
2. **ClinicalTrials.gov API v2 (live source):** `https://clinicaltrials.gov/api/v2/studies`.
   - Public, no key. Guidance is about 50 requests per minute per IP; stay at 40 or below.
   - pageSize is capped at 1000 (larger values are silently clamped). Pagination is by cursor: pass nextPageToken back as pageToken and stop when it is absent.
   - Use filter.advanced with AREA[] syntax (for example on LastUpdatePostDate) and the fields parameter to limit payloads. Step 2 confirms the exact syntax.
   - API v2 does not return version history.
3. **Internal history endpoint (verification only):** the dataset was built from ClinicalTrials.gov's undocumented `/api/int/studies/{NCT}?history=true` and `/api/int/studies/{NCT}/history/{version}`. Allowed only for small verification samples in Step 2, at 20 requests per minute or less. Never a production dependency.
4. **Current-record fields:** fields missing from the version history (for example phases and MeSH browse branches) come from one bulk API v2 snapshot using the fields parameter. They may be used as features only if Step 2's stability audit allows them (Section 8).
5. **LLM for reason labeling (Step 6 only):** Groq API, default model openai/gpt-oss-120b, provider configurable. Production never calls an LLM; it uses the distilled classifier.
6. **Biomedical sentence-embedding model** (open weights), chosen in Step 7 after a cost check.

## 8. Feature rules and families

Rules:

1. **Point-in-time:** a feature at landmark L uses only versions (of this trial and of all other trials) whose effective time is on or before L.
2. **Field whitelist:** features come from fields that have version history. A current-record-only field is allowed only if Step 2's stability audit shows it changed after version 0 in under 3% of sampled trials. Site counts are excluded unless versioned locations become available.
3. **Label-only information** (the terminal version's why_stopped, or anything from versions after L) is never a feature.
4. **Registry:** every feature is registered with name, family, source fields, versioned or not, and description. docs/features.md is generated from the registry, and a test fails if a computed feature is missing from it.
5. **Mandatory leakage tests:**
   - Future perturbation: for 500 sampled landmarks, randomly alter every version after L and assert the features at L are unchanged.
   - Whitelist test.
   - Sponsor track record uses only outcomes before L.

Families:

- **Design (as of L):** allocation, intervention model, primary purpose, masking, phase (if allowed), healthy volunteers, sex, age limits, enrollment target and type, planned duration (primary completion minus start), start date actual or anticipated, retrospective registration (start before first post), registration year.
- **Amendment signals (as of L):** versions so far, versions in the last 6 months, days since last update, cumulative slip of primary completion and completion dates versus version 0, start date slip, enrollment target change ratio versus version 0, ever suspended, currently suspended, start overdue (still not recruiting after the anticipated start), days since status last verified.
- **Sponsor (as of L):** sponsor class, organization class, number of prior registrations, and the early-stop rate among the sponsor's trials that ended before L, smoothed toward the class rate (empirical Bayes, prior weight 10). Sponsor identity is the normalized lead sponsor name (lowercase, punctuation stripped); no fuzzy entity resolution in v1.
- **Competition (as of L):** number of open interventional trials sharing at least one therapeutic area (MeSH browse branch, if allowed), overall and within the same phase group.
- **Text (as of L):**
  - Eligibility statistics: inclusion and exclusion counts, length, numeric lab thresholds, age span.
  - Biomedical sentence embeddings of eligibility criteria and brief summary, reduced by PCA to 32 dimensions fit per origin. Embeddings are computed once per distinct text hash and cached.
  - If embedding all distinct texts would take more than about 2 hours on CPU, use TF-IDF plus truncated SVD instead and record an ADR.
- **Time:** landmark index k, months since t0.

## 9. Modeling plan

Model ladder (each rung answers one question):

- **M0:** Aalen-Johansen CIF stratified by phase group (or by sponsor class if phase is not allowed). Is there any signal beyond base rates?
- **M1:** static IPCW LightGBM binary classifier at L0 only, one per horizon. The published-style static comparison.
- **M2:** discrete-time multinomial logistic regression, design features only.
- **M3:** M2 plus amendment signals. Do dynamic signals help?
- **M4:** discrete-time LightGBM with all feature families. The champion candidate.
- **Cause-specific Cox model at L0 (lifelines):** for interpretation only (hazard ratios with confidence intervals, proportional-hazards checks). Not a predictive competitor.

Random survival forests are excluded: scikit-survival forests do not handle competing risks, and a biased comparison is worse than none.

Discrete-time CIF: with interval hazards h_stop(j) and h_complete(j), survival is S(j) = product over i <= j of (1 - h_stop(i) - h_complete(i)), and CIF_stop(H) = sum over the intervals j within H of S(j - 1) * h_stop(j). Required unit test: with no covariates, the discrete-time CIF matches Aalen-Johansen at interval boundaries within tolerance.

Tuning: Optuna with a fixed budget of 40 trials per model, on development origins only. Fixed seeds. LightGBM early stopping on the last training year.

Selection rule (declared now): the champion is the model with the best mean AUC at 12 months across development origins. Ties are broken by Brier score at 12 months, then by the calibration slope closest to 1.

## 10. Evaluation discipline

- The evaluation harness (src/trialpulse/eval) is built and unit-tested on synthetic data with known answers before any model is evaluated.
- **Test lock:** evaluating origins 2018, 2019 or 2020 requires the `--unlock-test` flag AND a docs/preregistration.md committed before the run. The harness checks git history, refuses otherwise, and records the preregistration commit hash with the results and in MLflow.
- **Pre-registration** (same protocol as volatility-risk-engine): before unlocking, commit hypotheses with numeric predictions, acceptable ranges and empty results blocks. After the run, fill in the results in a new commit without editing the predictions. Falsified predictions are published, not hidden.
- EDA that informs modeling uses only landmarks before 2018-01-01. Plots of later years are allowed but labeled "descriptive only".

## 11. NLP: why trials stop

- **Taxonomy:** accrual, business, funding, administrative, covid19, safety, efficacy, other. Operational = accrual, business, funding, administrative, covid19. Scientific = safety, efficacy.
- **Labeling guide:** docs/labeling_guide.md defines each label with examples and tie-break rules. Examples: slow enrollment is accrual. A sponsor decision without detail is business. A PI leaving the institution is administrative. Futility at an interim analysis is efficacy. A data monitoring committee stop for harm is safety. A COVID mention is covid19 unless another reason clearly dominates. When several reasons are given, use the decisive one.
- **Gold set:** 400 texts, stratified by status and year, labeled by Aakrisht in a small Streamlit labeling app. Split 100 dev and 300 test with a fixed seed. Commit the labels keyed by nct_id and nct_version in labels/, without the raw texts.
- **LLM labels:** a stratified 10,000-text sample. Temperature 0, validated JSON output, prompt versioned in the repo, responses cached, batches of about 25 texts per request within provider limits, resumable. Prompt iteration uses gold-dev only, at most 3 iterations.
- **Distillation:** TF-IDF plus logistic regression, trained on the LLM labels and evaluated on gold-test. It labels every early stop in the cohort and is the only reason model used in production.
- **Reporting:** accuracy, macro-F1 with a bootstrap CI, Cohen's kappa and a confusion matrix for both the LLM and the distilled model on gold-test.

## 12. Live system

Daily job (GitHub Actions, cron 21:30 UTC):

1. Pull records with LastUpdatePostDate on or after (the last successful watermark minus 3 days). Use the fields parameter, stay at 40 requests per minute or less, and retry with exponential backoff plus jitter on 429 and 5xx responses.
2. Normalize into the same canonical version schema used offline, validate with Pandera, and quarantine failures with counts.
3. Append new versions to live.study_versions (keyed by nct_id and last_update_post_date plus a payload hash), update live.trial_state, and record status transitions in live.outcome_events.
4. Rescore affected trials daily and all open trials weekly. Write serving.trial_scores_latest. Write serving.trial_score_history only when a score moves by 0.01 or more or the record changed.
5. Grade matured predictions into monitoring.graded_predictions. Write run metadata (counts, durations, watermark) to monitoring.pipeline_runs.

Job properties:

- **Idempotent:** a second run on the same day changes nothing.
- **Failure:** the job exits non-zero and opens a GitHub issue. The embedding model and dependencies are cached in Actions.

Supporting pieces:

- **Bootstrap:** live.trial_state starts from the latest version per trial in the pinned dataset. API deltas are then applied from the dataset cutoff forward.
- **Storage:** Neon holds only compact live, serving and monitoring tables. Step 14 includes a sizing estimate that must stay under the free plan's storage cap with at least 30% headroom.
- **Replay mode:** the same scoring and grading code can run over a historical window, using the version-history dataset as its source. This produces monitoring history with matured outcomes.
- **Serving parity:** features and scores from the live path must equal the offline path for the same trial and date (tested).

## 13. Serving and product

- **FastAPI (read-only):** /health, /v1/trials/{nct_id}/risk, /v1/trials/{nct_id}/timeline, /v1/sponsors/{name}/portfolio, /v1/areas/{area}/landscape, /v1/reasons/summary, /v1/model.
  - Pydantic v2 response models, OpenAPI docs, input validation, no personal data.
  - It reads precomputed scores and SHAP drivers from Postgres; there is no model inside the API.
- **Streamlit dashboard:** five pages. It calls the API only and handles API cold starts gracefully.
  - Portfolio: sponsor search, trials ranked by risk.
  - Trial detail: risk timeline with amendment markers, top drivers.
  - Landscape: risk and competition by therapeutic area.
  - Why trials stop: reasons over time, including the COVID spike.
  - Model health: freshness, drift, graded performance.
- **Deployment:** the API as a Docker image on Hugging Face Spaces; the dashboard on Streamlit Community Cloud; secrets only in the platform secret stores.
- **Explanations:** SHAP for M4. The top 5 drivers, with direction, are stored with every served prediction.

## 14. Monitoring and retraining

- **Data:** watermark lag at most 3 days, row-count anomalies, Pandera failure rate, schema drift.
- **Model:**
  - A weekly Evidently report on feature and score drift against the training reference (HTML artifact plus summary table).
  - Performance on matured predictions (AUC at 12 months for predictions issued at least 12 months ago), plus partial-horizon AUC for recent ones.
- **Retraining:** a quarterly scheduled workflow plus manual dispatch.
  - Train a challenger on all available data, then evaluate champion and challenger on the same most recent window with matured outcomes.
  - Promote only if the challenger's AUC at 12 months is at least the champion's minus 0.005 and its calibration slope is within [0.8, 1.2].
  - Log every decision (MLflow tags plus monitoring.promotions).

## 15. Locked stack

- **Environment and quality:** Python 3.12, uv; ruff (lint and format), mypy (strict on src/), pytest with pytest-cov, pre-commit.
- **Storage:** DuckDB and Parquet for the offline warehouse; PostgreSQL 16 (Docker Compose locally, Neon in the cloud); SQLAlchemy 2.x with psycopg 3; Alembic.
- **Data handling:** Pandera; pydantic-settings; httpx with tenacity (respx for HTTP mocking in tests); datasets and huggingface_hub.
- **Modeling:** lifelines; LightGBM; scikit-learn; Optuna; sentence-transformers; SHAP.
- **Serving and ops:** MLflow (hosted free on DagsHub); FastAPI with uvicorn; Streamlit with Plotly; Evidently; Docker; GitHub Actions.
- **Step 6 only:** Groq SDK.

## 16. Repo layout

```
trialpulse/
  CLAUDE.md
  README.md
  LICENSE
  pyproject.toml
  uv.lock
  docker-compose.yml
  Dockerfile.api
  .env.example
  .pre-commit-config.yaml
  .github/workflows/        ci.yml, daily.yml, retrain.yml
  config/project.yaml       dataset revision, cutoff, landmarks, horizons, origins, seeds
  alembic/                  migrations for the live, serving and monitoring schemas
  labels/                   committed gold labels (ids and labels only, no raw text)
  src/trialpulse/
    config.py
    feasibility/            spike.py
    ingest/                 history.py, ctgov_api.py, current_fields.py
    contracts/              pandera schemas, canonical version schema
    warehouse/              build.py, audits
    cohort/                 population, outcomes, landmarks, person-period
    eda/                    report.py
    nlp/                    taxonomy.py, labeling_app.py, llm_labeler.py, distill.py
    features/               registry.py, design.py, amendments.py, sponsor.py, competition.py, text.py
    models/                 aalen_johansen.py, static_clf.py, discrete_time.py, cox.py
    eval/                   ipcw.py, metrics.py, walkforward.py, lock.py, bootstrap.py
    scoring/                score.py, registry.py
    live/                   daily.py, replay.py
    monitor/                drift.py, grading.py, alerts.py
    api/                    main.py, schemas.py, db.py
  app/                      Streamlit dashboard (Home.py plus pages)
  tests/                    unit and integration tests, recorded API fixtures
  docs/
    progress.md
    interview_notes.md
    adr/
    feasibility_report.md
    data_dictionary_history.md
    data_audit.md
    eda.md
    labeling_guide.md
    features.md
    results_dev.md
    cox_report.md
    preregistration.md
    results.md
    model_card.md
    data_card.md
    figures/
  data/                     gitignored: raw, warehouse, caches
```

## 17. Configuration and secrets

- .env (gitignored) and .env.example (names only): HF_TOKEN, DATABASE_URL, NEON_DATABASE_URL, MLFLOW_TRACKING_URI, MLFLOW_TRACKING_USERNAME, MLFLOW_TRACKING_PASSWORD, GROQ_API_KEY.
- GitHub Actions uses repository secrets with the same names.
- config/project.yaml holds every tunable constant from Section 6. Code never hardcodes them.

## 18. Roadmap

Each step lists Build, Acceptance and Verify. Verify commands are PowerShell, one per line.

### Step 1: Repo skeleton, tooling, CI

Build:

- uv project on Python 3.12 with a src layout; package trialpulse with config loading (pydantic-settings plus project.yaml).
- pre-commit: ruff, ruff format, mypy, end-of-file-fixer, trailing-whitespace, check-added-large-files.
- GitHub Actions CI running lint, types and tests on push and pull request.
- docker-compose.yml with Postgres 16 and a healthcheck.
- .gitignore covering data/, .env, mlruns/, *.parquet, *.duckdb; .env.example; a README stub; MIT LICENSE (Aakrisht Yadav, 2026).
- docs/progress.md, docs/interview_notes.md, docs/adr/0001-record-architecture-decisions.md, and docs/preregistration.md (header only).

Acceptance: lint, format, types and tests clean; a smoke test loads the config; the Postgres container is healthy; pre-commit passes on all files; the public GitHub repo exists and CI is green.

Verify:
```
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q
docker compose up -d
docker compose ps
uv run pre-commit run --all-files
gh run list --limit 1
```

### Step 2: Feasibility spike (go/no-go)

Build src/trialpulse/feasibility/spike.py, runnable per part:

- **a. Download:** fetch the pinned core revision to data/raw/history/. Aakrisht accepts the dataset terms and sets HF_TOKEN first.
- **b. Profile in DuckDB:**
  - Every column with its type (write docs/data_dictionary_history.md).
  - Row and trial counts, versions per trial, date ranges, null rates for key columns, share of ESTIMATED post dates by year.
  - Whether phases, conditions, interventions, arm counts, locations, status_verified_date and eligibility text exist as columns.
- **c. Cohort counts:** interventional trials first posted since 2008; latest-status counts (TERMINATED, WITHDRAWN, COMPLETED, UNKNOWN, open) by first-post year; why_stopped coverage among early stops.
- **d. Automated check:**
  - For 200 seeded random cohort trials, compare the dataset's latest version with the official API v2 record on overall status, start date, primary completion date, enrollment count, sponsor class and why_stopped.
  - Count a mismatch only if the official lastUpdatePostDate is on or before the dataset cutoff.
- **e. Manual check (Aakrisht):** for 10 of those trials, compare version 0 in the dataset with the original version on the clinicaltrials.gov Record History tab. Record the results in the report.
- **f. Stability audit for list fields:**
  - For 150 seeded trials, fetch version histories from the internal history endpoint (at most 20 requests per minute, verification only).
  - Measure how often phases, conditions, interventions, arm count and location count changed after version 0.
  - If that endpoint is unavailable, fall back to Aakrisht inspecting 20 trials' Record History tabs by hand, and treat every list field as not allowed unless that check shows no changes.
- **g. API v2 check:**
  - Pull the last 7 days of updates with the LastUpdatePostDate filter and pagination; report counts, pages and elapsed time.
  - Confirm the filter syntax and the JSON path of every field TrialPulse will ingest.
  - Run one bulk pull of the current-record-only fields for the cohort into a Parquet file.
- **h. Write docs/feasibility_report.md** with a GO or NO-GO against these criteria:
  - at least 200,000 cohort trials
  - last_update_post_date present for at least 99% of versions
  - automated agreement of at least 95% on the compared fields
  - manual check passing at least 9 of 10
  - at least 20,000 early stops in the cohort
  - why_stopped present for at least 80% of early stops
  - the API v2 delta pull working end to end

  Also list which current-record-only fields pass the under-3% stability rule.

Acceptance: the report is complete, with numbers and a stated decision. No module outside feasibility/ imports anything that calls the internal endpoint. If the result is NO-GO, stop; the planning chat decides the fallback.

Verify:
```
uv run python -m trialpulse.feasibility.spike --part all
uv run pytest -q
```
Then open docs/feasibility_report.md.

### Step 3: Warehouse, contracts and live schemas

Build:

- **DuckDB warehouse at data/warehouse.duckdb:**
  - raw_versions: typed from Parquet.
  - versions: cleaned, with canonical enums, dates parsed with a precision flag, normalized text.
  - trials: one row per trial with t0, first and last version, latest status.
- **One canonical version schema (Pandera),** shared by the offline dataset and live API v2 rows, so all downstream code is source-agnostic.
- **Alembic migrations** for the Postgres schemas live, serving and monitoring (tables created, empty).
- **Data audit part 1 in docs/data_audit.md:** duplicate (nct_id, nct_version) rows, non-monotonic post dates across versions, impossible dates, enum drift, status reversals.

Acceptance: building the warehouse twice gives identical row counts and table checksums; Pandera validates the cohort, with failures quarantined and counted; `alembic upgrade head` works on local Postgres; tests cover date parsing, enum mapping and idempotency on a small fixture.

Verify:
```
uv run python -m trialpulse.warehouse.build
uv run python -m trialpulse.warehouse.build
uv run alembic upgrade head
uv run pytest -q
```

### Step 4: Cohort, outcomes and landmarks

Build:

- Population filter.
- Event extraction per Section 6: first terminal version, reversals excluded, the UNKNOWN rule, the data cutoff.
- Landmark table (trial by k, with at-risk logic).
- Person-period expansion for discrete-time training.
- Per-landmark evaluation labels (event type and time, censoring time).
- Data audit part 2: cohort funnel and outcome counts by first-post year.

Acceptance:

- Unit tests on hand-built mini histories cover: withdrawn at month 3, terminated at month 20, completed at month 8, UNKNOWN at month 30, open at cutoff, a reversal, registration after start, and missing dates.
- An Aalen-Johansen sanity table (early-stop CIF at 12 and 24 months by phase group) is printed and saved.

Verify:
```
uv run python -m trialpulse.cohort.build
uv run pytest tests/cohort -q
```

### Step 5: Exploratory data analysis

Build src/trialpulse/eda/report.py, which regenerates docs/eda.md and docs/figures/:

- Early-stop CIF by phase, sponsor class and registration year.
- The stop-versus-complete competing view.
- Amendment behavior (date slips, enrollment target changes) for trials that later stopped versus completed, measured at the 12-month landmark.
- Registration lag.
- ESTIMATED post-date share by year.
- A descriptive COVID-era shift.
- End with 5 to 8 findings and their modeling implications.

Acceptance: one command regenerates everything; every claim in eda.md points to a figure or table; modeling-relevant analysis uses landmarks before 2018-01-01 only (Section 10).

Verify:
```
uv run python -m trialpulse.eda.report
```

### Step 6: Why trials stop (NLP)

Build:

- Taxonomy and labeling guide (Section 11).
- Streamlit labeling app for the 400-text gold set. Aakrisht labels it; expect 2 to 3 hours.
- LLM labeling of the stratified 10,000-text sample.
- Distilled TF-IDF plus logistic regression classifier.
- A reasons table in the warehouse for every early stop.
- A reasons addendum in docs/eda.md (distribution by year, phase and sponsor class).

Acceptance:

- Gold-test results are reported for the LLM and the distilled model (accuracy, macro-F1 with CI, kappa, confusion matrix).
- Target: LLM macro-F1 of at least 0.80 on gold-test. If it is not reached within 3 prompt iterations on gold-dev, stop and report.
- The gold-test split is never used for prompt or model tuning.

Verify:
```
uv run streamlit run src/trialpulse/nlp/labeling_app.py
uv run python -m trialpulse.nlp.llm_labeler --sample 10000
uv run python -m trialpulse.nlp.distill
uv run pytest tests/nlp -q
```

### Step 7: Point-in-time features

Build: the feature registry and families (Section 8); features for every landmark row; docs/features.md generated from the registry; a null-rate report; the embedding cache with its cost check.

Acceptance: all three leakage tests pass; the registry completeness test passes; building features twice gives identical output hashes.

Verify:
```
uv run python -m trialpulse.features.build
uv run pytest tests/features -q
```

### Step 8: Evaluation harness and test lock

Build: IPCW weights, AUC and Brier at a horizon, calibration, lift at 10%, cluster bootstrap, the walk-forward runner, and the test lock (Section 10).

Acceptance:

- Metric tests on synthetic data pass: perfect scores give AUC 1, random scores give about 0.5, no censoring makes IPCW equal to unweighted, and constructed censoring patterns give known values.
- M0 runs end to end on development origins.
- A test proves locked origins cannot be evaluated without the flag and a committed preregistration.

Verify:
```
uv run pytest tests/eval -q
uv run python -m trialpulse.eval.walkforward --model m0 --origins dev
```

### Step 9: Baselines and Cox analysis

Build:

- M0 and M1 on development origins.
- Cause-specific Cox at L0 with hazard ratios, confidence intervals and Schoenfeld-residual checks (docs/cox_report.md).
- Every run logged to MLflow on DagsHub with git commit, dataset revision, config, metrics and artifacts.

Acceptance: results table in docs/results_dev.md; MLflow runs visible on DagsHub; zero test-lock unlock events.

Verify:
```
uv run python -m trialpulse.eval.walkforward --model m1 --origins dev
uv run python -m trialpulse.models.cox
```

### Step 10: Discrete-time models and tuning

Build:

- M2, M3 and M4 with person-period training and CIF computation.
- Optuna tuning on development origins.
- An ablation table: design only, plus amendments, plus sponsor and competition, plus text.
- Champion selected by the declared rule.

Acceptance: the no-covariate CIF matches Aalen-Johansen within tolerance (tested); the ablation table is in docs/results_dev.md; runtime and model size are reported; zero unlock events.

Verify:
```
uv run pytest tests/models -q
uv run python -m trialpulse.eval.walkforward --model m4 --origins dev --tune
```

### Step 11: Pre-registration, locked test and results

Build:

- Draft docs/preregistration.md with the planning chat: hypotheses with numeric predictions and ranges. Examples:
  - M4 beats M1 at L0.
  - The gain from amendment signals grows with landmark index.
  - Operational stops are more predictable than scientific ones.
  - Calibration slope lands in [0.8, 1.2].
  - The COVID origin degrades AUC.
- Commit it. Then unlock and evaluate origins 2018 and 2019 (primary) and 2020 (COVID stress).
- Write docs/results.md: headline table with CIs, AUC by landmark index, calibration plots, lift, cause-specific results, the UNKNOWN sensitivity analysis, and every falsified prediction.

Acceptance: the preregistration commit precedes the results commit (both hashes cited); one command reproduces results.md; predictions are not edited after unlock.

Verify:
```
git log --oneline -- docs/preregistration.md docs/results.md
uv run python -m trialpulse.eval.walkforward --model all --origins test --unlock-test
```

### Step 12: Explainability, model card, data card

Build:

- SHAP global importance and per-prediction drivers for M4.
- Three example risk timelines chosen by a seeded draw from test-origin trials: one stopped, one completed, one still open.
- docs/model_card.md: intended use, out-of-scope uses, metrics, limitations, ethics, license.
- docs/data_card.md: sources, licenses, citation, known biases (self-reported data, US-centric mandate, estimated pre-2018 dates, UNKNOWN statuses).

Acceptance: figures regenerate from one command; both cards are complete and consistent with results.md.

### Step 13: Scoring pipeline and model registry

Build:

- score(as_of_date), which rebuilds features and predictions for all open trials on any date.
- MLflow registry with champion and challenger aliases.
- Change-only score history.
- Local Postgres serving tables.

Acceptance: scoring the same date twice gives identical hashes; for a historical date, scores equal the evaluation harness predictions for the same landmark rows (training-serving parity test).

Verify:
```
uv run python -m trialpulse.scoring.score --as-of 2019-06-30
uv run pytest tests/scoring -q
```

### Step 14: Live ingestion and daily workflow

Build:

- The API v2 client, within the Section 7 limits.
- Normalization into the canonical schema, validation, upserts to Neon, the watermark, outcome events and rescoring.
- Bootstrap from the dataset cutoff.
- .github/workflows/daily.yml with secrets and failure issue creation.
- CI tests use recorded API responses only; no network access in CI.

Acceptance:

- The first live run completes, and a second run the same day is a no-op.
- For 50 trials unchanged since the dataset cutoff, the API v2 path and the dataset path produce identical canonical rows.
- The storage estimate stays under the Neon free cap with at least 30% headroom.

Verify:
```
uv run python -m trialpulse.live.daily --target local
uv run python -m trialpulse.live.daily --target local
uv run pytest tests/live -q
gh workflow run daily.yml
gh run list --workflow daily.yml --limit 1
```

### Step 15: API service

Build: the FastAPI endpoints (Section 13), Pydantic schemas, tests against a seeded test database, Dockerfile.api, and deployment to Hugging Face Spaces.

Acceptance:

- All endpoint tests pass, and the OpenAPI docs render.
- The image builds and runs locally.
- p95 latency is under 200 ms for /v1/trials/{nct_id}/risk in a local load test.
- The public URL is in the README.

Verify:
```
uv run pytest tests/api -q
docker build -f Dockerfile.api -t trialpulse-api .
docker run --rm -p 8000:8000 --env-file .env trialpulse-api
```
Then open http://localhost:8000/docs.

### Step 16: Dashboard

Build: the five Streamlit pages (Section 13), deployed to Streamlit Community Cloud and reading only from the API.

Acceptance: every page works against the deployed API; cold starts are handled; the disclaimer is visible on every page; screenshots are saved to docs/figures/.

Verify:
```
uv run streamlit run app/Home.py
```

### Step 17: Monitoring, retraining and replay

Build:

- The weekly Evidently job and matured-prediction grading.
- A replay from 2023-01-01 to 2024-06-30 at weekly cadence to create monitoring history.
- .github/workflows/retrain.yml with the promotion gate.
- The Model health page and the alert path.

Acceptance: the replay populates the monitoring tables and the Model health page; a forced failure opens a GitHub issue; one full retrain cycle runs and logs its promotion decision.

Verify:
```
uv run python -m trialpulse.live.replay --start 2023-01-01 --end 2024-06-30 --cadence weekly
uv run python -m trialpulse.monitor.drift
gh workflow run retrain.yml
```

### Step 18: Documentation and polish

Build:

- The README: problem, users, headline results with CIs, a Mermaid architecture diagram, how point-in-time reconstruction, landmarks and competing risks work, a quickstart for Windows PowerShell, live links, limitations, license and dataset citation.
- Complete ADRs, a CHANGELOG and a demo GIF.
- Resume bullets drafted from results.md, with numbers copied exactly.

Acceptance: a fresh clone follows the README quickstart successfully on Windows; all links work; CI is green.

### Stretch (only after Step 18, in this order)

1. DeepHit (pycox) as an extra challenger inside the same walk-forward harness.
2. A per-trial risk memo written by an LLM strictly from stored SHAP drivers and version diffs, with an automated fabrication check: every number and date in the memo must appear in the source payload.
