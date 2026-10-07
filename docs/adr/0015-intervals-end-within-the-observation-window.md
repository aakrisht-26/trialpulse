# 0015. Discrete-time rows: an interval counts only if it ended within the observation window

- Date: 2026-10-06
- Status: **Accepted** by Aakrisht on 2026-10-07 (Step 4 review). Proposed on 2026-10-06 from the independent review of Step 4, and implemented in `trialpulse.cohort.person_period`.
- Refines CLAUDE.md Section 6 ("Discrete-time formulation").

## Context

Section 6 says: "An interval censored before its end is dropped and ends the sequence." The first Step 4 build read that per trial: an interval was dropped when the trial was censored inside it, and kept when the trial had its event inside it.

Observation ends for every trial on the same date: the data cutoff, or the origin T when rows are prepared for walk-forward training. For an interval that straddles that date, the per-trial reading keeps the trials that had an event before the date and drops the trials that were still open on it. Such intervals then hold events and no continuations. An independent reviewer measured the effect on the real person-period rows, and it was confirmed on the development origins:

- origin 2016-01-01: 6,080 training rows ended after T, none of them a continuation (1,062 early stops and 5,018 completions); every row from a landmark in the last 6 months before T was an event;
- the no-covariate CIF of early stop at 12 months was 0.0332 from these rows, against 0.0318 from Aalen-Johansen on the same trials (0.0655 against 0.0628 at 24 months); origin 2017-01-01: 0.0334 against 0.0321;
- at the data cutoff, 9,949 rows ended after it, all events.

A model with any calendar feature (registration year is in the design family) would learn that the newest landmarks nearly always end in an event, and Section 9's required test (the no-covariate discrete-time CIF matches Aalen-Johansen at interval boundaries) could not pass.

## Decision

1. **An interval exists for a trial only if the interval ended on or before the end of observation**, whatever happened in it. The end of observation is the data cutoff in the person-period table, and the origin T for training rows (since ADR 0016, the person-period rows of the cohort built as of T; before it, `training_rows`).
2. The other conventions stay: intervals are (start, end]; an event on the end belongs to that interval; a trial censored on the end keeps the interval; a trial censored inside an interval under the UNKNOWN rule (a per-trial censoring) loses that interval and ends its sequence.
3. An event dated T itself is not known at T (the harness censors it at T), so the interval ending on T is a continuation.

## Evidence

- After the change, on the real rows: origin 2016, CIF at 12 months 0.0319 against Aalen-Johansen's 0.0318, and 0.0629 against 0.0628 at 24 months; origin 2017, 0.0321 against 0.0321 and 0.0636 against 0.0634.
- `test_a_no_covariate_discrete_cif_matches_aalen_johansen` (200,000 synthetic trials with staggered entry and one cutoff): the discrete CIF equals Aalen-Johansen within 0.003; the former rule misses by 0.004 at 12 months and 0.007 at 24.
- The person-period table has 4,245,278 rows (4,255,227 before).

## Alternatives

- **Keep the per-trial reading.** Simpler to state, but biased as measured above.
- **Keep the straddling interval with fractional exposure weights** (an actuarial correction). It uses the events in unfinished intervals, at the cost of weighted rows in every model and a second convention to explain; the rows lost here are 0.2% of the table.

## Consequences

- An event that falls in an interval the observation window cuts short is not a training row; the trial's earlier intervals remain, as continuations. Evaluation is unaffected: it uses landmark rows and IPCW, not person-period rows.
- The newest landmarks contribute fewer rows: a landmark needs 6 months of observation after it to contribute its first interval.
- CLAUDE.md Section 6, "Discrete-time formulation", gains through the Amendments: "An interval that ends after the end of observation (the data cutoff, or the origin when training) is dropped for every trial."
