# 0079. Replay and live grades in one table

- Date: 2026-11-10
- Status: **Accepted.** Decided by Aakrisht on 2026-11-10 (the Step 17 plan, docs/step17_plan.md, decision 4).

## Decision

`monitoring.graded_predictions` gains a column `source` ('live' or 'replay', default 'live') in its primary key (migration 0007). The API reads grades, drift and promotions through read-only views in the `serving` schema (`serving.monitoring_grades`, `serving.monitoring_drift`, `serving.monitoring_promotions`), so its role keeps reading `serving` only (ADR 0049). The page labels replay rows "replay, from the dataset, a model fitted as of 2023-01-01". The migration runs on Neon only when no daily run is in progress.

## Alternatives

- **A committed JSON for the replay:** a second path for the same numbers.
- **Granting the API role the monitoring schema:** widens ADR 0049's role.
