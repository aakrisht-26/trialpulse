# Reference panel run (Step 6, ADR 0010)

The gold set holds **reference labels from an adjudicated model panel**. It is not human-labeled. This page records how the labels in `labels/gold_labels.csv` were made, so the run can be audited and repeated.

Research demo. Not medical advice. Not for patient decision-making.

## Setup

| Item | Value |
| --- | --- |
| Texts | 400 gold texts (100 dev, 300 test) from `data/nlp/gold_sample.csv`, ClinicalTrials.gov API v2 pulled 2026-09-23 |
| Labeling standard | `docs/labeling_guide.md` at commit `b076f71` (blob `981a5b4`), unchanged during the run |
| Panel model | `claude-opus-5-5` (Claude, Anthropic) for every agent, read from each agent's transcript |
| Labelers | 12 agents: 4 batches of 100 texts, 3 independent labelers per batch |
| Adjudicator | 1 agent that labeled nothing, run at effort `xhigh` |
| Consistency labeler | 1 fresh agent, same prompt and conditions as the labelers |
| Seed | 42 (`config/project.yaml`) |
| Workflows | `wf_a1d7f41d-068` (labeling), `wf_2d62ee88-a8c` (adjudication, then consistency) |
| Labeled at | 2026-09-23T15:16:41+00:00 (the `labeled_at` of every row) |

**Inputs.** `uv run python -m trialpulse.nlp.panel --prepare` gives the 400 texts opaque ids (`p001` to `p400`) in a seeded order that mixes dev and test, and cuts them into 4 batches of 100. Each of a batch's three labelers gets its own seeded order. The input files hold only the id and the text; the id to trial mapping (`key.csv`) is never shown to an agent.

**Isolation.** Each agent was told to read only the guide and its own input file, to use no other file, tool or web source, and to write nothing. An audit of all 14 agent transcripts found exactly two reads per agent (the guide and its own input) plus the structured answer, and no other tool calls. The labelers of one text never saw each other's work. The orchestrating session did not label and did not read test texts or test labels: the workflows returned counts only, and scripts wrote the outputs to `data/nlp/panel/` (gitignored) and printed counts only.

**Rebuilding the labels.** The labels file is rebuilt from the saved outputs with:

```powershell
uv run python -m trialpulse.nlp.panel --finalize
uv run python -m trialpulse.nlp.panel --report --examples 10
```

Running `--finalize` twice gives an identical file (checked). `--report` first checks that the consistency relabels match the seeded sample, one per text, each by a labeler that was not on that text's panel. `--examples 10` writes the 10 adjudicator decisions for review to `data/nlp/panel/adjudicated_examples.md` and prints counts only.

## Prompts

### Labelers (and the consistency labeler)

The consistency labeler got the same text with `data\nlp\panel\consistency_input.json` and 40 items.

```text
You are one of several independent labelers building reference labels for why clinical trials stopped early. This is a self-contained labeling task.

Read exactly two files, and nothing else:
1. E:\trialpulse\docs\labeling_guide.md : the labeling standard. Read all of it first.
2. E:\trialpulse\data\nlp\panel\inputs\<batch file> : 100 items, each an opaque id and a why_stopped text.

Rules for this task:
- Do not open, list or search any other file or folder. In particular do not read anything else under data\, and do not read docs\progress.md, labels\, src\ or tests\. Do not use git, the web or any other tool to learn about the trials. Do not follow the session-start routine in CLAUDE.md; this task needs only the two files above.
- Other labelers are labeling the same texts independently. Do not look for their work.
- Do not write or change any file.

Label every item like a careful human annotator:
- Read the whole text.
- Label only what the text states. Never infer a reason the text does not give.
- Use "other" when the text gives no reason, or the reason is too vague to place.
- Watch negations: "no safety issues" is not safety.
- Pick exactly one label: the decisive reason, applying the guide's tie-break rules.
- The labels are: accrual, business, funding, administrative, covid19, safety, efficacy, other.

For each item return:
- id: the item's id, copied exactly;
- label;
- deciding_words: the exact words from the text that decided the label, copied verbatim (keep the text's spelling). If several fragments decided it, separate them with " | ". For "other" with no reason given, quote the words that show it (the whole text if it is short);
- justification: one line saying why these words give this label under the guide, naming the tie-break rule when one decided it.

Return all 100 items, each exactly once. Work through the items one at a time; do not skim.
```

The answer schema required `id`, `label` (one of the eight labels), `deciding_words` and `justification` for exactly 100 items.

### Adjudicator

```text
You are the adjudicator for a panel that builds reference labels for why clinical trials stopped early. You took no part in labeling. This is a self-contained task.

Read exactly two files, and nothing else:
1. E:\trialpulse\docs\labeling_guide.md : the labeling standard. Read all of it first.
2. E:\trialpulse\data\nlp\panel\adjudication_input.json : 25 items on which three independent labelers disagreed. Each item has an opaque id, the why_stopped text, and the three labelers' labels, deciding words and one-line justifications.

Rules for this task:
- Do not open, list or search any other file or folder. In particular do not read anything else under data\, and do not read docs\progress.md, labels\, src\ or tests\. Do not use git, the web or any other tool to learn about the trials. Do not follow the session-start routine in CLAUDE.md; this task needs only the two files above.
- Do not write or change any file.

For each item:
- Read the whole text yourself first, then the three justifications.
- Decide by the guide, not by counting votes. Two labelers can share the same mistake, and you may choose a label none of them gave when the guide requires it.
- Apply the guide's "How to label" rules: label only what the text states, never infer a reason it does not give, use "other" when no reason is given or it is too vague to place, watch negations ("no safety issues" is not safety), and pick the single decisive reason using the tie-break rules.
- The labels are: accrual, business, funding, administrative, covid19, safety, efficacy, other.

For each item return:
- id: the item's id, copied exactly;
- label: your decision;
- rationale: one or two sentences naming the deciding words from the text and the guide rule you applied, and saying why the other label(s) were rejected.

Return all 25 items, each exactly once. Work through them one at a time.
```

## Results

**Panel outcomes:**

| Split | Texts | Unanimous | Majority | Adjudicated |
| --- | --- | --- | --- | --- |
| All | 400 | 375 (93.8%) | 22 (5.5%) | 3 (0.8%) |
| Dev | 100 | 96 (96.0%) | 4 (4.0%) | 0 |
| Test | 300 | 279 (93.0%) | 18 (6.0%) | 3 (1.0%) |

All 25 splits were 2 to 1; no text got three different labels. `majority` means the adjudicator kept the two-labeler label; `adjudicated` means it chose a label no two labelers gave.

**Reference labels per label:**

| Label | All | Dev | Test |
| --- | --- | --- | --- |
| accrual | 106 | 34 | 72 |
| other | 86 | 20 | 66 |
| administrative | 66 | 17 | 49 |
| business | 42 | 10 | 32 |
| funding | 33 | 6 | 27 |
| efficacy | 31 | 9 | 22 |
| covid19 | 24 | 2 | 22 |
| safety | 12 | 2 | 10 |

**Agreement:**

- Pairwise agreement between labelers of the same text: 95.8% (1,150 of 1,200 pairs).
- Deciding words quoted verbatim from the text, as whole words: 1,200 of 1,200 labeler answers.
- **Consistency check:** a fresh labeler relabeled a seeded 10% (40 texts: 31 test, 9 dev). It agreed with the reference label on 39 of 40 (97.5%), Cohen's kappa 0.969.

**Adjudicator decisions.** The adjudicator decided 25 split texts: 4 dev and 21 test. The 4 dev decisions are in the Step 6 section of `docs/progress.md`. Ten decisions with rationales are in `data/nlp/panel/adjudicated_examples.md` (gitignored), for review outside any prompt work: the 4 dev ones, the 3 test ones it overturned, and a seeded 3 of the 18 test ones where it kept the majority.

## Limitations

- These labels measure agreement with an adjudicated model panel, not with human judgment. Results graded against them say so.
- All panel members are instances of one model, so their errors can be correlated, and unanimity overstates certainty. The consistency check measures stability, not correctness.
- All panel members are the same model, so panel agreement and the consistency relabel measure the model's consistency with itself, not correctness. Treat them as an upper bound on label reliability.
- One adjudicator decided all 25 splits, so it saw the other split texts in its file (one rationale refers to another split item for consistency). It never saw the trials' other fields.
- Any model graded against these labels must come from a different model family than the panel (`trialpulse.nlp.panel.check_graded_model`).
