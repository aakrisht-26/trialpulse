# 0014. UNKNOWN: derived from the registry's rule on versioned fields

- Date: 2026-09-30
- Status: **Accepted** by Aakrisht on 2026-10-05, as written, including `last_known_status` as the submitted status under UNKNOWN, the status set, the month-end dates and "a later version resolves a lapse". He decided the principle on 2026-09-30 (Step 2 review, "Guidance for later steps", item 2): do not rely on the registry's UNKNOWN status; apply the registry's own rule to versioned fields, the same way offline and live. The details were set in Step 4 from the evidence below.
- Changes CLAUDE.md Section 6 ("UNKNOWN", "Landmarks") and the canonical version contract (Step 3).

## Context

The registry shows UNKNOWN for "a study whose last known status was recruiting, not yet recruiting, or active not recruiting, that has passed its completion date, and whose status has not been verified within the past 2 years". It computes this label; no sponsor submits it. Step 2 found that it appears without a new version (part d, and the Step 3 parity sample). Step 4 found how the history dataset stores it (revision v2026.09.26):

- All 84,976 versions with status UNKNOWN are the latest version of their trial; none is followed by another version.
- The dataset column `last_known_status` is filled on exactly those 84,976 versions and on no other. Its values are the four open statuses the definition names (RECRUITING 48,031, NOT_YET_RECRUITING 20,859, ACTIVE_NOT_RECRUITING 12,881, ENROLLING_BY_INVITATION 3,205).
- 37,495 trials have UNKNOWN on version 0, which no sponsor could have registered.
- At the version's own post date, the rule holds for only 172 of the 84,976; at the data cutoff it holds for all of them (below).

So the dataset replaced the latest version's submitted status with the status the registry computed when the record was fetched, and kept the submitted status in `last_known_status`. Used as Section 6 defines it ("censored at the status verified date recorded in the first version showing UNKNOWN"), that label would remove 37,495 trials from the risk set from registration on, and it goes stale: records the registry marks UNKNOWN later are not fetched again, because no version is posted.

## Decision

1. **The submitted status.** Where a version shows UNKNOWN, its submitted status is `last_known_status`. The canonical version contract carries `last_known_status` for both sources (API v2 `statusModule.lastKnownStatus`); a validation rule allows it only behind UNKNOWN.
2. **The rule, at a date t, on the state in effect at t.** The state is *lapsed* at t when all three hold:
   - its submitted status is one of NOT_YET_RECRUITING, RECRUITING, ENROLLING_BY_INVITATION, ACTIVE_NOT_RECRUITING (not SUSPENDED: no UNKNOWN version has it as last known status);
   - its completion date has passed: the completion date, or else the primary completion date, taken at the end of its precision period (a month-precision "2024-05" has passed from 2024-06-01), is before t; when both are missing this condition holds;
   - its status verification has lapsed: the end of the status-verified month plus 24 months is before t (verified in September 2024 lapses from 2026-10-01).

   The status set and the 24 months are constants in `config/project.yaml` (`unknown_rule`).
3. **Landmarks.** A state lapsed at L is not open at L, so it has no landmark row, as the registry would show UNKNOWN on that date.
4. **Censoring.** A trial whose last state before the data cutoff is lapsed at the cutoff is censored at that state's status verified date (fallback: its post date). This replaces Section 6's "status verified date recorded in the first version showing UNKNOWN". A lapse that a later version resolves (a new verification, or a terminal status) censors nothing: the later version is observed, and its outcome counts.
5. **The same code offline and live.** The rule is one function over canonical rows, used by the Step 4 cohort build and, later, by live scoring (Steps 13 and 14).

## Evidence

- **Against the dataset's own UNKNOWN labels**, applying the rule to each trial's latest version at the data cutoff (2026-09-25): 84,976 of 84,976 registry UNKNOWN labels reproduced. 11,901 more latest versions are lapsed by the rule while the dataset still shows them open, consistent with records the registry marked later and the dataset never fetched again (Step 2 part d found 5 of 200 cohort trials in this state).
- **Against the live registry**: per-trial API v2 records fetched on 2026-09-29 (398 trials unchanged since the dataset): the rule agrees with the registry's UNKNOWN or not for 397. The exception (NCT03669965) is shown UNKNOWN in the dataset and ACTIVE_NOT_RECRUITING on the registry today, with no new version, so the registry itself changed its label.
- **Variants rejected by the evidence**: the completion date alone misses 1,928 registry UNKNOWN trials that have no completion date of either kind; counting the verification from the first day of the month flags a trial verified in September 2024 on 2026-09-29, which the registry did not.

## Alternatives

- **Keep Section 6's definition** (the first version showing UNKNOWN): wrong at the start of a trial's life (37,495 trials), stale at the end, and unavailable live, where the daily delta pull never sees a status the registry sets without a version.
- **Censor at the first lapse, even when a later version resolves it**: discards observed outcomes of trials that report late, which are common.
- **Treat a lapsed state as still open at landmarks**: the public record said UNKNOWN on that date, so the landmark would describe a trial nobody could see as open.

## Consequences

- The Step 3 contract, warehouse and live schema gain `last_known_status` (warehouse schema version 2; Alembic revision 0002).
- The UNKNOWN sensitivity analysis (Step 11, "treats UNKNOWN as an early stop") uses the derived rule: `data/cohort/outcomes.parquet` records each trial's lapse date and censoring date.
- A trial censored under this rule loses its landmarks from its last verification on, as Section 6's "open and uncensored at L" requires.
