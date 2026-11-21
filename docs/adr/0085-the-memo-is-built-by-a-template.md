# 0085. The memo is built by a template; the language model is retired

- Status: **Accepted.** Decided by Aakrisht on 2026-11-19, after the audit of the memo_v3 memos. Amends ADR 0084 and deviates from CLAUDE.md's Stretch item 2, which asks for a memo "written by an LLM". **Amended 2026-11-19** (memo_v5, below): the sponsor rate says exactly how it was made, from inputs the daily job stores with each score (migration 0010), and the memo flag is turned on only if the audit of the regenerated memos finds no high or medium problem. **Amended again 2026-11-21** (memo_v6, below): the rate sentence says what its ended trials count, the change list uses the label's own words, every class reads naturally, and a change without an earlier version says so plainly.

## Context

The per-trial risk memo (ADR 0084) went through three versions in three days, each followed by a review or an automated read-only audit of 20 stored memos.

- **memo_v1 (2026-11-17).** Groq's `openai/gpt-oss-120b` wrote all three paragraphs from a payload of stored values, and a deterministic fabrication check held every number, date, registry id and name to the payload. The first run stored 87 memos and rejected 13. Every number in the 20 reviewed memos was right, and the review still failed. Four of the 20 said wrong things about the record changes: that no change was held when several were listed, the wrong kinds of change on a date, a garbled before and after value. The model had related true facts to each other wrongly, and a check of numbers and dates cannot see that.
- **memo_v2 (2026-11-18).** Paragraphs 1 and 3 became a template, and the model wrote only one sentence per driver from a table of plain-English labels, held to its driver's label, direction and numbers. Run 3 stored 100 memos with 0 rejected (895 tokens a memo). The audit (two auditors, two adversarial verifiers) confirmed 40 findings, 13 of them high. Every high finding was in the template's paragraph 3, and the per-sentence rule held only a value's numbers, so a sentence with the wrong words passed.
- **memo_v3 (2026-11-18 and 19).** The first posting stood apart, the labels were rewritten, and the per-sentence rule held each sentence to its driver's display value word for word, refused any other level of the feature and allowed only connective words beside the label, value and effect. The prompt then asked for exactly "The <label> is <value>, which <effect>." Runs 4 and 5 stored 140 memos with 0 rejected (887 and 902 tokens a memo). The audit confirmed 20 findings: 0 high, 6 medium and 14 low, almost all in the label wording.

With the memo_v3 check, the only text the model could store was the text a template writes from the same table. The model had been narrowed until its output equaled a template. It added no content, and it cost a GitHub secret, Groq's free daily token limit (about 100 memos a run, so a large day stopped partway) and an outside service whose failure stopped the run.

## Decision

1. **memo_v4: every paragraph from the template.** Paragraph 2 is one sentence per 12-month driver in the payload's order: "The <label> is <value>, which raises the risk." (or "lowers"). For a trial chosen because its risk moved, paragraph 1 adds after its lead-in: "They explain the current level of the 12-month score, not its change." The lead-in itself says the sentences "name the features that contributed most to the model's 12-month score".
2. **The fabrication check stays** as the test of the template at every generation: a memo that fails is not stored, the issue for rejections over 20% of a run stays, and every memo should pass. A failure is a template bug to fix.
3. **Groq leaves the memo path.** `memo.yml` no longer reads `GROQ_API_KEY`, the GitHub secret is deleted (after the push of this change), the memo prompt files and the generator's Groq client go, a test fails if any workflow names the key, and every stored memo's `model` reads "template" (`prompt_version` memo_v4). Step 6's labeler is unchanged, and the key stays in .env for it.
4. **Selection is unchanged** (the top 100 unflagged trials by 12-month risk, plus the trials whose risk moved by 0.05). With no token limit, `memo.cap_per_run` is 2,000, so one run covers every chosen trial. The Neon size is recorded after the first run.
5. **The audit's wording fixes:**
   - no label holds a comma (tested); the three that did are rephrased;
   - the sponsor counts say what each counts (features/sponsor.py, where every count leaves the trial itself out): "number of other interventional trials first posted under the sponsor", and the other ended and stopped trials "listed under the sponsor", with "trials first posted under another sponsor included"; the rate is the "early-stop rate used for the sponsor", shown as "24.1%, from the other ended trials listed under the sponsor smoothed toward the class rate (the class rate alone without a track record)". A rate above the class rate next to 0 registrations then reads as what it is;
   - "no earlier value is held for this trial" where a change since go-live has no earlier held version, with no claim that go-live is the cause;
   - "the 24-month checkpoint" becomes "stage 4 of 6, a stage every 6 months after the first posting", labeled "stage of the trial used by the score", so it cannot be read as the 24-month horizon;
   - a missing maximum age, which is the registry's "N/A (No limit)", is shown as "no upper limit";
   - "the phase named in the title is none", "the past or current suspension is recorded" (or "not recorded"), and no double colon in paragraph 3 (the head of the change list ends with a full stop);
   - approved: each change date as its own sentence, and the status feature's words limited to the open statuses.

## Implementation details decided while building (flagged for review)

- **No equality rule for paragraph 2 in the check.** Requiring paragraph 2 to equal the template would be a tautology at generation, where it is the template, as the existing equality rule for paragraphs 1 and 3 already is. The check's per-sentence driver rules and its general rules, written apart from the template, are the test.
- **The rate's wording holds without a track record.** For a sponsor without one (an individual or a placeholder name), the rate is the class rate itself (features/transforms.py), so the display says so in brackets rather than claiming trials that were never counted. This, and "other" in the three counts, came from the adversarial review of the diff before it was pushed.
- **One connection for a run's inserts,** each memo in its own short transaction (ADR 0058), so a run at the cap of 2,000 does not open 2,000 connections. The rules written for model text (connective words, other levels, past-tense outcome claims) are kept whole.
- **A missing minimum age is shown as "no lower limit".** It is the same registry convention as the maximum age, so it is applied to both, which goes beyond the decision's wording.
- **A maximum age of 99 years is shown as 99 years.** The registry encodes "no limit" as a missing age ("N/A"), which `contracts.versions.age_in_years` reads as missing. 99 is a value the sponsor entered, so the memo states what the record says.
- **The cap is 2,000, not removed:** at least 10 times the top 100, so a day of many movers is covered, with a bound that keeps a runaway run short. A run over it logs the rest as over the cap, as before.
- **Payload version 4**, so every stored memo is rebuilt. `--dry-run N` stays and prints the check results of N memos. The token columns of `monitoring.memo_runs` stay and read 0, so no migration.
- **The API's label and note no longer say "AI-written".** They say a fixed template builds every paragraph. The Trial detail caption says "Built on <date> by the template (memo_v4)".
- `months_since_t0` is labeled "time since the first posting used by the score", since the old label held a comma.

## Alternatives

- **Keep the model under the memo_v3 check.** The stored text is the same, and it keeps the secret, the daily limit and the outside dependency.
- **Free the model's wording and judge it with a second model.** That is a model grading a model, not a proof, and memo_v1 already showed that faithful-looking prose can relate true facts wrongly.
- **A smaller or cheaper model.** Under the same check its output is the same template text, so this saves tokens but still adds nothing.

## Consequences

- A memo is a pure function of its payload: reproducible, free and not limited per day. Building takes milliseconds a memo, and each stored memo is one insert on Neon; no secret or outside service is involved.
- Stretch item 2's "written by an LLM" is not met. The deviation is recorded in CLAUDE.md's Amendments, with this history as the reason.
- The memo can say no more than the label table and the template say. Its wording is now entirely the table's, and the tests hold the table.
- One known limit stays. Some of the model's drivers state one fact twice (the past or current suspension beside the suspension status; the start as now recorded beside the start date's move; the enrollment count beside its ratio to the first target). The memo states each driver as the model ranked it, and the lead-in says the drivers are not reasons.

## Amendment of 2026-11-19: memo_v5

Decided by Aakrisht on 2026-11-19, after the read-only audit of the memo_v4 memos (data/memo_review/2026-11-19-v4-audit.md: 13 findings, 0 high, 1 medium, 12 low).

### What the audit found

The medium finding: the sponsor rate's display named both ways a rate can be made ("from the other ended trials ... (the class rate alone without a track record)") without saying which applied or giving the class rate, so beside "0 other interventional trials first posted under the sponsor" a reader took 24.1% for the class rate. It was the sponsor's own 1 of 1 ended trials smoothed toward the class rate of 16.5%. The display could not say which form applied, because nothing the memo reads held the rate's inputs: the daily job computes all 54 features but stores only the five drivers of each horizon, and the class rates sit in the champion's bundle.

### Decision

1. **The sponsor rate (the medium finding).** The payload carries whether the sponsor has a track record, the class rate and the number of other ended trials behind the rate (`sponsor_rate`), and the display says exactly one of: "<rate>, from <N> other ended trials of the sponsor, smoothed toward the <class> class rate of <class rate>" (with "trial" for N = 1), or "<class rate>, the <class> class rate, as the sponsor has no track record". Every number in it is in the payload. Asked during the build (Aakrisht's choice of 2026-11-19): the daily job stores the three inputs with every score it writes (migration 0010: `serving.trial_scores_latest` gains `sponsor_has_record`, `sponsor_other_ended`, `sponsor_class_rate` and `sponsor_rate_class`), so the memo states the values behind the served score; every trial has them after the next full run (data date 2026-11-20). The rejected alternatives: the memo job fetching the bundle and asking the sponsor pass as of its own date (no schema change, but for a score of an earlier date its N would not be the one behind the rate shown), and a rate shown without its inputs (not the decision's forms).
2. **The cheap low findings:** the current suspension reads "The state of the trial is suspended now"; the stage reads "stage k (stages run 0 to 6, ...)"; the dates group has one name, "dates or status verification", in the memo as on the timeline (the exact names "dates" and "status verified" of memo_v3 are dropped, and the values since go-live still say which field moved); the status group is "recruitment status" in the memo and the API (ADR 0043's table of words, `trialpulse.markers`), and the dashboard's stubs are recorded again. Left as they are: two drivers that reflect one fact, a maximum age of 99 years, and a move from a score dated before go-live.
3. **The review command never overwrites a file:** a second review of a day is written as <day>-2.md, then <day>-3.md (tested).
4. **The flag.** After the fixes are deployed and the memos regenerated, the review file is written and the light audit runs (one auditor, one adversarial verifier). If it confirms 0 high and 0 medium findings, `api.memo_public` is set to true in the same run (commit, full suite, push, CI, Render); otherwise it stays off.

### Implementation details decided while building (flagged for review)

- **The stage keeps its spacing:** "stage 4 (stages run 0 to 6, a stage every 6 months after the first posting)", since "stage" alone does not say what a stage is; the decision named only "stage k (stages run 0 to 6)".
- **The current suspension's label is "state of the trial"** ("The state of the trial is suspended now" or "not suspended now").
- **The class in the rate's display** is the class whose prior the score used, stored with it (`sponsor_rate_class`, NULL when the priors hold no rate for the class, as for AMBIG or a missing class, and the overall rate was used), in the words of the sponsor class driver ("other (mostly universities and hospitals)" with brackets for its comma); without one it reads "the overall rate". This came from the adversarial review of the diff before the push (two reviewers, one verifier each: 7 findings confirmed, all low, all fixed), which also found the stage of a stored memo of a waiting trial misdescribed in a comment and stale marker words in ADR 0043.
- **A chosen trial whose drivers include the rate but whose score row lacks its inputs waits:** it gets no memo, its stored memo is deleted (never left beside a newer score), and the run's summary counts it as waiting (not stored in `monitoring.memo_runs`, so no migration there). Until the full run of data date 2026-11-20, only the trials rescored since the deploy have the inputs.
- **Scores made without the inputs** (a frame from a test or an older path) write NULL in the four columns. Parity layer 5a does not compare them: they are read by the memo only, and their equality on both paths is implied by equal risks, not proven.
- Payload version 5 and memo version memo_v5, so every stored memo is rebuilt.

### Consequences

- The daily job's score rows are about 17 bytes wider each (about 1.5 MB for 85,000 rows), well inside Neon's headroom.
- Neon must be migrated to 0010 before the push, since the daily job refuses a database not at its head.

## Amendment of 2026-11-21: memo_v6

Decided by Aakrisht on 2026-11-21, after the read-only audit of the memo_v5 memos (data/memo_review/2026-11-21-audit.md: 4 findings, 0 high, 1 medium, 3 low; the memo_v4 medium finding fixed, every rate display matching its payload).

### What the audit found

The medium finding: the rate sentence said "from 1 other ended trial of the sponsor" beside "0 other interventional trials first posted under the sponsor" (memo 2; 2,075 ended against 1,732 first posted in memo 5). Both are true, because ended trials listed under a sponsor include trials first posted under another sponsor (features/sponsor.py), but the rate sentence left out the qualifier its own count's label carries, so a reader saw more ended trials than were ever posted. The low findings: "intervention types or title phase" in the change list against the label "phase named in the title"; "the other (mostly universities and hospitals) class rate" read as "the other class"; a change of 2026-11-12 without an earlier held version left unexplained.

### Decision

1. **The rate sentence carries the ended count's qualifier:** "<rate>, from <N> other ended trial(s) listed under the sponsor, trials first posted under another sponsor included, smoothed toward <class rate>, the class rate for <the class's sponsors>" (tested with the memo 2 case, 1 ended and 0 first posted, and the memo 5 case, 2,075 against 1,732).
2. **The low findings:** the change list names code 32 "interventions or phase named in the title" in the memo and the API (ADR 0043's table of words, the dashboard's stubs recorded again); the OTHER class reads "the class rate for other sponsors (mostly universities and hospitals)", and every class reads "the class rate for <its sponsors>" in the same sentence (tested for all nine); a change with no earlier held version says "no earlier version of this trial is held, so there is nothing to compare it with".
3. **The flag:** turned on only if the light audit of the regenerated memos confirms 0 high and 0 medium findings; then the API's memo is checked on a top-100 trial, `scripts/check_dashboard.py` also checks the memo section, a screenshot goes into the README with one sentence and the model card's live system gains a line.

### Implementation details decided while building (flagged for review)

- **The class rate comes before its name** ("smoothed toward 16.5%, the class rate for other sponsors (mostly universities and hospitals)"; "16.0%, the class rate for industry sponsors, as the sponsor has no track record"), so no class's name is followed by "of 16.5%", which read as part of the name; the decision's phrase is kept word for word.
- **The class words for the rate** (`RATE_CLASS_WORDS`): "industry sponsors", "the US national research institutes", "US federal agencies", "other government sponsors", "research networks", "individual sponsors", "other sponsors (mostly universities and hospitals)", "sponsors of unknown class", "sponsors of ambiguous class"; without a stored class, "the overall rate".
- Payload version 6 and memo version memo_v6, so every stored memo is rebuilt.
