# TrialPulse

[![CI](https://github.com/aakrisht-26/trialpulse/actions/workflows/ci.yml/badge.svg)](https://github.com/aakrisht-26/trialpulse/actions/workflows/ci.yml)

> Research demo. Not medical advice. Not for patient decision-making.

TrialPulse is operational risk analytics for clinical trial portfolios. For every open interventional trial registered on ClinicalTrials.gov, it estimates the probability of an early stop (terminated or withdrawn) within the next 12 and 24 months and explains the main drivers. Each estimate is built from the registry's version history as it looked on the prediction date, so it uses only information that was public at that time.

The project uses registry metadata only. It contains no patient data, and it does not predict whether a treatment works.

**Status:** under construction. Progress is tracked step by step in [docs/progress.md](docs/progress.md).

## Development setup (Windows PowerShell)

Requires [uv](https://docs.astral.sh/uv/) and Docker Desktop.

```powershell
uv sync
uv run pre-commit install
docker compose up -d
uv run pytest -q
```

Copy `.env.example` to `.env` and fill in only the values the current step needs. `.env` is gitignored.

## License and data

Code is released under the MIT License (see [LICENSE](LICENSE)).

The trial version history comes from the Hugging Face dataset [`brbk/clinical_trials_history`](https://huggingface.co/datasets/brbk/clinical_trials_history), licensed CC-BY-NC-4.0 (non-commercial use only). The dataset is not redistributed in this repository. Live updates come from the official [ClinicalTrials.gov API v2](https://clinicaltrials.gov/data-api/api).
