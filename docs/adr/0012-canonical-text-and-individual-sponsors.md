# 0012. Canonical version contract: one plain-text form for both sources, and no names for person-named sponsors

- Date: 2026-09-30
- Status: **Accepted** by Aakrisht on 2026-09-30 (Step 3 review). Proposed in Step 3.

## Context

Step 3 builds one canonical version schema for the offline history dataset and the live API v2 path (CLAUDE.md Step 3, Section 12 serving parity). Two findings from building it need a decision.

1. **The two sources hold the same text in different markup.** The dataset stores most long texts as HTML: 4.42 million of 4.44 million versions have HTML tags in `eligibility_criteria`, with entities such as `&gt;` and `&#x27;`. API v2 returns Markdown for the same record (`* item`, `1. item`, backslash escapes such as `\>` and `\^`), and joins a paragraph's lines with a space where the dataset keeps a line break. Without a shared normal form, an unchanged trial would get a different text hash from each source, and the live path would look like a new version.
2. **Individual sponsors are not only class INDIV.** Step 2 found 567 trials with an INDIV-class sponsor, and 3,574 more trials (2,163 distinct names) whose lead sponsor name is a person's name with a degree title (for example "Jane Doe, MD") under another class, mostly OTHER. Aakrisht's instruction for Step 3 is to store no names for individual sponsors; CLAUDE.md Section 8 defines sponsor identity as the normalized lead sponsor name.

## Decision

1. **One canonical plain text** (`trialpulse.contracts.text.normalize_text`), applied to every free-text field from either source:
   - HTML input (detected by block tags) is split at block tags (`p`, `li`, `br`, `ul`, `ol`, headings), inline tags are removed and their text kept; Markdown and plain input is split at blank lines and at list items, whose markers are removed. List items follow CommonMark: a bullet or an item numbered 1 may interrupt a paragraph, other numbers may not ("2)" after a plain paragraph line stays in the paragraph); inside a list, including a paragraph indented under an item, any number starts the next item;
   - entities are unescaped (repeatedly), Markdown backslash escapes of ASCII punctuation are removed;
   - each block (a paragraph or a list item) becomes one line, with its whitespace, soft line breaks included, collapsed to single spaces;
   - email addresses are replaced with `[email removed]`.

   Each normalized text is stored once, keyed by its SHA-256; versions hold the hash.
2. **A sponsor is an individual** when its class is INDIV, or when its name carries a personal degree title (PhD, MD, MPH, DrPH, with or without periods) and no organization word (university, hospital, institute, foundation, pharma, inc and similar). An individual's name and key are stored empty on both paths; Step 7's sponsor track record uses the class-level rate for them.

## Evidence

- Text, tuning samples: two seeded samples of 60 and 200 interventional trials unchanged since the dataset (one API v2 request each). The first rule set left 5 differences, all Markdown escapes (`\^`, `\&`); the escape rule now covers all ASCII punctuation, as CommonMark does. The warehouse parity sample (below) then found one paragraph where "2)" follows a plain paragraph line and must not start a list item; fixing that by the CommonMark rule alone broke 8 texts in the 200-trial sample, where API v2 indents a paragraph that belongs to a list item. The final rule handles both. On these samples the normalized texts agree for 259 of 259 trials in every text field.
- Text, fresh check after the fixes: a new seeded sample of 300 trials (one request), not used for any rule change: 300 of 300 agree in every text field (brief title, official title, brief summary, eligibility criteria, why stopped).
- Whole rows: `uv run python -m trialpulse.warehouse.parity` compares every canonical column, with the results in `docs/data_audit.md`.
- Sponsor keys agree for every trial except 3 (1 in the fresh sample, 2 in the parity sample), each an organization whose name the registry changed without posting a new version (for example "Indonesia University" to "Universitas Indonesia").
- Sponsors: 2,676 distinct names under a class other than INDIV match the person rule; most have 4 words or fewer, and 3 contain a word that may indicate an organization the rule does not list (for example a non-English word for hospital). Counts per build are in `docs/data_audit.md`.

## Alternatives

- **Keep the raw markup and compare sources loosely.** Simpler, but every live record would hash differently from its dataset version, which breaks change detection, idempotency and the Step 14 parity test.
- **A full HTML-to-Markdown converter.** Heavier (a new dependency), and API v2's Markdown flavor would still need its own rules; the block-level plain form is what the features need (eligibility items are counted per line).
- **Individuals by class INDIV only.** Would store about 2,000 people's names that sit under class OTHER, against the Step 3 instruction's intent and CLAUDE.md Section 2.
- **Fuzzy person-name detection (a name model).** Catches more, but is heavier, less predictable and not explainable in one line; the degree-title rule is transparent and tested.

## Consequences

- Line breaks inside a paragraph are not kept; list numbering is not kept. Features count items per line.
- The degree-title rule misses individuals named without a title, and could in principle flag an organization with a title and no organization word; the audit reports its counts each build.
- CLAUDE.md Section 8 sponsor family: "Sponsor identity is the normalized lead sponsor name" gains "except for individual sponsors (this ADR), whose track record is the class-level rate."
