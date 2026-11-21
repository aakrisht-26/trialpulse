# TrialPulse

[![CI](https://github.com/aakrisht-26/trialpulse/actions/workflows/ci.yml/badge.svg)](https://github.com/aakrisht-26/trialpulse/actions/workflows/ci.yml) [![Python version from pyproject.toml](https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2Faakrisht-26%2Ftrialpulse%2Fmain%2Fpyproject.toml&query=%24.project%5B%27requires-python%27%5D&label=python&color=blue)](pyproject.toml) [![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE) [![Dashboard: live](https://img.shields.io/badge/dashboard-live-brightgreen)](https://trialpulse-demo.streamlit.app) [![API docs: live](https://img.shields.io/badge/API%20docs-live-brightgreen)](https://trialpulse-api.onrender.com/docs)

> Research demo. Not medical advice. Not for patient decision-making.

**TrialPulse ranks every open interventional trial on ClinicalTrials.gov by its risk of stopping early (terminated or withdrawn) within 12 and 24 months, so that clinical operations and portfolio teams at sponsors and contract research organizations know which trials to review first; a high score means elevated early-stop risk for operational planning, never a statement about whether a treatment works.**

**Headline, from a pre-registered locked test** ([results](docs/results.md)): a 12-month AUC of 0.730 (0.719 to 0.741) at the 2018 test origin and 0.723 (0.712 to 0.734) at 2019; the tenth of trials with the highest predicted risk held 3.23 (3.06 to 3.45) and 3.06 (2.89 to 3.25) times the average early-stop rate. Three of the five registered hypotheses were falsified, and are published as such.

**Live:** the [dashboard](https://trialpulse-demo.streamlit.app) and the [API documentation](https://trialpulse-api.onrender.com/docs), updated every day since 2026-11-08 (both sleep when idle and take about a minute to wake).

![A walk through the dashboard: Overview, a sponsor's Portfolio, a trial's risk, drivers and timeline, and Model health](docs/figures/demo.gif)

### What makes this different

- **Point-in-time reconstruction:** every trial is rebuilt from its version history as it stood on the prediction date, and a leakage test proves no later version moves a feature ([features](docs/features.md)).
- **A pre-registered, git-tagged locked test:** hypotheses with numeric ranges were tagged before the test origins could be scored, and the falsified ones are published ([pre-registration](docs/preregistration.md), [ADR 0009](docs/adr/0009-test-lock-requires-published-tag.md)).
- **Competing risks, censoring-weighted evaluation:** open trials count as censored, completion competes with an early stop, and metrics are IPCW-weighted ([ADR 0017](docs/adr/0017-ipcw-censoring-weights-by-sponsor-class.md)).
- **Live against offline parity:** the live rows are scored again offline and compared with what was served ([ADR 0038](docs/adr/0038-serving-parity-c.md)).
- **Monitoring with an honest promotion gate:** no challenger is promoted before outcomes it never saw have matured ([ADR 0080](docs/adr/0080-retrain-on-the-laptop-and-a-gate-that-waits.md)).
- **A risk memo that went from an LLM to a checked template:** three audits showed the model relating true facts wrongly, so a template now writes it and an automated check verifies every number and date ([ADR 0085](docs/adr/0085-the-memo-is-built-by-a-template.md)).

## What it does

For every open interventional trial, TrialPulse estimates the probability of an early stop within the next 12 and 24 months, names the five features that moved each score, and updates the estimates every day as sponsors amend their records. It reads registry metadata only and holds no patient data. The dashboard reads only the API; it also runs locally against the live API (Quickstart).

For the top 100 trials by 12-month risk and the trials whose risk moved, the Trial detail page shows a risk memo, a short summary that a fixed template builds from the trial's stored score, drivers and record changes (no language model writes it), and that an automated check verifies before it is shown, so that every number and date in it comes from those stored values ([ADR 0085](docs/adr/0085-the-memo-is-built-by-a-template.md)).

![The risk memo section of one trial's Trial detail page: its risks, its five 12-month drivers in plain words and its record changes, with the note that a template built it](docs/figures/memo_section.png)

## Results

The champion is a discrete-time LightGBM model (M4) of 54 point-in-time features. Its results come from a locked test on the 2018 and 2019 walk-forward origins. No model scored a landmark of those origins before the hypotheses were registered in [docs/preregistration.md](docs/preregistration.md) (tag `prereg-v1`), but outcomes from those years did inform development: the development rows are scored against outcomes through the data cutoff, and the registration lists every such exposure. Intervals are 95% cluster-bootstrap intervals over trials. Full tables, every model and the calibration figures: [docs/results.md](docs/results.md).

| Champion (M4), test origin | 2018 | 2019 |
| --- | --- | --- |
| AUC, 12 months (primary) | 0.730 (0.719 to 0.741) | 0.723 (0.712 to 0.734) |
| AUC, 24 months | 0.710 (0.702 to 0.718) | 0.696 (0.687 to 0.704) |
| Brier score, 12 months | 0.0300 (0.0289 to 0.0313) | 0.0297 (0.0285 to 0.0309) |
| Lift at 10%, 12 months | 3.23 (3.06 to 3.45) | 3.06 (2.89 to 3.25) |
| Calibration slope, 12 and 24 months | 0.855 and 0.782 | 0.803 and 0.732 |

What held:
- Both primary AUC predictions landed in their registered range of 0.7 to 0.75.
- H2, the second primary claim, held: the gain from amendment signals (M3 minus M2) grows with the landmark index, +0.0088 (+0.0060 to +0.0114) per index at 12 months.
- H5 held: in the COVID stress test, the 12-month AUC at the 2020 origin minus the mean of 2018 and 2019 was -0.0334 (-0.0464 to -0.0198).

What was falsified, and is published as such:
- **H1, falsified.** H1 predicted that at registration M4 and the static classifier M1 would tie at 12 months (less than 0.02 apart in AUC) and that M4 would lead at 24 months. The 12-month tie held: M4 minus M1 was -0.0027 (-0.0166 to +0.0114). The 24-month lead did not: -0.0013 (-0.0098 to +0.0075). A failed condition falsifies both predictions of the hypothesis.
- **H3, falsified.** Operational stops were not ranked better than scientific ones: +0.0229 (-0.0025 to +0.0494). TrialPulse makes no claim that it predicts the reason a trial stops.
- **H4, falsified.** The calibration slopes 0.855, 0.803, 0.782 and 0.732 did not all land in 0.8 to 1.2. At 24 months the predicted risks are spread too widely, so the scores are best used to rank trials ([docs/model_card.md](docs/model_card.md), calibration).

## Why it is more than a classifier

**Point-in-time reconstruction.** A registry record leaks its own outcome: after a trial stops, its status, dates and enrollment are rewritten. TrialPulse rebuilds every trial from its full version history exactly as it looked on the prediction date, and every feature reads only versions posted on or before that date, other trials' versions included. A leakage test alters every later version of sampled trials and checks that no feature moves ([docs/features.md](docs/features.md)).

**Landmarks and competing risks.** Predictions are made at registration and every 6 months after it, using only what was known then. Most trials are still open at the data cutoff, and a trial can end by completing or by stopping early. A discrete-time model predicts, for each 6-month interval, whether a trial continues, completes or stops, so open trials count as censored rather than as successes, and completion competes with an early stop. Evaluation uses inverse-probability-of-censoring weights (CLAUDE.md, Sections 6 and 9).

**A pre-registered locked test.** Models were developed on the 2016 and 2017 origins only. The hypotheses, with numeric predictions and acceptable ranges, were committed and tagged before the 2018, 2019 and 2020 origins could be evaluated, and the evaluation harness refuses those origins without the published tag. Falsified predictions are reported, not removed ([docs/results.md](docs/results.md)).

**A live loop.** Since 2026-11-08 a daily GitHub Actions job pulls changed records from the official ClinicalTrials.gov API v2, turns them into the same features through the same SQL as the offline path, rescores the affected trials and writes compact tables to Postgres on Neon. A read-only FastAPI service and a Streamlit dashboard read only those tables. Parity is checked in layers, from synthetic histories in CI to the live rows themselves, scored again offline on the laptop and compared with what was served ([docs/adr/0038-serving-parity-c.md](docs/adr/0038-serving-parity-c.md)).

**Monitoring and an honest gate.** A monthly workflow grades served predictions against the outcomes that have arrived; a replay of 2023 and 2024, scored by a model fitted as of 2023-01-01, gives monitoring history with matured outcomes, labeled as evidence, never as a registered result; each weekly full run measures drift of the features and risks against the trials served on 2026-11-03; alerts open GitHub issues. A quarterly retrain on the laptop trains a challenger, but the promotion gate waits until 12-month outcomes have matured for predictions made after the champion's training data, about November 2027, rather than compare the two models on data the champion was trained on ([ADR 0080](docs/adr/0080-retrain-on-the-laptop-and-a-gate-that-waits.md)).

## How this compares

Published studies have predicted trial termination from ClinicalTrials.gov; [docs/related_work.md](docs/related_work.md) sets four peer-reviewed ones beside TrialPulse, with their data, setup and results quoted as reported. Their numbers are not comparable with TrialPulse's, and nothing here claims to beat them:

- Three classify trials that had already ended, as of the download date, from the record as downloaded; TrialPulse ranks open trials from what was known on each prediction date.
- They test on random or cross-validated shares of the same years; TrialPulse tests on later years, locked behind a registration.
- None models completion as a competing event; the one survival study treats completion as its event.

TrialPulse's 12-month AUC is close to that of the largest of these studies, which classified ended trials, and below that of a study of 772 COVID-19 trials; the page explains why neither difference measures which approach is better.

## Architecture

```mermaid
flowchart LR
    subgraph laptop["Offline, on the laptop"]
        HF[("Version history dataset<br/>brbk/clinical_trials_history")]
        WH["DuckDB warehouse"]
        COH["Cohort, outcomes,<br/>landmarks"]
        FEAT["Point-in-time features"]
        EVAL["Walk-forward evaluation<br/>and locked test"]
        FIT["Champion M4,<br/>fitted on all data"]
        HF --> WH --> COH --> FEAT
        FEAT --> EVAL
        FEAT --> FIT
    end
    REG[("MLflow registry<br/>on DagsHub")]
    CTG["ClinicalTrials.gov API v2"]
    DAILY["Daily job<br/>GitHub Actions"]
    NEON[("Neon Postgres<br/>live, serving, monitoring")]
    API["FastAPI, read-only<br/>Render"]
    DASH["Streamlit dashboard<br/>Community Cloud"]
    FIT --> REG --> DAILY
    WH -.->|"one-time bootstrap"| NEON
    CTG --> DAILY --> NEON
    NEON --> API --> DASH
    GRADE["Monthly grading<br/>GitHub Actions"]
    RETRAIN["Quarterly retrain and gate,<br/>weekly backup, on the laptop"]
    NEON --> GRADE --> NEON
    NEON -.->|"live rows, decisions"| RETRAIN
```

The gated dataset never leaves the laptop except through the one-time bootstrap; after that only aggregates, models and named summaries do ([ADR 0040](docs/adr/0040-the-gated-dataset-stays-on-the-laptop.md)). Decisions are recorded as ADRs in [docs/adr/](docs/adr/).

Stack: Python, uv, DuckDB, LightGBM, scikit-learn, lifelines, Optuna, MLflow, PostgreSQL on Neon, FastAPI, Streamlit, Plotly, Docker and GitHub Actions.

## Status

| Part | State |
| --- | --- |
| Data, cohort, features, models, locked test, explanations, model and data cards, scoring pipeline (roadmap Steps 1 to 13) | Built and run on the full data |
| Daily job, API and dashboard (Steps 14 to 16) | Built and tested in CI against Postgres, recorded API responses and a seeded database |
| Live database on Neon, daily job and API (Steps 14 and 15) | Live since 2026-11-08: the daily job runs on GitHub Actions, and the read-only API is at https://trialpulse-api.onrender.com (its documentation at https://trialpulse-api.onrender.com/docs). The free instance sleeps when idle and takes about a minute to wake ([ADR 0065](docs/adr/0065-the-api-runs-on-renders-free-web-service.md)). Measured on 2026-11-17 for one trial's risk, over the public internet: warm p50 250 ms, p95 305 ms and maximum 837 ms over 50 trials; after 16 and 17 minutes without requests, under 2 seconds each (the instance had not spun down) |
| Dashboard on Streamlit Community Cloud (Step 16) | Live since 2026-11-17 at https://trialpulse-demo.streamlit.app, reading only the API; every page checked against the deployed API (8 of 8, the memo section included). The free app sleeps when idle; opening it wakes it, and a page waiting for the API says why ([ADR 0054](docs/adr/0054-waking-the-api.md)) |
| Monitoring, drift reports, grading of matured predictions, retraining (Step 17) | Live since 2026-11-17: a monthly grading workflow, a replay of 2023 and 2024 (monitoring evidence, never a registered result) on the Model health page, a weekly drift summary in the daily job's full run, the quarterly retrain on the laptop with its promotion gate (no promotion before matured outcomes exist after the champion's training data, about November 2027) and alerts as GitHub issues |

Progress, step by step, with evidence and the open items: [docs/progress.md](docs/progress.md).

## Quickstart (Windows PowerShell)

Needs [uv](https://docs.astral.sh/uv/) and Docker Desktop. The first block needs no dataset: it installs the pinned environment, starts the local Postgres and runs every test that does not need the data (tests that need it skip and say why). In `.env`, set `DATABASE_URL` to the local Postgres address that `.env.example` gives.

```powershell
git clone https://github.com/aakrisht-26/trialpulse.git
cd trialpulse
uv sync
Copy-Item .env.example .env
notepad .env
docker compose up -d
uv run alembic upgrade head
uv run pytest -q -m "slow or not slow"
```

**The gated dataset.** Every step that builds data needs the version history. Sign in to Hugging Face, open [brbk/clinical_trials_history](https://huggingface.co/datasets/brbk/clinical_trials_history), accept its terms (CC-BY-NC-4.0, non-commercial use), create a read token and set it as `HF_TOKEN` in `.env`. Then download the pinned revision and build the data (the feature build takes about 13 minutes on a laptop):

```powershell
uv run python -m trialpulse.ingest.history --config core
uv run python -m trialpulse.ingest.history --config interventions
uv run python -m trialpulse.warehouse.build
uv run python -m trialpulse.cohort.build
uv run python -m trialpulse.features.build
uv run python -m trialpulse.eval.walkforward --model m0 --origins dev
```

Files go to `data/`, which is gitignored; the dataset is never redistributed. The locked origins stay refused without the published pre-registration tag. The commands of each later step, and what each should print, are in [docs/progress.md](docs/progress.md).

**The API and the dashboard, locally, without the dataset.** In one window:

```powershell
$env:API_DATABASE_URL = "postgresql+psycopg://trialpulse:trialpulse@localhost:15432/trialpulse"
uv run uvicorn trialpulse.api.main:app --port 8000
```

In a second window:

```powershell
$env:TRIALPULSE_API_URL = "http://localhost:8000"
uv run streamlit run app/Home.py
```

On an empty local database the pages show what exists and say what does not.

## Limitations

- **Registry metadata only.** Safety and efficacy stops are scientific outcomes that registry metadata cannot anticipate. The claims are about operational early-stop risk, and H3 was falsified.
- **Calibration.** The 24-month calibration is below the registered range, and the slope fell at each later locked origin for a reason not established. Scores rank trials better than they state absolute risk.
- **At registration** M4 and the static classifier M1 rank trials about equally (H1). The amendment signals add more the longer a trial has been registered (H2); the static classifier scores registration only, so there is no comparison with it after registration.
- **Self-reported, US-centric data**, with estimated post dates before 2018 and records that go stale ([docs/data_card.md](docs/data_card.md)).
- **Older trials.** Trials 42 to 119 months after registration are scored outside the validated range and flagged; in development their scores ran above the observed risk ([docs/model_card.md](docs/model_card.md), served scope).
- **Live outcomes are young.** The live system started on 2026-11-08, so no live prediction has a matured 12-month outcome before November 2027; until then graded performance is partial-horizon, and the replay of 2023 and 2024 is monitoring evidence, not a registered result.
- **Free hosting.** The API sleeps when idle and takes about a minute to wake; the database runs on a free plan with a storage cap ([docs/deploy.md](docs/deploy.md)).

More in [docs/model_card.md](docs/model_card.md) and [docs/data_card.md](docs/data_card.md).

## License, attribution and citation

Code: MIT ([LICENSE](LICENSE)). Non-commercial use only for anything built on the data.

**Source: [ClinicalTrials.gov](https://clinicaltrials.gov).** Records are used under the ClinicalTrials.gov [terms and conditions](https://clinicaltrials.gov/about-site/terms-conditions). Data as of 2026-09-25, the date ClinicalTrials.gov processed the data behind the published results. TrialPulse normalizes the registry records and adds derived early-stop risk scores and drivers, which are not part of ClinicalTrials.gov. Live updates come from the official [ClinicalTrials.gov API v2](https://clinicaltrials.gov/data-api/api).

The version history is the Hugging Face dataset [`brbk/clinical_trials_history`](https://huggingface.co/datasets/brbk/clinical_trials_history), revision v2026.09.26, licensed CC-BY-NC-4.0 and not redistributed here. Later releases are published only at `anamnesis-data/clinical_trials_history`, and access approvals there do not carry over. Cite it as its author asks:

```bibtex
@misc{clinicaltrials_history_2026,
  title  = {ClinicalTrials.gov Version History: A longitudinal mirror},
  author = {Bret Gaulin},
  year   = {2026},
  url    = {https://huggingface.co/datasets/brbk/clinical_trials_history}
}
```

The live state also holds, once, the versions posted after that revision's cutoff, from the later release [`anamnesis-data/clinical_trials_history`](https://huggingface.co/datasets/anamnesis-data/clinical_trials_history) at v2026.11.04 (commit `2b7c8b0e4c00`), published by Anamnesis Data under the same CC BY-NC 4.0 terms and not redistributed here (ADR 0041, read under the pinned rules of ADRs 0073 and 0075). It is never a source of the registered results. Cite it as its card asks:

```bibtex
@misc{clinical_trials_history_2026,
  title  = {Clinical Trials Version History},
  author = {{Anamnesis Data}},
  year   = {2026},
  url    = {https://huggingface.co/datasets/anamnesis-data/clinical_trials_history}
}
```

Registry records: ClinicalTrials.gov, U.S. National Library of Medicine, https://clinicaltrials.gov, data as of 2026-09-25.
