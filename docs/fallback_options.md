# Fallback options for the version history

Written on 2026-09-23 while Hugging Face access to [`brbk/clinical_trials_history`](https://huggingface.co/datasets/brbk/clinical_trials_history) is still pending. The dataset's main branch was last modified on 2026-05-12 ([API metadata](https://huggingface.co/api/datasets/brbk/clinical_trials_history)), although its card promises weekly refreshes. This note compares the other ways to get point-in-time registry data. Every factual claim links to the page it was checked against. Figures marked "estimate" are computed here, not quoted. Nothing larger than 50 MB was downloaded; the pages read are cached under `data/research_cache/` (gitignored).

## 1. AACT monthly snapshots (CTTI)

[AACT](https://aact.ctti-clinicaltrials.org/landing) is CTTI's relational copy of ClinicalTrials.gov. The landing page says it is "refreshed daily" and offers "Historical snapshots", but downloading them now requires a free account: "Create an account to connect to the cloud database with your own credentials, download full snapshots". The old public listing URL now returns 404; a Wayback capture shows it was still public on 2026-04-06 ([CDX listing](https://web.archive.org/cdx/search/cdx?url=aact.ctti-clinicaltrials.org/downloads/snapshots*&output=txt&fl=timestamp,original&filter=statuscode:200&collapse=urlkey)).

**Which dates, how far back.** AACT's documentation stated: "On the first of each month, a permanent copy of the flat files is created, archived and will remain available for download from this website" ([archived page, captured 2024-12-07](https://web.archive.org/web/20241207113643/https://aact.ctti-clinicaltrials.org/archive/pipe_files)). The archived listings show monthly archives in two formats from **January 2017** onward:

| Year | PostgreSQL dump archives ([listing, captured 2025-07-24](https://web.archive.org/web/20250724005151/https://aact.ctti-clinicaltrials.org/downloads/snapshots?type=pgdump&year=2017)) | Flat-file archives ([listing, captured 2025-08-02](https://web.archive.org/web/20250802114102/https://aact.ctti-clinicaltrials.org/downloads/snapshots?type=flatfiles&year=2017)) |
| --- | --- | --- |
| 2017 to 2020 | 12 per year | 12 per year |
| 2021 | 10 (no July, September) | 10 (no July, August) |
| 2022 | 11 (no August) | 11 (no August) |
| 2023, 2024 | 12 per year | 12 per year |
| 2025, 2026 | Monthly archives present; the captures mix them with daily files | Same |

The year pages linked above differ only in the `year=` parameter. The earliest archive is `20170105_clinical_trials.zip` (2017-01-05). Before 2017 there is only a partial copy of the 2016-03-27 flat files on Figshare (section 2).

**Format and size.** The PostgreSQL dump zips hold a `pg_restore` dump plus a data dictionary ([archived instructions](https://web.archive.org/web/20250420063042/https://aact.ctti-clinicaltrials.org/archive/snapshots)). The flat-file zips hold one pipe-delimited file per table. The 2017 monthly dumps were 610 to 737 MB each, and daily dumps in February 2026 were 2.21 to 2.22 GB ([listing, captured 2026-02-07](https://web.archive.org/web/20260207045218/https://aact.ctti-clinicaltrials.org/downloads/snapshots?type=pgdump&year=2017)). Each download is the whole database, not single tables. Estimate: one snapshot every 6 months from 2017-01 to 2026-07 is about 20 files and 25 to 35 GB; every month is about 110 files and 150 to 200 GB.

**Content per snapshot.** The data dictionary lists 51 tables. `studies` holds "study title, date study registered ..., dates for study start and completion, phase of study, enrollment status". `conditions` holds the conditions as entered. `browse_conditions` holds NLM's MeSH terms for the conditions, and a `mesh_archive` schema keeps older MeSH versions ([data dictionary, captured 2024-05-17](https://web.archive.org/web/20240517143732/https://aact.ctti-clinicaltrials.org/data_dictionary?trk=article-ssr-frontend-pulse_little-text-block)). So every snapshot gives the phase, conditions and MeSH terms **as they stood on that date**. These are exactly the fields that the Step 2 stability audit showed change too often to use from the current record (phase 4.0%, phase group 3.3%, conditions 8.0%).

**Terms of use.** AACT pages carry this disclaimer: "CTTI encourages the use of all materials listed on this site ... We do ask that you acknowledge the source", with a suggested citation (footer of the [2026-02-07 capture](https://web.archive.org/web/20260207045218/https://aact.ctti-clinicaltrials.org/downloads/snapshots?type=pgdump&year=2017)). No non-commercial restriction is stated there. TrialPulse stays non-commercial under CLAUDE.md Section 2 either way.

**Would it support our landmarks?** Partly.

- Snapshots every month (or every 6 months) at or before each landmark give point-in-time phases, conditions and MeSH terms, with a lag of at most one month (monthly) or six months (semiannual). Taking the latest snapshot on or before L keeps it leak-free.
- Status changes and event times are observed only at snapshot resolution. The event date can be taken from `last_update_posted_date` of the first snapshot that shows TERMINATED or WITHDRAWN. That date is exact only when no later update came before that snapshot; otherwise it falls between two snapshots.
- **Coverage starts in 2017.** Landmarks before 2017-01 cannot be rebuilt, yet the locked origins need training landmarks before 2016-01-01 (origin 2016). With AACT alone, the earliest usable origin would be about 2018 (training on 2017 landmarks only). A realistic design would move the origins later, for example dev 2020 and 2021, test 2022 and 2023, which changes the locked Section 6.
- The schema changed over the years: file names moved from `YYYYMMDD_clinical_trials.zip` (2017) to `YYYYMMDD_clinical_trials_ctgov.zip` and `YYYYMMDD_export_ctgov.zip` (2025). Harmonizing the tables across nine years is real work.

## 2. Other public sources checked

| Source | What it is | Coverage | License | Useful? |
| --- | --- | --- | --- | --- |
| [Figshare 3811626](https://figshare.com/articles/dataset/AACT_Database_Pipe-Delimited_/3811626) ([API](https://api.figshare.com/v2/articles/3811626)) | "a static copy of the March 27, 2016 pipe-delimted download of the AACT ClinicalTrials.gov database" | One date; the record lists only 2 files (`clinical_study_noclob.txt`, 161 MB; `intervention_browse.txt`) | CC BY 4.0 | No: one partial date |
| [Zenodo 13984069](https://zenodo.org/records/13984069) | AACT flat files `20240927_export_ctgov.zip` | One date (2024-09-27) | CC BY 4.0 | No: one date |
| Wayback captures of ClinicalTrials.gov `AllPublicXML.zip` ([CDX](https://web.archive.org/cdx/search/cdx?url=clinicaltrials.gov/AllPublicXML.zip&output=txt&fl=timestamp,statuscode,length&filter=statuscode:200)) | Full-registry XML download | 5 captures: 2019-12-18, 2021-05-07, 2021-09-01, 2022-10-22 (twice); 1.45 to 2.02 GB | Not stated at source (see open questions) | No: too sparse |
| [`cthist` R package](https://cran.r-project.org/package=cthist) (v2.1.12, 2025-08-29; [source](https://github.com/bgcarlisle/cthist)) | "Retrieves historical versions of clinical trial registry entries" | Per trial, on demand | AGPL (>= 3) | Same as option 3: `R/clinicaltrials_gov_version.R` calls `clinicaltrials.gov/api/int/studies/`, the internal endpoint |
| [`louisbrulenaudet/clinical-trials`](https://huggingface.co/datasets/louisbrulenaudet/clinical-trials) | ClinicalTrials.gov records with embeddings | Current state only; last modified 2025-06-19; card mentions no version history | Apache-2.0 | No |

No other public dataset of ClinicalTrials.gov version history or dated full-registry snapshots turned up. The search was: web search for version-history datasets on Zenodo, Kaggle and Hugging Face, and Wayback CDX for the registry's full download.

## 3. Rebuilding histories through the internal endpoint (cost estimate only)

Measured in Step 2 part f: 150 trials needed 150 change-log requests plus 973 version requests, 1,123 requests in total. At 20 requests per minute or less, that took 56.1 minutes: 7.49 requests per trial, or 6.49 versions per trial. The dataset card reports 4,333,631 rows over about 583K trials, 7.43 versions per trial, which gives an upper bound of 8.43 requests per trial.

| Scope | Requests (estimate) | At 20 per minute | At 40 per minute |
| --- | --- | --- | --- |
| Full cohort, 420,073 trials | 3.15M to 3.54M | 109 to 123 days, running nonstop | 55 to 62 days |
| Stratified sample, 20,000 trials | 150K to 169K | 5.2 to 5.9 days | 2.6 to 2.9 days |

Using the endpoint this way would need an ADR. CLAUDE.md Section 7 item 3 allows it only for small verification samples in Step 2, at 20 requests per minute or less, and never as a production dependency. A multi-day job also falls under the one-hour rule (Section 3). A 20,000-trial sample would fail the "at least 20,000 early stops" GO criterion by construction, unless early stops were oversampled and weighted back.

## 4. Ranked recommendation

1. **AACT snapshots from 2017, used with the Hugging Face dataset if access ever arrives, or alone if it does not.**
   - Effort: medium to high. Download about 20 semiannual archives (25 to 35 GB, a few hours), harmonize the 2017 to 2026 schemas, and build point-in-time tables per snapshot date. It replaces Step 2 parts a to d and changes Step 3.
   - Gain: point-in-time phase, conditions and MeSH terms at snapshot dates, which could let phase and therapeutic area back into the model.
   - CLAUDE.md changes: Section 7 (new primary source and its citation); Section 6 (version clock becomes the snapshot date, the event-time definition gains snapshot resolution, and the walk-forward origins move later because coverage starts in 2017); Section 8 rule 2 (list fields become versioned at snapshot resolution); Step 2 criteria and Step 3 build.
   - First step for Aakrisht: create a free AACT account and confirm the 2017 archives are still downloadable.
2. **Keep waiting for Hugging Face access.** No effort and no CLAUDE.md change. The risk is an indefinite wait, since the dataset has not changed since 2026-05-12. Asking the author through the dataset's discussion page would be a public action, so it is Aakrisht's decision.
3. **Rebuild a 20,000-trial stratified sample through the internal endpoint.** Effort: medium, and about 5 to 6 days of polite background requests. It gives full versioned records, including list fields. It needs an ADR changing Section 7 item 3 and the one-hour rule, and a rethink of the early-stop criterion. The full cohort (about 109 to 123 days) is impractical.
4. **Not viable:** `cthist` (same endpoint as option 3), the Wayback `AllPublicXML.zip` captures (5 dates in four years), and the single-date Figshare and Zenodo copies.

## Open questions

- Do AACT's monthly archives back to 2017 remain downloadable after the site redesign? The old listing returns 404 and downloads now need an account, which only Aakrisht can create.
- Under what terms does ClinicalTrials.gov publish the registry data? Its terms page ([link](https://clinicaltrials.gov/about-site/terms-conditions)) is rendered in the browser and could not be read from a script, so no claim about it is made here.
