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

Current step: **Step 4, Cohort, outcomes and landmarks**, built, independently reviewed and waiting for Aakrisht's review (2026-10-06). Step 5 (EDA) is being built, unreviewed, on branch `provisional/step5-eda` under the extension of 2026-10-05; main holds nothing of it. **Step 6** is in progress (LLM test result in: macro-F1 0.842; Aakrisht runs the 10,000-text LLM sample labeling daily; the distilled model's test scoring waits for it). **Step 8** is approved.

| Step | Title | Status |
| --- | --- | --- |
| 1 | Repo skeleton, tooling, CI | Approved and fully verified 2026-09-23 |
| 2 | Feasibility spike (go/no-go) | **GO** (2026-09-30, ADR 0011): every automated criterion met; ADRs 0006, 0007 and 0011 accepted |
| 3 | Warehouse, contracts and live schemas | Approved 2026-09-30 (ADR 0012 accepted); verified by Aakrisht except the two rebuilds, run in this session on the same machine |
| 4 | Cohort, outcomes and landmarks | Built and independently reviewed 2026-10-06, waiting for review (ADRs 0013 and 0014 accepted, ADR 0015 proposed, 3 open questions) |
| 5 | Exploratory data analysis | Not started |
| 6 | Why trials stop (NLP) | In progress: LLM scored on test (macro-F1 0.842); Aakrisht labels the sample daily (1,375 of 10,000 on 2026-09-26); distilled model provisional |
| 7 | Point-in-time features | Not started |
| 8 | Evaluation harness and test lock | Approved 2026-09-23; M0 ran end to end on the development origins on the real cohort (Step 4), which closes its last criterion |
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

## Guidance for later steps (recorded 2026-09-30)

Aakrisht's guidance from the Step 2 review. Each item is formalized with an ADR when its step starts; until then it is guidance, not a locked definition.

1. **Step 4: the population is decided point-in-time.** A landmark row exists only if the version effective at that landmark says INTERVENTIONAL. This covers the 7,409 trials whose study type changed between versions. (Step 2's counts use each trial's latest version, which is right for the spike only.)
2. **Steps 4 and 14: do not rely on the registry's UNKNOWN status**, since it can appear without a new version. Apply the registry's own rule from versioned fields instead: an open trial whose completion date has passed and whose status has not been verified for 2 years is treated as unknown, and censored at its status verified date. Use the same rule offline and live.
3. **Step 7: evaluate the new `interventions` config.** If it is per version, intervention type becomes a candidate feature, adopted through an ADR.
4. **Step 7: individual sponsors fall back to the class-level rate** (Aakrisht, 2026-09-30, Step 3 instruction). The warehouse stores no name or key for an individual sponsor (class INDIV, and, under ADR 0012, a person's name with a degree title under another class), so the sponsor track record feature uses the smoothed class-level early-stop rate for them, and their prior-registration count is not computed per person.
5. **Step 7: sponsor renames are aliases, not new sponsors** (Aakrisht, 2026-09-30, Step 3 review). When the registry renames a trial's lead sponsor without a new version (the Step 3 parity sample found two, for example Endo Pharmaceuticals to "Endo USA Inc., a Keenova Therapeutics Company"), names that replace each other on the same trial are treated as aliases of one sponsor, so a rename does not start a new track record. Formalized with an ADR in Step 7.

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

Date: 2026-09-23 (unattended overnight run); parts a to e with the dataset on 2026-09-29 (below). Status at first: **draft report written, decision not stated, parts a to e blocked on dataset access**.

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

### Parts a to e with the dataset (2026-09-29)

Hugging Face approved access to `brbk/clinical_trials_history`. Parts a to e ran on the real dataset, and `docs/feasibility_report.md` was regenerated. The report states no GO or NO-GO.

**Access.** One read-only call (`HfApi.auth_check`, the token never printed): granted.

**Part a: download.** The latest (and only) tag, `v2026.09.26` (commit `3007d00`), is pinned in `config/project.yaml`. All 9 core Parquet files downloaded to `data/raw/history/v2026.09.26/core/`: 1,020,446,428 bytes, matching the published sizes. The download took about 16 minutes and did not stall. The dataset now also publishes an `interventions` config besides `core`; adopting it would need an ADR (CLAUDE.md Section 7).

**Part b: profile.** 4,444,542 versions across 604,583 trials, 0 duplicate (nct_id, nct_version) rows, 96 columns (all listed with types and null rates in `docs/data_dictionary_history.md`), no expected column missing. `status_verified_date` and `eligibility_criteria` exist as columns.

Per-version columns in the core config, field by field:

| Field | Per-version column in core | Evidence |
| --- | --- | --- |
| phases | **No** | no column name mentions a phase |
| conditions | **No** | no condition, MeSH or keyword column |
| interventions | **No** | only `intervention_model` and its description (the study design) and three FDA-regulation flags; the separate `interventions` config is not part of core |
| arm counts | **No** | no arm or group column |
| locations | **No** | no location, site, facility, country or city column |

All 96 columns are scalar (no list, struct or map types). What this means for ADRs 0006 and 0007 (proposed then; both accepted on 2026-09-30):

- **ADR 0006 (phase):** its condition is met, because core has no per-version phase column. Its title-based alternative is feasible: `official_title` and `brief_title` are versioned core columns.
- **ADR 0007 (competition):** its premise holds. Core has no per-version conditions or MeSH columns, so the therapeutic area cannot be measured point-in-time from core.

**Part c: cohort counts.** 420,582 cohort trials; 38,621 early stops; why_stopped present for 91.5% of them; last_update_post_date on 100.00% of versions. Latest-status totals: completed 226,294, open 99,710, unknown 55,957, terminated 25,810, withdrawn 12,811.

**Data cutoff: 2026-09-25** (the maximum last_update_post_date), recorded in `config/project.yaml`. The live system would need to catch up on the API v2 updates posted from the cutoff to today, 2026-09-29: **4 days, about 0.1 months**.

**Part d: automated check.** 200 seeded cohort trials, all comparable (none updated after the cutoff). Their official records were fetched with one API v2 call per trial on 2026-09-29, after the cutoff. Agreement: **99.3%** (1,192 of 1,200 field comparisons); 194 trials agree on every field. By field: overall_status 194, start_date 200, primary_completion_date 199, enrollment_count 199, lead_sponsor_class 200, why_stopped 200 (each of 200). All 8 mismatches have a known, systematic cause, labeled in the report; they still count in the strict share:

- 5 trials: an open trial the registry has since marked UNKNOWN, with no new version posted;
- 1 trial (3 fields): the dataset holds a version newer than the official record shows.

**Part e: manual check checklist.** 10 of the part d trials (seeded), in the report: NCT ID, a link to the Record History page, and the version 0 values to compare (submitted date, status, study type, first posted, start, primary completion, enrollment and sponsor class, with dates at the dataset's precision). Aakrisht records pass or fail in `docs/feasibility_manual_check.csv`.

**Automated GO criteria (numbers only; no decision stated):**

| Criterion | Threshold | Measured |
| --- | --- | --- |
| Cohort trials | at least 200,000 | 420,582 |
| last_update_post_date present on versions | at least 99% | 100.00% |
| Automated agreement | at least 95% | 99.3% |
| Early stops in the cohort | at least 20,000 | 38,621 |
| why_stopped present among early stops | at least 80% | 91.5% |
| API v2 delta pull end to end | works | 6,047 of 6,047 records (part g, 2026-09-22) |
| Manual check | at least 9 of 10 | pending (Aakrisht) |

**Findings for later steps (reported, not acted on):**

1. **UNKNOWN status is set without a new version (Steps 4 and 14).** The registry marks a lapsed open trial UNKNOWN without posting a version, so the version history can lag it, and a delta pull filtered on LastUpdatePostDate never sees the change. Step 4's UNKNOWN rule and Step 14's daily job must allow for this.
2. **The dataset can be ahead of the official record (Step 14).** One sampled trial has a dataset version (posted 2026-05-13) that the official API does not show.
3. **HTML entities in dataset text (Step 3).** An earlier sample showed why_stopped texts with entities such as `&#x27;` where the API has plain characters (and the API has backslash escapes such as `\>`). Text normalization must unescape both.
4. **Personal data beyond the 4 named investigator columns (Step 3).** Email addresses appear in free-text columns (most in the IPD-sharing fields), 4,086 trials have a sponsor name with a degree title, and 567 have an INDIV-class sponsor. Step 3 must drop the named columns, remove emails from text, and not store or display individual sponsors' names. The report gives counts only.
5. **Schema drift (Step 3).** `disp_first_submit_qc_date`, `fdaaa801_violation`, `is_ppsd` have a different Parquet type in some files; `version_holder`, `expanded_access_status_for_nct_id`, `unposted_responsible_party`, `first_mcp_post_date`, `first_mcp_post_date_type`, `estimated_results_first_submit_date` are always empty. Step 3 must type columns explicitly.
6. **Study type changes across versions (Step 4).** 7,409 trials changed study type between versions. The spike uses each trial's latest version; which version defines the population is for Step 4 to settle.
7. **MeSH browse branches are not returned by API v2.** The report now marks that path as not available to ingest.

**Review of 2026-09-30 (Aakrisht).** Approved: the four decisions from the report (fresh records for part d, the cohort from each trial's latest version for Step 2's counts, the cutoff written to config with the refusal on a mismatch, and the mismatch labels with the strict share unchanged). ADRs 0006 and 0007 are **accepted** and in the CLAUDE.md Amendments section. Guidance for Steps 4, 7 and 14 is recorded in "Guidance for later steps" near the top of this file. `docs/feasibility_manual_check.csv` is Aakrisht's to fill in and was not touched.

**Part i: version history against the official change logs (2026-09-30, no new request).** The part f cache holds the official change log of each of its 150 trials, with every version's number, date and status. Part i compares them with the dataset, reading the cache only:

- the version lists match for 150 of 150 trials (973 versions on each side, numbered the same way);
- each version's submitted date agrees for 973 of 973 versions. The change log dates versions by their submission and holds no posted date, so the dataset's posted date (last_update_post_date) cannot be checked from it;
- each version's status agrees for 971 of 973 versions. The 2 differences are the latest version of single-version trials, which the change log shows as UNKNOWN (the registry's status, set without a new version) and the dataset shows with the open status that was submitted.

**Internal review.** Two independent reviewers checked the code and the report; their confirmed findings are fixed:

- **Part d could use stale records.** The bulk-pull freshness check trusted part g's run timestamp, which a rerun refreshes although the bulk pages come from a cache. Part d now always fetches one record per sampled trial, cached per pinned revision, and refuses records dated before the cutoff.
- **The cohort was taken from the latest interventional version**, not the latest version, contrary to the function's docstring (422,927 trials and 38,691 early stops before the fix). It now takes each trial's latest version first; the sample and checklist changed with it.
- **One UNKNOWN label was wrong.** It now applies only when the API shows UNKNOWN for a trial whose dataset status is open.
- **The personal-data note named only 4 columns.** The profile now counts emails in free text and degree-titled and INDIV-class sponsors, and the report says what Step 3 must do.
- **Smaller fixes.** The data dictionary shows type drift by file and always-empty columns; the dataset parts refuse a download of a revision other than the pinned one; the part g table marks paths that return nothing; the manual-results file follows the chosen trials without overwriting recorded results.

### Part e automated, and the GO decision (2026-09-30)

Aakrisht replaced the manual check with an automated one (**ADR 0011, accepted**; its line is in the CLAUDE.md Amendments section). The Record History page is fed by the same internal history API the dataset was built from, so a script checks the same source as a person, on a larger sample.

**Part e: automated version check.** 50 seeded cohort trials with at least 2 versions; for each, version 0 and one seeded later version, fetched from the internal history endpoint (verification only, 20 requests per minute or less, projections cached). 150 requests (50 change logs not already cached, 100 versions), about 7.5 minutes, 0 failures.

- Agreement: **99.9%** (899 of 900 fields across 100 versions). Submitted date, study type, first posted, start date, primary completion, enrollment count, enrollment type and sponsor class agree for 100 of 100 versions; status for 99 of 100.
- The 1 mismatch has a known cause: NCT05306652's latest version (3) is RECRUITING in the dataset and UNKNOWN on the registry, which set UNKNOWN without posting a new version (the same finding as part d). 0 mismatches without a known cause.

**Criteria (all met):**

| Criterion | Threshold | Measured |
| --- | --- | --- |
| Cohort trials | at least 200,000 | 420,582 |
| last_update_post_date present on versions | at least 99% | 100.00% |
| Automated agreement (part d) | at least 95% | 99.3% |
| Automated version check (part e, ADR 0011) | at least 95% of fields across 100 versions, every mismatch with a known cause | 99.9%, 0 without a known cause |
| Early stops in the cohort | at least 20,000 | 38,621 |
| why_stopped present among early stops | at least 80% | 91.5% |
| API v2 delta pull end to end | works | 6,047 of 6,047 records |

**Decision: GO**, recorded in `docs/feasibility_report.md` by the rule in ADR 0011. No current-record-only field passes the under-3% stability rule (ADRs 0006 and 0007 settle phase and competition).

**The manual CSV.** `docs/feasibility_manual_check.csv` is now an optional human spot check that no criterion depends on. It was not modified (its SHA-256 is the same before and after the run); the report shows its status for information only.

**Decision flagged for approval.** The sample is drawn from cohort trials with at least 2 versions, so that every trial contributes version 0 and a later version, as ADR 0011 records. Single-version trials are covered by part d, which compares each trial's latest version.

**Files touched:** `docs/adr/0011-automated-version-check-replaces-the-manual-check.md` (new), `CLAUDE.md` (Amendments line), `src/trialpulse/feasibility/{checks,history_api,report,spike}.py`, `tests/feasibility/{test_checks,test_report_and_spike}.py`, `docs/feasibility_report.md`, `docs/progress.md`, `docs/interview_notes.md`. Tests: 88 in `tests/feasibility/`.

**Verify (PowerShell):**

```powershell
uv run python -m trialpulse.feasibility.spike --part e,h
uv run pytest -q tests/feasibility
```

On a rerun every projection comes from the cache, so the first command makes 0 requests.

## Step 3: Warehouse, contracts and live schemas

Date: 2026-09-30. Status: **approved** by Aakrisht on 2026-09-30.

### What was built

- `src/trialpulse/contracts/`: the canonical version contract shared by the offline dataset and live API v2 records.
  - `text.py`: one plain-text form for both sources (the dataset holds HTML, API v2 holds Markdown): one line per paragraph or list item, entities unescaped, Markdown escapes removed, email addresses replaced by a marker (ADR 0012, proposed).
  - `sponsor.py`: sponsor display name and key (lowercase, punctuation removed); no name or key for an individual sponsor (class INDIV, or a person's name with a degree title and no organization word; ADR 0012).
  - `versions.py`: the canonical columns and their types, the enums (API v2 names, with ANTICIPATED mapped to ESTIMATED), partial dates with a precision flag, placeholder dates (before 1901 or from 2100) stored as missing, ages in years, a content hash that is the same for an unchanged record from either source, the Pandera schemas (`VERSION_SCHEMA`, `TEXT_SCHEMA`), the quarantine split, and `canonical_from_api_v2`.
- `src/trialpulse/warehouse/build.py`: `uv run python -m trialpulse.warehouse.build` builds `data/warehouse.duckdb`:
  - `raw_versions`: 35 columns, each typed explicitly and each with a planned use (listed in the audit); the three drifting columns are typed and kept for the audit only; no personal-data column, no free text, no sponsor name;
  - `texts`: each distinct normalized text once, keyed by SHA-256;
  - `versions`: canonical rows that pass validation; `versions_quarantine`: rows that fail, with reasons;
  - `trials`: time zero, first and last version, version count, latest status and study type, quarantined versions;
  - `build_info`: the revision and the build's counts.

  The build writes a new file and swaps it in only when complete, runs text normalization, hashing and validation in a worker pool, and compares each table's row count and checksum with the previous build.
- `src/trialpulse/warehouse/audit.py`: writes `docs/data_audit.md` (part 1) from the warehouse; `uv run python -m trialpulse.warehouse.audit` regenerates it.
- `src/trialpulse/warehouse/parity.py`: `uv run python -m trialpulse.warehouse.parity` compares a seeded sample of live API v2 records, mapped with `canonical_from_api_v2`, with their latest warehouse versions, column by column. Raw API records are not stored.
- Alembic: `alembic.ini`, `alembic/env.py` (DATABASE_URL from the environment or .env, never printed), and revision `0001`, which creates the schemas `live` (study_versions, texts, trial_state, outcome_events, quarantine), `serving` (trial_scores_latest, trial_score_history) and `monitoring` (pipeline_runs, graded_predictions, promotions), all empty. `live.study_versions` has exactly the canonical columns (tested).
- CI: a Postgres 16 service, and a step that runs `alembic upgrade head`, `downgrade base` and `upgrade head` on every push.
- Tests: `tests/contracts/` and `tests/warehouse/`, on synthetic data only.
- Dependencies (locked stack, Section 15): pandera[pandas], SQLAlchemy 2, psycopg 3 (binary), Alembic.

### Acceptance criteria

| Criterion | Result |
| --- | --- |
| Building the warehouse twice gives identical row counts and table checksums | **Met in my shell.** Builds B and C of the final code, from revision v2026.09.26, give identical row counts and checksums for all six tables (build C printed "Identical to the previous build: yes"). Also tested on a synthetic dataset with 1 and 2 worker processes (`test_building_twice_gives_identical_tables`). An earlier build A, made before the last normalization fix, matched B on `raw_versions`, `trials` and `build_info` and differed only in `texts` and `versions`, as expected |
| Pandera validates the cohort, with failures quarantined and counted | **Met in my shell.** All 4,444,542 versions validated (every study type and year, so that Step 4 can apply the point-in-time population): 4,444,542 passed, 0 quarantined. The quarantine path is tested on six failure kinds: a non-canonical enum, a duplicated version key, an individual's name kept, a precision without a date, a dataset row without a version number, and a structural error, which raises instead (`test_invalid_rows_are_quarantined_with_reasons`, `test_quarantine_and_trials`) |
| `alembic upgrade head` works on local Postgres | **Met in my shell**: Docker Compose Postgres 16 on 127.0.0.1:15432; `upgrade head`, `downgrade base`, `upgrade head` give revision 0001 and 10 empty tables in the 3 schemas. CI now runs the same three commands on a Postgres 16 service |
| Tests cover date parsing, enum mapping and idempotency on a small fixture | **Met.** `test_partial_dates`, `test_ages_in_years`, `test_sql_twins_match_the_python_rules` (the SQL enum and date rules equal the Python ones), `test_versions_are_canonical` and `test_building_twice_gives_identical_tables`, on the synthetic dataset in `tests/warehouse/conftest.py` |

The additions from Aakrisht's Step 3 instruction:

| Instruction | Result |
| --- | --- |
| 1. Load only the columns a planned feature, label or audit needs; never write personal-data columns; scrub emails from kept text | 35 of 96 columns are loaded into `raw_versions`, each with its planned use listed in the audit. 6 more (the five text fields and the lead sponsor name) are read only to derive normalized values. The 4 personal-data columns are never read. 334 distinct texts had an email address removed; 0 remain (validated). `test_no_personal_data_is_written` searches every text column of every table for the fixture's investigator, individual sponsors and email |
| 2. No names for individual sponsors; the Step 7 note | 0 names or keys are stored for individual sponsors (2,625 versions with class INDIV, and 20,554 with a person's name under another class; 4,960 trials). The Step 7 fallback to the class-level rate is item 4 of "Guidance for later steps" above |
| 3. Unescape HTML entities in all text | Done for every text field, from both sources. 1,084,686 distinct raw texts were HTML; the audit counts them per field |
| 4. Explicit types for the drifting columns | `disp_first_submit_qc_date` DATE, `is_ppsd` BOOLEAN and `fdaaa801_violation` BOOLEAN (like every other loaded column), whatever type each Parquet file has; the audit shows the types by file |
| 5. Alembic on the local Postgres, port 15432 | Done, as above |

### Results

- **Warehouse** (`data/warehouse.duckdb`, 4.1 GB): 4,444,542 versions of 604,583 trials; 2,889,754 distinct normalized texts; 0 quarantined. A build takes about 18 minutes with 8 worker processes on this machine and uses about 7.4 GB of memory at its peak; `--workers 4` uses less.
- **Source parity** (`uv run python -m trialpulse.warehouse.parity`, one API v2 request): 200 seeded interventional trials, 199 unchanged since the dataset. 192 of 199 canonical rows are identical in every column, content hash included. Each of the other 7 has a known registry-side cause: 5 open trials the registry now shows as UNKNOWN, and 2 sponsors whose name the registry changed, all without a new version. 0 differences without a known cause. On text alone, 559 of 559 trials agree on all five text fields across three samples; the last sample (300 trials) was drawn fresh after the final rule fix.
- **Data audit, part 1** (`docs/data_audit.md`): 0 duplicate version keys; 0 non-monotonic post dates; 14,041 versions (4,846 trials) share a post date with the previous version; 19.8% of versions change only columns the warehouse does not load; placeholder dates set to missing (for example 1,287 primary completion dates); 5,321 trials show an open status after a first terminal one; no enum value outside the canonical sets.

### Findings for later steps

1. **The registry changes some fields without posting a version (Steps 7 and 14).** Besides UNKNOWN (Step 2), the parity sample found sponsor organizations renamed centrally (for example Endo Pharmaceuticals is now "Endo USA Inc., a Keenova Therapeutics Company"). A delta pull filtered on LastUpdatePostDate never sees such changes. Since the sponsor key follows the name, a renamed sponsor would start a new track record; Step 7 and Step 14 should decide how to handle that.
2. **Tie-break on post dates (Step 4).** 14,041 versions share a post date with the previous version; the state on such a day is the one with the higher version number.
3. **Reversals and terminal-status changes (Step 4).** 5,321 trials have an open status after a first terminal one, and in 4,879 trials COMPLETED is followed by another terminal status, which matters for which terminal version defines the event.
4. **Stand-in values (Step 7).** 11,591 versions have an age limit above 120 years and 5,842 an enrollment target of 1,000,000 or more (up to 999,999,999); the features need caps.

### Decisions flagged for approval

1. **Individual sponsors include person-named sponsors under other classes** (ADR 0012, proposed): a degree title and no organization word. 2,676 distinct names match; 3 contain a word that may indicate an organization.
2. **One canonical plain text for both sources** (ADR 0012, proposed), including the list rules that make the dataset's HTML and API v2's Markdown agree. The content hash depends on it: an unchanged record gets the same hash from either source.
3. **Placeholder dates** before 1901 or from 2100 on are stored as missing, with counts in the audit; unusual but possible dates are kept.
4. **Columns kept for planned uses**: both titles (ADR 0006, phase stated in the title), `why_stopped` (the Step 6 label) and `last_update_submit_date` (version-order audit, live parity). The drifting columns are loaded for the audit only.
5. **Ages in years** in `versions` ("6 Months" is 0.5); the raw strings stay in `raw_versions`.
6. **Quarantined rows leave `versions`** (none today); `trials.quarantined_versions` counts them so that Step 4 can exclude such trials.
7. **Two live tables beyond the names in CLAUDE.md**: `live.texts` (the normalized texts that versions reference by hash) and `live.quarantine` (Section 12's quarantined records). The columns of the empty serving and monitoring tables are provisional; Steps 13, 14 and 17 may change them with new migrations.
8. **The local DATABASE_URL host is 127.0.0.1, not localhost.** In my shell `localhost` resolves to IPv6 first and stalls, because the container port is bound to 127.0.0.1 only. `.env.example` and `alembic.ini` now show `postgresql+psycopg://trialpulse:trialpulse@127.0.0.1:15432/trialpulse`, and `alembic/env.py` sets a 10-second connect timeout.
9. **Tooling**: mypy treats pandas as untyped, like scikit-learn (pandas-stubs would be a new dependency); ruff treats `alembic` as a third-party import; CI gets a Postgres 16 service and a migrations step.
10. **API v2 requests during Step 3**: 8 in total (a first look at two records, text samples of 60, 200 and 300 trials, two diagnostics, and two parity runs), far below 40 per minute.

### Blocked, skipped or deferred

- **Your `.env` has no DATABASE_URL** (checked through the Secrets class, without opening the file). `uv run alembic upgrade head` refuses with one line until you add it; the value is in `.env.example`.
- **Faster chunk conversion deferred.** The main process spends about 20 seconds per chunk converting rows to pandas. Moving that conversion into the workers would need pyarrow as a direct dependency (today it comes only through Streamlit), so I did not do it without approval. The build is well under the one-hour limit.
- The daily Step 6 sample labeling was not run (yours). `docs/feasibility_manual_check.csv` was not touched.

### Files touched

- New: `src/trialpulse/contracts/{__init__,text,sponsor,versions}.py`, `src/trialpulse/warehouse/{__init__,build,audit,parity}.py`, `alembic.ini`, `alembic/{env.py,script.py.mako}`, `alembic/versions/0001_live_serving_monitoring.py`, `tests/contracts/{test_text_and_sponsor,test_versions}.py`, `tests/warehouse/{conftest,test_build,test_parity,test_migrations}.py`, `docs/adr/0012-canonical-text-and-individual-sponsors.md`, `docs/data_audit.md`.
- Changed: `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, `.env.example`, `docs/progress.md`, `docs/interview_notes.md`.

### Verify (PowerShell)

First add one line to `.env`: `DATABASE_URL=postgresql+psycopg://trialpulse:trialpulse@127.0.0.1:15432/trialpulse`. Each build takes about 18 minutes; the second should print "Identical to the previous build: yes".

```powershell
uv sync
uv run python -m trialpulse.warehouse.build
uv run python -m trialpulse.warehouse.build
docker compose up -d
uv run alembic upgrade head
uv run alembic current
uv run pytest -q
```

Optional: one API v2 request, then the audit regenerated with its result.

```powershell
uv run python -m trialpulse.warehouse.parity --sample 200
uv run python -m trialpulse.warehouse.audit
```

Then open `docs/data_audit.md`.

### Review of 2026-09-30 (Aakrisht)

**Approved:** Step 2 GO and Step 3; sampling only trials with at least 2 versions for part e (ADR 0011); ADR 0012 (the canonical text form and the individual-sponsor rule), now **accepted** and in the CLAUDE.md Amendments section; placeholder dates stored as missing and ages in years; both titles kept; quarantined rows leaving the versions table; `live.texts` and `live.quarantine`; 127.0.0.1 in the local URL; mypy treating pandas as untyped; the CI Postgres service. pyarrow is approved as a direct dependency wherever it makes builds meaningfully faster.

**Verification on Aakrisht's machine:** he added DATABASE_URL to `.env` and ran `uv sync`, `uv run alembic upgrade head`, `uv run alembic current` and `uv run pytest -q`, all as expected. He skipped the two full rebuilds, because they were run in this session on the same machine with identical checksums (builds B and C above).

**Guidance added for Step 7:** sponsor renames without a new version are treated as aliases of one sponsor (item 5 of "Guidance for later steps").

## Step 4: Cohort, outcomes and landmarks

Date: 2026-09-30 to 2026-10-06. Status: **built and independently reviewed, waiting for Aakrisht's review**.

### What was built

- **ADRs 0013 and 0014**, written first as instructed, accepted by Aakrisht on 2026-10-05 and added to the CLAUDE.md Amendments:
  - ADR 0013: the population is decided at each landmark, from the version in effect then.
  - ADR 0014: UNKNOWN is derived from the registry's rule on versioned fields. The dataset stores the registry's computed UNKNOWN over the latest version's submitted status, which it keeps in `last_known_status`.
- **A change back into Step 3** (approved): `last_known_status` in the canonical contract (API v2 `statusModule.lastKnownStatus`), the warehouse (schema version 2) and `live.study_versions` (Alembic revision 0002). pyarrow (approved) carries text batches and version chunks to the workers, and the version stage is read once instead of once per chunk.
- `src/trialpulse/cohort/`:
  - `rules.py`: the constants from `config/project.yaml`, the submitted status, and the lapse rule, each in SQL and as a Python twin;
  - `outcomes.py`: the state sequence per trial, and one outcome per trial (the first terminal version, reversals, censoring under the UNKNOWN rule or at the cutoff);
  - `landmarks.py`: candidates at t0 + 6k months, the state at each landmark (an as-of join), the population and at-risk rules, and the reason every rejected candidate failed;
  - `person_period.py`: up to four 6-month intervals per landmark, and `training_rows`, which applies administrative censoring at a walk-forward origin with a filter and a recode;
  - `audit.py`, `build.py`: `uv run python -m trialpulse.cohort.build` writes `data/cohort/{landmarks,person_period,outcomes}.parquet`, the sanity table, and part 2 of `docs/data_audit.md`.
- `src/trialpulse/reports.py`: each audit part regenerates only its own section of `docs/data_audit.md`.
- `config/project.yaml`: `unknown_rule` (the four lapsing statuses, 24 months), validated in `config.py`.
- Tests: `tests/cohort/` (52 tests) and `tests/test_reports.py`, plus additions in `tests/warehouse/` and `tests/test_config.py`.
- The `slow` marker: a default local run (410 tests) skips 7 slow ones (two extra warehouse builds, one with a worker pool, the Streamlit app runs and the real-data sample check); CI runs all 417. Measured on this machine while a browser and other applications were running: 45 seconds for the default run and 57 for the full one (72 for the default run before the last two changes). Two changes made the difference: the warehouse tests share one synthetic warehouse per session, and the tests share one TLS context instead of loading the CA bundle for every HTTP client (about 0.15 seconds each, some 70 times).

### Acceptance criteria

| Criterion | Result |
| --- | --- |
| Mini-history tests: withdrawn at month 3, terminated at month 20, completed at month 8, UNKNOWN at month 30, open at cutoff, a reversal, registration after start, missing dates | **Met.** `tests/cohort/test_cohort.py`: `test_withdrawn_at_month_3`, `test_terminated_at_month_20`, `test_completed_at_month_8`, `test_unknown_at_month_30_is_censored_at_its_verification`, `test_open_at_the_cutoff`, `test_a_reversal_is_excluded_and_counted`, `test_registration_after_start_counts_from_registration`, `test_missing_dates` (no completion date, no verification date, no first-post date) |
| Added by Aakrisht: unknown by the derived rule with no UNKNOWN version; study type changing between versions; a status reversal | **Met.** `test_unknown_by_the_derived_rule_without_an_unknown_version`, `test_study_type_is_decided_at_each_landmark`, `test_a_reversal_is_excluded_and_counted` (two forms, one reopened on the same day) |
| Aalen-Johansen sanity table printed and saved, by sponsor class (ADR 0006) | **Met in my shell.** Printed by the build, saved in `docs/data_audit.md` part 2 and `data/cohort/aj_sanity_by_sponsor_class.csv`. Early-stop CIF from L0 (landmarks before 2018-01-01, Section 10): 2.0% at 12 months and 5.0% at 24 months overall; INDUSTRY 3.5% and 7.0%; the other classes 0.8% to 1.5% and 2.7% to 4.2% (the UNKNOWN sponsor class, 66 trials, has no early stop in 24 months) |
| The landmark output satisfies the Step 8 input contract exactly | **Met.** `data/cohort/landmarks.parquet` has trial_id, landmark_index, landmark_date, event, event_date and `stratum` (M0's feature), and nothing else; `test_the_landmarks_file_is_the_step_8_input_contract` loads it with the harness's own `load_landmark_rows`. M0 ran on it (below) |
| The person-period table feeds the discrete-time models without reshaping | **Met.** One row per (trial, landmark, interval) with `interval` (j), `outcome` (0 continue, 1 stop, 2 complete) and the landmark's features; `training_rows(rows, origin)` gives an origin's training set by filtering and recoding only (`tests/cohort/test_person_period.py`). With no covariates the rows reproduce Aalen-Johansen's CIF (0.0319 against 0.0318 at 12 months on the 2016 origin), the property Section 9 requires of the discrete-time models |
| M0 through the Step 8 harness on the development origins (closes Step 8's last criterion) | **Met in my shell.** `uv run python -m trialpulse.eval.walkforward --model m0 --origins dev`, 1,000 resamples, 12 minutes, 0 invalid resamples, no unlock (results below) |

### Results

- **Cohort** (`uv run python -m trialpulse.cohort.build`, about 18 seconds; a second build is identical): 330,121 trials with at least one landmark row; 1,448,969 landmark rows (328,421 at L0, falling to 99,965 at L6); 4,245,278 person-period rows; 604,583 trial outcomes.
- **Funnel:** 604,583 trials in the warehouse; 556,296 first posted from 2008; 422,927 of them interventional in some version; 3,065 excluded for a reversal. Of the 419,862 left, 89,741 have no landmark row: 51,697 were already terminal when first posted (registered after they ended), 35,938 are censored at registration under the UNKNOWN rule (never verified again), and 2,106 are not interventional, or lapsed, at every landmark.
- **Outcomes of cohort trials:** 34,255 early stops (22,446 terminated, 11,809 withdrawn), 176,659 completed, 28,013 censored under the UNKNOWN rule, 91,194 open at the cutoff.
- **UNKNOWN:** the registry's label is fully contained in the derived rule (55,942 trials with both, 8,098 by the rule only, 0 by the label only). 43,348 trials had a lapsed state followed by a later version; 39,919 of them have an outcome or are open at the cutoff, which censoring at the first lapse would have discarded.
- **Point-in-time population:** 16,525 landmark candidates were rejected because the trial was not interventional on that date (ADR 0013); 31,502 because the state was lapsed on that date (ADR 0014); 20 because no version was public yet.
- **M0 on the development origins** (sponsor class at L as the only feature; pooled over landmark indices; 95% cluster-bootstrap intervals):

  | Origin | Evaluation rows (trials) | Horizon | AUC | Brier | Lift at 10% | Calibration slope |
  | --- | --- | --- | --- | --- | --- | --- |
  | 2016-01-01 | 76,734 (46,327) | 12 months | 0.543 (0.532 to 0.557) | 0.0325 | 1.41 | 0.88 |
  | 2016-01-01 | 76,734 (46,327) | 24 months | 0.534 (0.525 to 0.544) | 0.0597 | 1.25 | 1.22 |
  | 2017-01-01 | 82,627 (49,416) | 12 months | 0.551 (0.541 to 0.563) | 0.0310 | 1.58 | 1.09 |
  | 2017-01-01 | 82,627 (49,416) | 24 months | 0.534 (0.526 to 0.542) | 0.0590 | 1.25 | 1.32 |

  This is the base-rate floor the later models must beat: sponsor class alone carries a little signal. The full table for Step 9 goes in `docs/results_dev.md` then; the JSON is `data/results/walkforward/m0_dev.json`.
- **Warehouse rebuild** (new schema): build F on an idle machine gave the same row counts and checksums as build E for all six tables, in 485 seconds (8.1 minutes; about 18 minutes before the pyarrow path). `texts` and `trials` are also identical to the Step 3 builds. Build G, after the review fixes to the build code, is identical again.
- **Source parity, repeated on 2026-10-05** with `last_known_status` in the contract: 191 of 199 unchanged trials identical in every column; the 8 differences all have a known registry-side cause (5 UNKNOWN set without a version, with API v2's `lastKnownStatus` equal to the status the dataset holds; 3 sponsor renames). The implemented UNKNOWN rule agrees with the live registry for all 199.

### Independent review (2026-10-06)

Two independent reviewers, as instructed (the earlier four-reviewer attempts failed on a network outage and then the session limit). One covered outcomes, censoring and landmarks against Section 6 and ADRs 0013 and 0014; the other the Step 3 contract change, the fit with the Step 8 contract, and test adequacy. Every finding was verified before acting on it.

**What they confirmed.**

- Reviewer 1 re-derived every outcome and every landmark decision on the real warehouse with its own SQL written from the ADR text: 0 mismatches over 604,583 trials and 2,939,034 landmark candidates, and the audit's arithmetic is consistent.
- Reviewer 2 recomputed `stratum` from the warehouse for all 1,448,969 landmark rows (0 differences), found the Arrow chunk path free of dropped, duplicated or reordered rows at any chunk size, and the content hash identical between the offline path and `canonical_from_api_v2` for the same record.

**Findings and what was done** (commit `a3595ed` and the review-fix commits after it):

| Finding | Verified | Action |
| --- | --- | --- |
| Person-period intervals that straddle the end of observation kept events and dropped continuations, biasing hazards upward (both reviewers) | Yes: origin 2016, no-covariate CIF at 12 months 0.0332 against Aalen-Johansen's 0.0318 | Fixed: such intervals are dropped for every trial (0.0319 after). Recorded as **ADR 0015, proposed**, because it refines Section 6's wording |
| Walk-forward training labels are the final ones truncated at T, not the cohort as it would have been built at T (both reviewers) | Yes, by construction; reviewer 1 sized it: origin 2016, 405,251 training landmark rows built against 399,905 as of T | **Not changed. Open question 1 below** |
| A version shown UNKNOWN without `last_known_status` was treated as neither open nor lapsed | Yes, on a mini history; 0 such versions in the dataset | Fixed: lapsed from its post date, and a reversal after a terminal version; tested |
| States follow post dates and events follow version numbers, unchecked | Yes; 0 out-of-order versions in the dataset | Fixed: the build refuses out-of-order versions; tested |
| The audit's "lapse resolved by a later version" overcounted | Yes: 3,429 of the 43,348 are censored under the rule anyway | Fixed: the audit reports both numbers |
| ADR 0014's Context implied the rule keeps never-updated trials in the risk set | Yes: 35,938 eligible trials are censored at or before registration | ADR Context corrected and the figure added to its Consequences. **Open question 2 below** |
| Tests did not pin several rules: 15 mutants survived (first of several terminal versions, stratum at the landmark, the completion reference, same-day ties, relabeled trials, boundary days, the cutoff filter) | Yes, reproduced on a scratch copy of the code | 15 cohort tests added; all 15 mutants, and 5 more for the new fixes, are now killed |
| The sponsor lookup could hold two rows for one key (a missing and an empty name), duplicating versions (latent since Step 3) | Yes: the new test fails on the previous code | Fixed and tested |
| A BIGINT column with a NULL passed through float64 in the chunk conversion, changing integers above 2^53 (latent) | Yes: the new test fails on the previous code | Fixed (nullable types straight from Arrow) and tested |
| Section replacement split on any line starting with "# "; part 2 did not say which warehouse it came from | Yes | Fixed: only part headings separate parts; part 2 states the revision, schema version and versions checksum; unit tests added |
| The content hash covers the registry's computed UNKNOWN, so one version would hash two ways before and after the registry flips it | Yes | **Not changed. Open question 3 below** (a Step 14 decision); the API v2 parity test now covers an UNKNOWN version |

### Decisions pending approval

1. **ADR 0015 (proposed):** an interval counts only if it ended within the observation window (the data cutoff, or the origin when training). Implemented, since the alternative is measurably biased.
2. **A bare UNKNOWN** (no `last_known_status`) is lapsed from its post date; after a terminal version it is a reversal. None exists in the dataset; a live record could carry one.
3. **Out-of-order versions are refused** by the cohort build instead of being interpreted.
4. **`landmarks.parquet` holds only the contract columns and `stratum`** (the lead sponsor class of the state at L, "MISSING" when absent). Everything else about a trial's outcome is in `outcomes.parquet`, so that no label-derived column can reach a model as a feature.
5. **Person-period conventions:** intervals are (start, end]; an event on the end belongs to that interval (as the harness counts a stop on the horizon); a trial censored on the end keeps the interval (as Aalen-Johansen's risk set does).
6. **The sanity table** uses L0 landmarks before 2018-01-01 (Section 10) and 365.25 / 12 days per month.
7. **Slow tests:** the test-lock tests stay in the default run although they take about 7 seconds, because they guard the lock; the warehouse tests share one synthetic warehouse per session; `tests/conftest.py` makes every test share one default TLS context by wrapping an httpx helper (test-only, and skipped if a future httpx drops the helper).
8. **One more API v2 request** on 2026-10-05, to repeat the parity sample with the new column.

### Open questions

1. **Should walk-forward training labels be rebuilt as of each origin T?** Today the cohort is built once at the data cutoff and training rows are truncated at T, as Section 6 words it ("every outcome administratively censored at T"). Two things are then decided with hindsight: which trials are censored under the UNKNOWN rule and where, and which trials are excluded as reversals. Sizes at origin 2016 (reviewer 1): 19,314 of 405,251 training landmark rows exist only with hindsight (mostly lapsed at T and resolved later), 13,968 would exist only as of T (10,961 from UNKNOWN censoring decided after T, 3,007 from reversals after T), and 13,960 common rows have a different censoring date. No event label differs. Options:
   - (a) **Build each origin's training labels as of T** (`CohortRules.from_config(cfg, cutoff=T)` already supports it; the harness would read one training file per origin). The training set is then what a model trained at T would have seen, which is also what production retraining sees. **Recommended**, before Step 9 fits anything beyond M0.
   - (b) Keep hindsight labels and state the limitation in the preregistration and the model card.
   - (c) As (a) for the UNKNOWN rule only, keeping the reversal exclusion with hindsight as Section 6 mandates.

   Each changes how Section 6 is read and the approved Step 8 harness, so it needs an ADR. M0's numbers would move slightly.
2. **Trials censored at or before registration.** Under ADR 0014 a trial whose last state is lapsed is censored at that state's status verified date, stored as the first day of its month. For 35,938 eligible trials (8.6%) that is not after registration, so they have no landmark row (the old definition gave them none either). Options: (a) keep as approved, **recommended for the main analysis**: these rows would carry no follow-up, and AUC does not change; (b) censor at the end of the verified month, or at the last state's post date if later, so that they keep an L0 row with a few days of follow-up. Either way the Step 11 sensitivity analysis (UNKNOWN as an early stop) needs these rows and the landmarks between a trial's last verification and its lapse, so it needs its own cohort variant.
3. **The content hash and the registry's computed UNKNOWN (before Step 14).** A version bootstrapped from the dataset as RECRUITING and fetched from API v2 after the registry flips it to UNKNOWN hashes differently, although no version was posted (5 of 199 trials in the parity sample). With the live key (nct_id, post date, content hash) that would store one version twice. Options: (a) hash the submitted status instead of the shown status and `last_known_status`, **recommended**; (b) key live versions by (nct_id, post date) and treat the flip as an update. (a) changes the Step 3 contract and every content hash, so it needs approval and a warehouse rebuild (8 minutes).

### Blocked, skipped or deferred

- **`docs/results_dev.md` is not written.** M0's development results are in this file and in `data/results/walkforward/m0_dev.json`; the results document belongs to Step 9.
- **The UNKNOWN sensitivity analysis** (Step 11) is not built; `outcomes.parquet` records each trial's lapse date for it (see open question 2).
- **Your local database is at revision 0001.** `uv run alembic upgrade head` applies 0002 (one added column).
- **Build D failed at its last step** (the audit) because I edited `config/project.yaml` while it ran; its process held the old config class. No code defect; builds E, F and G are the valid ones.
- The daily Step 6 sample labeling was not run (yours).

### Files touched

- New: `src/trialpulse/cohort/{__init__,rules,outcomes,landmarks,person_period,audit,build}.py`, `src/trialpulse/reports.py`, `alembic/versions/0002_last_known_status.py`, `tests/cohort/{conftest,test_cohort,test_rules,test_person_period,test_build}.py`, `tests/test_reports.py`, `tests/conftest.py`, `docs/adr/0013-population-decided-at-each-landmark.md`, `docs/adr/0014-unknown-from-the-registry-rule.md`, `docs/adr/0015-intervals-end-within-the-observation-window.md`.
- Changed: `CLAUDE.md` (Amendments: ADRs 0012, 0013, 0014), `config/project.yaml`, `src/trialpulse/config.py`, `src/trialpulse/contracts/versions.py`, `src/trialpulse/warehouse/{build,audit,parity}.py`, `tests/warehouse/{conftest,test_build,test_migrations,test_parity}.py`, `tests/test_config.py`, `tests/nlp/{test_labeling_app,test_llm_labeler}.py`, `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, `docs/data_audit.md`, `docs/adr/0012-canonical-text-and-individual-sponsors.md`, `docs/progress.md`, `docs/interview_notes.md`.

### Verify (PowerShell)

Light on purpose: no warehouse rebuild (builds E, F and G ran on this machine with identical checksums). The cohort build takes about 20 seconds and should print "Identical to the previous build: yes".

```powershell
uv sync
uv run alembic upgrade head
uv run alembic current
uv run python -m trialpulse.cohort.build
uv run pytest -q
uv run pytest -q -m "slow or not slow"
```

Optional, about 12 minutes (M0 on the development origins, 1,000 resamples; it rewrites `data/results/walkforward/m0_dev.json`):

```powershell
uv run python -m trialpulse.eval.walkforward --model m0 --origins dev
```

Then open `docs/data_audit.md` (part 2).

## Step 5 (provisional): Exploratory data analysis

Date: 2026-10-06. Status: **in progress, unreviewed, on branch `provisional/step5-eda` only**. Built under the extension of 2026-10-05 while Aakrisht was away; main holds nothing of it, and the draft PR is not to be merged.

Checkpoints (one line per finished item):

- 2026-10-06: branch created from main at `0ab1da2`; matplotlib added as a dependency (approved in the extension, for static PNG figures in `docs/figures/`).

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

Status: **in progress**. The gold set holds reference labels from an adjudicated model panel (ADR 0010). The LLM is scored on test; the LLM sample is partly labeled (daily token limit), and the distilled model is provisional until it is complete.

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
| Gold-test results for the LLM and the distilled model | LLM: met (accuracy 0.847, macro-F1 0.842 with CI 0.791 to 0.883, kappa 0.817, per-label F1 and confusion matrix in `docs/reason_labeling.md`). Distilled model: pending the full 10,000 labels |
| LLM macro-F1 >= 0.80 on gold-test | Met on the point estimate (0.842); the 95% interval's lower end is 0.791 |
| Gold-test never used for tuning | Met: prompt work used dev only; the final prompt was committed (`16ba93f`) before any test item was labeled; each model is scored on test once |
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

**Finding for the prompt work.** A count-only check found that 2 test texts appear word for word in the label table of `docs/labeling_guide.md`, one in a definition and one in an example (generic phrases; the guide predates the sample). An earlier version of this line said both were example phrases; a per-section count on 2026-09-26 corrected it. The LLM prompt therefore cannot copy every guide example verbatim; the guard names any leak by hash prefix only. A scan of all 70 tracked text files with the guard found test texts in only two, both written before the sample existed: the guide (2) and one synthetic string in `tests/nlp/test_gold.py` (1). Neither is a prompt file, and none of the docs written in this run contains a test text.

**Decisions made in this step (all eight approved by Aakrisht on 2026-09-26):**

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
- **The guard only saw files.** `prompt_leaks` now checks any rendered prompt. The prompts README and ADR 0010 require the Step 6 labeler to run it on the prompt it sends, and warn that the labeling guide holds 2 test texts in its label table.
- **The review app opened test texts.** It served the 300 test texts first. It now shows dev texts only, and a test keeps a test item and its suggestion in the sample and checks that the app never shows them.
- **The 10 examples were not reproducible.** A scratch script chose them. The selection is now `decision_examples` in `panel.py`, behind `--report --examples 10`, and it picks the same 10 ids.
- **Consistency relabels were not validated.** `check_consistency` now requires exactly the seeded sample, one valid label per text, and a labeler that was not on that text's panel. The real relabels pass.
- **The verbatim check matched parts of words.** It now matches whole words only. The real result is unchanged (1,200 of 1,200).
- **Untested paths.** New tests cover every command end to end (including a panel with no splits), the report's kappa and shares, a non-zero verbatim share, stray adjudications, input-file determinism and old-schema rows in `write_labels`.
- **Two wording fixes:** an interview note wrongly said the agents returned counts only, and the report used "adjudicated" for all 25 splits, clashing with the panel outcome of that name.

The labels file is byte-identical after the fixes.

**Recorded for later in Step 6:** the LLM labeler builds its prompt only from `src/trialpulse/nlp/prompts/` and dev items, calls `check_graded_model` on its model id, and a test runs `prompt_leaks` on the rendered prompt against the committed test hashes. The prompt must not paste the labeling guide whole. Done on 2026-09-26 (below).

### Step 6 LLM labels and distillation (2026-09-26)

Aakrisht approved the eight panel decisions and asked for two follow-ups, then for the rest of Step 6. The full results are in `docs/reason_labeling.md`.

**Follow-ups:**

- ADR 0010 and `docs/reference_panel.md` now state that all panel members are the same model, so panel agreement and the consistency relabel measure the model's consistency with itself and are an upper bound on label reliability.
- `data/nlp/dev_suggestions.csv` is deleted. It was sent to the Windows Recycle Bin rather than erased, so it can be restored.

**Built:**

- `src/trialpulse/nlp/llm_labeler.py`: labels texts with `openai/gpt-oss-120b` on Groq's OpenAI-compatible API.
  - Temperature 0, strict JSON-schema output validated again in code, 25 texts per request.
  - Every valid answer is cached under a hash of the full request, so reruns make no repeat call and resume after a stop.
  - Per-minute budgets are read from the rate-limit headers; a daily limit (named by the provider as TPD or RPD) stops the run cleanly.
  - Before any call it checks the rendered prompt for gold-test texts (`prompt_leaks`), the model against the panel's family, and, for test items, that the final prompt is committed and unchanged.
  - It builds the 10,000-text sample: stratified by status and stop year, all 400 gold texts excluded after normalization, stored in a seeded random order.
- `src/trialpulse/nlp/evaluate.py`: accuracy, macro-F1 with a bootstrap interval (1,000 resamples), Cohen's kappa, per-label precision, recall and F1, and the confusion matrix. A second test scoring of the same model is refused, and test results print aggregate numbers only.
- `src/trialpulse/nlp/distill.py`: TF-IDF plus logistic regression. C and class weighting are chosen by 5-fold cross-validation on the LLM-labeled training texts only, and a gold text in training is refused. It also labels every early stop, writing CSV and Parquet.
- `src/trialpulse/nlp/prompts/reason_v1.md` and `reason_v2.md` (final, commit `16ba93f`).
- `pyproject.toml`: scikit-learn moves to the runtime dependencies (locked stack).
- `docs/reason_labeling.md`: the results.
- `docs/results/test_llm.json`: the LLM's single test result (aggregate only), tracked in git.
- Tests: `tests/nlp/test_llm_labeler.py` (26), `tests/nlp/test_evaluate.py` (13), `tests/nlp/test_distill.py` (7). The suite has 271 tests.

**Results:**

| Step | Result |
| --- | --- |
| Prompt v1 on dev | macro-F1 0.858 (95% CI 0.718 to 0.925), accuracy 0.860, kappa 0.825 |
| Prompt v2 on dev | macro-F1 0.980 (0.945 to 1.000), accuracy 0.980, kappa 0.975; final by the declared rule |
| **LLM on test (once)** | **macro-F1 0.842 (0.791 to 0.883), accuracy 0.847, kappa 0.817. The point estimate meets 0.80; the interval's lower end is 0.791.** |
| LLM sample | 1,375 of 10,000 labeled, 0 failures; about 129 tokens per text; the daily token limit stopped the run |
| Distilled model (provisional, 1,375 labels) | cross-validated macro-F1 0.748 against the LLM labels; dev macro-F1 0.732 (0.561 to 0.842); not scored on test |
| All early stops | 35,198 labeled with the provisional model: 27,128 operational, 3,076 scientific, 4,994 other |

**Test isolation:** Claude saw only aggregate test numbers. Dev errors were read for prompt work, as ADR 0010 allows.

**Decisions made in this step.** Review of 2026-09-26: the report listed seven decisions, and Aakrisht approved its 1 to 5 and 7, which are items 1 to 5 and 7 below (7 together with the move of scikit-learn to the runtime dependencies). The report's decision 6 is item 8. Item 6 was not in the report; Aakrisht approved it on 2026-09-29, together with the exit codes (2 for refusals, 4 for limit or outage stops) and `--status` refusing without a sample.

1. **httpx instead of the Groq SDK.** It calls Groq's OpenAI-compatible endpoint with the HTTP client the project already uses, so there is no new dependency, respx mocks it in tests, and the provider is configurable by base URL. Alternative: the Groq SDK (in the locked stack for Step 6).
2. **Request settings:** reasoning effort medium, reasoning text not returned, at most 4,096 completion tokens, strict JSON schema. They were fixed before the first dev run and never changed.
3. **Two prompt iterations, not three.** The two dev errors left after v2 are single hard cases, and a rule for them would fit the dev items. v2 is final by the rule declared before the first run (highest dev macro-F1, ties to the earlier version).
4. **The distilled model's single test scoring waits for the full 10,000 labels.** Scoring the provisional model now would spend the one test look on a model that will be replaced. The code enforces it (see the review below). The provisional model labeled all 35,198 early stops to prove the pipeline; those rows name `n=1375` in their `model` column and will be replaced.
5. **Distillation design:** word 1- and 2-grams plus character 3- to 5-grams; grid C in {0.5, 2, 8, 32} and class weight in {none, balanced}; 5-fold cross-validation against the LLM labels. C = 32 was added after the first provisional fit put the best C at the top of the grid; C = 8 stayed best.
6. **The sample is drawn from distinct texts with a known stop year** (24,534, minus the 400 gold texts), because the strata need a stop year. The final labeling covers all 35,198 early stops with a reason, stop year or not.
7. **Outputs** go to `data/nlp/llm/` (sample, cache, labels), `data/nlp/models/`, `data/nlp/results/` (dev results and test predictions) and `data/nlp/reasons/`, all gitignored. Test results go to `docs/results/`, tracked in git, so the once-only rule survives a cleanup of `data/`.
8. **v2's clarifications stay in the prompt only (Aakrisht, 2026-09-26).** The labeling guide is unchanged: it is the standard the reference labels were made under. The clarifications are prompt-level guidance consistent with the guide, documented in `docs/reason_labeling.md`.

**A bug found and fixed during the run:** the first sample run treated any 429 wait over 2 minutes as a daily limit. The labeler now reads the limit type the provider names (TPD, RPD, TPM, RPM), stops only on a daily one, and waits out per-minute limits up to 15 minutes. A probe between the runs made one tiny request (91 tokens) and printed only the limit type and counts. The resumed run stopped on a named TPD limit, so the result for the day did not change.

**Internal review.** Three independent reviewers checked the code and docs before the commit, without access to `data/`, `labels/` or `.env`. Their confirmed findings are fixed:

- **Split batches were invisible on a rerun.** A batch split after invalid answers was cached only under its halves, so a rerun sent the full batch again and the status count missed it. A split now leaves a marker under the full batch's key. No batch split in today's run (all 55 were full 25-text batches), so no data changed.
- **The key could reach a traceback.** A key with a space or control character would be quoted in the HTTP library's error. The key is now stripped and checked, and network and server errors are re-raised without the original message.
- **A provider 400 aborted the run.** Groq's `json_validate_failed` (for example, a completion that hits the token cap) is now treated as an invalid answer, so the batch is retried and then split. Any other provider error stops the run cleanly and keeps every label so far.
- **Sample-size edge cases.** `--sample 0` passed the final-prompt check, a first `--sample N` with a small N built a small sample, and a small N overwrote the teacher labels. Now N must be 1 to 10,000, the sample file is always the full 10,000, and the labels file always holds every sample text labeled so far.
- **The distilled model's one test scoring could be misspent.** Stale test predictions could be scored under a newer model's metadata, and nothing stopped a provisional model from using the single scoring. `--predict-test` now records the model's file hash and training size, and both it and `evaluate --test distilled` refuse a provisional model or a mismatch.
- **The once-only rule rested on a gitignored file.** Test results are now saved in `docs/results/` and committed, and a second scoring is refused if the file exists or ever existed in git history. The LLM's result moved there.
- **Smaller fixes.** `confidence` is numeric in the Parquet file. The printed interval names the configured level. `distill` with no flag trains, so the CLAUDE.md Step 6 verify command works. In the docs: business precision is 0.848 (it had been rounded twice), efficacy (not "scientific labels") is the weakest label, and v2's rules are described as drawn from the dev errors, not from the guide.

**Blocked or deferred:**

- **8,625 sample texts remain**, about 5.5 more days at the free tier's 200,000 tokens per day. Run `uv run python -m trialpulse.nlp.llm_labeler --sample 10000` once a day; it resumes from the cache.
- **After the sample is complete:** train the final distilled model, score it once on test, and relabel all 35,198 early stops (commands in `docs/reason_labeling.md`).
- **The warehouse reasons table and the reasons addendum in `docs/eda.md`** (Step 6 build list) wait for the Step 3 warehouse and the Step 5 EDA, which do not exist yet.

**CI duration (question from Aakrisht).** The run for `f288c78` took 10 min 6 s, but its job ran for 52 s (types 23 s, tests 16 s, against 15 s and 11 s in the previous run, with 44 more tests). The rest was on GitHub's side: about 4 minutes queued before a runner started the job, and about 5 minutes between the job finishing and the run being marked complete. GitHub's status page shows an incident with delayed processing on API requests from 10:11 UTC on 2026-09-23 to 04:55 UTC the next day, which covers that run. Nothing in the code or the workflow caused it, so nothing was changed.

### Step 6 review and follow-ups (2026-09-26, later)

**Aakrisht's verification on his machine (2026-09-26), all as expected:**

- `uv sync` clean;
- 271 tests passed;
- `llm_labeler --status` showed 1,375 of 10,000 labeled, with no API calls;
- the v2 dev score reproduces (macro-F1 0.980);
- the provisional distilled model reproduces (dev macro-F1 0.732);
- CI green on the latest commit;
- `evaluate --test llm` correctly refused to score the test split a second time.

**Fix: expected refusals print one line, not a traceback.** That refusal printed a full Python traceback. Every expected refusal in the Step 6 commands now prints one line to stderr and exits non-zero, with no traceback (`src/trialpulse/cli.py`):

- `refused: <reason>`, exit code 2 (the Step 8 test lock's code): a test split already scored, a provisional model sent to test scoring, test predictions from another model, an uncommitted or non-final prompt, a bad `--sample` size, a missing key or input, a gold text in training, and panel files that break the protocol;
- `stopped: <reason>`, exit code 4: a run stopped by a provider limit or outage (the daily token limit, a long rate limit, or network or server errors that outlasted the retries), after it saves its labels and prints its summary. A client error such as a wrong key or model is a refusal (exit code 2), because trying again later will not fix it.

The domain errors subclass both the refusal and their former built-in type, so library callers are unaffected; any other exception is a bug and keeps its traceback. `llm_labeler --status` now refuses when there is no LLM sample yet, instead of building one from the network.

**Tests.** Each case is run through the real entry point and checked for one stderr line, the exit code and no traceback:

- `tests/nlp/test_refusals.py`: a test split already scored, a provisional model (for `distill --predict-test` and `evaluate --test distilled`), test predictions from another model, an uncommitted prompt, the daily limit (exit code 4, with the summary still printed and the labels saved), a client error (401, exit code 2), a missing key, `--status` without a sample (no network call), a gold text in training, no labels yet, a panel file with a label outside the taxonomy, and missing inputs (model, dev labels, gold sample, panel files);
- `tests/nlp/test_llm_labeler.py`: a non-final prompt and a bad `--sample` size.

Checked in PowerShell on the real repo: `evaluate --test llm` and `distill --predict-test` each print one `refused:` line and exit with code 2. An independent two-reviewer check after the first version found the gaps fixed here (client errors exiting 4, several missing-input tracebacks, `--status` reaching the network, and an overstated coverage line). The suite has 284 tests. The first push of this fix (`3437ab4`) failed CI: two refusal tests depended on the gitignored gold sample, which exists on the development machine but not in CI. `a675571` fixed it (the most fundamental refusal is checked first, and the tests point the gold-sample path at a missing file). Before that push, the full suite also ran in a clean worktree without `data/` (283 passed, 1 skipped), and CI is green on `a675571`.

**Daily sample labeling.** Aakrisht runs `uv run python -m trialpulse.nlp.llm_labeler --sample 10000` once a day until the sample reaches 10,000. Claude does not run it.

**Hugging Face access (checked 2026-09-26): still pending.** One read-only call (`HfApi.auth_check` on `brbk/clinical_trials_history`, the token never printed) raised `GatedRepoError`. Step 2 parts a to d were not run.

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
