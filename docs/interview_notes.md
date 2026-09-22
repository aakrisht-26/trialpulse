# Interview notes

One entry per roadmap step: what was built, the key decision and why, the rejected alternative and why not, and one likely interview question with a short answer.

## Step 1: Repo skeleton, tooling, CI

**What was built.** A uv project on Python 3.12 with a src layout and a typed `trialpulse` package. Configuration is split in two: `config/project.yaml` holds the locked study definitions (population, event statuses, landmarks, horizons, walk-forward origins, metric constants, seeds), and a `Secrets` model reads credentials from the environment. Also: pre-commit hooks for ruff, mypy and file hygiene; a GitHub Actions CI job (lockfile install, lint, format, strict types, tests with coverage); a Postgres 16 container with a healthcheck; ADRs; and the progress log.

**Key decision and why.** The locked constants live in a committed YAML file validated by a strict, frozen schema, and environment variables cannot override them. A commit then fully determines the definitions a run used, which matters because the whole project depends on outcome and time definitions staying fixed across dozens of runs. Validators catch inconsistent definitions early, for example a horizon that does not land on the discrete-time interval grid, where the CIF would be undefined.

**Rejected alternative and why not.** A single settings class where environment variables override every value. It is convenient for experiments, but a leftover shell variable could silently change a locked definition. The results would then no longer match the committed config, and that is very hard to detect afterwards.

**Likely interview question.** "How do you make sure an experiment ran with the constants you think it did?"

**Short answer.** The constants are in a version-controlled file, loaded into an immutable schema that rejects unknown keys and ignores the environment. A smoke test pins the locked values, so changing one fails CI until the change goes through an ADR. Later steps log the git commit and the pinned dataset revision with every run, so any result traces back to the exact definitions and data.
