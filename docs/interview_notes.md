# Interview notes

One entry per roadmap step: what was built, the key decision and why, the rejected alternative and why not, and one likely interview question with a short answer.

## Step 1: Repo skeleton, tooling, CI

**What was built.** A uv project on Python 3.12 with a src layout and a typed `trialpulse` package. Configuration is split in two: `config/project.yaml` holds the locked study definitions (population, event statuses, landmarks, horizons, walk-forward origins, metric constants, seeds), and a `Secrets` model reads credentials from the environment. Also: pre-commit hooks for ruff, mypy and file hygiene; a GitHub Actions CI job (lockfile install, lint, format, strict types, tests with coverage); a Postgres 16 container with a healthcheck; ADRs; and the progress log.

**Key decision and why.** The locked constants live in a committed YAML file validated by a strict, frozen schema, and environment variables cannot override them. A commit then fully determines the definitions a run used, which matters because the whole project depends on outcome and time definitions staying fixed across dozens of runs. Validators catch inconsistent definitions early, for example a horizon that does not land on the discrete-time interval grid, where the CIF would be undefined.

**Rejected alternative and why not.** A single settings class where environment variables override every value. It is convenient for experiments, but a leftover shell variable could silently change a locked definition. The results would then no longer match the committed config, and that is very hard to detect afterwards.

**Likely interview question.** "How do you make sure an experiment ran with the constants you think it did?"

**Short answer.** The constants are in a version-controlled file, loaded into an immutable schema that rejects unknown keys and ignores the environment. A smoke test pins the locked values, so changing one fails CI until the change goes through an ADR. Later steps log the git commit and the pinned dataset revision with every run, so any result traces back to the exact definitions and data.

## Step 2: Feasibility spike

**What was built.** A spike, runnable part by part, that checks whether the plan can work before any pipeline is built: it downloads and profiles the version-history dataset, counts the cohort and early stops, compares a seeded sample against the official API, audits how often list fields change after a trial's first version, and exercises the live API v2 delta pull. Each part writes its numbers to a results file, and a report is generated from those files. Every HTTP call is rate limited, retried with backoff and cached, so a crash or network outage costs only the requests in flight.

**Key decision and why.** Parts that cannot run are recorded as "blocked" with a reason instead of failing the whole run. Overnight, the dataset access request was still awaiting the authors' review, and the spike still produced real API numbers, a working delta pull and the stability audit. The report states each criterion as met, not met, or blocked, so a missing input is never mistaken for a passing one.

**Rejected alternative and why not.** Pulling the 200 part d records one by one from API v2. The cohort-wide bulk pull already holds the same official fields for all 420,073 cohort trials, so part d reads them from that Parquet file and only calls the API for trials missing from it. That saves 200 requests and keeps both sides of the comparison from the same snapshot.

**Likely interview question.** "Your registry data rewrites itself after a trial stops. How did you check that the version history is trustworthy before building on it?"

**Short answer.** Three independent checks: an automated comparison of the dataset's latest version against the official API for a seeded sample, counting a mismatch only when the official record was not updated after the dataset cutoff; a manual comparison of version 0 against the registry's own Record History page; and a stability audit that measures, from real version histories, how often fields that lack history in the dataset change after registration. A field is allowed only if it changes in under 3% of trials.

## Step 6 (partial, provisional): Why trials stop

**What was built.** The eight-label taxonomy with definitions and tie-break rules, a labeling guide, a seeded sampler for the 400-text gold set (stratified by status and stop year, split 100 dev and 300 test), and a Streamlit app that shows one text at a time and saves each label at once, keyed by trial and text hash, without the text. After the PR review the texts come from the current API v2 records of the cohort's early stops, normalized and deduplicated, so labeling does not wait for the version-history dataset; the real 400-text sample is built, and the logic is tested on synthetic data, including the app itself through Streamlit's test harness.

**Key decision and why.** Labels are stored separately from texts, keyed by (nct_id, text_sha256), the SHA-256 of the normalized text, with the pull date as source, so they survive a later change of history source. The labels file can be committed and reviewed, while the texts stay under the gitignored data folder, which respects the dataset license and keeps the repository free of redistributed content.

**Rejected alternative and why not.** Sampling uniformly at random. Early stops cluster in some years (and COVID-19 created a spike), so a uniform sample could leave small strata empty. Proportional allocation with at least one item per non-empty stratum keeps the gold set representative and still covers rare cells.

**Likely interview question.** "How do you know your LLM labels are good enough to train on?"

**Short answer.** A gold set of reference labels from an adjudicated model panel, split before any prompt work: 100 texts for prompt development and 300 held out. The LLM and the distilled classifier are scored once on the held-out 300 with macro-F1 (with a bootstrap interval), Cohen's kappa and a confusion matrix, and the target (macro-F1 of at least 0.80) was declared in advance.

## Step 8 (provisional): Evaluation harness and test lock

**What was built.** Inverse probability of censoring weights from a Kaplan-Meier estimate of the censoring distribution; IPCW time-dependent AUC and Brier score at a horizon; calibration against Aalen-Johansen by risk decile, plus slope and intercept; lift at 10%; a cluster bootstrap that resamples trials; the walk-forward runner; M0 (Aalen-Johansen per stratum); and the test lock from ADR 0004. Metric tests use synthetic data with known answers, including a hand-worked censoring example (IPCW AUC 0.625 against 2/3 unweighted).

**Key decision and why.** The lock is checked before any data is loaded, and it trusts only an annotated git tag on a commit whose registration is complete and is an ancestor of the code being run. A header stub or a later edit cannot satisfy it, and every locked run records the exact commit it was registered at.

**Rejected alternative and why not.** Using 1 minus Kaplan-Meier for the early-stop probability. With competing completions, it treats completed trials as if they could still stop and overstates risk; Aalen-Johansen weights each stop by the probability of still being open just before it.

**Likely interview question.** "Most of your trials have not finished yet. How is your AUC not biased?"

**Short answer.** Rows censored before the horizon get weight zero, and every row whose outcome is known is weighted by the inverse probability of remaining uncensored until its event time or the horizon. That makes the known outcomes stand in for the unknown ones. Without censoring every weight is 1 and the metric reduces to the ordinary AUC, which a test checks.

## Step 6 (continued): Reference labels from an adjudicated model panel (ADR 0010)

**What was built.** The 400-text gold set now holds reference labels from an adjudicated model panel instead of hand labels. Three independent labeler agents label each text, seeing only the labeling guide and the text under an opaque id, and each quotes the deciding words and gives a one-line justification. A fourth agent that labeled nothing decides every split from the text, the guide and the three justifications. A fresh labeler then relabels a seeded 10% as a consistency check. The code prepares the inputs, validates the outputs, resolves labels and panel outcomes, and writes the labels file with a method and a panel-outcome column. A guard test fails if any test text appears in the LLM prompt files. Results: 93.8% unanimous, 5.5% kept by the adjudicator, 0.8% overturned, and 39 of 40 consistency relabels agreeing (kappa 0.969).

**Key decision and why.** The test labels were finished and committed before any prompt work, and the orchestrating session never saw a test text: the agents read the files themselves, and the workflows and collection scripts passed only counts back to it. That keeps the held-out 300 genuinely held out, which is the whole point of a test split. The model graded against these labels must come from a different model family, because a model from the panel's family would share its habits and inflate agreement.

**Rejected alternative and why not.** Majority vote with no adjudicator. Two labelers from the same model can make the same mistake, so the adjudicator judges the justifications against the guide and may overturn a 2-to-1 vote; it did so 3 times.

**Likely interview question.** "Your gold labels came from a model. How do you know your LLM results mean anything?"

**Short answer.** I describe them honestly as reference labels from an adjudicated model panel, not human labels, so the metric measures agreement with a documented, auditable process. The panel ran under strict isolation (audited from the agent transcripts), every label has quoted evidence, splits were adjudicated rather than voted, and a consistency relabel measured stability. The graded model comes from a different family, so it cannot score well just by sharing the panel's biases, and the test split was frozen and committed before any prompt work.

## Step 6 (continued): LLM labels and the distilled classifier

**What was built.** An LLM labeler for why_stopped texts (openai/gpt-oss-120b on Groq, temperature 0, strict JSON-schema output validated in code, batches of 25, every answer cached by request hash so runs resume within the free tier's daily limits), a scoring module (accuracy, macro-F1 with a bootstrap interval, kappa, per-label F1, confusion matrix), and a distilled TF-IDF plus logistic regression classifier trained on the LLM's labels of a 10,000-text sample. Guards run before any call: the rendered prompt must contain no gold-test text, the model must come from a different family than the reference panel, and test items are labeled only with a committed prompt. Each model is scored on test once.

**Key decision and why.** The prompt was tuned on the 100 dev items only, with the selection rule declared first, and frozen in a commit before any test item was labeled. Dev macro-F1 went from 0.858 to 0.980 in two iterations, but test macro-F1 was 0.842: the gap is the optimism of tuning on the same 100 items, which is exactly why the test split stayed untouched. The production model is the distilled classifier, not the LLM, because it is free, fast, deterministic and runs offline.

**Rejected alternative and why not.** Adding few-shot examples copied from the dev items. They would have made the dev score meaningless (the answers would be in the prompt), so v2 states general rules drawn from the dev errors instead, and the third allowed iteration was not used because the two remaining dev errors were single hard cases.

**Likely interview question.** "How do you know your prompt engineering did not overfit?"

**Short answer.** I declared the rule for choosing the prompt before running it, iterated only on a 100-item dev split, froze the prompt in git, and then scored once on 300 held-out items that were never opened during prompt work, with a bootstrap interval. Dev said 0.98 and test said 0.84: the held-out number is the one reported, and the gap itself shows why the split matters.
