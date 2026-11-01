# 0042. The census of unchanged trials is the parity acceptance test

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 14 decision 7: Option 7b, on four conditions).
- Changes the Step 14 acceptance criterion "For 50 trials unchanged since the dataset cutoff, the API v2 path and the dataset path produce identical canonical rows". Refines ADR 0012 (canonical text) on the Markdown path only.

## Context

A comparison of every open or UNKNOWN interventional record found 1,950 of 146,982 unchanged trials (1.33%) differing on something, nearly all from registry edits made without a new version (sponsor renames, class changes, title case). A literal 50-trial check would stop on a known cause about half the time and miss real bugs most of the time: the 288 fields where API v2 writes `<!-- -->` between adjacent lists, which `normalize_text` kept, sit on 0.2% of rows.

## Decision

1. **The census is the test:** the API path against the dataset path on every unchanged trial (the official post date equals the dataset's last version), with the seeded 50 reported as a named subset. Run on the laptop at bootstrap (parity layer 4, ADR 0038).
2. **Condition 1: `normalize_text` is fixed first, on the Markdown path only.** A line that is only an HTML comment (`<!-- ... -->`, CommonMark HTML block type 2) is dropped as a blank line. HTML texts (the dataset's) take another path and are unchanged; dataset texts in plain text with such a line, if any, are found by the old-against-new warehouse comparison on the laptop.
3. **Condition 2: every field a served feature reads must match,** except a difference caused by a registry change made without a new version (such as a sponsor rename or a class change). Each such difference is counted with its cause; the same raw value giving a different canonical value is a bug with no exception. For titles, `title_phase` must match.
4. **Condition 3:** brief summary and eligibility differences are counted by cause, not failed: no served feature reads them.
5. **Condition 4:** a test shows that a difference in a field no feature reads never creates a version row, a history row or a `record_change` reason. This holds by construction: the live identity of a record is the feature hash of ADR 0037, and `serving.content_hash` is that hash.

## Alternatives

- **Option 7a, a literal 50:** honest, likely to stop for a known cause, weak if it passes.
- **Redrawing the sample until it passes:** rejected.
- **A wider rule for backslashes:** fixed 26 fields and broke 68 in the census.

## Consequences

- Condition 3 is a weakening of the original criterion, accepted by Aakrisht because no served feature reads those fields.
- A difference in a summary or eligibility text, or in the sponsor's display name alone, does not move a score or write a history row.
