# 0080. Retrain on the laptop, and a gate that waits for a fair window

- Date: 2026-11-10
- Status: **Accepted.** Decided by Aakrisht on 2026-11-10 (the Step 17 plan, docs/step17_plan.md, decision 5, 6 and 7).

## Context

Section 14: a quarterly challenger trained on all available data, compared with the champion on the most recent window with matured outcomes, promoted only if its 12-month AUC is at least the champion's minus 0.005 and its calibration slope lies in [0.8, 1.2]. ADR 0040 puts the retrain and the gate on the laptop. The champion was fitted on data through 2026-09-25, and its 24-month slopes lie below the range (ADR 0032).

## Decision

1. **Data:** the pinned rows, the gap rows (ADR 0074) and the exported live rows (ADR 0037's export adapter, with the retention's exports), as parity layer 5a reads them. ADR 0073's rule for versions that failed review stays as it is; the retrain records that it kept it.
2. **The gate** reads the calibration slope at 12 months only; the 24-month slope is reported and tracked as ADR 0032's limitation, not gated.
3. **No promotion before a fair window:** a fair window needs matured 12-month outcomes of predictions made after the champion's training cutoff, so none exists before about November 2027. Until then each run trains the challenger, scores it, logs everything to MLflow and `monitoring.promotions`, and records "not promoted: no window after the champion's training data with matured outcomes yet". The gate's comparison is built and tested on synthetic data with known answers and switches on by itself once a clean window exists.
4. A quarterly Windows scheduled task runs it; Aakrisht registers it.

## Alternatives

- **A new dataset release each quarter:** a new pin and an overlap check each time.
- **Gating both horizons:** the champion itself would fail at 24 months.
- **A window inside the champion's training data:** an unfair comparison that favours the champion.

## Implementation notes (2026-11-16, made during the unattended run; for review)

These were chosen without asking, as the most conservative reversible options, and are flagged in the run's report.

1. **A window-blind challenger.** The challenger fitted on all the data has learned the outcome of every prediction a window grades, so grading it there favours it. Once a window exists, M4 with the tuned settings is fitted as of the window's first graded date, as a walk-forward origin is (ADR 0016, the replay's code); it and the champion score the window's latest monthly dates; both are graded against the retrain's final cohort (status reversals left out, Section 6); the gate reads the mean over those dates of each model's matured 12-month AUC and of the challenger's 12-month calibration slope. The all-data challenger is the one a promotion would serve: the window judged its method on data neither model saw. Alternatives: grade the all-data challenger on the window (biased towards it); hold the window out of the challenger's fit and serve that fit (older data served).
2. **Three dates** (`retraining.window_dates`): each date costs two `score()` calls of 1 to 3 minutes, and three keep the retrain inside an hour: a smoke run on 2026-11-16 with a pretend champion cutoff of 2025-05-31 (window dates 2025-09-09, 2025-10-09 and 2025-11-09) fitted the window challenger on 1,338,346 landmark rows and graded 508,968 predictions in 1,415 s, which with the 1,194 s of the cycle without a window is about 44 minutes. Its numbers (champion 0.8169, challenger 0.7817, slope 0.946) mean nothing, since the real champion had seen those outcomes; it checked the code path and its time. Alternative: six dates.
3. **The window starts after the champion's own training cutoff,** read from its bundle (`data_cutoff`), so a promoted challenger moves it.
4. **A promotion moves no alias by itself.** The decision is recorded in MLflow and `monitoring.promotions`; the champion alias is moved by hand (docs/deploy.md, Part 5), because the model statement of `/v1/model` (ADR 0048) and the laptop's copy of the champion change with it. The challenger's `meta.json` names itself under `name`, with no `version`, so a registered challenger fetches as the registry's version (ADR 0045). Alternative: the retrain registers the challenger and moves the alias itself.
5. **`--from-5a`:** the retrain can reuse the warehouse of the last parity 5a run (the same pinned, gap and live rows, read from Neon by 5a) when the network blocks Neon's port. The one cycle of 2026-11-16 ran this way, with its decision written to local Postgres.
