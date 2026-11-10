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
