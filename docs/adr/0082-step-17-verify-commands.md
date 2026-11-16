# 0082. Step 17's verify commands are the commands that were built

- Date: 2026-11-17
- Status: **Accepted.** Decided by Aakrisht on 2026-11-17 (written after the operations review of Step 17, finding F6).

## Context

CLAUDE.md's Step 17 lists three verify commands and a `retrain.yml` workflow written before the step's decisions:

- `uv run python -m trialpulse.live.replay --start 2023-01-01 --end 2024-06-30 --cadence weekly`: the replay is `trialpulse.monitor.replay`, with a model step, quarterly score steps and a grade step (ADR 0078); there is no `live.replay`.
- `uv run python -m trialpulse.monitor.drift`: the drift module's command takes a step (`reference` or `training-rows`); the drift itself runs inside the daily job's full run (ADR 0076).
- `gh workflow run retrain.yml`: the retrain reads the gated dataset, so it runs on the laptop (ADRs 0040 and 0080); there is no `retrain.yml`. The monthly grading and its forced failure are `grade.yml` (ADR 0077).

ADR 0030 replaced Step 11's verify command in the same way.

## Decision

Step 17's verify commands, in PowerShell, one per line:

```
uv run python -m trialpulse.monitor.replay grade --target local
uv run python -m trialpulse.monitor.drift reference
uv run python -m trialpulse.monitor.retrain --target local --from-5a
uv run pytest tests/monitor -q
gh workflow run grade.yml -f fail_on_purpose=true
gh run list --workflow grade.yml --limit 1
```

The replay's model and score steps (`uv run python -m trialpulse.monitor.replay model`, then `score --quarter 2023Q1` to `2024Q2`) take about 12 minutes and 2 hours and are run once, not as a check. The `drift reference` command rewrites `config/drift_reference.json`; `git diff --stat config/drift_reference.json` then shows whether it changed. The retrain takes about 20 minutes and 13 GB of memory. The forced grading failure opens or reuses the issue "Monthly grading failed", which is then closed by hand. CLAUDE.md's repository layout and Step 17 build list name `retrain.yml`; read them as the laptop's quarterly task (`scripts/retrain_quarterly.ps1`, docs/deploy.md Part 5).

## Alternatives

- **A `retrain.yml` that only dispatches a reminder:** a workflow that cannot run the retrain would suggest it can.
- **Keeping the old commands:** they fail as written.
