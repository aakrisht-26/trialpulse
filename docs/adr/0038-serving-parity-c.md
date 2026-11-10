# 0038. Serving parity: one rule on any feed, in five layers

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 14 decision 2: Parity C).
- Amended 2026-11-10 (decided by Aakrisht): layer 5a compares the risks with an absolute tolerance of 1e-12 (see "Amendment of 2026-11-10").
- Refines CLAUDE.md Section 12 ("Serving parity") and the Step 14 acceptance.

## Context

Under ADR 0037 the live path computes features from compact state, not from versions, so "features and scores from the live path must equal the offline path" needs a statement that a test can hold. The API also hides versions (several posted on one day, versions that never pass review), so the live path cannot see everything the dataset holds.

## Decision

1. **The rule.** For any feed F that holds at most one record per trial and post date, and in which every record is seen before the sponsor pass goes past its post date, live(F) equals offline(F) on all 54 features and all rows, on several dates. offline(F) is `compute_served` on the versions of F. The difference between offline on every version and offline(F) is the accepted loss, reported apart.
2. **Outside the rule:** a same-date replacement and a late record have no offline twin. Each has a fixture whose answer is worked by hand.
3. **The layers.**

| Layer | Where | What |
| --- | --- | --- |
| 1 | CI, no data, no network | Synthetic histories and recorded API pages through the real path to the final SELECT, against offline; fixtures for NULL keys, a late record, a same-date replacement, a new key, a closed trial, a lapse exit, a weekend date and changed API shapes; a crash matrix (fail after each write, rerun, tables equal an uninterrupted run) |
| 2 | Laptop, before go-live | The rule on the real warehouse: no misses, 1 in 30 runs missed, and the gap of ADR 0041; dates across a month end, a weekend and a landmark |
| 3 | Laptop, then one runner, before go-live | State at the cutoff plus live code reproduces Step 13's risks and drivers for every scored trial: exact on the laptop; on a runner within a tolerance declared before the run (1e-9 proposed) |
| 4 | Laptop, at bootstrap | The census of ADR 0042 |
| 5a | Laptop, after go-live | The offline path on the pinned rows plus exported live rows (ADR 0037, the export adapter), compared on risks |
| 5b | Laptop, once | The later dataset read of ADR 0041 against the real API pull: what the API really hid |

4. Layers 1 to 4 pass before go-live.

## Amendment of 2026-11-10: layer 5a's risks

Layer 5a compares `risk_12m` and `risk_24m` with an absolute tolerance of **1e-12**, the project's existing reproduction tolerance (ADR 0034, decision 2: a refit is used only if it reproduces the saved scores to 1e-12); every other column (the landmark indexes, the flag, the model version, the four driver arrays, and the unscored rows' index and reason) stays exact, and so does the population.

Why: the live risks are computed on GitHub's Linux runner and 5a's offline risks on the laptop (Windows). The first run of 5a (2026-11-10) found 15 of 3,808 risks apart by at most 1.4e-17. The same `predict()` inputs, run in a local Linux container with the locked packages, reproduced Neon's risks bit for bit on all 1,904 trials, while Windows differed on exactly those 15: the difference is the platforms' math libraries, not the live path. A difference above 1e-12 still fails layer 5a.

## Alternatives

- **Parity A** (CI, the day-0 check and the census): nothing tests accumulation over time; a bug that shows on day 30 passes.
- **Parity B** (exact with every version fed, then the differing rows must be the hidden versions): its first leg tests code the runner never runs, and its second is wrong with missed runs.

## Consequences

- The replay harness of layer 2 costs 30 to 45 minutes a run on the laptop (estimated, not measured).
- What the API never showed is visible only once, through layer 5b.
- A row not rescored on day d equals offline only on its own scoring date, so the API says which date a score is from.
