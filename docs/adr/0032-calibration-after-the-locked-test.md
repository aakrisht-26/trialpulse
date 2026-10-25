# 0032. Calibration after the locked test

- Date: 2026-10-25
- Status: **Accepted.** Decided by Aakrisht in the review of Step 11 on 2026-10-25 (rules 2 and 4 for Step 12).
- Refines CLAUDE.md Sections 13 and 14 (Steps 12 and 17).

## Context

H4 was falsified on the locked origins (`docs/results.md`). The champion's pooled calibration slope at 24 months was 0.782 at 2018 and 0.732 at 2019, below the registered range [0.8, 1.2]; at 12 months it was 0.855 and 0.803, inside it. At the COVID stress origin it was 0.664 at 12 months and 0.644 at 24. The slope fell at each later locked origin at both horizons (12 months: 0.855, 0.803, 0.664; 24 months: 0.782, 0.732, 0.644). A slope below 1 means the predicted risks are spread too widely. The cause of the fall is not established. The recalibration of ADR 0027 was measured against its rule before registration and failed it, so the registered champion has none.

## Decision

1. **A known limitation.** The 24-month calibration below the registered range is documented as a known limitation of the champion in the model card.
2. **Recalibration is a post-test product change.** Any recalibration from now on is labeled as a post-test product change, is never mixed into the registered results (`docs/results.md`, the Results blocks of the registration), and is judged by the live monitoring of Step 17 on matured predictions, not by the locked origins.
3. **The trend is tracked.** The model card states the fall of the slope over the locked origins as a trend whose cause is not established, and the monitoring of Step 17 tracks the calibration slope of matured predictions over time.

## Alternatives

- **Recalibrate now on the locked origins.** It would turn the locked test into a development set and mix a post-test change into the registered results.
- **Leave calibration out of the model card's limitations.** It would hide a falsified prediction.

## Consequences

- The model card (Step 12) has the limitation and the trend; the served scores of Steps 13 to 16 carry the same caveat.
- Step 17's model health page shows the calibration slope of matured predictions beside the AUC.
- Section 14's promotion gate asks for a calibration slope within [0.8, 1.2]. On the locked origins the champion's 24-month slopes and its 2020 slopes would fail that range; Step 17 decides how the gate reads the slope, with its own ADR.
