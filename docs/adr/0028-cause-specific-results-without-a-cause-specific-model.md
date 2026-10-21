# 0028. Cause-specific results without a cause-specific model

- Date: 2026-10-21
- Status: **Accepted.** Decided by Aakrisht in the review of the Step 10 follow-up and the pre-registration draft on 2026-10-21, option (b) of section 5.2 of the draft: "Report the H3 analysis as the cause-specific results of Step 11 and fit no cause-specific model; record the change to Section 6's secondary target as an ADR."
- Changes CLAUDE.md Section 6 (the secondary target) and Step 11 (what "cause-specific results" means in `docs/results.md`).

## Context

Section 6 sets a secondary target: the cumulative incidence of operational stops (reasons accrual, business, funding, administrative, covid19) and of scientific stops (safety, efficacy), using the reason classifier of Step 6. No model of Steps 9 and 10 predicts a cause-specific cumulative incidence: every rung of the ladder predicts the early stop of any cause, with completion as the competing event. The cause-specific Cox model at landmark 0 (Section 9) interprets; it does not predict. Step 11 asks `docs/results.md` for "cause-specific results".

The pre-registration states H3, that operational stops are more predictable than scientific stops, and scores it with the champion's all-cause score held against each kind of early stop. Registry metadata cannot anticipate a safety or efficacy finding (Section 2), so the question a reader of the cause-specific results asks is which kind of stop the score ranks, and H3 answers it.

## Decision

1. **No cause-specific model is fitted.** The secondary target of Section 6 is replaced by the H3 analysis: the champion's all-cause score evaluated twice at each origin and horizon, once with operational early stops as the cases and once with scientific ones, against the rows with no early stop by the horizon. The labels and the censoring weights are those of the main metric, estimated on all evaluation rows of the origin (ADR 0017); only then are the rows with an early stop of the other kind, of reason "other", or without a reason text within the horizon left out of each score.
2. **These are the cause-specific results of Step 11**: the two AUCs and their difference at 12 and 24 months for each test origin, with the paired cluster-bootstrap interval of the difference (`trialpulse.eval.hypotheses h3`), reported in `docs/results.md` with the statement that errors in the reason labels push the difference toward zero.
3. **The reason labels** are those of the final distilled classifier of Step 6, pinned by the SHA-256 of the model file and of the reasons table in the registration.

## Alternatives

- **A cause-specific M4** (option (a) of the draft): four outcomes per interval (continue, complete, operational stop, scientific stop), fitted on the development origins after Step 6 and registered as a secondary model. It would give a predicted risk of each kind of stop, about 35 to 46 minutes per locked origin. Not chosen by Aakrisht, who gave no reason in the review. Claude's notes against it: scientific stops are few (177 to 389 per development origin and horizon), so their hazard would be estimated from little, and a predicted risk of a safety or efficacy stop invites the reading Section 2 forbids.
- **Leave the secondary target as written and defer it.** A quiet deferral of a locked definition, which the working agreements do not allow.

## Consequences

- Section 6's secondary target reads, through the Amendments: the H3 analysis of the champion's all-cause score against operational and against scientific early stops.
- No product surface shows a predicted risk by cause: the API and the dashboard (Steps 13 to 16) serve the all-cause risk; the "Why trials stop" page shows the reasons themselves, descriptively.
- The model card (Step 12) states that the risk is of any early stop and that its ranking of operational and of scientific stops is what H3 measured.
