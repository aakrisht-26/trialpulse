# 0018. Sponsor identity and aliases for the track record

- Date: 2026-10-07, alias rule revised on 2026-10-08 after the independent review of Step 7
- Status: **Accepted** by Aakrisht on 2026-10-10 (review of Step 7): the rule as built, with the one-year wait. He had asked for the rule to be recorded as an ADR (Step 2 review, guidance item 5; Step 7 instructions). The rule differs from the wording of guidance item 5, for the reason measured under Context; his review: "My original wording (names that replace each other are aliases) was wrong: it chains into one giant sponsor."
- Refines CLAUDE.md Section 8 (sponsor family): "Sponsor identity is the normalized lead sponsor name (lowercase, punctuation stripped); no fuzzy entity resolution in v1."

## Context

The sponsor family counts, for the sponsor of a landmark row, its earlier registrations and the early-stop rate among its ended trials. That needs an answer to "which trials belong to this sponsor on day L".

**Renames.** The registry renames organizations without posting new versions: a record shows the new name only from its next version on. Guidance item 5 says that "names that replace each other on the same trial are treated as aliases of one sponsor, so a rename does not start a new track record".

**What the data shows.** Among 4,444,542 versions the warehouse holds 52,797 distinct sponsor keys. The key of a trial changes between two of its versions on 35,443 trials, in 13,718 distinct ordered pairs of keys.

- Taken literally, "names that replace each other are aliases" joins 6,615 keys into one sponsor that includes the largest companies, institutes and hospitals of the registry: most changes are trials changing hands, not renames.
- Of the 582 pairs where one key replaced another on 10 or more trials, the old key goes on appearing on newly posted versions in 509. In 73 it is never shown again: those are the renames.
- A rename is not visible at once. Records switch to the new name one by one, as each gets its next version: for those 73 pairs, a median of 151 days lies between the first and the last trial that switched, and for the largest it is years.

**Rules that were tried and measured, in order.**

1. "The old name has not been shown since, and one name replaced it." It broke on the first trial of a renamed sponsor that went to a third party.
2. "The name that took most trials, at least two." It gave an active sponsor's whole record to whoever took two of its trials on one day: of 1,450 links whose old name had registered 100 or more trials, 1,444 ended, 1,276 of them within a week, when the old name posted again. One large company's record passed back and forth to three other companies a dozen times.
3. Rule 2, and "no version has shown the old name for 90 days". It stopped the back and forth, but one version under the old name erased a rename: any record of the old sponsor that was posted late, still showing the old name, took the inherited record away from every trial of the new name (found by a reviewer in the Step 7 review). And it took the majority among the trials that left the old name only: a trial that went on posting under the old name could switch the link off for 90 days, but never weighed against it.
4. The rule below, which weighs the trials that left a name against the trials that still post under it. With a wait of 90 days it still lent an active sponsor's record after a batch hand-over, because the trials that stay need time to show themselves: several cooperative oncology groups counted as renamed to the national institute that took over a batch of their trials, for three to seven months, until enough of their other trials had posted. The wait is therefore one year (see Evidence).

**Names that are not a sponsor identity.** The key `redacted` (the registry's placeholder for a withheld name) stands for 2,744 trials of many companies. And the warehouse rule of ADR 0012 (an individual has class INDIV, or a degree title such as MD or PhD and no organization word) misses names that open with a personal title: 934 keys on 1,359 trials.

## Decision

1. **Identity.** A sponsor is its normalized name (`sponsor_key`), as Section 8 says. A key carries no track record of its own when the sponsor is an individual (ADR 0012), when the key is a placeholder (`redacted`, `no sponsor`), or when the key opens with a personal title (dr, prof, professor, doctor, mr, mrs, ms, miss, sir and two Spanish and Dutch forms) and holds no organization word. `sponsor_has_identity` says which. Without an identity the three counts are missing and the early-stop rate is the class rate. Trials of such sponsors build nobody's record.
2. **The alias rule.** A trial's key on a day is the key of the version in effect at the end of that day. Take the versions posted before day L. For key A, a run starts with the first replacement of A: a trial posts a version, and its key is another one where it was A. From then on count
   - the trials that have left A, by the key they went to, and
   - the trials that still post under A: those with a version posted after the run's first day whose key is A, and that have not left A since.

   **Key A is an alias of key B on day L if B took at least 2 of the trials that left A, those are more than half of the trials that left A or still post under A, and the run's first replacement was posted more than 365 days before L.**

   The run is forgotten, and A stands alone again, once more trials still post under A than have left it: a sponsor that goes on using its name handed trials over and was not renamed. The year gives the old name's other trials time to show up: the registry asks for an update of every active record at least once in 12 months, so after a year the active trials of the old name have had to post.
3. **Choices that keep the rule this simple.**
   - A trial that left A for a name without an identity counts as having left A, for nobody. It can deny B the majority.
   - A trial that left A and came back counts once as having left and once as still posting, and one that leaves twice counts twice.
   - Aliases chain (A to B, later B to C). A link that would close a loop is not made, and it stays unmade until the majority of the trials that left A changes hands, even if the loop opens again.
   - Events of one day are applied in a fixed order (by kind, then by key and trial), so the result does not depend on the order the database returns rows in.
4. **The record of a sponsor on day L** is the sum, over its key and every key that is an alias of it on that day, of
   - registrations: interventional trials first posted under the key before L;
   - ended: interventional trials that the version in effect just before L shows as completed, terminated or withdrawn under the key;
   - stopped: of those, the terminated or withdrawn ones.

   The row's own trial is left out. A trial that was terminated and reopened before L is not ended.
5. **Strictly before L.** These counts, and the alias rule, read versions posted before L, not on L (Section 8: "the sponsor's trials that ended before L"). Every other feature reads the state at L, which includes versions posted on L.
6. The constants are in `config/project.yaml`: `features.sponsor_alias_min_trials` (2) and `features.sponsor_alias_wait_days` (365).

## Evidence

- Hand-built registries in `tests/features/test_sponsor.py`, one per clause of the rule: a rename keeps the record; one trial changing hands does not; a rename posted on the landmark day is not known yet; the rename counts from the day after the wait, measured from its first replacement, also when the second trial leaves later; one stray version under the old name does not undo a rename, and two against two is no majority; three trials still posting against two that left forget the run, and a later hand-over starts a new wait; a trial that left no longer counts as still posting; the majority successor inherits and an even split has none, seen from both names; a trial that left for a placeholder name still left; two versions on one day count as the later one; chains; two names that replace each other on one day; a refused link is not retried.
- The one-pass computation equals a plain replay of the registry up to each landmark, on the scripted test registry, with the configured wait, with none and with two years.
- A mutation check after the review: 33 one-line faults were put into the feature code, one at a time, 16 of them into the sponsor pass and the identity rule (for example: an exact half counts as a majority, the wait runs from the latest change, a trial that left still counts as posting, a refused link is retried, a name without identity keeps a key). 32 fail a test. The last one (a link chosen after the wait counts from the same day instead of the next) gives the same answers, because a day's landmark rows are answered before that day's versions are applied.
- On the real warehouse, landmark rows before 2018-01-01 with a sponsor identity (593,653 rows):

| Rule | Rows whose record gains registrations | Largest record | Links started, in force on 2018-01-01, ended | Ended within 90 days of starting | Links of names with 100 or more trials: started, ended |
| --- | --- | --- | --- | --- | --- |
| No aliases | 0 | 2,888 | | | |
| 1. One successor, old name not shown since | 155,474 (26.2%) | 8,818 | | | |
| 2. Majority of at least 2 trials | 86,637 (14.6%) | 6,164 | | | 1,450 and 1,444 |
| 3. Rule 2, and the old name quiet for 90 days | 82,133 (13.8%) | 2,892 | 736, 420, 316 | 147 | 27 and 21 |
| 4. Left against still posting, wait of 90 days | 102,634 (17.3%) | 3,076 | 774, 526, 248 | 80 | 39 and 24 |
| 4. Wait of 180 days | 98,308 (16.6%) | 2,896 | 690, 512, 178 | 39 | 30 and 15 |
| 4. Wait of 270 days | 94,166 (15.9%) | 2,896 | 641, 494, 147 | 37 | 24 and 10 |
| **4. Wait of 365 days (this ADR)** | **90,192 (15.2%)** | **2,896** | **582, 469, 113** | **11** | **21 and 8** |

- **Why a year.** With a wait of 90 days the rule lent an active sponsor's record after a batch hand-over. A large company's record (about 1,350 registrations) passed to the name of an affiliate three times, for 6 to 33 days each; five cooperative oncology groups counted as aliases of the national institute that took over a batch of their trials, for 87 to 213 days, and their own trials were given the institute's record (the largest gain of a single row was 3,010 registrations). In each case the trials that stayed had not posted yet: at day 91, 59 trials of one group had left and 11 had posted under its name; by day 190, 59 had. The step from 270 to 365 days removes most of the links that end soon after they start (37 to 11), which fits records that are updated once a year.
- **Under this rule,** 582 links started before 2018-01-01 and 469 were in force on that day. The 113 that ended all ended because the old name's own trials, or another name, took the majority away; 61 of them had been in force for more than a year, 41 for 91 to 365 days and 11 for 90 days or less. 2 links were refused because they would have closed a loop.
- **What the review's finding was about:** 111 of the 469 links in force have trials that still post under the old name, 794 trials in all. Rule 3 would have ended each of those links at the first such version.
- The median row that inherits gains 14 registrations (90th percentile 125, largest 2,416). The largest record is 2,896 registrations, against 2,888 without aliases: no runaway merge.
- **The largest links in force on 2018-01-01** are reorganizations one would expect: a federal department and its research office, a pharmaceutical company and the company that bought it (twice), cooperative groups merged into an alliance, universities and the medical center or health system their trials moved to, a state university that absorbed a medical school.
- **Where the rule is still wrong, or cannot know.** One of the largest links joins a company to the business it split off: most of its trials went to the new company, 37 still post under the old name, and both names now share one record. And 5 of the 8 ended links of large names are a university and its own hospital or cancer center, or two names of one center: trials move between the two names in both directions, so a link forms and ends. The registry gives no way to tell these from a rename; the one-year wait keeps each such link from flickering, not from existing.

## Alternatives

- **Guidance item 5 as worded** (every replacement is an alias). One sponsor of 6,615 names.
- **No aliases.** Simple and never wrong about hand-overs, but every renamed sponsor restarts at zero: the new name's rate falls back to the class rate until it has ended trials of its own.
- **Rule 3 above** (majority among the trials that left, and 90 quiet days). One late version under the old name ends the link for good, and a name most of whose trials stay put can still count as renamed.
- **A shorter wait.** See the table: with 90 days, 80 links end within 90 days of starting and a large company's record passes to its affiliate's name three times for a week or a month. Each step toward a year removes more of those, and the last step (270 to 365 days) removes most of what is left, which fits records that are updated once a year. The price is that a true rename counts a year after its first trial switched, where 90 days would do for the clean cases.
- **One more clause: a trial first posted under the old name during a run puts the name back in use.** Measured with a 90-day wait: 89,611 rows gain (15.1%), and of 27 links of large names 17 still end. It removes some false links and also true ones (a company that was split, a university whose hospital took over its trials), and it is one more rule to explain. The year does the same job with the rule as it is.
- **Weigh the trials that left against all open trials of the old name,** posted or not. That would adapt the wait to how often each sponsor posts. It needs the open portfolio of every name on every day, which the pass does not keep; it stays a possible refinement if the Step 10 ablation shows the sponsor family earning its place.
- **A curated list of renames,** or fuzzy matching of names. Section 8 rules out entity resolution in v1, and a list would need upkeep with every dataset refresh.

## Consequences

- For a year after the first trial of a renamed sponsor shows the new name, the new name has no inherited record and its rate leans on the class rate. That is the price of not knowing yet.
- A dormant sponsor whose trials mostly go to one other sponsor is joined to it, in both directions: the other inherits its record, and a trial that still shows the old name is given the other's record. For an acquisition that is right. For a sponsor that simply ended and handed its trials on, it is not, and the trials that still show its name get a record that is not theirs.
- A link can end: when enough trials post under the old name again, or when another name overtakes the successor. The record of the new name then drops back. The feature is point-in-time either way, because the rule reads only versions posted before the landmark.
- **A privacy gap in the warehouse, closed on 2026-10-10 (ADR 0022).** Names that open with a personal title were stored in the warehouse (`lead_sponsor_name`, `sponsor_key`), because the ADR 0012 rule did not catch them. Since ADR 0022 they are individuals: no name and no key is stored, and the canonical contract refuses such a key. Bare personal names without any title cannot be found by a rule at all.
- **Live path (Step 14).** A rename without a version shows there as an API v2 record whose sponsor name differs from the latest stored version with the same post date. Step 14 must record that as a replacement dated on the day it is first seen, so that the live and offline records agree from then on.
- The pass over the registry takes about 10 seconds on the real warehouse (6.7 million events).
