# Progress

## Morning report (overnight run, 2026-09-23)

**Read first: your working copy of `.env.example` contains a Hugging Face token.** That file is tracked and the repository is public. It was never staged, committed or pushed tonight (every commit staged explicit paths only). Restore the file with the first command below. Consider rotating the token on Hugging Face, since it sat in a tracked file. When pre-commit stashed unstaged changes during commits, it wrote patch files containing the token to `%USERPROFILE%\.cache\pre-commit\`; the second command deletes them.

### Done

- Follow-up 1: CI runs on `ubuntu-24.04`, and runs on main are never cancelled (each gets its own concurrency group).
- Follow-up 2: ADR 0004 defines the test lock (annotated `prereg-v1` tag on a completed registration).
- Step 2 code for parts a to h, with 55 tests on synthetic fixtures. Parts g and f ran live; parts a to e are blocked; `docs/feasibility_report.md` is drafted with no decision stated. Details in the Step 2 section below.
- Provisional work (your extension): the gate was not met, so only the Step 6 subset and Step 8 were built, as code and synthetic-data tests. **Correction, added after review on 2026-09-23: the branch `provisional/steps-3-8` and its draft PR were never created.** The work was committed later that day to branch `provisional/step6-step8` (draft PR #1).

### Decisions made overnight (pending approval)

1. HTTP requests use httpx's default User-Agent, because ClinicalTrials.gov returned 403 for a custom one.
2. Retries go up to 8 attempts with exponential backoff and jitter capped at 2 minutes. A DNS outage stopped one pull after 182 of 421 pages; it resumed from the cache.
3. Part d takes the official side from the part g cohort pull, which has the same fields, and calls the API only for trials missing from it.
4. Part e writes the checklist with dataset version 0 values under `data/` (gitignored). You record pass or fail in `docs/feasibility_manual_check.csv`, which holds ids only.
5. Part f sampled from the part g API cohort, because the dataset was unavailable (seed 42). The mode is chosen at run time under the 60-minute rule: all versions fit (56.1 minutes). Only projections are cached, never contact data.
6. Part a never picks a revision silently. It stops until `config/project.yaml` pins one, and `--pin-latest` pins the newest tag when you ask for it.
7. Spike constants (sample sizes, request rates, the 3% threshold) are documented constants in `feasibility/`, not `project.yaml`: they are Step 2 settings, not Section 6 definitions.
8. The delta window is the 7 days before the current UTC date.
9. Parts c and d take study type and status from each trial's latest version. The full population and outcome logic belongs to Step 4.
10. MeSH terms and MeSH ancestor terms were added to both pulls after browse branches came back empty.
11. A blank or whitespace-only why_stopped counts as missing.
12. The stability table shows Wilson 95% intervals next to each share. The under-3% rule is unchanged.

### Open questions for Aakrisht

1. **Dataset access.** Hugging Face reports the request as awaiting the authors' review, so parts a to e cannot run until they approve it. Also, the dataset's main branch was last modified on 2026-05-12, although the card says weekly refreshes. The data cutoff would then be about May 2026, and the live bootstrap (Section 12) would start with about four months of API deltas.
2. **No list field passes the stability rule** (phases 4.0%, conditions 8.0%, interventions 13.3%, arm count 6.7%, location count 21.3%). By CLAUDE.md, current-record phases are therefore not allowed: M0 would stratify by sponsor class, and the design features lose phase. Phases is the closest (interval 1.8% to 8.5%). Any exception needs an ADR; I recommend keeping the rule as written.
3. **Therapeutic areas.** API v2 no longer returns MeSH browse branches (0 of 50 full records, 0 of 420,073 cohort trials). MeSH terms (77.5% of the cohort) and ancestors (76.0%) exist, but NLM derives them from the current conditions, which change in 8% of trials, and they have no history to audit. Options:
   - (a) Keep only the fully versioned competition count: open interventional trials overall. **Recommended.**
   - (b) Use current MeSH ancestor terms and accept the leakage risk.
   - (c) Map conditions to MeSH tree branches with NLM's MeSH files. That is a new data source, needs an ADR, and inherits the conditions instability.

   Each option changes Section 8 and needs an ADR.

### Commands to run (PowerShell)

Security clean-up (restores the committed `.env.example`, then deletes pre-commit's stash patches):

```powershell
git checkout -- .env.example
Remove-Item "$env:USERPROFILE\.cache\pre-commit\patch*"
```

Verify main (Step 1 carry-over included):

```powershell
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q
uv run pre-commit run --all-files
docker compose up -d
docker compose ps
uv run python -m trialpulse.feasibility.spike --part all
gh run list --limit 3
```

Once Hugging Face grants access (this pins the newest tag in `config/project.yaml`, downloads about 1 GB, and runs every part):

```powershell
uv run python -m trialpulse.feasibility.spike --part all --pin-latest
```

Then fill in `docs/feasibility_manual_check.csv` from `data/spike/part_e_checklist.csv` and regenerate the report:

```powershell
uv run python -m trialpulse.feasibility.spike --part h
```

Current step: **Step 2, Feasibility spike** (draft done, awaiting review; parts a to e blocked on dataset access).

| Step | Title | Status |
| --- | --- | --- |
| 1 | Repo skeleton, tooling, CI | Approved and fully verified 2026-09-23 |
| 2 | Feasibility spike (go/no-go) | Draft done, awaiting review (parts a to e blocked) |
| 3 | Warehouse, contracts and live schemas | Not started |
| 4 | Cohort, outcomes and landmarks | Not started |
| 5 | Exploratory data analysis | Not started |
| 6 | Why trials stop (NLP) | Not started |
| 7 | Point-in-time features | Not started |
| 8 | Evaluation harness and test lock | Not started |
| 9 | Baselines and Cox analysis | Not started |
| 10 | Discrete-time models and tuning | Not started |
| 11 | Pre-registration, locked test and results | Not started |
| 12 | Explainability, model card, data card | Not started |
| 13 | Scoring pipeline and model registry | Not started |
| 14 | Live ingestion and daily workflow | Not started |
| 15 | API service | Not started |
| 16 | Dashboard | Not started |
| 17 | Monitoring, retraining and replay | Not started |
| 18 | Documentation and polish | Not started |

## Step 1: Repo skeleton, tooling, CI

Date: 2026-09-23. Status: **approved and fully verified** by Aakrisht on 2026-09-23 (Docker Postgres check passed on his machine).

### What was built

- uv project on Python 3.12, src layout, typed package `trialpulse`, build backend `uv_build`.
- `src/trialpulse/config.py`: `load_project_config()` reads `config/project.yaml` into a frozen, strictly validated `ProjectConfig` that environment variables cannot override. `Secrets` reads credentials from the environment or `.env` as `SecretStr` (ADR 0002).
- `config/project.yaml` with every tunable constant from CLAUDE.md Section 6. `dataset.revision` and `dataset.cutoff` are `null` until Step 2.
- `tests/test_config.py`: 17 tests, including the smoke test that loads the committed config and checks it against Section 6.
- pre-commit: end-of-file-fixer, trailing-whitespace, check-added-large-files, and ruff check, ruff format and mypy as local `uv run` hooks (ADR 0003).
- `.github/workflows/ci.yml`: on push and pull request, runs `uv sync --locked`, ruff check, ruff format check, mypy on src, and pytest with coverage.
- `docker-compose.yml`: Postgres 16 with a `pg_isready` healthcheck, bound to 127.0.0.1.
- `.gitignore`, `.env.example`, README stub with the required disclaimer, MIT LICENSE (Aakrisht Yadav, 2026).
- Docs: this file, `interview_notes.md`, ADRs 0001 to 0003, and the `preregistration.md` header.

### Acceptance criteria

Evidence below is from Claude's shell on 2026-09-23. It is not a claim about Aakrisht's machine; the verify commands are the check.

| Criterion | Evidence |
| --- | --- |
| Lint clean | `ruff check .`: All checks passed |
| Format clean | `ruff format --check .`: 11 files already formatted (ruff 0.16 also checks Markdown) |
| Types clean | `mypy src` (strict): no issues in 2 source files |
| Tests clean, smoke test loads the config | `pytest -q`: 17 passed; `config.py` at 100% line and branch coverage |
| Postgres container healthy | Met on Aakrisht's machine, 2026-09-23: `docker compose up --wait` reported the container Healthy, and `docker compose ps` showed postgres (healthy) on `127.0.0.1:15432->5432/tcp` (after the port move in ADR 0005). |
| pre-commit passes on all files | `pre-commit run --all-files`: all 6 hooks passed, no files modified |
| Public GitHub repo exists and CI is green | https://github.com/aakrisht-26/trialpulse created; CI run 35792432458 on `b7c2a96` passed (lint, format, types, 17 tests, CPython 3.12.3 on ubuntu-latest) |

### Decisions

Approved by Aakrisht on 2026-09-23:

- PyYAML through `pydantic-settings[yaml]` for YAML parsing (ADR 0002).
- ruff and mypy as local pre-commit hooks through `uv run` (ADR 0003).
- Create the public repository `aakrisht-26/trialpulse` and push `main`.
- Repo-local git identity: Aakrisht Yadav with the GitHub noreply email.

Made by Claude, flagged for review:

- Only Step 1 dependencies were added (pydantic, pydantic-settings[yaml]; dev: ruff, mypy, pytest, pytest-cov, pre-commit). The rest of the locked stack is added by the step that first uses it, so each addition is reviewed in context.
- `project.yaml` also records the status groups, stop-reason groups, calibration bins (10) and lift fraction (0.10), because they are Section 6 constants. The default seed is 42.
- `.python-version` (3.12) and `src/trialpulse/py.typed` were added. Neither is in the Section 16 layout, but both are standard for a typed uv package.
- Ruff rule set: E, W, F, I, B, UP, SIM, C4, N, PT, PTH, RUF, with line length 100.
- CI: third-party actions pinned to commit SHAs, uv pinned to 0.11.26, a read-only token, `persist-credentials: false`, and cancellation of superseded runs. Coverage is reported but has no threshold.
- Docker Compose uses fixed local-only credentials (`trialpulse`), the Debian `postgres:16` image (glibc collation, like Neon) and a named volume.
- `.env.example` has comments, including the local Compose connection URL, which is not a secret.
- The pre-commit git hook is installed in `.git/hooks`.

### Blocked, skipped or deferred

- The Postgres health check (`docker compose up -d`, `docker compose ps`) could not be run by Claude. Aakrisht runs it.
- 2026-09-23: `docker compose up -d` failed on Aakrisht's machine. A native PostgreSQL 18 service (`postgresql-x64-18`) holds port 5432; 5432 is not in any Windows excluded port range. The container now publishes host port 15432, and the local URL uses it (ADR 0005). Resolved: the health check passed on Aakrisht's machine the same day.
- The dataset revision and cutoff are deferred to Step 2, by design.
- Note for Step 8: `docs/preregistration.md` exists in git history from Step 1 as a header stub. The test lock must require a substantive registration (for example, the status line changed and hypotheses present), not just any committed version of the file. (Resolved by ADR 0004 on 2026-09-23.)
- CI annotation: GitHub will move `ubuntu-latest` to Ubuntu 26 starting 2026-10-19. The workflow still uses `ubuntu-latest`. Pinning `ubuntu-24.04` is an option for review; nothing was changed. (Resolved: pinned to ubuntu-24.04 on 2026-09-23.)
- Local note: uv warns that it cannot hardlink from its cache (on C:) into the project (on E:) and falls back to copying. This is harmless. `$env:UV_LINK_MODE = "copy"` silences it.

## Step 2: Feasibility spike (go/no-go)

Date: 2026-09-23 (unattended overnight run). Status: **draft report written, decision not stated, parts a to e blocked on dataset access**.

### What was built

- `src/trialpulse/feasibility/`, runnable with `uv run python -m trialpulse.feasibility.spike --part <a..h, a list, or all>`:
  - `fetch.py`: rate limiter, retries with exponential backoff and jitter on 429, 5xx and network errors, an atomic gzip JSON cache, and a scrubber that removes personal-data keys before anything is cached.
  - `ctgov_v2.py`: the JSON path of every field TrialPulse will ingest, the AREA filters, resumable cursor pagination, normalization of the cohort pull to Parquet, and field-presence statistics.
  - `dataset.py`: part a (download of a pinned revision, `--pin-latest` to pin the newest tag), part b (DuckDB profile and data dictionary), part c (cohort counts) and the dataset side of part d.
  - `checks.py`: seeded sampling, the dataset-versus-API comparison (date precision, the cutoff rule), the stability measure and Wilson intervals.
  - `history_api.py`: the internal history endpoint, verification only, at 20 requests per minute or less; it caches projections, never raw records.
  - `report.py`: writes `docs/feasibility_report.md` with each criterion's measured value and no GO or NO-GO statement.
- `tests/feasibility/`: 55 tests on synthetic fixtures, no network. They include the boundary test that no module outside `feasibility/` imports it or names the internal endpoint.
- Dependencies, all in the locked stack: httpx, tenacity, duckdb, huggingface_hub; dev: respx.

### Results

- **Parts a to e: blocked.** The token works, but Hugging Face reports the access request to `brbk/clinical_trials_history` as awaiting the authors' review (the dataset is gated with manual approval). Parts b to e depend on part a.
- **Part g: done.**
  - Delta pull (`AREA[LastUpdatePostDate]RANGE[2026-09-15,MAX]`): 6,047 of 6,047 records, 7 pages, 16.5 s.
  - Cohort pull (`AREA[StudyType]INTERVENTIONAL AND AREA[StudyFirstPostDate]RANGE[2008-01-01,MAX]`): 420,073 of 420,073 records, 421 pages, 648 s, written to `data/spike/current_fields.parquet` (13.3 MB, not committed).
  - Official current records: 38,482 of those trials are TERMINATED or WITHDRAWN, with why_stopped present for 91.5%. These are API numbers, not the dataset criteria.
  - Every ingested field is present at 95% or more of the records it applies to, except `maximum_age` (48.8%, often absent by design), `intervention_types` (87.2%; observational studies are included in the delta) and MeSH browse branches (0%, see open questions).
- **Part f: done.** 150 seeded trials from the part g cohort, all 973 versions, 1,123 requests in 56.1 minutes. No audited list field changes after version 0 in under 3% of trials: phases 4.0% (Wilson 95% interval 1.8% to 8.5%), conditions 8.0%, interventions 13.3%, arm count 6.7%, location count 21.3%.

### Acceptance criteria

| Criterion | Result |
| --- | --- |
| Report complete with numbers and a stated decision | Partial: numbers for parts f and g; parts a to e blocked; decision intentionally not stated (Aakrisht's instruction for this run) |
| No module outside `feasibility/` imports anything that calls the internal endpoint | Met: `test_no_module_outside_feasibility_uses_the_internal_endpoint` |
| GO criteria on the dataset (cohort size, post-date coverage, agreement, early stops, why_stopped coverage) | Blocked: dataset access |
| Manual check, at least 9 of 10 | Blocked until part d runs; then Aakrisht's |
| API v2 delta pull end to end | Met: 6,047 of 6,047 records |
| Current-record-only fields under 3% | None pass |

### Files touched

- New: `src/trialpulse/feasibility/{__init__,fetch,ctgov_v2,dataset,checks,history_api,report,spike}.py`, `tests/feasibility/{conftest,test_fetch,test_ctgov_v2,test_dataset,test_checks,test_history_api,test_report_and_spike}.py`, `docs/feasibility_report.md`, `docs/adr/0004-test-lock.md`.
- Changed: `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, `docs/progress.md`, `docs/interview_notes.md`.
- Not created yet: `docs/data_dictionary_history.md` (written by part b once the dataset is available).

### Verify (PowerShell)

```powershell
uv run python -m trialpulse.feasibility.spike --part all
uv run pytest -q
```

To recompute part f from the cache without any request (a cache miss fails the run instead of fetching):

```powershell
uv run python -m trialpulse.feasibility.spike --part f --offline
```

Then open `docs/feasibility_report.md`. Until dataset access is granted, parts a to e report "blocked". Part g repeats only the 7-page delta pull (the cohort pull comes from the cache), and part f comes entirely from the cache.

### Follow-ups after review (2026-09-23)

- **Secret check.** `.env.example` and `.env` are clean in git status. `.env` is ignored and untracked, and `.env.example` is identical to the Step 1 commit. A search of all 15 commits (every branch, origin, the worktree HEAD) and all commit messages for `hf_` followed by 30 or more characters found **no** match, so no commit was needed.
- **Parts a to d rerun: still blocked.** Hugging Face still reports the access request as awaiting the authors' review, and the dataset viewer API refuses the schema for the same reason. `docs/feasibility_report.md` was regenerated.
- **Part b, per-version columns:** the report now states each of phases, conditions, interventions, arm counts and locations explicitly. Today every one is "not verifiable yet". The dataset card indicates interventions and locations are planned as separate configs. Column matching is now by exact name, so the answer will be exact once part b runs (tested).
- **Phase group stability (from the cache, 0 requests):** changed after version 0 in 5 of 150 trials, 3.3% (Wilson 95% interval 1.4% to 7.6%), so **not under 3%**. The moves were early to mid (twice), and late, early and post-approval to N/A (once each). Groups at version 0: N/A 76, mid 24, early 22, late 15, post-approval 13. The threshold is unchanged.
- **Offline mode:** `--part f --offline` recomputes from the cache with a fetcher that fails on any request. The rerun reproduced every earlier number exactly, and the result keeps the original run's cost (1,123 requests, 56.1 minutes).
- **Approved:** numpy as a direct dependency. It is used by the provisional Step 8 code, which is not on main yet.
- Tests: 71 in `tests/feasibility/`, 88 in total.

Decisions from these follow-ups, approved by Aakrisht on 2026-09-23:

1. A version with no phase recorded maps to the N/A group (1 of 973 cached versions). Any phase combination outside the five groups maps to `other` (none occurred).
2. Offline mode is a flag on part f only.

## Overnight run (2026-09-23)

One line per finished item. On resume, continue after the last line.

- [x] Follow-up 1: CI pinned to ubuntu-24.04; runs on main are never cancelled (commit: chore: pin CI runner).
- [x] Follow-up 2: ADR 0004 (test lock via annotated prereg-v1 tag) written (commit: docs: add ADR 0004).
- [x] Step 2 code: spike parts a to h (feasibility package) with 54 tests on synthetic fixtures; parts a to c ran and are blocked (dataset access pending author approval) (commit: feat(step2): add feasibility spike).
- [x] Part g done: delta pull 6,047 records in 7 pages (16.5 s); cohort pull 420,073 records in 421 pages (648 s); MeSH browse branches absent from API v2, MeSH terms on 77.5% of cohort trials (commit: docs: checkpoint part g).
- [x] Parts d and e ran: blocked (need the dataset).
- [x] Part f done: 150 trials, all 973 versions, 1,123 requests in 56.1 min at 20 per minute or less; no audited list field is under 3% (phases 4.0%, conditions 8.0%, interventions 13.3%, arm count 6.7%, location count 21.3%).
- [x] Part h done: docs/feasibility_report.md drafted, decision not stated (commit: feat(step2): write the draft feasibility report).
- [x] Step 2 report, interview notes and morning report written (commit: docs(step2): report the feasibility spike).

## Break run (2026-09-23, docs only)

One line per finished item. On resume, continue after the last line.

- [x] Item 0: Docker check recorded (Aakrisht's machine: container healthy on 127.0.0.1:15432); Step 1 marked fully verified.
- [x] Item 1: docs/fallback_options.md written (AACT monthly archives from January 2017, other sources, internal-endpoint cost estimate, ranked recommendation), every claim linked to its source (commit f44abc2).
- [x] Item 2: ADR 0006 (phase stated in the versioned title; current-record phase only in a sensitivity analysis) and ADR 0007 (versioned count of all open interventional trials) drafted with status Proposed; the cached part g pull holds no titles, so the title evidence waits for Step 7.
- [x] Item 3: 'Amendments' section appended to the end of CLAUDE.md listing accepted ADRs 0002 to 0005 (9 lines added, none changed).
- [x] Item 4: this report written.

### Report (break run)

**Done (docs only, on main):**

- Step 1 is fully verified: the Docker check passed on Aakrisht's machine (container healthy on 127.0.0.1:15432).
- `docs/fallback_options.md`:
  - AACT has monthly archives from January 2017 (missing: July and September 2021 for dumps, July and August 2021 for flat files, and August 2022 for both), 0.6 to 2.2 GB each.
  - Each AACT snapshot holds phase, conditions and MeSH terms as of that date.
  - Every other public source found is a single date, too sparse, or a wrapper around the internal endpoint.
  - A rebuild through the internal endpoint would take an estimated 109 to 123 days for the full cohort, or 5.2 to 5.9 days for a 20,000-trial sample, at 20 requests per minute.
  - Ranking: AACT first, waiting for Hugging Face second, an internal-endpoint sample third.
- ADR 0006 (phase) and ADR 0007 (competition) drafted as Proposed.
- CLAUDE.md now ends with an Amendments section for ADRs 0002 to 0005.

No code changed, the provisional branch and PR #1 were not touched, and the internal history endpoint was not called. Page fetches were polite, one at a time, and cached under `data/research_cache/`.

**Decisions pending approval:**

1. ADR 0006 parser details: official title with a fallback to the brief title; Arabic and Roman numerals and combined phases; a "not stated" category.
2. ADR 0007 counts a trial as open at L when its latest version on or before L has an open status, SUSPENDED included, as in `config/project.yaml`.
3. The Amendments section opens with "Where an amendment and an earlier section differ, the amendment applies."
4. The AACT download sizes for semiannual (25 to 35 GB) and monthly (150 to 200 GB) snapshots are estimates from the listed file sizes.

**Open questions:**

1. AACT downloads now need a free account. Can you create one and confirm the archives back to 2017 are still downloadable after the redesign?
2. ClinicalTrials.gov's terms page only renders in a browser, so the licence of the registry data was not verified. **Answered by Aakrisht on 2026-09-23:** see the follow-up below.
3. Should someone ask the author of `brbk/clinical_trials_history` about the pending access request? That would be a public post, so it is your call.

### Follow-up: ClinicalTrials.gov terms (2026-09-23)

Aakrisht read the [ClinicalTrials.gov terms and conditions](https://clinicaltrials.gov/about-site/terms-conditions). Anyone publishing or distributing the data should attribute the source as ClinicalTrials.gov, keep the data current, clearly display the date the data were processed by ClinicalTrials.gov, and state any modifications.

New requirement (ADR 0008, accepted; listed in the CLAUDE.md Amendments section). The README, the data card (Step 12) and every dashboard page (Step 16) show:

- the source attribution;
- a "data as of" date (the date ClinicalTrials.gov processed the data);
- a note that TrialPulse normalizes the records and adds derived risk scores.

The README now states the attribution and the note. It will show a data-as-of date once data or results are published.

- [x] Follow-up: terms recorded; ADR 0008 written; README, CLAUDE.md Amendments and this log updated.
