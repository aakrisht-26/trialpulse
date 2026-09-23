# 0008. Attribute ClinicalTrials.gov, show a "data as of" date, and state what TrialPulse adds

- Date: 2026-09-23
- Status: Accepted (decided by Aakrisht on 2026-09-23)

## Context

The [ClinicalTrials.gov terms and conditions](https://clinicaltrials.gov/about-site/terms-conditions) ask anyone who publishes or distributes the data to:

- attribute the source as ClinicalTrials.gov;
- keep the data current;
- clearly display the date the data were processed by ClinicalTrials.gov;
- state any modifications made to the data.

The page renders in the browser only, so a script could not read it (see `docs/fallback_options.md`). Aakrisht read it and reported these terms on 2026-09-23.

TrialPulse modifies the data: it normalizes records into a canonical schema and adds derived early-stop risk scores and drivers, which are not part of ClinicalTrials.gov.

## Decision

Every place where TrialPulse publishes or displays registry data, or anything derived from it, shows these three items:

1. **Source attribution:** "Source: ClinicalTrials.gov", linked to https://clinicaltrials.gov.
2. **"Data as of" date:** the date ClinicalTrials.gov processed the data shown. For live data this is the data timestamp that API v2 reports (`/api/v2/version`, `dataTimestamp`). For a pinned historical source it is that source's date, for example the dataset revision or snapshot date.
3. **Modifications note:** a statement that TrialPulse normalizes the records and adds derived risk scores, and that those scores are not part of ClinicalTrials.gov.

This is a requirement for:

- the **README** (it states the attribution and the note now; once results or data are published, it also shows the data-as-of date of the data behind them);
- the **data card** (Step 12);
- **every dashboard page** (Step 16), next to the existing disclaimer.

"Keep the data current" is met by the daily job (CLAUDE.md Section 12), and the Model health page already shows data freshness.

## Alternatives

- **Attribution only in the README and data card.** Fewer places to maintain, but dashboard pages display registry data directly, so they would not meet the terms.
- **Show TrialPulse's processing date instead of ClinicalTrials.gov's.** Simpler to track, but the terms ask for the date ClinicalTrials.gov processed the data.

## Consequences

- The dashboard reads only from the API (Section 13), so the API must provide the data-as-of date, for example in `/v1/model` or in each response, for the pages to show it.
- The Step 16 acceptance check ("the disclaimer is visible on every page") extends to the attribution, the data-as-of date and the modifications note.
- The scoring and live pipeline must record the API v2 data timestamp with every run.
