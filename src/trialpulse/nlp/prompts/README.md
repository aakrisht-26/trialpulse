# LLM labeling prompts

The versioned prompts for the Step 6 LLM labeler live here.

- Prompt work uses the 100 dev items only. Test texts and test labels are never opened for it (ADR 0010).
- `tests/nlp/test_holdout.py` checks every file in this folder against the gold-test texts and fails if any test text appears, so examples must come from the dev items or be written from scratch. A failure names the line and a hash prefix, never the text.
- The labeler builds its prompt only from the files here and dev items, and a test must run `trialpulse.nlp.holdout.prompt_leaks` on the rendered prompt it sends.
- Do not paste `docs/labeling_guide.md` into the prompt whole: 2 of its example phrases are gold-test texts word for word (generic phrases; the guide was written before the sample). The label definitions and tie-break rules in `trialpulse.nlp.taxonomy` contain no test text.
- The labeler must come from a different model family than the reference panel (Claude). `trialpulse.nlp.panel.check_graded_model` enforces this.
