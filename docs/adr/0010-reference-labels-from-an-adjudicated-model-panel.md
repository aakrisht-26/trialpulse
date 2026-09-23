# 0010. Gold set: reference labels from an adjudicated model panel

- Date: 2026-09-23
- Status: Accepted (decided by Aakrisht on 2026-09-23). Changes CLAUDE.md Section 11 (the gold set) and the Step 6 build list.

## Context

Section 11 has Aakrisht label the 400 gold texts by hand in the Streamlit labeling app, which takes 2 to 3 hours. Aakrisht decided not to label the gold set by hand. The gold set still has the same job: a 100-text dev split for prompt work, and 300 held-out test texts on which the LLM labels and the distilled classifier are scored once (Step 6 acceptance).

## Decision

The gold labels are **reference labels from an adjudicated model panel**. Every document, result and resume line uses that wording and never calls them "human-labeled".

The protocol, applied to all 400 texts:

1. **Three independent labelers per text.** Each sees only `docs/labeling_guide.md` and the text, nothing else about the trial: no NCT ID, status, dates or split. Texts carry opaque panel ids, and the labelers of one text cannot see each other's work.
2. **Careful-annotator rules.** Each labeler reads the whole text, labels only what is stated, never infers an unstated reason, uses `other` when no reason is given, watches negations ("no safety issues" is not `safety`), picks the decisive reason by the tie-break rules, and writes a one-line justification naming the deciding words.
3. **Adjudication.** Unanimous labels stand. For a split, a fourth labeler that took no part in labeling that text sees the text, the guide and the three justifications, decides by the guide, and records its rationale.
4. **Consistency check.** After the rest are done, a fresh labeler relabels a seeded random 10% of the texts under the conditions of point 1, and the agreement with the reference labels is reported.
5. **Test isolation.** The test labels are finished and committed before any work on the LLM labeling prompt. From then on, prompt work uses only the 100 dev items, and test texts and labels are never opened. The prompt lives in `src/trialpulse/nlp/prompts/`, and a test checks that no test text appears in any file there.
6. **Labels file.** `labels/gold_labels.csv` keeps its schema, with `assisted` false, and adds `method` (`model-panel-v1`) and `panel_outcome`. Justifications, rationales and the consistency relabels stay in `data/nlp/panel/` (gitignored).
7. **Report.** Per-label counts, the shares that were unanimous, majority and adjudicated, the consistency agreement, and 10 adjudicated examples with their rationales.

**Panel outcomes:**

- `unanimous`: the three labelers agreed;
- `majority`: two labelers agreed, and the adjudicator kept their label;
- `adjudicated`: the adjudicator chose a label that no two labelers gave (it overturned a 2-to-1 vote, or all three differed).

**Different model family.** The panel is Claude (Anthropic). Any model graded against these labels must come from a different model family. The Step 6 LLM labeler (Groq, `openai/gpt-oss-120b`) qualifies, and a Claude model does not; `trialpulse.nlp.panel.check_graded_model` enforces this for the model id the labeler is configured with. The distilled TF-IDF plus logistic regression classifier learns from the graded LLM's labels, not from the panel's.

## Alternatives

- **Hand labeling (Section 11 as written).** The reference would be human judgment, but it costs 2 to 3 hours that Aakrisht chose not to spend.
- **One model pass without a panel.** Cheaper, but it gives no measure of how uncertain a label is and no second look at hard cases.
- **Majority vote without adjudication.** Simpler, but two labelers can make the same mistake. The adjudicator checks the justifications against the guide.
- **Grading a Claude model against these labels.** Rejected: a model from the panel's family shares its habits, so agreement would overstate accuracy.

## Consequences

- Gold-test metrics measure agreement with the adjudicated panel, not with human judgment. Results must say so.
- The three labelers are instances of one model, so their errors can be correlated, and unanimity overstates certainty. The consistency check measures stability, not correctness.
- The Step 6 acceptance criteria are unchanged: LLM macro-F1 of at least 0.80 on gold-test, and gold-test never used for prompt or model tuning.
- The Streamlit labeling app is no longer the labeling path. It stays as an optional review tool that writes to `data/nlp/review_labels.csv` (gitignored), and `save_label` refuses to overwrite a panel label.
- The earlier dev suggestions (`data/nlp/dev_suggestions.csv`) are superseded, because their labelers saw the NCT ID and status, their notes did not have to name the deciding words, and their splits were adjudicated by the orchestrating session rather than by a fourth labeler. All 400 texts, dev included, go through this protocol.
- The panel's agent runs are not a repository command. Their outputs are saved in `data/nlp/panel/`, and `uv run python -m trialpulse.nlp.panel --finalize` rebuilds the labels file from them. `docs/reference_panel.md` records the prompts, the model and the run.
