# 0031. A presentation-only regeneration of docs/results.md

- Date: 2026-10-25
- Status: **Accepted.** Decided by Aakrisht in the review of Step 11 on 2026-10-25 (open questions 1 and 3 of the Step 11 report).
- Refines CLAUDE.md Step 11 and section 4 of `docs/preregistration.md` ("After the run").

## Context

Section 4 of the registration says that `docs/results.md` cites the commit tagged `prereg-v1` and the results commit. The generator, frozen at the tag for the locked run, cited the tagged commit only, and a file cannot name the commit it is committed in. Its table of hypotheses also printed H1's failed condition as a false inequality ("-0.0013 > 0"): it filled a fixed template with the observed values whether or not the comparison held.

## Decision

1. **The record stays.** `docs/results.md` as committed in `4991ea5` is the record of the locked run.
2. **The generator is changed for presentation only:** it names the commits that hold the record (the first commit after the tag that added `docs/results.md`, and the first that changed the registration, which filled its Results blocks), and it writes each comparison of a condition as it came out ("-0.0013 is not above 0"). It reads the same result files, runs the same checks and formats the same numbers.
3. **A note at the top** of the regenerated file says that only the presentation changed and how to compare it with the record (`git diff 4991ea5 -- docs/results.md`).
4. **A test** holds the regenerated file to the record: the same numbers in the same order, outside the note and the record line.

## Alternatives

- **Leave the file as it was** and cite the record commits in the Step 11 report only (the recommendation of that report). The registered sentence would stay unmet in the file itself.
- **Edit the file by hand.** Rejected: the file says it is generated and must not be edited by hand.

## Consequences

- `docs/results.md` is now written by code newer than the tag, and its note says so. The result files were all made from the tagged commit, and the generator still refuses any other.
- Section 1 of the registration asks for every job to run again if a defect forces a code change after the unlock. This change touches no job and no computation (the generator formats numbers the jobs computed), so no job runs again; the decision is Aakrisht's, recorded here.
