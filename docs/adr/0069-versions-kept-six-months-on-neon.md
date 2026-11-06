# 0069. Versions kept six months on Neon

- Date: 2026-11-07
- Status: **Accepted.** Decided by Aakrisht on 2026-11-07 (option 3 of the phase B Neon estimate), with the safeguards that a test shows the daily job never reads a version row the retention deletes, and that rows are exported to the laptop and verified before any delete.
- Amends ADR 0045 (retention at 12 months, run on the 600 MB alert) for `live.study_versions` only.

## Context

`live.study_versions` holds every record the daily job keeps: 482 B a row with its indexes, as built. On the warehouse's real versions, the 432 days from the cutoff to 12 months after go-live (67 days to go-live, as in the Step 14 sizing) hold 346,871 versions: 167.3 MB with no retention. ADR 0045 exports and deletes rows older than 12 months, which at month 12 removes almost nothing.

Nothing on Neon needs old versions. The daily job reads them through four functions only (`store.latest_live`, `versions_after`, `latest_post_date`, `overlap_rows`): each trial's newest row, the rows the sponsor pass has not consumed, the newest post date, and the rows of the last few days. The retention keeps the first two by its own conditions (ADR 0045). The retrain runs on the laptop (ADR 0040), from the dataset and the exported rows.

## Decision

1. `live.version_retention_months: 6` in `config/project.yaml`. The retention command exports, verifies and deletes versions older than 6 months; score history keeps `live.retention_months: 12`.
2. Each trial's newest row and every row the sponsor pass has not consumed stay, whatever their age, as before.
3. The export, its check against the database by count and by a digest of every value, and the delete that must return exactly the exported rows are unchanged (ADR 0045, ADR 0058). The files stay on the laptop in `data/live_exports/`.
4. The command runs by hand on the laptop at the start of every month, and whenever the daily job's alert reports 600 MB. It never runs on a runner.

## Alternatives

- **Twelve months (ADR 0045 as it was).** 167.3 MB at month 12: the estimate stays over 700 MB.
- **Delete each trial's newest row too.** Ingest compares a new record with it; the rows would have to be rebuilt from the state, which is new code for about 23 MB.
- **Retention inside the daily job.** It must not export to a runner (ADR 0040) and must not delete without a confirmed export, so it stays on the laptop.
- **Three months.** More saving, but a missed monthly run would leave less margin before rows the overlap or the sponsor pass still read; six months is far from either.

## Consequences

- Measured on the real versions, a monthly run keeps between 212,519 rows (just after it) and 233,354 rows (just before the next): 112.5 MB at worst, 54.8 MB less than without retention. Of these, 48,724 are trials' newest rows older than the window, which the retention must keep. (The first estimate of this option, 94.1 MB saved, left those rows out.)
- A missed month adds about 12 MB (one month of versions) until the next run.
- Tests: versions go after 6 months while history of the same age stays; every read the daily job makes of `live.study_versions` returns the same answer before and after the retention; and the only functions that read the table are those four (a source scan).
