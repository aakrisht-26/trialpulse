# TrialPulse

[![CI](https://github.com/aakrisht-26/trialpulse/actions/workflows/ci.yml/badge.svg)](https://github.com/aakrisht-26/trialpulse/actions/workflows/ci.yml)

> Research demo. Not medical advice. Not for patient decision-making.

TrialPulse is operational risk analytics for clinical trial portfolios. For every open interventional trial registered on ClinicalTrials.gov, it estimates the probability of an early stop (terminated or withdrawn) within the next 12 and 24 months, names the five features that moved each score, and is built to update the estimates daily as sponsors amend their records. It is meant for clinical operations and portfolio teams at sponsors and contract research organizations who want to know which trials to review first. It reads registry metadata only: it holds no patient data, and a high score means elevated early-stop risk for operational planning, not a statement about whether a treatment works.

## Results

The champion is a discrete-time LightGBM model (M4) of 54 point-in-time features. Its results come from a locked test: two walk-forward origins that no model saw until the hypotheses were registered in [docs/preregistration.md](docs/preregistration.md) (tag `prereg-v1`). Intervals are 95% cluster-bootstrap intervals over trials. Full tables, every model and the calibration figures: [docs/results.md](docs/results.md).

| Champion (M4), test origin | 2018 | 2019 |
| --- | --- | --- |
| AUC, 12 months (primary) | 0.7303 (0.7191 to 0.7414) | 0.7231 (0.7122 to 0.7340) |
| AUC, 24 months | 0.7099 (0.7016 to 0.7180) | 0.6956 (0.6872 to 0.7041) |
| Brier score, 12 months | 0.0300 (0.0289 to 0.0313) | 0.0297 (0.0285 to 0.0309) |
| Lift at 10%, 12 months | 3.2335 (3.0643 to 3.4466) | 3.0637 (2.8888 to 3.2537) |
| Calibration slope, 12 and 24 months | 0.8554 and 0.7818 | 0.8030 and 0.7325 |

What held:
- Both primary AUC predictions landed in their registered range of 0.7 to 0.75.
- H2, the second primary claim, held: the gain from amendment signals (M3 minus M2) grows with the landmark index, +0.0088 (+0.0060 to +0.0114) per index at 12 months.
- H5 held: in the COVID stress test, the 12-month AUC at the 2020 origin minus the mean of 2018 and 2019 was -0.0334 (-0.0464 to -0.0198).

What was falsified, and is published as such:
- **H1, falsified.** At registration, M4 did not beat the static classifier M1: -0.0027 (-0.0166 to +0.0114) at 12 months.
- **H3, falsified.** Operational stops were not ranked better than scientific ones: +0.0229 (-0.0025 to +0.0494). TrialPulse makes no claim that it predicts the reason a trial stops.
- **H4, falsified.** The calibration slopes 0.855, 0.803, 0.782 and 0.732 did not all land in 0.8 to 1.2. At 24 months the predicted risks are spread too widely, so the scores are best used to rank trials ([docs/model_card.md](docs/model_card.md), calibration).

## Why it is more than a classifier

**Point-in-time reconstruction.** A registry record leaks its own outcome: after a trial stops, its status, dates and enrollment are rewritten. TrialPulse rebuilds every trial from its full version history exactly as it looked on the prediction date, and every feature reads only versions posted on or before that date, other trials' versions included. A leakage test alters every later version of sampled trials and checks that no feature moves ([docs/features.md](docs/features.md)).

**Landmarks and competing risks.** Predictions are made at registration and every 6 months after it, using only what was known then. Most trials are still open at the data cutoff, and a trial can end by completing or by stopping early. A discrete-time model predicts, for each 6-month interval, whether a trial continues, completes or stops, so open trials count as censored rather than as successes, and completion competes with an early stop. Evaluation uses inverse-probability-of-censoring weights (CLAUDE.md, Sections 6 and 9).

**A pre-registered locked test.** Models were developed on the 2016 and 2017 origins only. The hypotheses, with numeric predictions and acceptable ranges, were committed and tagged before the 2018, 2019 and 2020 origins could be evaluated, and the evaluation harness refuses those origins without the published tag. Falsified predictions are reported, not removed ([docs/results.md](docs/results.md)).

**A live loop.** A daily GitHub Actions job pulls changed records from the official ClinicalTrials.gov API v2, turns them into the same features through the same SQL as the offline path, rescores the affected trials and writes compact tables to Postgres on Neon. A read-only FastAPI service and a Streamlit dashboard read only those tables. Tests on synthetic histories check that the live path gives the same features as the offline path ([docs/adr/0038-serving-parity-c.md](docs/adr/0038-serving-parity-c.md)).

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
    API["FastAPI, read-only<br/>Hugging Face Spaces"]
    DASH["Streamlit dashboard<br/>Community Cloud"]
    FIT --> REG --> DAILY
    WH -.->|"one-time bootstrap"| NEON
    CTG --> DAILY --> NEON
    NEON --> API --> DASH
```

The gated dataset never leaves the laptop except through the one-time bootstrap; after that only aggregates, models and named summaries do ([ADR 0040](docs/adr/0040-the-gated-dataset-stays-on-the-laptop.md)). Decisions are recorded as ADRs in [docs/adr/](docs/adr/).

## Status

| Part | State |
| --- | --- |
| Data, cohort, features, models, locked test, explanations, model and data cards, scoring pipeline (roadmap Steps 1 to 13) | Built and run on the full data |
| Daily job, API and dashboard (Steps 14 to 16) | Built and tested in CI against Postgres, recorded API responses and a seeded database |
| Live database on Neon, first live run, API on Hugging Face Spaces, dashboard on Streamlit Community Cloud | Waiting for the deployments; there is no public link yet |
| Monitoring, drift reports, grading of matured predictions, retraining (Step 17) | Not started |

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
- **At registration** M4 ranks trials no better than a static classifier (H1). Its advantage comes from amendment signals as a trial ages (H2).
- **Self-reported, US-centric data**, with estimated post dates before 2018 and records that go stale ([docs/data_card.md](docs/data_card.md)).
- **Older trials.** Trials 42 to 119 months after registration are scored outside the validated range and flagged; in development their scores ran above the observed risk ([docs/model_card.md](docs/model_card.md), served scope).
- **Not live yet.** The live loop has run only in tests, not in production.

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

Registry records: ClinicalTrials.gov, U.S. National Library of Medicine, https://clinicaltrials.gov, data as of 2026-09-25.
