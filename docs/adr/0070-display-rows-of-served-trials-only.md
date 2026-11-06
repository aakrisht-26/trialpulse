# 0070. Display rows of served trials only

- Date: 2026-11-07
- Status: **Accepted.** Decided by Aakrisht on 2026-11-07 (option 4 of the phase B Neon estimate), with the safeguard that a test shows no API endpoint or dashboard page names a trial outside the served set.
- Amends ADR 0051 (display rows of the live trials) and the count of ADR 0059 (the trials a sponsor leads).

## Context

`serving.trial_display` held a row for every trial of the live state (217,282 rows, 69.3 MB, with 6.6 MB of sponsor names). The live state holds every trial that can still be scored or counted (ADR 0037); about 91,600 of them are served: scored, or listed with the reason they are not. The API reads display rows only through the served set: portfolios and landscapes join the scores or the reasons (`TRIALS_SQL`), the area counts do the same, and a trial's risk answers 404 unless it is served. The dashboard reaches only the API (ADR 0063). The other 125,000 rows are never shown.

## Decision

1. Only served trials keep a display row. After publication, in the same transaction, the daily job deletes the display rows of trials that are neither in `serving.trial_scores_latest` nor in `serving.trials_not_scored` (`display.keep_served`), decides their sponsors' names again (`display.refresh`), and deletes the name of every sponsor that leads no displayed trial.
2. The bulk command (`trialpulse.live.display`) keeps the pulled records of served trials only, then prunes as the job does. `--all-pulled` still keeps everything, to measure.
3. The count of ADR 0059 ("leads at least 5 live trials") now counts served trials. This shows fewer names, never more.
4. The job records the served trials without a display row (`served_without_display`); they get one when their next record arrives, or from the bulk command.

## Alternatives

- **Every live-state trial (ADR 0051 as it was).** 43.7 MB more at month 12, for rows no answer reads.
- **Titles only, no sponsor names, for unserved trials.** Still rows nobody reads.
- **Display rows written at publication instead of at ingest.** The job has the API record only on the day it arrives, so a trial entering the served set later would have no row either way.

## Consequences

- Measured on local Postgres with the served set of 2026-09-25: 89,419 display rows (27.1 MB) and 10,774 named sponsors (3.5 MB), 30.6 MB against 75.9 MB.
- A served trial whose current record API v2 no longer returns with a live status has no display row until its next record (2,180 at the local measure, because the served set was dated 2026-09-25 and the pull 2026-11-07). The API answers its risk without a title or sponsor name, and portfolios cannot list it, since they find trials through their display rows.
- A trial's timeline (`/v1/trials/{nct_id}/timeline`) never read display rows: it still answers for a trial that left the served set, by its id, with its history and markers and no title.
- Tests: no GET route names a planted unserved trial (its title nowhere, its id only in its own timeline, its risk 404); after a run every display row is a served trial's and every stored sponsor leads one.
