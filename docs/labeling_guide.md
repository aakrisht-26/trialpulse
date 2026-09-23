# Labeling guide: why trials stop

This guide defines the eight labels used for the `why_stopped` text of early stops (TERMINATED or WITHDRAWN). It is the reference for the gold set that Aakrisht labels in the Streamlit app, and for the LLM prompt in Step 6. The label set and the operational and scientific groups are fixed in CLAUDE.md Section 11 and `config/project.yaml`.

Research demo. Not medical advice. Not for patient decision-making.

## How to label

- Read only the `why_stopped` text. Do not look up the trial or infer a reason from its other fields.
- Pick exactly one label: the **decisive** reason, meaning the one the text presents as causing the stop.
- If the text gives no reason, or the reason is too vague to place, use `other`.
- The examples below are paraphrased patterns written for this guide, not quotations from the registry.

## Labels

| Label | Group | Use it when the text says | Examples |
| --- | --- | --- | --- |
| `accrual` | operational | Recruitment or enrollment failed: too slow, too few eligible participants, target not reachable. | "Slow enrollment." "Unable to recruit enough participants." "Low accrual." |
| `business` | operational | A sponsor or company decision, portfolio or strategy change, with no more specific reason. | "Sponsor decision." "Business reasons." "Company discontinued the program." |
| `funding` | operational | Money ran out, was withdrawn, or was never secured. | "Lack of funding." "Grant not renewed." "Funding withdrawn." |
| `administrative` | operational | Operational or organizational problems: the investigator left, the site closed, contracts, regulatory paperwork, drug supply, study-team capacity. | "PI left the institution." "Study drug no longer available." "Site closed." |
| `covid19` | operational | The COVID-19 pandemic, unless another reason clearly dominates. | "Halted due to COVID-19." "Pandemic restrictions prevented study visits." |
| `safety` | scientific | Harm or a safety signal, including a data monitoring committee stop for harm. | "Stopped by the DMC due to adverse events." "Safety concerns." |
| `efficacy` | scientific | Lack of benefit or futility, including futility at an interim analysis; also a stop for early proof of benefit. | "Futility at interim analysis." "Primary endpoint unlikely to be met." |
| `other` | neither | Anything else, or no usable reason. | "Study terminated." "See publication." "Investigator decision." |

## Tie-break rules

1. **Several reasons:** label the decisive one. "Slow enrollment and the sponsor decided to close the study" is `accrual` if enrollment is presented as the cause of the decision, and `business` if the text presents the decision as independent.
2. **COVID-19:** any COVID-19 mention is `covid19`, unless another reason clearly dominates ("Terminated for futility; COVID-19 also slowed visits" is `efficacy`).
3. **Sponsor decision without detail** is `business`. A sponsor decision *because of* a named reason takes that reason's label ("Sponsor stopped the trial after the interim futility analysis" is `efficacy`).
4. **Interim analyses:** futility is `efficacy`; harm is `safety`. An interim analysis with no stated result is `other`.
5. **Investigator decision** with no reason is `other`. The investigator *leaving* is `administrative`.
6. **Funding versus business:** a company choosing not to spend money is `business`; an external funder (grant, agency) not paying is `funding`.
7. **Regulatory:** a regulator's request for more information or paperwork problems is `administrative`; a regulator stopping the trial for harm is `safety`.

## Where labels are stored

The app writes `labels/gold_labels.csv` with `nct_id`, `nct_version`, `split`, `label` and `labeled_at` only. The texts stay in `data/nlp/gold_sample.csv`, which is gitignored and never committed. The split (100 dev, 300 test) is fixed by the seed in `config/project.yaml`; the test split is never used for prompt or model tuning.
