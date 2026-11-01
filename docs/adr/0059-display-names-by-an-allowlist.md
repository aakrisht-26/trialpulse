# 0059. Display names by an allowlist

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (review of Steps 14 to 16, finding 3).
- Amends ADR 0044, decision 2 (who gets a display name). The keyed sponsor ids of ADR 0044 are unchanged.

## Context

ADR 0044 hid a sponsor's name only when its class was OTHER, UNKNOWN or AMBIG and the name was two or three plain words with no organization word; every other name was stored and shown by the API and the dashboard. The review of Steps 14 to 16 found that this blocklist showed people's names: a middle initial ("Jane A. Example"), a surname first ("Example, Jane"), degrees outside ADR 0012's list (MSc, PharmD, MBBS), four or more words, and any plain two-word name under INDUSTRY, NETWORK, OTHER_GOV or FED. On 20,000 of the 222,417 live API v2 records, 31 distinct person-like names would have been shown (counted by masked shape only). CLAUDE.md Section 2 forbids storing or displaying the names of people.

## Decision

1. **A name is shown only if:**
   - it holds an organization word from the committed list in `src/trialpulse/live/organization_words.py` (English, Spanish, Portuguese, French, Italian, German, Dutch, Nordic, Polish, Czech, Hungarian and Turkish stems, legal forms such as GmbH and Ltd, dotted forms such as S.A. and A/S, and abbreviations such as NHS and IRCCS), matched word by word on the sponsor key; or
   - it is a single word once punctuation is a space; or
   - its class is INDUSTRY, NIH, FED, OTHER_GOV or NETWORK and it leads at least 5 live trials (rows of `serving.trial_display`).
2. **Every other name is hidden**: no row in `serving.sponsors`, and the API answers a lookup of it exactly as a name that does not exist (ADR 0047). An individual (ADR 0012, ADR 0022) has no name to show, as before.
3. **When it is decided.** The number of live trials changes, so the decision is made when display rows are saved (the bulk command and every daily run) and again for each sponsor one of whose trials leaves the live state. A name shown only for leading 5 trials goes when the sponsor leads fewer. When several records of one sponsor arrive together, the name is shown only if every one of them allows it.
4. **The list errs toward hiding.** Stems are long enough not to open common names ("medic" would open "Medici", so the full words are listed), and short legal forms that are also names or initials (Ab, Co, Sa, Se, Va) are matched only with their dots. A word is added by a reviewed change to the list, with a test.

## Alternatives

- **Extend ADR 0044's blocklist** (more degree titles, initials, comma order): each new person shape needs a new rule, and a missed shape is published.
- **Show no name at all:** the portfolio page (Section 13) then needs another way to find a sponsor, and large sponsors lose a public, useful label.

## Consequences

- Some organizations' names are hidden: a small sponsor whose name holds no listed word and has more than one word, and organizations of class OTHER, UNKNOWN or AMBIG without a listed word whatever their size. Their trials keep their scores, with the sponsor shown by class only.
- The measurement on real records, before and after, is in docs/progress.md (review of Steps 14 to 16).
- Tests hold every person shape the review found, the masked shapes of its measurement, organization names in each listed language, the boundary at 5 trials, and the removal of a stored name when a later record or a smaller count hides it.
