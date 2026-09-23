# Interview notes

One entry per roadmap step: what was built, the key decision and why, the rejected alternative and why not, and one likely interview question with a short answer.

## Step 1: Repo skeleton, tooling, CI

**What was built.** A uv project on Python 3.12 with a src layout and a typed `trialpulse` package. Configuration is split in two: `config/project.yaml` holds the locked study definitions (population, event statuses, landmarks, horizons, walk-forward origins, metric constants, seeds), and a `Secrets` model reads credentials from the environment. Also: pre-commit hooks for ruff, mypy and file hygiene; a GitHub Actions CI job (lockfile install, lint, format, strict types, tests with coverage); a Postgres 16 container with a healthcheck; ADRs; and the progress log.

**Key decision and why.** The locked constants live in a committed YAML file validated by a strict, frozen schema, and environment variables cannot override them. A commit then fully determines the definitions a run used, which matters because the whole project depends on outcome and time definitions staying fixed across dozens of runs. Validators catch inconsistent definitions early, for example a horizon that does not land on the discrete-time interval grid, where the CIF would be undefined.

**Rejected alternative and why not.** A single settings class where environment variables override every value. It is convenient for experiments, but a leftover shell variable could silently change a locked definition. The results would then no longer match the committed config, and that is very hard to detect afterwards.

**Likely interview question.** "How do you make sure an experiment ran with the constants you think it did?"

**Short answer.** The constants are in a version-controlled file, loaded into an immutable schema that rejects unknown keys and ignores the environment. A smoke test pins the locked values, so changing one fails CI until the change goes through an ADR. Later steps log the git commit and the pinned dataset revision with every run, so any result traces back to the exact definitions and data.

## Step 2: Feasibility spike

**What was built.** A spike, runnable part by part, that checks whether the plan can work before any pipeline is built: it downloads and profiles the version-history dataset, counts the cohort and early stops, compares a seeded sample against the official API, audits how often list fields change after a trial's first version, and exercises the live API v2 delta pull. Each part writes its numbers to a results file, and a report is generated from those files. Every HTTP call is rate limited, retried with backoff and cached, so a crash or network outage costs only the requests in flight.

**Key decision and why.** Parts that cannot run are recorded as "blocked" with a reason instead of failing the whole run. Overnight, the dataset access request was still awaiting the authors' review, and the spike still produced real API numbers, a working delta pull and the stability audit. The report states each criterion as met, not met, or blocked, so a missing input is never mistaken for a passing one.

**Rejected alternative and why not.** Pulling the 200 part d records one by one from API v2. The cohort-wide bulk pull already holds the same official fields for all 420,073 cohort trials, so part d reads them from that Parquet file and only calls the API for trials missing from it. That saves 200 requests and keeps both sides of the comparison from the same snapshot.

**Likely interview question.** "Your registry data rewrites itself after a trial stops. How did you check that the version history is trustworthy before building on it?"

**Short answer.** Three independent checks: an automated comparison of the dataset's latest version against the official API for a seeded sample, counting a mismatch only when the official record was not updated after the dataset cutoff; a manual comparison of version 0 against the registry's own Record History page; and a stability audit that measures, from real version histories, how often fields that lack history in the dataset change after registration. A field is allowed only if it changes in under 3% of trials.
