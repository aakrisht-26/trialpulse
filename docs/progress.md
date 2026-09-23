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

Current step: **Step 2, Feasibility spike** (draft done, awaiting review; parts a to e blocked on dataset access). **Step 6** is in progress (reference labels from an adjudicated model panel committed; LLM labeling waits for GROQ_API_KEY). **Step 8** is approved.

| Step | Title | Status |
| --- | --- | --- |
| 1 | Repo skeleton, tooling, CI | Approved and fully verified 2026-09-23 |
| 2 | Feasibility spike (go/no-go) | Draft done, awaiting review (parts a to e blocked) |
| 3 | Warehouse, contracts and live schemas | Not started |
| 4 | Cohort, outcomes and landmarks | Not started |
| 5 | Exploratory data analysis | Not started |
| 6 | Why trials stop (NLP) | In progress: reference labels from an adjudicated model panel committed (ADR 0010); LLM labeling waits for GROQ_API_KEY |
| 7 | Point-in-time features | Not started |
| 8 | Evaluation harness and test lock | Approved 2026-09-23 (the real M0 run waits for Step 4) |
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

## Steps 6 and 8 (merged from PR #1 on 2026-09-23)

Built during the overnight run under the extension's gate. The gate was not met (the dataset could not be downloaded), so only these two items were allowed, as code and tests. The files were committed to branch `provisional/step6-step8` on 2026-09-23 from main at `bd87054`, reviewed in PR #1, approved by Aakrisht, and merged into main the same day (merge commit `0d2248c`). The branch was then deleted. Steps 3, 4, 5, 7, 9 and 10 were not started: the gate blocked them, and Steps 9 and 10 also need Steps 4 and 7.

### Review of PR #1 (2026-09-23)

**Approved as proposed:**
- Step 8 decisions 1 to 5. The contract now states that `event_date` holds the censoring date when `event` is 0.
- Step 6 decisions 3 and 4.
- ADR 0008 and its Amendments line (both on main).

**Changes requested, and done on this branch:**

1. The gold sample comes from the API v2 cohort's current records: TERMINATED and WITHDRAWN trials with a non-blank why_stopped (`2a12318`).
2. Texts are normalized and deduplicated before sampling, so each text appears once and in only one split (`2a12318`).
3. Labels are keyed by `(nct_id, text_sha256)`, with a `source` column holding the pull date (`2a12318`).
4. The new stop-year rule, and a dev/test split stratified by status (`2a12318`).
5. The real sample is built, and the app loads all 400 texts. It shows "0 of 400 labeled", checked with Streamlit's test harness on the real file, with labels written to a throwaway file, so nothing was labeled.
6. Differential tests against lifelines and scikit-learn (`e5d4d52`).
7. The lock requires `prereg-v1` on origin at the same commit, tested with a local bare repository as origin. This is ADR 0009 (`72e054e`, tightened in `98824c5`).
8. The bootstrap fails with a clear error above 1% invalid resamples and always reports the count (`67e1701`, with context added in `3d4f9db`).

**Internal review.** Three independent reviewers checked the changes against the list above. Their confirmed findings are fixed:
- a bootstrap failure now names its origin, horizon, slice and metric, and the CLI exits with code 3 (`3d4f9db`);
- origin's tag must be the same annotated tag object, not only the same commit (`98824c5`);
- the pull date is fixed when a pull starts, so a resumed pull keeps it, and the test checks the full cohort filter (`cb60d8f`);
- the API client scrubs personal data before caching, and tests now cover no-retry on a 404 and a mid-pull resume (`5dfc0a7`);
- this report is updated.

### Step 6 (partial): Why trials stop

Status: **in progress**. The gold set holds reference labels from an adjudicated model panel (ADR 0010), committed before any prompt work. LLM labeling and distillation wait for GROQ_API_KEY.

**Built:**

- `src/trialpulse/nlp/taxonomy.py`: the eight labels, their definitions and tie-break rules. The operational and scientific groups are read from `config/project.yaml`.
- `docs/labeling_guide.md`: definitions, paraphrased examples, seven tie-break rules, and how texts and labels are stored.
- `src/trialpulse/ingest/ctgov_api.py`: a minimal API v2 client. It stays at 40 requests per minute or less, retries with backoff, paginates by cursor, caches pages, and scrubs personal data before caching.
- `src/trialpulse/nlp/gold.py`:
  - pulls the current records of the cohort's early stops;
  - normalizes and deduplicates the why_stopped texts;
  - draws a seeded sample of 400, stratified by status and stop year;
  - splits it 100 dev and 300 test, stratified by status;
  - stores labels in `labels/gold_labels.csv` as `nct_id`, `text_sha256`, `split`, `label`, `source` and `labeled_at`, with no text.

  Command: `uv run python -m trialpulse.nlp.gold --build`.
- `src/trialpulse/nlp/labeling_app.py`: Streamlit app. It shows one text at a time, saves each label at once, can revise earlier labels, and shows the disclaimer and the text source.
- Tests: 21 in `tests/nlp/` (including an AppTest run) and 7 in `tests/ingest/`.
- Dependency: streamlit (locked stack).

**The real sample** (pulled 2026-09-23, API data timestamp 2026-09-22T09:00:04, 40 requests):

- 38,482 early stops in the cohort, 35,198 with a non-blank reason, 24,534 distinct normalized texts;
- 400 sampled: 272 TERMINATED and 128 WITHDRAWN;
- dev: 68 TERMINATED and 32 WITHDRAWN; test: 204 and 96;
- all 400 hashes are distinct and match their texts;
- the texts are in `data/nlp/gold_sample.csv` (gitignored).

**Acceptance (Step 6):**

| Criterion | Result |
| --- | --- |
| Gold-test results for the LLM and the distilled model | Not started: the reference labels are committed; needs GROQ_API_KEY (not set) |
| LLM macro-F1 >= 0.80 on gold-test | Not started |
| Gold-test never used for tuning | Enforced: test labels committed before any prompt work, and a test fails if a test text appears in a prompt file |
| The 400-text gold set labeled | Met: 400 reference labels from an adjudicated model panel (ADR 0010), `labels/gold_labels.csv` |

**Decisions from the review (Aakrisht, 2026-09-23):**

1. The gold texts come from the current API v2 records, not the history dataset. This replaces the overnight decision to use event-version texts.
2. Texts are normalized (lowercase, collapsed whitespace, surrounding punctuation trimmed) and deduplicated before sampling.
3. Labels are keyed by `(nct_id, text_sha256)` plus a `source` column with the pull date.
4. **Stop-year rule:** the year of the actual completion date when present, otherwise the year of the last update post date. The dev/test split is also stratified by status. This replaces the overnight decision on strata and the split.
5. Approved: file locations; `efficacy` covers early proof of benefit; the app hides the split.

**Decisions approved (Aakrisht, 2026-09-23, PR #1 review):**

1. **A fresh pull instead of the part g cache.** The part g cache has the why_stopped texts but no completion date or completion type, which the stop-year rule needs. So the approved fresh pull was used: the cohort's early stops only, 40 requests at 40 per minute. Compared with part g, it has the same 38,482 trials, with 0 status differences and 0 why_stopped differences.
2. **Location of the API client.** It is in `src/trialpulse/ingest/ctgov_api.py`, the Step 14 location, because production code may not import the feasibility spike (a test enforces this). It repeats a little of the spike's code by design.
3. **Repeated texts.** The trial with the lowest NCT ID represents the text, and the app shows that trial's own wording.
4. **Normalization details.** Only whitespace and Unicode punctuation (category P) are trimmed at the ends. Symbols such as "<" or "+" stay.

**Also approved** (the three remaining decisions, approved later on 2026-09-23):

5. **Source value.** It reads "ctgov-api-v2 pulled YYYY-MM-DD". The pull date and data timestamp are saved in `pull.json` when the pull starts.
6. **Dev share per status** uses proportional allocation, as above.

**Decision (Aakrisht, 2026-09-23): keep the 14 texts with a stop year before 2008** (1996 to 2007). They are trials registered after they had already ended, and their reasons are valid labeling material. Step 4's at-risk rule keeps such trials out of the landmarks, since they are never open after registration.

**Recorded for later in Step 6:** every normalized gold text, matched by `text_sha256`, must be excluded from the LLM-labeled training sample, and a test must enforce it.

### Step 6 labeling plan (changed by Aakrisht on 2026-09-23)

**Superseded later the same day by ADR 0010** (reference labels from an adjudicated model panel, below). Kept as a record.

The goal is to save labeling time without touching the test set's independence.

- **Order:** the app serves all 300 test texts first, then the 100 dev texts.
- **Blind test texts:** no suggestion is ever shown for a test item. Three things enforce it:
  - suggestions load for dev items only;
  - `save_label` refuses `assisted` for any item that is not dev;
  - an app test uses a suggestion file that also holds test-item rows and checks that nothing is shown for them.
- **Dev suggestions:** Claude suggested labels for the 100 dev texts only, saved in `data/nlp/dev_suggestions.csv` (gitignored). The app shows a suggestion on dev items, and Aakrisht confirms or changes it. `labels/gold_labels.csv` gains an `assisted` column.
- **One click per text:** clicking a label saves it and moves on. **Back** revises an earlier text, and **Next unlabeled** returns to where labeling stopped.

**How the suggestions were made:**

1. Three independent labelers followed `docs/labeling_guide.md` and worked from a scratch file holding the 100 dev texts only; no test text was shown to them.
2. 95 of the 100 texts were unanimous, 5 were decided 2 to 1, and none had three different labels. Pairwise agreement was 96 to 97 of 100.
3. Claude read all 100 and adjudicated the 5 split cases:
   - #43 "Change of Trial Sponsor": administrative, keeping the majority;
   - "no patients included": other, keeping the majority and matching the unanimous "Study never recruited";
   - "The study did not enroll participants...": accrual, keeping the majority;
   - an interim analysis that "will not adequately inform the clinical development programme": efficacy, keeping the majority (borderline);
   - "postponed pending the completion of other ongoing pre-clinical and clinical work": **business**, overriding the majority's other, because deferring a trial behind other development work is a strategic program decision.
4. The suggestions are: accrual 32, other 20, administrative 18, business 11, efficacy 9, funding 6, covid19 2, safety 2.

**Verified on the real files, without labeling anything:**

- positions 1 to 300 are test texts and 301 to 400 are dev texts;
- no test item has a suggestion;
- the app serves a test item first, with no suggestion shown;
- a dev item shows its suggestion, matching the file;
- no labels file was written.

**Decisions approved (Aakrisht, 2026-09-23, labeling plan):**

1. `assisted` is `true` when a suggestion was shown for the item at the time its label was saved. Revising a dev item keeps it `true`, and test labels are always `false`.
2. The suggestions came from three independent labelers plus Claude's adjudication, not a single pass.
3. A **Next unlabeled** button sits beside **Back**.
4. The earlier decision that the app hides the split no longer holds in practice: the order and the suggestions reveal the split, by design of this plan.

### Step 6 reference labels (ADR 0010, 2026-09-23)

Aakrisht decided not to label the gold set by hand. It now holds **reference labels from an adjudicated model panel**, never described as human-labeled (ADR 0010). The full run record, with the exact prompts, is in `docs/reference_panel.md`.

**Built:**

- `docs/adr/0010-reference-labels-from-an-adjudicated-model-panel.md`, and its line in the CLAUDE.md Amendments section (nothing else in CLAUDE.md changed).
- `docs/labeling_guide.md`: the careful-annotator rules in "How to label", and "How the reference labels are made". The label definitions and tie-break rules are unchanged. Committed in `b076f71` before the panel ran.
- `src/trialpulse/nlp/panel.py`:
  - prepares the panel inputs (opaque ids, text only);
  - reads and checks the votes, adjudications and consistency relabels;
  - resolves each text's label and panel outcome;
  - writes `labels/gold_labels.csv` and the report;
  - `check_graded_model` refuses a Claude model as the graded LLM.
- `src/trialpulse/nlp/holdout.py` and `src/trialpulse/nlp/prompts/README.md`: the guard that keeps test texts out of the LLM prompt files.
- `src/trialpulse/nlp/gold.py`: the labels file gains `method` and `panel_outcome`; `write_labels` is shared; `save_label` writes method `manual` and refuses to overwrite a panel label.
- `src/trialpulse/nlp/labeling_app.py`: now an optional review tool for the dev texts only (it never shows a test text), writing to `data/nlp/review_labels.csv`.
- `labels/gold_labels.csv`: 400 reference labels (ids, hashes and labels only, no text), `assisted` false, method `model-panel-v1`.
- `docs/reference_panel.md`: the run record.
- Tests: `tests/nlp/test_panel.py` (17, including an end-to-end run of every command) and `tests/nlp/test_holdout.py` (25, including the real check on the committed test hashes and 16 ways of embedding a text in a prompt), two new tests in `tests/nlp/test_gold.py` and one replaced (the test-first serving order became the dev-only review order), and `tests/nlp/test_labeling_app.py` rewritten for the dev-only review tool. The suite has 225 tests.

**The protocol as run:**

1. **Labeling.** 12 labeler agents (`claude-opus-5-5`) worked on 4 batches of 100 texts, 3 per batch, each labeler with its own seeded order. Each labeler returned a label, the deciding words and a one-line justification for every text.
2. **Adjudication.** A fresh adjudicator agent that labeled nothing decided the 25 splits from the text, the guide and the three justifications, and recorded a rationale for each.
3. **Consistency.** After that, a fresh labeler relabeled a seeded 10% (40 texts) under the labelers' conditions.
4. **Isolation.** Every agent was shown only the guide and the text. An audit of all 14 transcripts found only two reads per agent (the guide and its own input file) and no other tool calls. Claude, orchestrating, never viewed a test text or test label: the workflows and collectors returned counts only.
5. **Order.** The test labels are committed here, before any work on the LLM prompt. No prompt file exists yet.

**Report:**

| Split | Texts | Unanimous | Majority | Adjudicated |
| --- | --- | --- | --- | --- |
| All | 400 | 375 (93.8%) | 22 (5.5%) | 3 (0.8%) |
| Dev | 100 | 96 | 4 | 0 |
| Test | 300 | 279 | 18 | 3 |

| Label | All | Dev | Test |
| --- | --- | --- | --- |
| accrual | 106 | 34 | 72 |
| other | 86 | 20 | 66 |
| administrative | 66 | 17 | 49 |
| business | 42 | 10 | 32 |
| funding | 33 | 6 | 27 |
| efficacy | 31 | 9 | 22 |
| covid19 | 24 | 2 | 22 |
| safety | 12 | 2 | 10 |

- **Consistency agreement:** 39 of 40 (97.5%), Cohen's kappa 0.969.
- **Pairwise agreement** between labelers of the same text: 95.8% (1,150 of 1,200 pairs). All 25 splits were 2 to 1.
- **Deciding words** were quoted verbatim, as whole words, in 1,200 of 1,200 answers.

**Adjudicator decisions (examples).** The adjudicator decided the 25 split texts: 4 dev and 21 test. The 4 dev decisions are below. The requested 10 are in `data/nlp/panel/adjudicated_examples.md` (gitignored), written by `uv run python -m trialpulse.nlp.panel --report --examples 10`: these 4, the 3 test texts the adjudicator overturned, and a seeded 3 of the 18 test texts where it kept the majority. Claude did not open that file, so the later prompt work never sees a test text.

1. p018, votes safety, safety, accrual; decision **safety** (majority). Text: "Due to unproven issues associated with hydroxychloroquine use and safety, further complicated by media and political misinformation which in effect rendered all global studies on HCQ to stop enrolling participants." Rationale: the stated cause is safety concerns about the study drug; "unproven" qualifies them but does not negate them. "Stop enrolling" is the halt the concerns caused, not a recruitment failure, and COVID-19 is never mentioned.
2. p170, votes other, other, administrative; decision **other** (majority). Text: "The clinical trial number for this study was previously assigned. This was done in error." Rationale: this describes a registry numbering error, not a reason a trial stopped; no regulatory or paperwork problem stopped a study.
3. p326, votes business, efficacy, efficacy; decision **efficacy** (majority). Text: an interim analysis showing the study "will not adequately inform the clinical development programme ... in the way that the study was intended", with "no concerns regarding participant safety". Rationale: an interim result showing the study cannot meet its objective is a futility-type finding (tie-break 4); safety is explicitly negated; tie-break 3 gives a data-driven named reason its own label instead of business.
4. p365, votes business, business, other; decision **business** (majority). Text: "Due to changes to the standard of care within the proposed market for CS-7017." Rationale: a market and strategy reason for the product's development is business; the reason is stated and placeable even though "sponsor" does not appear.

**Finding for the prompt work.** A count-only check found that 2 test texts appear word for word among the example phrases in `docs/labeling_guide.md` (generic phrases; the guide predates the sample). The LLM prompt therefore cannot copy every guide example verbatim; the guard names any leak by hash prefix only.

**Decisions made in this step (pending approval):**

1. **Panel outcome definitions.** Every split goes to the adjudicator, as the protocol says. `majority` means the adjudicator kept the two-labeler label; `adjudicated` means it chose a label no two labelers gave. Alternative: `majority` for every 2-to-1 split whatever the adjudicator decided, and `adjudicated` only for three-way splits.
2. **Panel composition.** All labelers are independent agents of one model (`claude-opus-5-5`) in separate contexts, 100 texts per agent, with different seeded orders. Alternative: mix Claude models for more diverse errors, at some cost in label quality.
3. **One adjudicator, at effort `xhigh`,** for all 25 splits. It saw the other split texts in its file, and one rationale refers to another split item.
4. **Consistency sample.** A seeded 10% of all 400 texts (31 test, 9 dev; 5 were splits), compared with the final reference labels.
5. **The labeling app is kept** as an optional review tool for the dev texts only, writing to `data/nlp/review_labels.csv`, and `save_label` refuses to overwrite a panel label. Alternative: delete the app, its tests and the dev suggestions.
6. **Prompt guard.** Prompts live in `src/trialpulse/nlp/prompts/`, and every file there is checked, as written, with escapes decoded and without Markdown line prefixes. In CI, every run of words up to 250 characters (the why_stopped limit) is hashed against the 300 committed test hashes, also with characters fused to its first or last word cut away. Where the gitignored sample is present, the normalized test texts are also searched for directly. It catches verbatim copies, not paraphrases. The Step 6 labeler must also run it on the rendered prompt.
7. **Model-family check.** `check_graded_model` refuses any model id containing "claude" or "anthropic".
8. **The 10 examples** (above): 4 dev in this report; the 6 test examples only in a gitignored file that Claude did not open.

**Internal review.** Three independent reviewers checked the code and docs before the commit, without access to `data/` or `labels/`. Their confirmed findings are fixed:

- **The prompt guard missed common embeddings.** The CI hash check split on whitespace only. It missed a test text inside compact JSON, backticks, table pipes, links, arrows, `key="..."`, escaped strings (`\n` in JSON or Python) and wrapped blockquotes. It now checks decoded and Markdown-stripped variants and cuts fused characters, and a test covers all 16 cases with the hash check alone.
- **The guard only saw files.** `prompt_leaks` now checks any rendered prompt. The prompts README and ADR 0010 require the Step 6 labeler to run it on the prompt it sends, and warn that the labeling guide holds 2 test texts among its example phrases.
- **The review app opened test texts.** It served the 300 test texts first. It now shows dev texts only, and a test keeps a test item and its suggestion in the sample and checks that the app never shows them.
- **The 10 examples were not reproducible.** A scratch script chose them. The selection is now `decision_examples` in `panel.py`, behind `--report --examples 10`, and it picks the same 10 ids.
- **Consistency relabels were not validated.** `check_consistency` now requires exactly the seeded sample, one valid label per text, and a labeler that was not on that text's panel. The real relabels pass.
- **The verbatim check matched parts of words.** It now matches whole words only. The real result is unchanged (1,200 of 1,200).
- **Untested paths.** New tests cover every command end to end (including a panel with no splits), the report's kappa and shares, a non-zero verbatim share, stray adjudications, input-file determinism and old-schema rows in `write_labels`.
- **Two wording fixes:** an interview note wrongly said the agents returned counts only, and the report used "adjudicated" for all 25 splits, clashing with the panel outcome of that name.

The labels file is byte-identical after the fixes.

**Recorded for later in Step 6:** the LLM labeler builds its prompt only from `src/trialpulse/nlp/prompts/` and dev items, calls `check_graded_model` on its model id, and a test runs `prompt_leaks` on the rendered prompt against the committed test hashes. The prompt must not paste the labeling guide whole.

### Step 8: Evaluation harness and test lock

Status: **approved** by Aakrisht on 2026-09-23. The real M0 run on development origins waits for the Step 4 landmark table.

**Built:**

- `src/trialpulse/eval/ipcw.py`: Kaplan-Meier, the censoring survival G, and case and control labels with IPC weights at a horizon (a scalar or one per row).
- `src/trialpulse/eval/metrics.py`:
  - IPCW AUC (O(n log n), ties count half) and IPCW Brier score;
  - lift at the top fraction;
  - calibration slope and intercept by IPCW logistic recalibration;
  - a calibration table against Aalen-Johansen by risk decile.
- `src/trialpulse/eval/bootstrap.py`: cluster bootstrap over trials with percentile intervals. It always reports `invalid_resamples`, and raises `BootstrapError` when more than 1% of resamples are invalid.
- `src/trialpulse/eval/lock.py`: the lock of ADR 0004 and ADR 0009. It checks:
  - the flag;
  - the annotated local `prereg-v1` tag;
  - the `Registered:` date;
  - the placeholder list;
  - the ancestor rule;
  - that the same annotated tag object is on origin at the same commit.

  It returns the hashes and only reads git state.
- `src/trialpulse/eval/walkforward.py`:
  - origins, with training rows censored at T;
  - evaluation rows with T <= L < T + 12 months;
  - per-row calendar-month horizons;
  - metrics pooled and per landmark index, with intervals, written as JSON.

  The lock is checked before any data is loaded. A bootstrap failure names its slice and metric.
- `src/trialpulse/models/aalen_johansen.py`: the Aalen-Johansen CIF and M0 (one curve per stratum, pooled curve for unseen strata).
- `src/trialpulse/dates.py`: calendar-month arithmetic with month-end clamping.
- Tests: 60 in `tests/eval/`. They include differential tests on seeded random data with censoring and competing events:
  - Kaplan-Meier matches lifelines to 1e-12, with and without tied times;
  - Aalen-Johansen matches lifelines to 1e-10 on continuous times;
  - IPCW AUC matches scikit-learn's `roc_auc_score` with the IPCW weights to 1e-12;
  - the IPCW weights and Brier score match a direct computation on lifelines' Kaplan-Meier.
- Dependencies: numpy (approved 2026-09-23); lifelines and scikit-learn (dev group, locked stack).

**Acceptance (Step 8):**

| Criterion | Result |
| --- | --- |
| Perfect scores give AUC 1 | Met: `test_perfect_scores_give_auc_one` |
| Random scores give about 0.5 | Met: 20,000 censored rows, within 0.02 |
| No censoring makes IPCW equal to unweighted | Met: AUC and Brier (`test_no_censoring_makes_ipcw_equal_to_unweighted`) |
| Constructed censoring patterns give known values | Met: hand-worked example, AUC 0.625 against 2/3 unweighted, Brier 0.253 |
| M0 runs end to end on development origins | **Not met on real data**: it needs the Step 4 landmark table. It runs end to end on synthetic landmark rows |
| Locked origins refused without the flag and a committed preregistration | Met. Refused for test, stress, all, 2018, 2019 and 2020, with and without the flag, before any data is read. Also refused: the Step 1 stub, a lightweight tag, placeholders, a bad date, a non-ancestor tag, a missing origin, a tag not pushed, a lightweight tag on origin, a tag on origin at another commit, a tag re-created after publishing, and the CLI |

No `prereg-v1` tag was created in this repository, nothing was pushed to a tag, `--unlock-test` was never passed, and no locked origin was evaluated. The lock tests use throwaway repositories, including bare ones as origin, under pytest's temporary directory.

**Decisions approved (Aakrisht, 2026-09-23):**

1. Rows censored at or before the horizon get weight 0. Controls are rows event-free at H (weight 1/G(H)) and completions by H (weight 1/G(T-)).
2. Calibration slope and intercept come from an IPCW-weighted logistic recalibration on logit(p). The calibration table uses the median per-row horizon.
3. Horizons are per-row calendar months converted to days. Training outcomes dated on or after the origin are censored at the origin.
4. Kaplan-Meier, Aalen-Johansen and the weighted AUC are small numpy implementations. They are now also checked against lifelines and scikit-learn.
5. The input contract for Step 4 is one row per (trial, landmark), in `data/cohort/landmarks.parquet`, with:
   - `trial_id`, `landmark_index`, `landmark_date`, `event` and `event_date`, plus feature columns;
   - `event_date` holding the date of the event when `event` is 1 or 2, and the censoring date when `event` is 0.

Also from the review: ADR 0009 (the tag must be on origin), and the 1% bootstrap rule.

**Decisions approved (Aakrisht, 2026-09-23, PR #1 review):**

1. **Dependency group.** lifelines and scikit-learn are in the dev group, since only tests use them so far; the first model step that needs them moves them to runtime dependencies. lifelines requires pandas below 3, so pandas moved from 3.0.6 to 2.3.3. The Streamlit tests pass.
2. **ADR number.** ADR 0009 is numbered after main's 0005 to 0008. On merge, ADR 0009 is added to the Amendments section of CLAUDE.md.
3. **Stricter origin check.** The lock requires the same annotated tag object on origin, not only the same commit. That is stricter than requested; the review found that a re-created tag would otherwise pass with an unpublished hash.

**Also approved** (the three remaining decisions, approved later on 2026-09-23):

4. **Failure behavior.** A bootstrap failure stops the walk-forward run with a message naming origin, horizon, slice and metric. The CLI exits with code 3.

**Logged for later (Step 11):** a sensitivity check that estimates the censoring weights separately by sponsor class.

### Verify (PowerShell, on main)

```powershell
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q
uv run python -m trialpulse.nlp.gold --build
uv run streamlit run src/trialpulse/nlp/labeling_app.py
```

`gold --build` reads the cached pull under `data/nlp/api_pull/` (on a fresh clone it makes about 40 requests). The app shows "0 of 400 labeled".

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
