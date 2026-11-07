# 0074. The rebootstrap from the pinned rows and the gap rows, and its reference check

- Date: 2026-11-08
- Status: **Proposed.** Drafted in the overnight run of 2026-11-08; not decided. The code exists (`trialpulse.gap.rebootstrap`, tested on synthetic data) but was not run on real rows: the overlap check of the gap read stopped below its bar (docs/progress.md), which stops the rebootstrap until Aakrisht decides how to read that result.
- Refines ADR 0041 (decision 3, the bootstrap at the release minus a tail) and ADR 0038 (layer 3, the day-0 check, which has no Step 13 files at a later date).

## Context

ADR 0041 seeds the live state at the release of the gap read minus a tail of 1 to 7 days, so that versions API v2 never shows (superseded, posted on one day with others, failed review) are in the state. The bootstrap of Step 14 is dated the data cutoff and is held to Step 13's files of that date, and the day-0 check compares the live path's scores with the same files. Step 13 wrote no files for a later date, so a later seed needs its own reference.

## Decision (proposed)

1. **The combined rows.** The pinned warehouse's versions plus the gap store's versions posted on or before the seed date (ADR 0073's rules on both), their texts and the pinned warehouse's stop reasons, in a warehouse file of their own under data/gap/combined/, with the pinned interventions config plus the gap rows' adapted interventions. Every gap version must be after every pinned version of its trial, by number and by post date, or the step refuses. Nothing pinned is modified.
2. **The reference files.** Step 13's code (`scoring.score.score`) on the combined rows, dated the seed: `scores_<seed>.parquet` and `unscored_<seed>.parquet` under data/gap/reference/, with their SHA-256 values. They take the place of Step 13's files.
3. **The rebootstrap.** `live.bootstrap.run_bootstrap` dated the seed on the combined rows, held to the reference files, with the reason counts of the pinned cutoff: `labeled_through` stays 2026-09-25 (ADR 0050), so early stops posted after it count as unlabeled.
4. **The reference check** (replacing the day-0 check at this date): the day-0 check's code at the seed, against the reference files. The live path's scores from the seeded state, as the daily job computes them, must equal the reference on every trial and every column (risks, drivers, flag, reason, the true and the landmark index, the model's name and version), and the content hash of every served row must equal the live path's. Any difference fails it.
5. **The seed date** is chosen from parity layer 5b: the latest day on or before the release date minus one day for which the API pull holds no record the release lacks, so the tail the first daily run covers starts after the last complete day of the release.

## Alternatives

- **Compare the live bootstrap only with the offline scores it regenerated** (decision 3's own check): circular, since both come from the same function on the same rows. Decision 4 compares the state the job will run on.
- **Rebootstrap from the release alone** (its rows posted by the cutoff in place of the pinned ones): one source for every row, but it moves the history that every registered number rests on (ADR 0073's measurements and the sponsor renames found by the overlap check).
- **No rebootstrap (Gap (a) of ADR 0041):** the live state stays at the cutoff and the first run catches up from API v2 alone, with the hidden versions lost for good.

## Consequences

- A rebootstrap on Neon replaces the live state in one transaction (`--rebuild`), as the bootstrap of phase B did. Its undo is a rebootstrap at the pinned cutoff with the Step 14 bootstrap command, then the day-0 check.
- The first daily run after it pulls from the seed minus 3 days; its conversion time is measured on the laptop first.
- A check that the API and the release agree after go-live is layer 5's post-go-live check of ADR 0038, not part of this one.
