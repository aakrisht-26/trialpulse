# Why trials stop: LLM labels and the distilled classifier (Step 6)

Research demo. Not medical advice. Not for patient decision-making.

This page reports how the stop reasons of early-stopped trials are labeled. Every score is measured against the gold set's **reference labels from an adjudicated model panel** (ADR 0010, `docs/reference_panel.md`), not against human labels. All panel members are the same model (Claude), so panel agreement is an upper bound on label reliability. The graded LLM comes from a different model family (OpenAI's open-weight gpt-oss), as ADR 0010 requires.

## Headline

**On the 300 held-out test texts, the LLM reached macro-F1 0.842 (95% bootstrap interval 0.791 to 0.883), accuracy 0.847 and Cohen's kappa 0.817. The point estimate meets the 0.80 target; the interval's lower end (0.791) is just below it.**

**The distilled classifier, trained on the LLM labels of the full 10,000-text sample, reached macro-F1 0.769 (0.706 to 0.819), accuracy 0.767 and kappa 0.723 on the same 300 test texts, scored once (2026-10-21).** It is the only reason model used in production, and it labels every early stop.

## Setup

| Item | Value |
| --- | --- |
| Model | `openai/gpt-oss-120b` on Groq (OpenAI-compatible API), reasoning effort medium |
| Request | temperature 0, strict JSON-schema output validated again in code, 25 texts per request |
| Prompt | `src/trialpulse/nlp/prompts/reason_v2.md`, frozen in commit `16ba93f` before any test item was labeled |
| Protections | the rendered prompt is checked for gold-test texts before any call; the model is checked against the panel's family; test items need the committed final prompt; each model is scored on test once (the result is kept in `docs/results/`, tracked in git), with aggregate results only |
| Code | `trialpulse.nlp.llm_labeler`, `trialpulse.nlp.evaluate`, `trialpulse.nlp.distill` |

## Prompt iterations on the 100 dev items

The rule was declared before the first run: at most 3 prompt versions, each scored once on all 100 dev items, and the final prompt is the one with the highest dev macro-F1 (ties to the earlier version). Request settings stayed fixed.

| Version | Accuracy | Macro-F1 (95% CI) | Kappa | Change |
| --- | --- | --- | --- | --- |
| v1 | 0.860 | 0.858 (0.718 to 0.925) | 0.825 | Taxonomy definitions, annotator rules and the guide's tie-break rules |
| v2 | 0.980 | 0.980 (0.945 to 1.000) | 0.975 | Clarifications for business versus other, funding, site and staff problems, design changes, registration errors, and explicit evidence for efficacy and covid19 |

v1 over-used business for texts that name no decision maker or reason, and filed site problems as accrual and registration errors as administrative. v2's clarifications come from these dev errors: they state, as general rules and without copying any dev text, how to apply the guide to cases it does not spell out (a bare cancellation, a change of sponsor, sites that would not take part, a registration error, a redesigned study). They are prompt-level guidance consistent with the guide, and they stay in the prompt only. The labeling guide is unchanged, because it is the standard the reference labels were made under (Aakrisht's decision, 2026-09-26). Iteration stopped after two of the three allowed versions: the two remaining dev errors are single hard cases, and a rule written for them would fit the dev items, not the task. v2's dev score is optimistic, because v2 was written after reading the dev errors; the test score is the honest estimate.

## LLM on the 300 test items (scored once)

| Label | Precision | Recall | F1 | Support |
| --- | --- | --- | --- | --- |
| accrual | 0.893 | 0.931 | 0.912 | 72 |
| business | 0.848 | 0.875 | 0.862 | 32 |
| funding | 0.852 | 0.852 | 0.852 | 27 |
| administrative | 0.774 | 0.837 | 0.804 | 49 |
| covid19 | 0.905 | 0.864 | 0.884 | 22 |
| safety | 0.818 | 0.900 | 0.857 | 10 |
| efficacy | 0.739 | 0.773 | 0.756 | 22 |
| other | 0.877 | 0.758 | 0.813 | 66 |

Confusion matrix (rows: reference label, columns: LLM label):

| | accrual | business | funding | administrative | covid19 | safety | efficacy | other |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| accrual | 67 | 0 | 0 | 2 | 0 | 0 | 1 | 2 |
| business | 0 | 28 | 1 | 0 | 0 | 0 | 1 | 2 |
| funding | 0 | 0 | 23 | 3 | 1 | 0 | 0 | 0 |
| administrative | 2 | 2 | 2 | 41 | 0 | 0 | 0 | 2 |
| covid19 | 2 | 0 | 1 | 0 | 19 | 0 | 0 | 0 |
| safety | 0 | 0 | 0 | 0 | 0 | 9 | 1 | 0 |
| efficacy | 1 | 0 | 0 | 0 | 1 | 2 | 17 | 1 |
| other | 3 | 3 | 0 | 7 | 0 | 0 | 3 | 50 |

The largest confusion is reference `other` labeled `administrative` (7 texts). Efficacy is the weakest label (F1 0.756 on 22 texts), which fits CLAUDE.md Section 2: headline claims focus on operational stops.

## The 10,000-text LLM sample

The sample holds 10,000 distinct normalized texts, drawn from the 24,534 distinct texts of the cohort's early stops (ClinicalTrials.gov API v2, pulled 2026-09-23) after removing all 400 gold texts. It is stratified by status and stop year (6,731 terminated, 3,269 withdrawn; stop years 2000 to 2026) with seed 42, and stored in a seeded random order, so the texts labeled on any one day are a random subset. A test checks that no gold text is in it after normalization.

**Complete on 2026-10-21: 10,000 of 10,000 labeled**, in daily batches that stopped at Groq's free-tier limit of 200,000 tokens per day (TPD), from 2026-09-26 on (by a Windows scheduled task from 2026-10-19). Labeling cost about 129 tokens per text. The LLM labels: accrual 2,613, administrative 1,843, other 1,551, business 1,292, funding 888, efficacy 866, covid19 584, safety 363. `uv run python -m trialpulse.nlp.llm_labeler --status` reads the cache and makes no request.

## The distilled classifier

TF-IDF (word 1- and 2-grams, character 3- to 5-grams) plus logistic regression, with C and class weighting chosen by 5-fold cross-validation on the LLM-labeled training texts only. It is the only reason model used in production.

**Status: final.** Trained on the LLM labels of all 10,000 sample texts (2026-10-21; best C = 2 with balanced class weights; cross-validated macro-F1 0.812 against the LLM labels). On the 100 dev items: macro-F1 0.790 (95% CI 0.628 to 0.897), accuracy 0.850, kappa 0.810. On the 300 test items, scored once: macro-F1 0.769 (0.706 to 0.819), accuracy 0.767, kappa 0.723 (`docs/results/test_distilled.json`). By label on test, F1: accrual 0.84 (n=72), business 0.77 (n=32), funding 0.81 (n=27), administrative 0.73 (n=49), covid19 0.94 (n=22), safety 0.70 (n=10), efficacy 0.69 (n=22), other 0.67 (n=66). The largest confusion is reference `other` labeled `administrative` (12 texts), as for the LLM. The distilled model is below the LLM on test (0.769 against 0.842 macro-F1), the price of a model that runs without an API.

**All early stops labeled.** The final model labeled all 35,198 early stops with a non-blank reason (24,534 distinct texts) into `data/nlp/reasons/early_stop_reasons.parquet` and `.csv` (gitignored) and the warehouse table `stop_reasons`: accrual 10,822, business 5,325, administrative 5,191, other 4,754, funding 3,669, efficacy 2,489, covid19 1,872, safety 1,076. Of the 35,772 early stops of the Section 6 cohort, 32,144 (89.9%) have a label; the rest have no reason text in the registry's current record. The reason text is the one each record showed when it was pulled (API v2, 2026-09-23), which can differ from the text posted with the first terminal version. The `model` column names the training size and time (`n=10000`). The descriptive breakdown by stop year, phase group and sponsor class is section 9 of `docs/eda.md`.

Expected refusals in these commands (for example a test split already scored, a provisional model sent to test scoring, or a wrong API key) print one `refused:` line and exit with code 2. A labeling run stopped by the daily limit or a provider outage saves its labels, prints its summary and one `stopped:` line, and exits with code 4.

The commands that made the final model and labels (`distill` with no flag also trains, as in the CLAUDE.md Step 6 verify list; the test scoring is refused a second time):

```powershell
uv run python -m trialpulse.nlp.distill --train
uv run python -m trialpulse.nlp.distill --dev
uv run python -m trialpulse.nlp.distill --predict-test
uv run python -m trialpulse.nlp.evaluate --test distilled
uv run python -m trialpulse.nlp.distill --label-all
```

## Limitations

- Scores measure agreement with reference labels from an adjudicated model panel. The panel members are one model, so panel agreement is an upper bound on label reliability, and a gap between the LLM and the panel can be the panel's error as well as the LLM's.
- The dev set is small (100 texts, two covid19 and two safety texts), so dev scores are noisy and v2's dev score is optimistic.
- The test interval for the LLM (0.791 to 0.883) reaches below the 0.80 target.
- The distilled classifier is less accurate than its teacher (macro-F1 0.769 against 0.842 on test), and its errors move the H3 difference of the pre-registration: swaps between the operational and scientific labels push it toward zero, and errors involving the label other pull a score toward the AUC of the stops labeled other (section H3 of `docs/preregistration.md`).
- Reasons are self-reported free text; many give no usable reason (label `other`).
