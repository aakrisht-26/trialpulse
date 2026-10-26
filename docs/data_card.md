# Data card

Research demo. Not medical advice. Not for patient decision-making.

**Source: [ClinicalTrials.gov](https://clinicaltrials.gov).** Data as of 2026-09-25, the latest date on which ClinicalTrials.gov posted a record version in the pinned dataset revision. TrialPulse normalizes the registry records and adds derived early-stop risk scores and drivers, which are not part of ClinicalTrials.gov (ADR 0008).

This card describes the data TrialPulse is built from. What the model does with it is in [model_card.md](model_card.md); the registered results are in [results.md](results.md).

## Sources

| Source | What TrialPulse uses it for | Version |
| --- | --- | --- |
| Hugging Face dataset [`brbk/clinical_trials_history`](https://huggingface.co/datasets/brbk/clinical_trials_history), config `core` | Every version of every registry record: the version clock, the outcomes and every versioned feature | Revision `v2026.09.26` (commit `3007d00a5a07`): 4,444,542 versions of 604,583 trials, 96 columns ([data_dictionary_history.md](data_dictionary_history.md)) |
| The same dataset, config `interventions` | The interventions of each version (ADR 0020) | Same revision |
| [ClinicalTrials.gov API v2](https://clinicaltrials.gov/data-api/api) | The stop-reason texts labeled in Step 6, the parity checks against the dataset, and the current-record phase of the descriptive phase tables of [eda.md](eda.md) (Tables 5 and 15; ADR 0006 keeps it out of every model and allows it only in a labeled sensitivity analysis, which has not been run); from Step 14, the daily updates | Snapshot pulled on 2026-09-23 |
| ClinicalTrials.gov's internal history endpoint | Verification samples only, in Step 2 (ADR 0011); never a source of features, outcomes or scores | Step 2 samples |
| Stop-reason labels | An LLM (`openai/gpt-oss-120b` on Groq) labeled a stratified sample of 10,000 reason texts; a TF-IDF and logistic regression classifier distilled from those labels labels every early stop ([reason_labeling.md](reason_labeling.md)) | Final classifier of 2026-10-21 |

The dataset author built the version history from ClinicalTrials.gov's own record history. Its agreement with the registry was checked in Step 2 ([feasibility_report.md](feasibility_report.md)): 99.3% agreement with the official API v2 record on the compared fields of 200 trials, and 99.9% on 900 fields of 100 versions against the registry's own version history.

## Licenses and terms

- **The dataset** is licensed CC BY-NC 4.0, and access to it is gated by terms that ask users to cite it, to attribute the underlying data to ClinicalTrials.gov, and not to use the files commercially. TrialPulse is non-commercial, always (CLAUDE.md Section 2). The raw files are never redistributed: they stay in the gitignored `data/` folder.
- **ClinicalTrials.gov records** are used under the [ClinicalTrials.gov terms and conditions](https://clinicaltrials.gov/about-site/terms-conditions): every page that shows registry data names the source, says when ClinicalTrials.gov processed the data ("data as of") and says that TrialPulse changed it (ADR 0008).
- **TrialPulse's code** is MIT licensed ([LICENSE](../LICENSE)). TrialPulse publishes and uses the derived scores for non-commercial purposes only (CLAUDE.md Section 2), as the dataset's license and access terms require of TrialPulse.

## Citation

Cite the version history as its author asks, and attribute the records to ClinicalTrials.gov:

```bibtex
@misc{clinicaltrials_history_2026,
  title  = {ClinicalTrials.gov Version History: A longitudinal mirror},
  author = {Bret Gaulin},
  year   = {2026},
  url    = {https://huggingface.co/datasets/brbk/clinical_trials_history}
}
```

Registry records: ClinicalTrials.gov, U.S. National Library of Medicine, https://clinicaltrials.gov, data as of 2026-09-25.

## What the data covers

- **Population** (CLAUDE.md Section 6, ADR 0013): interventional trials first posted on or after 2008-01-01. Whether a trial is interventional is decided at each landmark from the version in effect then.
- **Cohort** ([data_audit.md](data_audit.md), part 2): 330,121 trials with at least one landmark row, and 1,448,969 landmark rows. Through the data cutoff, 22,446 of these trials were terminated and 11,809 withdrawn (the early stops), 176,659 completed, 28,013 are censored under the UNKNOWN rule (ADR 0014) and 91,194 were open at the cutoff. 3,065 trials whose terminal status was later replaced by an open one are excluded (Section 6).
- **Fields:** single-value protocol fields that have a version history (Section 8): design, dates, enrollment, status, sponsor class and versioned text. The core config has no per-version phase, condition, arm or location field, so the model reads no phase (only the phase a title states, ADR 0006) and no therapeutic area (ADR 0007). Interventions come from the `interventions` config (ADR 0020).

## Personal data

No patient data exists anywhere in TrialPulse: registry records describe trials, not participants.

Registry records do name people: investigators, study contacts and some sponsors. TrialPulse never stores, logs or displays them:

- the warehouse build never reads the four core columns that name a person, and every API v2 page loses its contact, official and investigator keys before it is cached, even when the request did not ask for them;
- every free text is stored in one canonical form in which email addresses are replaced by a marker (ADR 0012);
- a sponsor that is an individual, or that is named by a personal title, gets no stored name and no key, and its track record is the class-level rate (ADRs 0012, 0018 and 0022);
- every document reports counts and classes only; a sponsor appears by its class.

## Known biases and limitations

1. **Self-reported data.** Sponsors register and update their own records. Updates can be late, incomplete or never made, so a record can show a trial as open long after it ended. The version clock (CLAUDE.md Section 6) dates each change by when the public could see it, not by when it happened.
2. **A US-centric mandate.** The 2007 FDAAA law made registration mandatory for many trials under US jurisdiction, and the 2017 Final Rule tightened what must be reported and when. Trials outside that mandate register voluntarily or in other registries, so coverage and reporting habits differ by country and sponsor, and they changed over time, which is a source of drift.
3. **Estimated post dates before 2018.** Every version posted from 2008 to 2016, and 11.9% of those posted in 2017, carry an ESTIMATED post date: about the submission date plus one day, derived by the registry rather than recorded ([eda.md](eda.md), Table 11). Event times and point-in-time features in those years are correct to within days, not exactly.
4. **UNKNOWN statuses.** The registry shows 55,942 of the eligible trials as UNKNOWN in their latest version: their completion date passed without a status verification in 2 years. TrialPulse derives the same state from the registry's rule (ADR 0014) and censors the 64,040 eligible trials whose last state is lapsed at the data cutoff at that state's status verified date, rather than counting them as stopped or completed. 28,013 of them are cohort trials (see What the data covers). For 35,938 that date is on or before registration, so they have no landmark row, and the remaining 89 have none because they are lapsed or not interventional at every landmark ([data_audit.md](data_audit.md), cohort funnel). Counting them as early stops instead (the sensitivity analysis of ADR 0029) lowers the champion's AUC at 12 months from 0.730 and 0.723 to 0.667 and 0.655 on the test origins ([results.md](results.md)). Some of these trials may have stopped without their record saying so.
5. **Status reversals.** 3,065 trials showed a terminal status that a later version replaced with an open one; they are excluded from training and evaluation.
6. **Stop reasons.** Of the 35,772 early stops of the Section 6 cohort, 32,144 (89.9%) have a reason text in the registry's current record ([reason_labeling.md](reason_labeling.md)); Step 2 found 91.5% on the broader feasibility cohort. The texts are those each record showed when it was pulled (2026-09-23), which can differ from the text posted with the first terminal version. The distilled classifier agrees with the reference labels on 76.7% of 300 held-out texts (macro-F1 0.769). The reference labels come from an adjudicated model panel (ADR 0010), not from people.
7. **Follow-up.** Outcomes are known through 2026-09-25 only. Recent registrations have short follow-up, and the evaluation handles the open trials as censored (IPCW, ADR 0017).
8. **What is not measured.** No phase or therapeutic area per version, no site counts (no versioned locations), and no information from outside the registry (funding, regulatory decisions, scientific results).
