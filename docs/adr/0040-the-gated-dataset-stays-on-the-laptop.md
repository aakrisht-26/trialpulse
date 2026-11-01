# 0040. The gated dataset stays on the laptop

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 14 decision 5, his reading of rule 2 confirmed).
- Changes CLAUDE.md Section 12 (the bootstrap), Section 14 (where the retrain and the promotion gate run) and Step 17 (`retrain.yml`, its verify and acceptance).

## Context

The version-history dataset is gated and licensed CC-BY-NC-4.0, and Section 3 says it must never be pushed anywhere. Read literally, rule 2 of Aakrisht's Step 13 review also forbade laptop jobs that plainly need the dataset (the warehouse rebuild, parity layers 2 to 4, the replay and the retrain).

## Decision

1. **The reading.** On the laptop the dataset may be read by any job Aakrisht approves. The one-time bootstrap is the only path from it into the live system. After that, only aggregates, models and the summaries named here leave the laptop.
2. **What crosses at bootstrap:** the per-trial state of ADR 0037, the sponsor file summed from every version, the cutoff's scores regenerated on the rebuilt warehouse (held to Step 13's risks and drivers), the stop-reason labels of the early stops, and the amendment markers of ADR 0043. No version rows and no text.
3. **The retrain and the promotion gate run on the laptop.** Only the decision reaches `monitoring.promotions` and MLflow. `retrain.yml` is not a GitHub workflow in Step 17; its verify becomes a laptop command, and its acceptance says so. A self-hosted runner is no escape, since it would put the dataset behind a GitHub job.
4. **How it is held:**
   - no `HF_TOKEN` secret in GitHub (`gh secret list` is part of the Step 14 verify);
   - `HF_HUB_OFFLINE=1` and `UV_NO_SYNC=1` in every workflow step that runs Python;
   - `trialpulse.ingest.history` and the bootstrap refuse to run under `GITHUB_ACTIONS`;
   - a test walks the imports of every `python -m` module a workflow runs and fails on `trialpulse.ingest.history`, `trialpulse.warehouse.build` or `trialpulse.features.build`, and scans the workflows for `hf://`, `huggingface.co` and `data/raw`;
   - failure paths of the daily job log the stage, the exception type and the SQLSTATE only, never a message, because a failed COPY's message holds the row; the failure issue carries the run id and the error class only;
   - Actions caches hold dependencies and the model bundle only.
5. Moving `huggingface-hub` to its own dependency group is left until the daily job's imports are split from the offline modules (Step 14 laptop work), because `mypy src` needs it today.
   Done on 2026-11-01 (approved by Aakrisht): `huggingface-hub` is the dependency group `dataset`, a default group, so a plain `uv sync` on the laptop and in CI installs it and `mypy src` still sees it; the daily and timing workflows install with `--no-group dataset`. `datasets` was never a dependency of the project. `tests/test_workflows.py` holds both.

## Alternatives

- **The literal reading:** no warehouse rebuild, no replay and no retrain anywhere.
- **The retrain on a GitHub runner with the dataset downloaded there:** pushes the dataset to a third party's machine.

## Consequences

- These guards stop accidents, not intent: the real barriers are no token, no data on the runner and allowlisted uploads.
- The quarterly retrain needs the laptop on, by a scheduled task like the backups of ADR 0045.
