# 0034. Scoring between landmarks and beyond landmark 6

- Date: 2026-10-27
- Status: **Accepted.** Decided by Aakrisht in the review of the Step 12 follow-ups on 2026-10-27 (questions 1 and 2 of his rule 1 for Step 13). Committed before any measurement of the two checks below.
- Refines CLAUDE.md Sections 6, 8, 12 and 13 for the served scores (Steps 13 to 17). Amends ADR 0021 item 8 (the recent-versions window) for rows off the landmark grid. The registered results (`docs/results.md`) cover landmark rows only and are not touched.

## Context

The model was trained and evaluated on landmark rows only: L_k = t0 + 6k months, k = 0 to 6, with the time features `landmark_index` = k and `months_since_t0` = 6k. A served score is asked for on any date, for every open trial. On a given day about 0.5% of open trials sit on a landmark. At the data cutoff, about 21,600 of 91,500 open trials were registered 42 months or more earlier, beyond every row the model saw. The trees route any landmark index above 6, or any months above 36, exactly as landmark 6.

## Decision

### 1. The served row on a date d

- **k\***, the last landmark passed: the largest k in 0 to 6 with add_months(t0, 6k) on or before d, computed from t0 each time with the project's calendar arithmetic (the day clamped to the month's length). The true index, without the cap at 6, is kept with the score.
- **Every feature is computed as of d:** the state of the version in effect on d (on a tie of post dates, the higher version number), the month comparisons against d's month, the competition count on d, the sponsor record strictly before d.
- **The time features are held at the last landmark passed:** `landmark_index` = k\* and `months_since_t0` = 6k\*.
- **The recent-versions window** slides the trial's last landmark gap: from add_months(t0, 6k\* - 6) plus the days from add_months(t0, 6k\*) to d, up to d. On a landmark day it is the window of ADR 0021 item 8, clamped days included.
- **The horizon starts on d:** the four intervals of the model are the 6-month intervals after d.
- **The population on d:** first posted on or after 2008-01-01, a version public on d, the state on d interventional, open (SUSPENDED included) and not lapsed under ADR 0014. A trial with a status reversal visible by d is not scored and gets the reason code `reversal`.

### 2. Check 1: between landmarks (option B against option A)

Development origins only (2016-01-01 and 2017-01-01). For origin T:

- **Rows:** mid-landmark rows (trial, k, d) with d = add_months(t0, 6k + 3), k = 0 to 6, such that the landmark row (trial, k, add_months(t0, 6k)) is an evaluation row of T in the final cohort, d is before T + 12 months, and the trial passes the cohort's landmark rules on d (follow-up not ended on d, a version public, interventional, open, not lapsed).
- **Labels:** the trial's outcome in the final cohort (early stop, completion, or the censoring date), with the follow-up counted from d; IPCW censoring weights by the lead sponsor class on d (ADR 0017), the groups decided on the check rows of the origin.
- **B:** the origin's champion, fitted again as the locked run's development fits and used only if it reproduces their saved scores to 1e-12, scores the rows as in decision 1, with the origin's sponsor priors.
- **A:** the same model's saved 12-month score of the landmark row (trial, k, add_months(t0, 6k)), as option A would serve it.
- **Metric:** the IPCW AUC at 12 months from d, on the same rows, for B and for A; the difference B minus A with a paired cluster bootstrap over trials (1,000 resamples), reported.
- **Rule:** B is served if, at each of the two origins, its AUC is no more than 0.005 below A's (point estimates). Otherwise A is served, and Sections 1, 12 and 13 change as follows: the served score is the latest landmark row's, refreshed on the trial's landmark days; registry changes after that landmark are shown beside it as facts, not as model output; the daily job scores the trials whose landmark fell since the last run; and a trial with no valid row at its latest landmark gets the reason code `no_landmark_row`.

Option C (a blend of the two landmarks around d) is not compared; it can return as a Step 17 challenger.

### 3. Beyond landmark 6 (option 1), by age band

- A trial on a date d on or after add_months(t0, 42) is scored as in decision 1 with k\* = 6, and flagged **"outside the validated range"**.
- **Bands** by the true landmark index on d: months 42 to 59 (true index 7 to 9) and 60 to 119 (true index 10 to 19). A trial 120 months or older (true index 20 or more) gets the reason code `beyond_120_months` and no score: no development row can reach that age.
- **Check 2 rows:** pseudo-landmark rows (trial, k, add_months(t0, 6k)) for k = 7 to 19, with T on or before the date and the date before T + 12 months, that pass the cohort's landmark rules on that date, labeled from the final cohort and scored by the origin's champion as in decision 1 with k\* = 6.
- **Metric:** the IPCW AUC at 12 months per band and origin, averaged over the two origins; landmark 6's averaged AUC is the mean of the pooled landmark-6 AUC at 12 months of the development runs `m4-2016` and `m4-2017` (0.7027 and 0.7294 in their result files); the early stops within 12 months per band and origin.
- **Rule:** a band is served, flagged, if its averaged AUC is at least landmark 6's averaged AUC minus 0.03 and it has at least 100 early stops within 12 months at each origin. Otherwise its trials get the reason code `band_not_validated` and no score.
- **Reported beside the AUC, with no rule:** each band's mean predicted risk against its observed (Aalen-Johansen) risk at 12 months, per origin.

### 4. How the checks are run and reported

- One command runs both checks on the development origins; no locked origin is read. Each run is logged to MLflow like the Step 10 runs (commit, clean tree, dataset revision, configuration, metrics, result file) and written to `data/results/checks/`.
- The results are development evidence about the served scores, kept outside the registered results (ADR 0032): they appear in the model card and the Step 13 report, never in `docs/results.md`.
- The decisions follow the rules mechanically; the command writes which option and which bands are served, and the scoring code reads that file.

### 5. The served champion and the score history

- **The served champion** is M4 with the tuned settings, fitted on all available data: the final cohort with the origin set to the day after the data cutoff, so every interval that ended by the cutoff is a training row. Its sponsor priors are fitted on the same rows and saved with it. It is a new fit: registered in MLflow as the champion, kept apart from the registered per-origin models, and no metric of it is reported as a result.
- **The score history** gets a row when the score moves by 0.01 or more, the record changed, a landmark passed, or the model changed, and each row records why: `record_change`, `landmark_passed`, `new_model`, or, when none of these applies, `calendar` (the month-based features, the competition count and the sponsor record moved with time and with other trials). A jump on a landmark day is therefore explained.

## Alternatives

- **Option A, the latest landmark row, served from the start.** Every served number would be a validated row type, but it ignores record changes for up to 6 months and drops the daily loop of Sections 1, 12 and 13; it stays as the fallback of decision 2.
- **Option C, the blend of two landmarks.** Its weekly driver pass would cross the one-hour line, its drivers are blended, and it only removes the landmark-day step.
- **Abstaining past landmark 6 for every trial.** A quarter of open trials, the long-running ones, would get no score; it stays as the fallback per band.
- **Extending the landmark grid.** It changes a locked definition after the locked test; a Step 17 challenger at most.

## Consequences

- The model card states the served scope: open interventional trials registered since 2008, scored on any date as in decision 1; trials between months 42 and 119 flagged or not scored as decided by check 2; trials 120 months or older and trials with a visible status reversal not scored.
- Step 17 grades the flagged group separately and declares one graded prediction per trial per replay date.
- The registry description of `n_versions_recent` and `docs/features.md` say "since the previous landmark"; the served window rule is this ADR's.
