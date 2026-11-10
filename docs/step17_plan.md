# Step 17 plan: monitoring, retraining and replay (proposal)

Status: a proposal for Aakrisht's review, written 2026-11-10. Nothing in it is built. It follows Section 14 and Step 17 of CLAUDE.md, as amended by ADRs 0032 (calibration after the locked test), 0039 (grades as aggregates, replay rows on the laptop), 0040 (the dataset stays on the laptop; retrain and gate on the laptop) and 0053 (the Model health page before monitoring). The numbered decisions at the end are his.

## Where things stand

- Neon holds 283.5 MB (2026-11-10); the 12-month projection with Step 14's storage options is about 692.5 MB, under the 700 MB line by about 7.5 MB (docs/progress.md, 2026-11-08). **Any new Neon table in Step 17 must fit in that margin**, or move something else out.
- The live system records what grading needs (ADR 0039): one score file a month in `monitoring.score_files` (risks to 4 decimals, the sponsor class, the landmark index, the flag, the model version), status changes and lapse exits in `live.outcome_events`, and run metadata in `monitoring.pipeline_runs`.
- Neon holds **no features**: the daily job computes the 54 features of the trials it scores, scores them and keeps only the scores. A drift report on features must run where features are computed.
- The first 12-month predictions served live mature on 2026-11-08 + 12 months, so live graded performance at 12 months starts in November 2027; until then only partial horizons and the replay give numbers.
- The champion's 24-month calibration slopes are below the registered range (0.78 and 0.73, ADR 0032). Section 14's promotion gate asks a challenger's calibration slope to lie in [0.8, 1.2] (decision 6 below).

## The parts

### 1. Weekly drift report (Evidently)

What: feature drift and score drift of the served trials against the training reference, weekly; an HTML report and a summary table (per feature: the test, its statistic, drifted or not).

Where the features are: only inside the daily job's full run (weekly, ADR 0045), which computes all 54 features of every open trial.

| Option | How | Neon cost | Notes |
| --- | --- | --- | --- |
| **A. In the weekly full run (recommended)** | After publication, the full run compares its features and scores with a reference summary (per-feature quantile bins and category shares of the champion's training rows, made once on the laptop: an aggregate, allowed by ADR 0040) and writes one summary row per feature and week to `monitoring.drift` plus the HTML as a GitHub Actions artifact (90 days) | 56 rows a week, about 0.3 MB a year | Adds Evidently to the daily job's dependencies and about 1 to 2 minutes to a full run (to be measured); the job's 60-minute alarm is far away (full runs 6 to 16 minutes) |
| B. A weekly laptop job | The laptop reads the live state from Neon, recomputes the features (as the day-0 check does) and runs Evidently locally; summary to Neon, HTML in `data/` | the same | Needs the laptop on each week; recomputes what the job already computed |
| C. Store weekly feature files in Neon | A compressed file of every served trial's features each week | about 5 to 10 MB a week, 250 to 500 MB a year | Breaks the 700 MB line; rejected |

Evidently is in the locked stack (Section 15) but not installed yet.

### 2. Grading matured predictions

What: Section 14's performance on matured predictions (AUC at 12 months for predictions issued at least 12 months ago) and partial-horizon AUC for recent ones, plus the calibration slope (ADR 0032), as aggregates in `monitoring.graded_predictions` (ADR 0039: counts, sums, metrics per prediction date, horizon, landmark index and group).

Inputs, all on Neon: the score files (predictions), `live.outcome_events` (early stops, completions, lapse exits) and the served trials' state (censoring at the data date). The metric code is the evaluation harness's (IPCW AUC with censoring weights by sponsor class, ADR 0017; calibration slope), which needs no dataset.

| Option | How | Notes |
| --- | --- | --- |
| **A. A monthly GitHub workflow (recommended)** | `grade.yml`, monthly, after the month's score file exists: reads Neon, writes aggregates | Reads no dataset (ADR 0040 holds); fails like the daily job, with an issue |
| B. A step of the daily job | Grades on every full run | Adds work and a failure point to the job that serves users; grades move monthly anyway |
| C. On the laptop | A scheduled task | Same inputs as A, but depends on the laptop |

Neon cost: about 2 horizons x 7 landmark indexes x 2 groups = 28 rows a month (live) plus the replay's (part 3): under 1 MB in all.

### 3. The replay 2023-01-01 to 2024-06-30, weekly

What: the same scoring and grading code over a historical window, from the dataset, to give the Model health page a monitoring history with matured outcomes (Step 17's acceptance). Laptop only (ADR 0040). By ADR 0039 the replay's rows stay in `data/`; only its aggregates leave.

Cost: 78 weekly dates. A full live run on the laptop took 350 to 395 s in Step 14's replay, about 280 s of it the drivers. 78 full runs with drivers would take about 8 hours, which breaks the one-hour rule.

| Option | How | Run time |
| --- | --- | --- |
| **A. Scores without drivers, in quarterly chunks (recommended)** | Step 13's `score()` on each Friday, `with_drivers=False` (the replay grades risks; it serves nothing), from the pinned warehouse; one command per quarter of the window, resumable by date | about 60 to 90 s a date, so 13 dates a chunk in about 15 to 20 minutes; 6 chunks, each under an hour (to be measured on the first chunk) |
| B. The daily job's code through local Postgres | `live.replay` drives `daily.run_daily` on a feed, as parity layer 2 did | The most faithful, but about 6 minutes a date with drivers; still over an hour per quarter |
| C. Monthly cadence | 18 dates | Fewer points on the page than Step 17 asks |

How the replay's aggregates reach the page:

| Option | How | Notes |
| --- | --- | --- |
| **A. Neon, labeled (recommended)** | The same `monitoring.graded_predictions` table, with a column `source` ('live' or 'replay'); a migration | The API and page read one table; the page labels replay rows "replay, from the dataset" |
| B. A committed JSON | Loaded like the model statement (ADR 0048) | No migration, but a second path for the same numbers |

The replay's dates (2023 to mid-2024) lie after every walk-forward origin's evaluation window (the last, the 2020 origin's, ends 2020-12-31). It is monitoring evidence of the served model, never a registered result, and it is labeled so (ADR 0032).

### 4. Retraining and the promotion gate (laptop)

What: Section 14's quarterly challenger, trained on all available data, compared with the champion on the most recent window with matured outcomes; promoted only if its AUC at 12 months is at least the champion's minus 0.005 and its calibration slope is within [0.8, 1.2]; every decision logged to MLflow and `monitoring.promotions`. By ADR 0040 it runs on the laptop; `retrain.yml` is not a GitHub workflow.

The data question: "all available data" and "matured outcomes" need versions after the pinned cutoff.

| Option | How | Notes |
| --- | --- | --- |
| **A. Pinned + gap + exported live rows (recommended)** | The rows of parity layer 5a: the pinned warehouse, the gap store and the live versions through the export adapter (ADR 0037); outcomes from all three | No new download; the gap rows already passed the overlap check. The live rows hide intermediate versions (accepted loss, ADR 0038) |
| B. A new dataset release each quarter | Pin a newer release (an ADR each time), rebuild the warehouse | The fullest history, but a new pin, a rebuild (about 10 minutes) and an overlap check each quarter |
| C. Live outcomes only | Train on the pinned rows, grade on live outcomes | Outcomes mature too slowly for a quarterly gate in the first year |

Run time: M4 fits in 307 to 319 s per origin on the development origins; on all data with the tuned settings about 6 to 8 minutes, plus scoring the window (about 1 to 2 minutes). Well under an hour. A quarterly Windows scheduled task (like the backup) plus a manual command.

The first window with matured 12-month outcomes of live predictions is November 2027, so until then the gate's window is the latest 12 months of the replayed history (matured through the gap release) or the pinned data's last origin-like window (decision 7).

### 5. The Model health page

The sections ADR 0053 left as "arrives with Step 17":

- **Drift:** this week's drifted features and the score distribution against the reference, with the summary table; a link to the HTML report is not public (it is an Actions artifact), so the page shows the table only.
- **Graded performance:** matured AUC at 12 months with its interval, partial-horizon AUC for recent predictions, the calibration slope over time against [0.8, 1.2] (ADR 0032's trend), replay and live separated and labeled.
- **Promotions:** the last gate decision and its numbers.

All through new read-only endpoints (`/v1/monitoring/...`) or an extension of `/v1/model`; the page keeps calling the API only.

### 6. The alert path

Today: a failed daily run opens one "Daily live run failed" issue (ADR 0045).

| Option | How | Notes |
| --- | --- | --- |
| **A. GitHub issues for every alert (recommended)** | The grading workflow and the full run's drift step open (or reuse) an issue per alert kind: watermark lag over 3 days, Pandera failure share over a threshold, drift on more than a set share of features, matured AUC below the registered interval's lower bound | One channel, already tested; thresholds in config/project.yaml |
| B. E-mail from the workflows | A mail action with a secret | A new secret and a third-party action |
| C. Only the page | No push | Easy to miss |

A forced failure (Step 17's acceptance) is a dispatch with a flag that raises on purpose, on the grading workflow.

## What runs where

| Part | Where | Reads | Writes to Neon |
| --- | --- | --- | --- |
| Drift (1A) | GitHub, inside the weekly full run | Neon, the reference summary (committed or in Neon) | `monitoring.drift` |
| Grading (2A) | GitHub, monthly | Neon | `monitoring.graded_predictions` |
| Replay (3A) | Laptop | The pinned warehouse | aggregates only, labeled replay |
| Retrain and gate (4A) | Laptop, quarterly task | Pinned, gap and live rows | `monitoring.promotions`; MLflow |
| Page (5) | Streamlit, through the API | The API | none |
| Alerts (6A) | GitHub | Neon | none (issues) |

**Neon storage added by Step 17:** drift about 0.3 MB a year, grades under 1 MB, promotions negligible, the reference summary under 0.1 MB if stored there: **about 1.5 MB in all**, inside the 7.5 MB margin. Nothing in the plan stores rows of features or predictions beyond the existing score files.

## Time estimates

| Part | Build | Run |
| --- | --- | --- |
| 1. Drift | 1 to 1.5 days (reference summary, Evidently in the job, table, tests) | +1 to 2 min per weekly full run (to measure) |
| 2. Grading | 1.5 days (aggregates, IPCW by class on live data, workflow, tests) | minutes a month |
| 3. Replay | 1 day | 6 chunks of 15 to 20 min (option A); option B breaks the one-hour rule |
| 4. Retrain and gate | 1.5 days | 10 to 15 min a quarter |
| 5. Page and API | 1 day | none |
| 6. Alerts and the forced failure | 0.5 day | none |

The only part that would break the one-hour rule is the replay with drivers (option 3B, about 8 hours), which is why 3A splits it and leaves the drivers out.

## Decisions for Aakrisht

1. **Drift:** in the weekly full run (A, recommended), a weekly laptop job (B), or weekly feature files in Neon (C, rejected on storage).
2. **Grading:** a monthly GitHub workflow (A, recommended), a daily-job step (B) or a laptop task (C).
3. **Replay method:** scores without drivers in quarterly chunks (A, recommended), the daily job's code through local Postgres (B), or monthly cadence (C).
4. **Replay aggregates on the page:** in Neon with a `source` column (A, recommended) or a committed JSON (B).
5. **Retrain data:** pinned + gap + exported live rows (A, recommended), a new release each quarter (B) or live outcomes only (C).
6. **The calibration gate:** keep [0.8, 1.2] as written, read on the 12-month slope (recommended: Section 14 gives one slope; the 24-month slope stays a tracked limitation under ADR 0032), or apply it to both horizons (then the current champion would itself fail it at 24 months).
7. **The gate's window until live predictions mature (November 2027):** the latest replayed year (recommended, labeled), or no promotion before November 2027.
8. **Alerts:** GitHub issues for every alert kind (A, recommended), e-mail (B) or the page only (C); and the thresholds (proposed: watermark lag 3 days, Pandera failures 1% of a run's records, drift on 20% of the features, matured AUC below the registered lower bound).
9. **Order of work:** grading and the replay first (they fill the page), then drift, then retrain, then alerts; or another order.
