# Labeling guide: why trials stop

This guide defines the eight labels used for the `why_stopped` text of early stops (TERMINATED or WITHDRAWN). It is the standard for the gold set, which holds reference labels from an adjudicated model panel (ADR 0010), and for the LLM prompt in Step 6. The label set and the operational and scientific groups are fixed in CLAUDE.md Section 11 and `config/project.yaml`.

Research demo. Not medical advice. Not for patient decision-making.

## How to label

- Read the whole `why_stopped` text, and nothing else: do not look up the trial or use its other fields.
- Label only what the text states. Never infer a reason the text does not give.
- Pick exactly one label: the **decisive** reason, meaning the one the text presents as causing the stop. The tie-break rules below settle texts with several reasons.
- If the text gives no reason, or the reason is too vague to place, use `other`.
- Watch negations: "no safety issues" is not `safety`.
- Write a one-line justification that names the deciding words, quoted from the text.
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

## How the reference labels are made

The gold set is not labeled by hand. It holds **reference labels from an adjudicated model panel** (ADR 0010):

1. Three independent labelers label each text. Each sees only this guide and the text, under an opaque id, and follows "How to label" above.
2. A unanimous label stands. For a split, a fourth labeler that did not label the text reads the text, this guide and the three justifications, decides by this guide, and records a rationale.
3. After that, a fresh labeler relabels a seeded random 10% of the texts, and the agreement with the reference labels is reported.

The panel outcome of each text is `unanimous` (all three agreed), `majority` (two agreed and the adjudicator kept their label) or `adjudicated` (the adjudicator chose a label no two labelers gave). Any model graded against these labels must come from a different model family than the panel. The Streamlit labeling app (`uv run streamlit run src/trialpulse/nlp/labeling_app.py`) remains as an optional review tool. It writes to `data/nlp/review_labels.csv`, never to the reference labels.

## Where labels are stored

The gold texts are the `why_stopped` texts of the cohort's early stops, taken from their current ClinicalTrials.gov records (API v2), so labeling does not wait for the version-history dataset. Each text is normalized (lowercase, whitespace collapsed, surrounding punctuation trimmed) and appears only once, so it can be in only one split.

`labels/gold_labels.csv` holds `nct_id`, `text_sha256` (the SHA-256 of the normalized text), `split`, `label`, `source` (the API pull date), `labeled_at`, `assisted` (always `false`), `method` (`model-panel-v1`) and `panel_outcome`, and no text. Keying by the text hash keeps the labels valid if the history source changes later. The texts stay in `data/nlp/gold_sample.csv`, and the justifications, rationales and consistency relabels in `data/nlp/panel/`, all gitignored and never committed. The split (100 dev, 300 test, stratified by status) is fixed by the seed in `config/project.yaml`.

The test labels are committed before any work on the LLM labeling prompt. Prompt work uses only the dev items, and test texts and labels are never opened for it. The prompt lives in `src/trialpulse/nlp/prompts/`, and a test checks that no test text appears in any file there. No gold text may appear in the LLM-labeled training sample (a later Step 6 test enforces this).
