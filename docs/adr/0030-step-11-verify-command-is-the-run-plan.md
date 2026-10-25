# 0030. Step 11's verify command is the approved run plan

- Date: 2026-10-25
- Status: **Accepted.** Decided by Aakrisht in the review of Step 11 on 2026-10-25 (open question 2 of the Step 11 report).
- Changes the Verify block of CLAUDE.md Step 11.

## Context

CLAUDE.md Step 11 lists `uv run python -m trialpulse.eval.walkforward --model all --origins test --unlock-test` as its verify command. The harness has no `--model all`: it fits one model per run. The registration's run plan (section 4 of `docs/preregistration.md`), approved before the unlock, ran the locked origins as one job per model and origin and one job per comparison pair, all from the commit tagged `prereg-v1` with a clean tree. That is how the locked run of 2026-10-21 to 2026-10-23 was made.

## Decision

Step 11's verify command is replaced by the approved run plan's jobs: one walk-forward job per model and origin, one comparison job per pair, the H3 and H5 jobs and the jobs of the UNKNOWN sensitivity analysis, each with `--unlock-test` from the tagged commit, then `uv run python -m trialpulse.eval.results`, which reproduces `docs/results.md` from their result files. No `--model all` option is added.

## Alternatives

- **Add `--model all` to the harness.** One process would run about 15 hours of jobs; one failure would lose them all, and it could not skip a finished job. The per-job plan was the registered procedure, and adding the option now would change nothing about the record.

## Consequences

- A step report's verify block names `trialpulse.eval.results` (which needs the locked result files) or the per-job commands of the run plan, never `--model all`.
- The Amendments section of CLAUDE.md records the change.
