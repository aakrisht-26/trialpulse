# 0084. The per-trial risk memo (Stretch item 2)

- Date: 2026-11-17
- Status: **Accepted.** Decided by Aakrisht on 2026-11-17: all nine recommendations of [memo_plan.md](../memo_plan.md) accepted, with changes A to D below. **Amended 2026-11-18** (memo_v2, below): the review of the first memos failed, so paragraphs 1 and 3 are built by a template and the model writes only the driver sentences. **Amended again 2026-11-18** (memo_v3, below): the audit of the memo_v2 memos found statements the template and the labels got wrong. Two details flagged while building memo_v3 were approved the same day; whether a model keeps writing paragraph 2 is decided after the review of the memo_v3 memos.

## Context

CLAUDE.md's Stretch item 2: a per-trial risk memo written by an LLM strictly from stored SHAP drivers and version diffs, with an automated fabrication check (every number and date in the memo must appear in the source payload). Section 2 holds: operational early-stop risk only, "elevated early-stop risk", never "will fail", nothing about whether a treatment works, no advice to patients, no personal data. The plan and its alternatives are in docs/memo_plan.md.

## Decision

The nine recommendations of the plan:

1. **Where:** a separate workflow, `memo.yml`, started when the daily job succeeds (`workflow_run`) and by hand (`workflow_dispatch`), with its own failure issue. `daily.yml` is unchanged.
2. **Which trials:** the top 100 unflagged trials by 12-month risk, plus unflagged trials whose 12-month risk moved by 0.05 or more (the latest row of `serving.trial_score_history` against the row before it, the latest dated within the last 7 days). A memo is regenerated only when its payload hash changes; the payload holds the risks as whole percents, so small moves of a weekly full run do not regenerate it. Flagged trials get no memo in v1.
3. **Provider:** Groq, `openai/gpt-oss-120b`, temperature 0, validated JSON; a cap of 100 memo calls per run; Step 6's limit handling (a daily limit, TPD or RPD, stops the run cleanly; per-minute limits are waited out). `GROQ_API_KEY` is a GitHub secret read by `memo.yml` only.
4. **Payload:** stored values only: the risks (whole percents) and their date, the five drivers per horizon (feature, value as shown, direction), the record changes behind the amendment markers (post date, field, before and after value, from `live.study_versions`), the sponsor class, the status, the phase stated in the title, and why the trial was chosen (top 100, or moved with its previous risk and direction). No title, no sponsor name, no free text. A fixed schema with a version; its SHA-256 is the payload hash.
5. **The fabrication check** (deterministic; a memo that fails is never stored): every number is in the payload in an allowed rendering; every date is a payload date in ISO form, and no other date form; no registry id but the trial's own; no sponsor display name and no personal title; banned language from Section 2 (efficacy and safety claims, outcome claims such as "will fail", patient advice, certainty words); the fixed structure (three short paragraphs, as JSON). Rejections are counted by reason in `monitoring.memo_runs`; a run whose rejections exceed 20% of its calls opens a GitHub issue.
6. **Storage:** `serving.trial_memos`, the latest memo per trial with its payload, payload hash, prompt version and model (about 1 MB at 500 trials), and `monitoring.memo_runs`, one row per run. The API's role reads `serving.trial_memos` (ADR 0049).
7. **Display:** a collapsed section on Trial detail, "AI-written summary of the stored drivers and record changes", with its date, model, the line that every number and date was checked against the stored values, ADR 0033's note on drivers and the notice; served as a `memo` field of `/v1/trials/{id}/risk`, null when there is no current memo.
8. **Tests:** offline only, with deliberately fabricated memos the check must reject and recorded Groq responses; no network in CI.
9. **Time:** about 3 to 4 sessions.

The changes decided with the approval:

- **A. Two more rules in the check,** each with fabricated memos it must reject. No number written in words: cardinal number words ("one" to "nineteen", the tens, "hundred", "thousand", "million", "billion"), "dozen", "half" and "a couple" are refused, so every number is in digits and passes the number rule. No invented comparisons: multiplier and ratio words ("double", "doubled", "triple", "tripled", "quadrupled", "halved", "twice", "thrice", "times" after a number or "x", and any word ending in "fold") are refused.
- **B. The required wording depends on why the trial was chosen.** A trial in the top 100 by 12-month risk must contain "elevated early-stop risk". A trial chosen because its risk moved must state its current risk and the direction of the change ("rose" or "increased" for a rise, "fell" or "decreased" for a fall), must not state the opposite direction, and must not call its risk elevated if it fell. A trial that is both must contain the phrase (its risk is still among the 100 highest) and state its risk and direction without the opposite one; the ban on "elevated" applies to a trial chosen for a fall alone.
- **C. The section stays off until a review.** The flag is `api.memo_public` in config/project.yaml, `false` by default: while it is off, the API's `memo` field is always null, so no memo is public on the API or the dashboard. After the first real memo run, `uv run python -m trialpulse.memo.review --target neon` writes the first 20 stored memos with their payloads and check results, and up to 5 rejected memos with their reasons, to `data/memo_review/<date>.md` (gitignored); Aakrisht reviews them, and the flag is turned on only in a later run.
- **D. The provider checked before building** (2026-11-17, one manual call, not in tests): Groq serves `openai/gpt-oss-120b` (active, context 131,072 tokens); its free limits are 30 requests a minute, 1,000 a day, 8,000 tokens a minute and 200,000 tokens a day (the response headers and Groq's rate-limit page agree), unchanged from Step 6. At about 1,800 tokens a memo, the per-minute limit allows about 4 memos a minute, so a run of 100 takes about 25 minutes, and the daily limit about 110 memos.

## Implementation details decided while building (flagged for review)

- **Current memo.** A memo is current when its payload hash equals the hash of the trial's payload built now. Each run rebuilds the payload of every stored memo: an unchanged hash moves the memo's `valid_as_of` to the trial's score date; a changed hash regenerates it if the trial is still chosen and the cap allows, and otherwise deletes it. The API shows a memo only when its `valid_as_of` equals the score date it serves, so a memo is never shown beside a score it was not checked against.
- **Rejected samples.** For the review of change C, up to 5 rejected memos per run are kept in `monitoring.memo_runs` with their reasons. They are never served: the API reads `serving` only.
- **Dashes.** An em or en dash in the model's text is replaced by a comma or "to" before the check, so the project's writing rule holds on the page.
- **Names.** The check refuses every sponsor display name the API can show (`serving.sponsors`), and personal titles (Dr, Prof, MD, PhD).

## Amendment of 2026-11-18: memo_v2

Decided by Aakrisht on 2026-11-18, after the review of the first 20 memos (data/memo_review/2026-11-17.md). The flag stays off.

### What the review found

Every number checked out, but statements about record changes were wrong in ways the number check cannot catch:

- memos 1, 16 and 18 say that no record change is held while their payloads list several: the model confused `field_changes` (before and after values, held only since go-live) with `record_changes` (the amendment markers, held from the bootstrap on);
- memo 17 contradicts itself (it names a record change, then says none is held);
- memo 6 attaches the wrong change kinds to dates (2025-09-04 changed the status only, and the enrollment change of 2026-05-20 is missing);
- memo 19 is garbled ("start_date after 2026-11-19 before 2026-11-18 post_date 2026-11-16");
- the memos mostly restate raw feature names (`enrolled_to_first_target_ratio = 0.05`), which a reader cannot use.

The 12 `required_phrase` rejections of the first run were false: the model wrote "early-stop" with a non-breaking hyphen (U+2011).

### Decision

1. **Paragraphs 1 and 3 are a template, not the model's.** Paragraph 1 states the risks, the score date and why the trial was chosen; for a move, the direction, the previous risk and its date. "elevated early-stop risk" appears only for a trial in the top 100. Paragraph 3 lists the record changes newest first (at most 10) with exactly the payload's change kinds, then every field change as "<field label> moved from <before> to <after> on <post date>". When no field change is held, it says plainly that before and after values are held only from go-live (2026-11-08, `memo.values_held_from`) onward. It never says that no record change is held when the payload has one.
2. **The model writes only paragraph 2:** exactly one sentence per 12-month driver, in the payload's order. The payload gains, per driver, a plain-English label and a display value, from a committed table of the 54 served features (`trialpulse.memo.labels`, built from the registry's descriptions in docs/features.md): a ratio as a percent with one decimal, months as "N months", a flag as a phrase, a category in plain words. The model is sent only the 12-month drivers (label, value, effect), not the whole payload.
3. **The check gains a per-sentence rule** for paragraph 2: as many sentences as drivers; sentence i holds driver i's label verbatim (case aside) and its direction as "raises the risk" or "lowers the risk" (never the other), no other driver's label, and no number but driver i's display value in an allowed rendering. Paragraphs 1 and 3 must equal the template built from the payload. Every earlier rule (numbers, dates, ids, banned language, names, links) still applies to the whole memo.
4. **Normalization before the check:** every Unicode hyphen, dash and minus (U+2010 to U+2015, U+2212, U+FE58, U+FE63, U+FF0D) becomes "-", and non-breaking, narrow and thin spaces (U+00A0, U+202F, U+2009, U+2007) become a space. The stored memo is exactly the normalized text that was checked. This replaces the earlier dash rule (a dash read as a comma, or as "to" between numbers).
5. **Tests:** the five real failures above as fabricated v2 cases that the template or the check makes impossible, plus a swapped direction, a swapped driver order, an extra label, and the hyphen and space cases.
6. **Prompt `memo_v2`**, payload version 2, so every memo regenerates on the next run; a stored memo of another prompt version is deleted rather than left beside a newer score. Before the first v2 dispatch, a dry run on 3 payloads (`--dry-run 3`, which writes nothing to Neon) measures the v2 tokens per memo, and the dispatch waits until the free daily limit leaves enough for 100 memos rather than stopping halfway. Relaxed by Aakrisht on 2026-11-18, before the first v2 run: a run that stops partway at the daily limit is acceptable, because `api.memo_public` is off (no memo is shown) and the next run resumes through the payload-hash skip (stored v2 memos stay current and only the rest are asked for). The automatic run after a daily run may start v2 on an empty bucket and stop early; the one dispatched run still waits for an estimated 100,000 tokens.

### Consequences

- The memo carries less model-written text: two of its three paragraphs are deterministic and tested, and the model's paragraph is held sentence by sentence to the stored drivers.
- The API's note on the memo says which paragraph a model wrote.
- The table of labels is a new place to keep in step with the registry: a test fails when a served feature has no entry.

## Amendment of 2026-11-18 (second): memo_v3

Decided by Aakrisht on 2026-11-18, after an automated read-only audit of the 20 memo_v2 memos (data/memo_review/2026-11-18-audit.md: two auditors, one adversarial verifier per auditor). The flag stays off.

### What the audit found

40 findings stand (13 high, 12 medium, 15 low) and the verifiers refuted 4; no memo was free of findings. Every high finding is in paragraph 3, the template: a trial's first posting was listed as a status change and counted (its marker has the status code, ADR 0043 as amended); "The record changed on N dates" counted only changes to fields the model reads, so it contradicted "versions posted" in the same memo; and a change since go-live whose version is the first one held was listed beside "no change in them is held". The medium findings: "(dates)" also covers a re-verification of the status; labels that misled ("slip is 33 months earlier", a registration lag that counts a start move again, "earlier suspension" for the current one, "landmark step", "ended" beside "stopped", a smoothed sponsor rate shown as the sponsor's own, an enrollment count without its type); and the per-sentence rule held only a display's numbers, not its words. The low findings: jargon ("unflagged", "go-live", "held"), no horizon and no caution before the driver sentences, labels repeated by their values, plural labels with "is", the date of a move read as the date of the rise, and "or" inside a list joined by "and". The v1 review's "2025-09-04 changed the status only" (above) was that trial's first posting.

### Decision

1. **The first posting (A).** Markers are read through `trialpulse.markers` (ADR 0043 as amended): the marker on the trial's first-post date from the live state is its first posting, kept apart in the payload (`first_posted_on`) and counted nowhere; trimmed markers keep their stored codes. The Trial detail page shows the first posting as "First posted".
2. **Paragraph 3 (B).** It says exactly what it counts ("Since its first posting on D, changes to fields the model reads were posted on N dates", then "Versions that changed only other fields are not counted."), lists each of the latest changes as its own sentence with its kinds apart ("2026-05-20: status; dates or status verification; enrollment."), states when the status was last verified in its own sentence, and, for each change since go-live, either the values before and after (every field of the marker's groups; a change of the lead sponsor is stated without values) or "first version held since go-live; no earlier value to compare". The dates bit is named "dates" or "status verified" where two held versions show which changed (Aakrisht's choice of 2026-11-18), and "dates or status verification" elsewhere.
3. **The labels (C).** The table is rewritten so that every label is a singular noun phrase ("The <label> is <value>" reads as English), counts are "number of ...", dates that moved are named by the date ("The start date is 39 months later than in the first version"), the registration lag is the start as now recorded with "any move of the start included", the suspension history and status are named apart, the landmark is "the 24-month checkpoint", ended trials are "by completion or early stop", the sponsor rate is "smoothed toward its class rate", and the enrollment count carries its type from the latest record.
4. **The check (D).** Sentence i must hold driver i's display value word for word (case aside), with its numbers as before; it also may not hold another level or flag phrase of the same feature that the display does not contain, since "not suspended now" holds "suspended now". Beyond its label, display value and effect, a sentence may hold only connective words (the, a, an, is, which, and, this, that, it, its, of, at, with, being, here): the model is sent nothing else, so any other word (a negation, another feature, an outcome) would be invented. No served feature's label but its own may appear in it.
5. **Paragraph 1.** "counting only scores inside the validated range" replaces "unflagged"; a move gives both scores ("rose from 47% in the score of 2026-11-03 to 55%"); and a fixed sentence before the drivers says they give what moved the model's 12-month score and that a driver is not a reason why a trial stops.
6. **Prompt `memo_v3`**, payload version 3, so every memo regenerates on the next run, which may stop partway at the daily limit (item 6 above). Tests hold the 40 findings as cases memo_v3 makes impossible (`tests/memo/test_memo_audit_v2.py`).

### Implementation details decided while building (flagged for review)

Aakrisht approved the first two items on 2026-11-18, with the null `first_posted` of a trial the live state no longer holds (ADR 0043 as amended).

- The API's names of the marker codes change with the memo's (one table): "dates" becomes "dates or status verification" and "title phase or interventions" becomes "intervention types or title phase".
- The other-level rule and the connective-words rule of item 4 go beyond the decision's wording. An adversarial review of the diff before its commit (two reviewers, two verifiers) showed why: with the display rule alone, "The recruitment status is no longer recruiting", "which never raises the risk", "and the sponsor class is industry" and "although the trial was terminated" all passed. The connective-words rule closes them and also refuses a plural "are" after a singular label; the prompt asks for exactly "The <label> is <value>, which <effect>."
- The same review led to: outcome claims in the past tense or as a likelihood ("was terminated", "has stopped", "likely to end") in the banned list; the word rules reading a snake_case token as words ("the_trial_will_fail"), the number rule still skipping feature names; flag phrases that claim no more than the feature ("this month or earlier" for a start that is no longer ahead, "not closed" for enrollment, "not shown as reached in the registry" for primary completion, the first posting against the recorded start month); "not stated in the record" for a missing value, so a missing flag never reads as its "no"; a completion date shown at its recorded precision; the status feature's words limited to the open statuses a served trial can have (ADR 0014); and, once retention (ADR 0069) can have deleted a version posted since go-live, "no earlier version is still held" in place of "first version held since go-live".
- The enrollment count's type comes from the latest record (`live.trial_state`), the checkpoint and the recent window from `landmarks.spacing_months`; a change since go-live whose version retention removed says "its version is no longer held"; of several versions held on one day, the last ingested is compared.

### Consequences

- More of paragraph 3 is fixed text, and none of it can call a first posting a change or contradict a count of versions.
- The labels are longer; a sentence can run to about 30 words.
- With the connective-words rule the model's freedom of wording is nearly nil: a driver sentence is "The <label> is <value>, which <effect>." with connective words only, which a template could write as well. After the review of the memo_v3 memos, Aakrisht decides whether to keep the model for paragraph 2 or to replace it with a template (noted 2026-11-18).

## Alternatives

The alternatives of each decision are in docs/memo_plan.md. For the flag of change C: a constant in the dashboard's code (memos would still be public on the API) or a Streamlit secret (a switch outside the repository's history).

## Consequences

- A new workflow and secret, a migration (0008: `serving.trial_memos`, `monitoring.memo_runs`), a `memo` package, an API field and a page section, all with offline tests.
- No memo is public until Aakrisht turns `api.memo_public` on after the review.
- Memos are summaries of stored values, never results: they are labeled as AI-written on the page and carry the notice.
