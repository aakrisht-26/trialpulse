# 0022. No stored names for sponsors named by a personal title

- Date: 2026-10-10
- Status: **Accepted.** Decided by Aakrisht in his review of Step 7 on 2026-10-10 ("fix it now, not at Step 14. Never storing personal names is a non-negotiable rule. Extend the ADR 0012 rule to sponsor names that open with a personal title, rebuild the warehouse, then rebuild the cohort and the features."). Decision 3 (more title forms) is Claude's extension of that instruction and is flagged for his review in the Step 9 report.
- Extends ADR 0012, decision 2 (who is an individual sponsor). Refines CLAUDE.md Section 2 (privacy) and Section 8 (sponsor identity).

## Context

ADR 0012 stores no name and no key for an individual sponsor: one whose class is INDIV, or whose name carries a degree title (PhD, MD, MPH, DrPH) and no organization word. Step 7 found that the rule misses names that open with a personal title. In the warehouse of schema version 2:

| Sponsor keys | Keys | Trials | Versions |
| --- | --- | --- | --- |
| Open with dr, dra, drs, prof, professor, doctor, mr, mrs, ms, miss or sir, and hold no organization word | 934 | 1,359 | 4,329 |
| Open with one of those and hold an organization word (a hospital, company or foundation named after a person) | 82 | 1,054 | 2,826 |
| Open with another title form (Pr, Mme, Dott., or an academic rank in front of a title: PD Dr., Priv.-Doz. Dr., Univ.-Prof., Assoc. Prof.) and hold no organization word | 48 | 76 | 189 |

None of the first kind was marked as an individual, so the warehouse stored those names and keys. The feature build of Step 7 already gave them no track record (ADR 0018), and no output held a name; the names sat in `versions.lead_sponsor_name` and `versions.sponsor_key` only.

## Decision

1. **A sponsor is also an individual when its normalized name opens with a personal title and holds no organization word.** The test runs on the key (lowercase, punctuation removed), with the title list and the organization words of `trialpulse.contracts.sponsor` (`PERSONAL_TITLE_KEY`, `ORGANIZATION_WORD_KEY`). As for every individual, the name and the key are stored empty, on the offline path and on the live path, which share `contracts.sponsor.sponsor`.
2. **The canonical contract refuses such a key.** A row whose `sponsor_key` opens with a personal title and holds no organization word fails the check `no_personal_title_sponsor_key` and is quarantined, however it was built.
3. **More title forms.** The title list also holds French, Italian and Spanish titles (pr, mme, mlle, docteur, professeur, dott, dottor, dottoressa, profesor, profesora, doctora), and a title may follow any number of academic ranks (PD, Priv.-Doz., Univ.-, ao., apl., Assoc., Asst., Adj., Hon., em.): "PD Dr. med. ...", "Univ.-Prof. Dr. ...", "Assoc. Prof. ...". A rank alone is not a title: "ASST" opens the names of Italian hospital trusts. This covers 48 more keys on 76 trials, all of class OTHER or OTHER_GOV. Unlike decision 1 it changes feature outputs, because the Step 7 feature build treated these keys as organizations: 177 landmark rows of 40 trials lose their sponsor identity and their three sponsor counts and get the class rate. Nothing else changes.
4. **An organization named after a person stays an organization:** a key that opens with a title and holds an organization word (hospital, university, institute, foundation, pharma, ltd, gmbh and the rest of the list). This is the same carve-out ADR 0012 makes for degree titles.
5. **Warehouse schema version 3.** The cohort build and the feature build refuse an older warehouse, which still holds the names.

## Evidence

- `tests/contracts/test_text_and_sponsor.py`: every listed title, with and without a period, in any case, with a degree title or a name outside ASCII behind it, gives an individual with no name and no key; organizations named after a person keep theirs; no key that `sponsor` stores is a person named by title. `tests/contracts/test_versions.py`: a row carrying such a key is quarantined.
- The rebuild of 2026-10-10 on the real data, in two stages so that each effect can be seen alone:

| | Before (schema 2) | Stage 1: decision 1 | Stage 2: decision 3 as well |
| --- | --- | --- | --- |
| Distinct stored sponsor keys | 52,797 | 51,863 | 51,815 |
| Stored keys that open with a personal title (the list of decision 3) and hold no organization word | 982 | 48 | 0 |
| Versions of individuals under a class other than INDIV | 20,554 | 24,883 | 25,072 |
| Trials with an individual sponsor | 4,960 | 6,302 | 6,374 |
| Names or keys stored for individuals | 0 | 0 | 0 |
| Cohort files (landmark rows, person-period rows, outcomes, for the final cohort and each origin) | | identical | identical |
| Feature outputs (20 tables) | | identical | 19 identical. In `features.parquet`, 177 rows of 40 trials differ, only in `sponsor_has_identity` (true to false) and the three sponsor counts (now missing) |

  Stage 1 is the acceptance Aakrisht set: no personal-title key remains, and the feature outputs are identical, because the feature build never read those keys. Stage 2 goes beyond it and is the reason the feature outputs are no longer byte for byte those of Step 7; the difference is the 177 rows above and nothing else.

## Alternatives

- **Fix it at the Step 14 rebuild,** as first proposed. Rejected by Aakrisht: the names would stay stored until then, and not storing personal names is not negotiable.
- **Every key that opens with a title is an individual, organization word or not.** It would also drop the names of 82 organizations named after a person, 17 of them with ten or more trials, and take their track records with them.
- **A name model that finds bare personal names.** A sponsor entered as a bare personal name under a class other than INDIV is still stored, because no rule can tell it from a short organization name. A model could find some, at the price of a new dependency and of a rule nobody can state in one line. It stays open; see Consequences.

## Consequences

- The data audit (`docs/data_audit.md`) reports, with every build, how many stored keys open with a personal title and hold no organization word. It must be 0.
- 82 keys that open with a title and hold an organization word are kept. Some of them may be a person's name followed by an affiliation ("Dr ..., ... University Hospital"); 36 of the 82 sit on a single trial. The rule cannot tell those from an organization named after a person.
- 20 keys on 89 trials open with a word that is only sometimes a title and are kept: 7 open with ASST (the hospital trusts), 9 with MD (4 of them of class INDUSTRY), and 4 with don, sr or "m d". Counted on 2026-10-10 with a wider list of words than the rule uses.
- Bare personal names without any title, under a class other than INDIV, remain possible. Their number is unknown by construction.
- Raw downloads under `data/raw/` are the registry's own records and contain every sponsor name. They are gitignored, never committed and never redistributed. No other file under `data/` holds a title-led sponsor name: the warehouse was the only derived store with sponsor names, apart from a handful of organization names in the parity and spike reports (7 values, none title-led, checked on 2026-10-10).
- The feature tests keep a title-led key in their scripted registry on purpose: `has_identity` must still refuse it if one ever reaches the feature build.
