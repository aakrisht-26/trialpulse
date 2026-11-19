# 0085. The memo is built by a template; the language model is retired

- Status: **Accepted.** Decided by Aakrisht on 2026-11-19, after the audit of the memo_v3 memos. Amends ADR 0084 and deviates from CLAUDE.md's Stretch item 2, which asks for a memo "written by an LLM".

## Context

The per-trial risk memo (ADR 0084) went through three versions in three days, each followed by a review or an automated read-only audit of 20 stored memos.

- **memo_v1 (2026-11-17).** Groq's `openai/gpt-oss-120b` wrote all three paragraphs from a payload of stored values, and a deterministic fabrication check held every number, date, registry id and name to the payload. The first run stored 87 memos and rejected 13. Every number in the 20 reviewed memos was right, and the review still failed. Four of the 20 said wrong things about the record changes: that no change was held when several were listed, the wrong kinds of change on a date, a garbled before and after value. The model had related true facts to each other wrongly, and a check of numbers and dates cannot see that.
- **memo_v2 (2026-11-18).** Paragraphs 1 and 3 became a template, and the model wrote only one sentence per driver from a table of plain-English labels, held to its driver's label, direction and numbers. Run 3 stored 100 memos with 0 rejected (895 tokens a memo). The audit (two auditors, two adversarial verifiers) confirmed 40 findings, 13 of them high. Every high finding was in the template's paragraph 3, and the per-sentence rule held only a value's numbers, so a sentence with the wrong words passed.
- **memo_v3 (2026-11-18 and 19).** The first posting stood apart, the labels were rewritten, and the per-sentence rule held each sentence to its driver's display value word for word, refused any other level of the feature and allowed only connective words beside the label, value and effect. The prompt then asked for exactly "The <label> is <value>, which <effect>." Runs 4 and 5 stored 140 memos with 0 rejected (887 and 902 tokens a memo). The audit confirmed 20 findings: 0 high, 6 medium and 14 low, almost all in the label wording.

With the memo_v3 check, the only text the model could store was the text a template writes from the same table. The model had been narrowed until its output equaled a template. It added no content, and it cost a GitHub secret, Groq's free daily token limit (about 100 memos a run, so a large day stopped partway) and an outside service whose failure stopped the run.

## Decision

1. **memo_v4: every paragraph from the template.** Paragraph 2 is one sentence per 12-month driver in the payload's order: "The <label> is <value>, which raises the risk." (or "lowers"). For a trial chosen because its risk moved, paragraph 1 adds after its lead-in: "They explain the current level of the 12-month score, not its change." The lead-in itself says the sentences "name the features that contributed most to the model's 12-month score".
2. **The fabrication check stays** as the test of the template at every generation: a memo that fails is not stored, the issue for rejections over 20% of a run stays, and every memo should pass. A failure is a template bug to fix.
3. **Groq leaves the memo path.** `memo.yml` no longer reads `GROQ_API_KEY`, the GitHub secret is deleted, the memo prompt files and the generator's Groq client go, a test fails if any workflow names the key, and every stored memo's `model` reads "template" (`prompt_version` memo_v4). Step 6's labeler is unchanged, and the key stays in .env for it.
4. **Selection is unchanged** (the top 100 unflagged trials by 12-month risk, plus the trials whose risk moved by 0.05). With no token limit, `memo.cap_per_run` is 2,000, so one run covers every chosen trial. The Neon size is recorded after the first run.
5. **The audit's wording fixes:**
   - no label holds a comma (tested); the three that did are rephrased;
   - the sponsor counts say what each counts (features/sponsor.py): "number of interventional trials first posted under the sponsor", and the ended and stopped trials "listed under the sponsor", with "trials first posted under another sponsor included"; the rate is the "early-stop rate of the ended trials listed under the sponsor", shown as "24.1% after smoothing toward the class rate". A rate above the class rate next to 0 registrations then reads as what it is;
   - "no earlier value is held for this trial" where a change since go-live has no earlier held version, with no claim that go-live is the cause;
   - "the 24-month checkpoint" becomes "stage 4 of 6, a stage every 6 months after the first posting", labeled "stage of the trial used by the score", so it cannot be read as the 24-month horizon;
   - a missing maximum age, which is the registry's "N/A (No limit)", is shown as "no upper limit";
   - "the phase named in the title is none", "the past or current suspension is recorded" (or "not recorded"), and no double colon in paragraph 3;
   - approved: each change date as its own sentence, and the status feature's words limited to the open statuses.

## Implementation details decided while building (flagged for review)

- **No equality rule for paragraph 2 in the check.** Requiring paragraph 2 to equal the template would be a tautology at generation, where it is the template. The check's own rules, written apart from the template, are the test. The rules written for model text (connective words, other levels, past-tense outcome claims) are kept whole.
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

- A memo is a pure function of its payload: reproducible, free and not limited per day. A run takes seconds, and no secret or outside service is involved.
- Stretch item 2's "written by an LLM" is not met. The deviation is recorded in CLAUDE.md's Amendments, with this history as the reason.
- The memo can say no more than the label table and the template say. Its wording is now entirely the table's, and the tests hold the table.
- One known limit stays. Some of the model's drivers state one fact twice (the past or current suspension beside the suspension status; the start as now recorded beside the start date's move; the enrollment count beside its ratio to the first target). The memo states each driver as the model ranked it, and the lead-in says the drivers are not reasons.
