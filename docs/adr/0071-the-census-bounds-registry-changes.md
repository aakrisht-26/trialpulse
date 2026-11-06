# 0071. The census bounds registry changes

- Date: 2026-11-07
- Status: **Accepted.** Proposed after the independent check of phase B (finding F3), approved by Aakrisht on 2026-11-07.
- Refines ADR 0042 (the census as the acceptance test).

## Context

The census reads each differing record's raw values from API v2 through `API_V2_PATHS`, the table the conversion itself uses. A wrong path there would make the canonical value and the raw value wrong together, so the difference would be counted as a registry change made without a new version, which passes. Nothing bounded how many such changes a pass allows. The census of 2026-11-04 found at most 0.94% of the unchanged trials changed by the registry in one column (sponsor names, 1,374 of 145,535).

## Decision

1. The census fails when the registry causes of one column pass 2% of the unchanged trials and 100 trials.
2. A test checks every shared path on records API v2 returned (the recorded pages in `tests/live/fixtures`): each resolves in some record, but the last known status and the why-stopped text, which those records lack. The census itself reads the last known status of the UNKNOWN records through its path.
3. In the same review: a missing raw value gives the failing cause `NO_RAW` in every rule; a differing trial that the second read does not return fails; a repeated id or an unreadable record in the pull fails.

## Alternatives

- **A second, hand-written path table for the census.** Written from the same documentation by the same author, so not independent; the recorded records are.
- **A sample checked against the internal history endpoint.** That endpoint is for small verification samples only (CLAUDE.md Section 7), and the bound already catches the systematic error.

## Consequences

- The census of 2026-11-04 passes under the bound. A real registry event that changed more than 2% of one column would fail the census and need a look before it is accepted.
