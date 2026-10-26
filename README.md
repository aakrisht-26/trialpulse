# TrialPulse

[![CI](https://github.com/aakrisht-26/trialpulse/actions/workflows/ci.yml/badge.svg)](https://github.com/aakrisht-26/trialpulse/actions/workflows/ci.yml)

> Research demo. Not medical advice. Not for patient decision-making.

TrialPulse is operational risk analytics for clinical trial portfolios. For every open interventional trial registered on ClinicalTrials.gov, it estimates the probability of an early stop (terminated or withdrawn) within the next 12 and 24 months and explains the main drivers. Each estimate is built from the registry's version history as it looked on the prediction date, so it uses only information that was public at that time.

The project uses registry metadata only. It contains no patient data, and it does not predict whether a treatment works.

**Status:** under construction. Progress is tracked step by step in [docs/progress.md](docs/progress.md).

**Results so far:** the pre-registered results of the locked test, including the hypotheses that were falsified, are in [docs/results.md](docs/results.md), and the pre-registration in [docs/preregistration.md](docs/preregistration.md). The model and its limits are described in [docs/model_card.md](docs/model_card.md), the data in [docs/data_card.md](docs/data_card.md), and the drivers of the predictions in [docs/explanations.md](docs/explanations.md).

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

The trial version history comes from the Hugging Face dataset [`brbk/clinical_trials_history`](https://huggingface.co/datasets/brbk/clinical_trials_history), licensed CC-BY-NC-4.0 (non-commercial use only). The dataset is not redistributed in this repository. TrialPulse uses v2026.09.26, the last release at `brbk/clinical_trials_history`, cited as that release's card asks; later releases are published only at `anamnesis-data/clinical_trials_history`, and access approvals there do not carry over. Live updates come from the official [ClinicalTrials.gov API v2](https://clinicaltrials.gov/data-api/api).

**Source: [ClinicalTrials.gov](https://clinicaltrials.gov)**, used under its [terms and conditions](https://clinicaltrials.gov/about-site/terms-conditions). TrialPulse modifies the data: it normalizes the registry records and adds derived early-stop risk scores and drivers, which are not part of ClinicalTrials.gov. The pages written for readers, [docs/results.md](docs/results.md), [docs/explanations.md](docs/explanations.md), [docs/model_card.md](docs/model_card.md) and [docs/data_card.md](docs/data_card.md), name the source and state the date ClinicalTrials.gov processed the data ("data as of"), as ADR 0008 requires: data as of 2026-09-25. The pre-registration is the registered record of the same run; its source and data-as-of date are those given in docs/results.md.

Cite the version history as its author asks ([docs/data_card.md](docs/data_card.md), Citation).
