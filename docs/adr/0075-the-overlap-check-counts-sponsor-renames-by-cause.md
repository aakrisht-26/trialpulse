# 0075. The overlap check counts sponsor renames by cause

- Date: 2026-11-08
- Status: **Accepted** (decided by Aakrisht on 2026-11-08).
- Amends item 4 of the gap read (ADR 0041, the overlap check), in the spirit of ADRs 0042 and 0071.

## Context

Item 4 of the gap read compares every version posted by the pinned cutoff in the pinned revision and in the newer release (`anamnesis-data/clinical_trials_history` at v2026.11.04, read under ADR 0073), and required 99.9% of them to agree on every column a served feature reads. The run of 2026-11-08 found 99.8131%, with 0 bugs. The shortfall was the sponsor: 7,437 versions of 4,477 trials hold another sponsor key in the release. 4,452 of those trials differ on their latest pinned version too, so the release did not invent per-version names; it holds another name across the history. For the 103 such trials in the kept API v2 pull, the current API's sponsor key equals the release's in 102, the pinned revision's in 0. The registry renamed these sponsors without a new version, and the newer scrape shows the new name on every version. Without the sponsor columns the agreement was 99.980%.

The census of ADR 0042 already treats a registry change made without a new version as a cause to count, not a failure, and ADR 0071 bounds such changes by a share. The overlap check had no such rule.

## Decision

1. **Sponsor differences by cause.** Every version both releases hold whose `sponsor_key` or `sponsor_is_individual` differs is classified from the two releases' raw lead sponsor names for that version (the pinned core files and the release's adapted core files):
   - a **registry rename made without a new version** when the raw names differ (after HTML entities are decoded and spaces collapsed), split into "another name" and "case or punctuation";
   - otherwise it is **unexplained**: the same raw name (a bug, or a class change under the same name), a name on one side only, the same name in another Unicode form, or no raw row.
2. **The check passes only if all three hold:**
   - every sponsor difference is a registry rename, with 0 unexplained;
   - the renamed trials are at most 2% of the release's trials (`MAX_RENAMED_SHARE`);
   - the agreement on every other column a served feature reads is at least 99.9% (`MIN_AGREEMENT`), with 0 bugs in any column.

   The agreement on all feature columns, sponsor included, is still computed and reported.
3. **Pinned rows keep their names.** The release's names are never written onto pinned rows, in the warehouse, the combined rows of ADR 0074 or the live state. The gap store holds only the release's versions posted after the cutoff.
4. **The renamed trials with gap rows** are reported by count only, with the count whose first gap version shows another sponsor key than their last pinned version: that is what API v2 would also show on their next update.

## Alternatives

- **Keep the 99.9% bar on every feature column:** the check would stop on a registry behavior the census already accepts, and the gap read would fall back to Gap (a) of ADR 0041.
- **Take the release's names onto the pinned rows in the live state only:** the live state would match API v2 from day one, but the live state would no longer equal the rows the registered results and the reference files rest on, and the reference check of ADR 0074 would compare two different histories.
- **Gap (a) of ADR 0041 (no gap rows):** the versions API v2 never shows are lost for good.

## Consequences

- `trialpulse.gap.compare` writes `sponsor_differences` and a three-condition `verdict` to data/gap/overlap.json, and writes the gap store only when the verdict passes.
- A renamed trial shows a sponsor change at its first gap version on the live path. Its sponsor track record (ADR 0018) then follows the new key, as it would after any rename the live path sees.
- The rule applies to this gap read only. A later read of another release needs its own check.
