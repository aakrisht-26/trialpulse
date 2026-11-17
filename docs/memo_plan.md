# Plan: the per-trial risk memo (Stretch item 2)

Status: **a plan for review, nothing built.** Written 2026-11-17 for Aakrisht's decisions; each decision below is numbered, with 2 or 3 options and a recommendation. The accepted decisions become an ADR before any code.

CLAUDE.md's Stretch item 2: a per-trial risk memo written by an LLM strictly from stored SHAP drivers and version diffs, with an automated fabrication check: every number and date in the memo must appear in the source payload. Section 2 still holds: operational early-stop risk only, "elevated early-stop risk", never "will fail", nothing about whether a treatment works, no advice to patients, no personal data.

## What already exists

- `serving.trial_scores_latest`: per trial, the risks at 12 and 24 months, the score date, the landmark indexes, the flag and the five drivers per horizon (feature, value, contribution; ADRs 0033 and 0035).
- `serving.amendment_markers`: per trial, the post dates of its record changes and a code of what changed (ADR 0043).
- `live.study_versions`: the versions posted since go-live, with every field a feature reads (kept 6 months, ADR 0069), so a change behind a marker can be shown as a before and after value.
- `serving.trial_display`: brief title, sponsor class, areas; display names only through ADR 0059's allowlist.
- Neon held 263.2 MB on 2026-11-17, against the 700 MB alert line (ADR 0045).
- Step 6's provider: Groq, `openai/gpt-oss-120b`, temperature 0, validated JSON, cached responses; free tier measured at 200,000 tokens a day.

## Decisions

### 1. Where memos are generated

| Option | For | Against |
| --- | --- | --- |
| a. A step at the end of the daily job, after publication, never failing the run (like drift, ADR 0076) | One workflow; reads the run's fresh scores | The daily job gains an LLM call and a new secret; a slow provider stretches the 90-minute job |
| b. **A separate workflow, `memo.yml`, started when the daily job succeeds (`workflow_run`), with its own failure issue** | The daily job is unchanged; a provider outage never touches scoring; its own timeout and concurrency | One more workflow and a second reader of Neon |
| c. On the laptop, by a scheduled task | No secret in GitHub | Memos go stale whenever the laptop is off or on a network that blocks Neon (as today's Wi-Fi) |

**Recommendation: b.** It keeps the production job exactly as it is and fails on its own.

### 2. Which trials, and when a memo is regenerated

| Option | Count per day (estimate) |
| --- | --- |
| a. **The top 100 unflagged trials by 12-month risk, plus any trial whose 12-month risk moved by 0.05 or more since its last memo; regenerated only when the payload hash changes** | about 100 on the first day, then about 10 to 40 a day |
| b. Every scored trial | about 88,000: far over the provider's free limit |
| c. Only trials a visitor opens, generated on request | Puts an LLM call in the API's request path, against Section 13's "no model inside the API" |

**Recommendation: a**, with one refinement: the payload hash is computed on the payload as shown (risks rounded to whole percents, driver features and directions, marker diffs), so the weekly full run's small moves of every score do not regenerate all 100 memos each week. Flagged trials (outside the validated range) get no memo in v1.

### 3. Provider, model, cost and the secret

| Option | For | Against |
| --- | --- | --- |
| a. **Groq, `openai/gpt-oss-120b`, temperature 0, as in Step 6** | Known behaviour, free tier, the labeler's limit handling can be reused | Free limits; output can still drift between calls |
| b. A smaller Groq model | More memos a day | Weaker at following the strict rules; more rejections |
| c. A paid provider | Higher limits | Section 2: non-commercial project, no spending |

**Recommendation: a.** Cost against the free tier: a prompt (rules and banned language) of about 600 tokens, a payload of about 700, an answer of about 250 and the model's reasoning of about 250 make about 1,800 tokens a memo. At 200,000 tokens a day that is about 110 memos a day: the first day's 100 fit, and the steady 10 to 40 use a fifth of the limit. A per-run cap (100 memos) and the labeler's TPD/RPD handling stop cleanly at the limit and continue the next day. `GROQ_API_KEY` becomes a GitHub secret available to `memo.yml` only (a new secret is Aakrisht's to add; Section 17 already names it).

### 4. The payload

| Option | For | Against |
| --- | --- | --- |
| a. **Stored values only: the risks and score date, the flag, the five drivers per horizon (feature, its value, direction), the marker diffs (post date, field, before and after value) from `live.study_versions`, the sponsor class, the phase from the title and the status; no title, no sponsor name, no free text** | Every fact is a stored, checkable value; no personal data can reach the provider | Memos are plain; a reader sees the title on the page anyway |
| b. As a, plus the brief title | A more readable memo | Free text from the registry can carry names and claims the check cannot verify |
| c. As a, plus the eligibility and summary text | Richer context | Large, unverifiable, and outside "drivers and version diffs" |

**Recommendation: a.** The payload is JSON with a fixed schema and its own version, and its SHA-256 is the payload hash of decision 2. Trials registered before go-live have markers only from go-live on (ADR 0043); the memo says so rather than guessing at older changes.

### 5. The fabrication check (a memo that fails is never stored)

| Option | What it checks |
| --- | --- |
| a. **Numbers, dates and language, deterministic** | (1) Every number in the memo is one of the payload's numbers in an allowed rendering (a risk as a percent with 0 or 1 decimals; a count as an integer; a driver value as stored or rounded to its shown precision); (2) every date is one of the payload's dates, in ISO form only; (3) no registry id but the trial's own; (4) a banned-language list from Section 2: efficacy and safety claims ("works", "effective", "efficacy", "safe"), outcome claims ("will fail", "will stop", "will be terminated", "failure"), patient advice ("patients should", "consider enrolling", "talk to your doctor", "you should"), and certainty words ("certainly", "guaranteed"); (5) the required phrase "elevated early-stop risk" or a stated risk, and the notice added by the page, never by the model |
| b. a plus a second LLM pass that grades faithfulness | Catches paraphrased claims that contain no number | Doubles the tokens; a model grading a model is not proof |
| c. Numbers and dates only | Simplest | Misses "this treatment is unlikely to work" |

**Recommendation: a.** Each rejection is counted by reason in `monitoring` (no memo text stored), and a rejection share over 20% in a run opens a GitHub issue. The prompt asks for a fixed structure (three short paragraphs: the risk, what moved it, what changed in the record) in JSON, validated before the check.

### 6. Storage in Neon

| Option | Size |
| --- | --- |
| a. **`serving.trial_memos`: one row per trial (nct_id, payload hash, payload version, prompt version, model, memo text, generated date), the latest memo only; rejections as counts in `monitoring.memo_runs`** | about 2 KB a memo, so about 1 MB at 500 trials: 0.2% of the 700 MB line |
| b. As a, plus a history of every memo | Grows about 2 MB a month at 30 a day; retention needed |
| c. Memos as files (an artifact per run) | Nothing in Neon, but the API cannot read them |

**Recommendation: a.** One Alembic migration; the API role reads it like the other serving tables (ADR 0049).

### 7. How the Trial detail page shows it

| Option | For | Against |
| --- | --- | --- |
| a. **A collapsed section under the score card, titled "AI-written summary of the stored drivers and record changes", with its date and model, the line "Written by a language model from this page's stored values; every number and date was checked against them", ADR 0033's note that drivers say what moved the score, not why a trial stops, and the notice; shown only when the memo's payload hash equals the current one** | Clearly labeled; never stale; the page works without it | One more API field and stub |
| b. Open by default, above the score card | More visible | Puts generated text above the measured numbers |
| c. A separate page | Isolated | Hard to find; separates the memo from what it summarizes |

**Recommendation: a**, served through a new field of `/v1/trials/{id}/risk` (null when no current memo), so the dashboard makes no extra request.

### 8. Tests

| Option | Scope |
| --- | --- |
| a. **Offline only:** the check on hand-written memos, including deliberately fabricated ones it must reject (an invented number, a risk off by one point, an invented date, a date in another format, another registry id, a sponsor name, "this drug is unlikely to work", "the trial will fail", "patients should avoid it"), and faithful ones it must accept; the payload builder on synthetic trial rows (no title, no name, schema version); the workflow logic on recorded Groq responses with respx (cap, TPD stop, a rejected memo not stored, hash unchanged means no call); the API field and the page section in AppTest stubs; a test that the prompt files hold no trial text | No network in CI |
| b. a plus a monthly live sample reviewed by hand | Catches what the rules miss | Needs a person each month |

**Recommendation: a**, with a one-off hand review of the first 20 memos before the section is shown on the page.

### 9. Time estimate

About 3 to 4 working sessions after the ADR: the payload, the check and their tests (1); `memo.yml`, the migration, the cap and limit handling (1); the API field and the page section with stubs (1); the first run, the hand review of 20 memos, the full suite, CI and Render (0.5 to 1). The free tier sets the pace of the first fill: 100 memos fit in one day.

## Open questions for Aakrisht

1. Decision 1: a separate `memo.yml` (recommended), the daily job, or the laptop?
2. Decision 2: the top 100 unflagged plus movers of 0.05 (recommended), with the hash on the payload as shown?
3. Decision 3: Groq `openai/gpt-oss-120b` (recommended), and adding `GROQ_API_KEY` as a GitHub secret for `memo.yml` only?
4. Decision 4: stored values only, no title or free text (recommended)?
5. Decision 5: the deterministic check with the banned-language list (recommended), and the 20% rejection alert?
6. Decision 6: the latest memo only in `serving.trial_memos` (recommended)?
7. Decision 7: a collapsed, labeled section on Trial detail, served through the risk endpoint (recommended)?
8. Decision 8: offline tests with fabricated memos, and a hand review of the first 20 (recommended)?
9. Decision 9: is the estimate acceptable, and is this the next piece of work?
