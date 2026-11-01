# 0048. What /v1/model shows

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 15 decision 3: option a).
- Refines CLAUDE.md Section 13 (`/v1/model`) and guidance item 24.

## Decision

1. A statement is kept in the repository (`docs/model_statement.json`): the registered headline results of the locked test with their 95% intervals, the hypotheses that were falsified, the known limitations (the 24-month calibration below the registered range, ADR 0032), the served model and the cutoff of its training data, the served-scope rule of ADR 0034, and the flag text of guidance item 24.
2. A test holds every number in it to `docs/results.md` and `docs/model_card.md`, as `tests/test_cards.py` holds the cards.
3. A command loads it into `serving.model_statement` (run on the laptop at deployment). The API reads it from there.
4. The daily figures (the share of served scores flagged, the not-scored trials by reason, the data-as-of date) are computed by the API from the serving tables, under its cache.

## Alternatives

- **Links only:** no numbers that could drift, but the API shows no evidence.
- **MLflow at runtime:** breaks the rule that the API reads only serving tables.
