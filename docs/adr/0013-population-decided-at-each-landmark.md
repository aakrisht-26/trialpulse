# 0013. Population: decided point-in-time, from the version in effect at each landmark

- Date: 2026-09-30
- Status: **Accepted.** The principle was decided by Aakrisht on 2026-09-30 (Step 2 review, "Guidance for later steps", item 1). The details in Decision items 3 to 5 were set in Step 4 and are flagged for his review in the Step 4 report.
- Changes CLAUDE.md Section 6 ("Population", "Landmarks").

## Context

CLAUDE.md Section 6 defines the population as study_type = INTERVENTIONAL and study_first_post_date on or after 2008-01-01, without saying which version's study type counts. Study type changes between versions: among the 556,296 trials first posted on or after 2008-01-01 (revision v2026.09.26), 422,927 are interventional in at least one version, 416,592 in every version, and 6,335 change between interventional and another value (observational, expanded access, or empty while a record is withheld).

Taking the study type from the latest version would decide past landmarks with information published later: a trial relabeled observational after it stopped would vanish from the past, and one relabeled interventional late would appear in landmarks when the public still saw an observational study. Both are the kind of leak the project exists to avoid (Section 1, point-in-time correctness).

## Decision

1. **A landmark row exists only if the version in effect at the landmark says INTERVENTIONAL**, in addition to the other conditions of Section 6 (open and uncensored at L). The version in effect at L is the state at time L (Section 6): the latest version posted on or before L; when two versions share a post date, the higher version number.
2. **The first-post condition stays trial-level.** t0 = study_first_post_date must be on or after 2008-01-01. It is constant across versions (the Step 3 audit found no trial where it varies).
3. **Outcomes use the trial's whole version history.** A trial that is interventional at L and later relabeled keeps its outcome (its first terminal version, or its censoring). Relabeling is not an event, and excluding such trials would select on the future.
4. **No version yet means no landmark.** If a trial's first version is posted after t0, nothing about it is public at L0, so it has no L0 row. This affects 21 trials, each by a few days.
5. **Trial-level counts** in the data audit call a trial "in the cohort" when it has at least one landmark row. The funnel reports each exclusion step separately.

## Alternatives

- **Latest version decides** (as the Step 2 spike counted): simple, but it uses information from after the landmark, as described above.
- **Version 0 decides** (the registration): point-in-time at t0, but wrong for later landmarks of trials whose study type changed after registration.
- **Any version interventional**: includes landmarks where the public record said observational.

## Consequences

- A trial can enter or leave the landmark table between landmarks (for example, observational at L0 and interventional from L1).
- Feature code (Step 7) and live scoring (Steps 13 and 14) use the same rule: a trial is scored on a date only if its state on that date is interventional.
- The rule lives in `trialpulse.cohort` and is tested with a mini history whose study type changes between versions.
