# 0019. Text components by TF-IDF and truncated SVD, not sentence embeddings

- Date: 2026-10-07
- Status: **Accepted** under the rule CLAUDE.md Section 8 already sets: "If embedding all distinct texts would take more than about 2 hours on CPU, use TF-IDF plus truncated SVD instead and record an ADR." The cost check below shows the condition holds. The details of the replacement (decisions 2 to 5) were accepted by Aakrisht on 2026-10-10 (review of Step 7), with decision 6.
- Refines CLAUDE.md Section 8 (text family) and Section 7, item 6 (the embedding model).

## Context

Section 8 asks for "biomedical sentence embeddings of eligibility criteria and brief summary, reduced by PCA to 32 dimensions fit per origin", computed once per distinct text and cached, and gives a fallback if that would take more than about 2 hours on CPU. The project assumes a mid-range laptop without a GPU (Section 3).

**How many texts.** A feature at a landmark reads the text in effect at that landmark. Over the 1,511,335 landmark rows of the final cohort and of the cohort as of each origin:

| Field | Distinct texts | Characters | Median length |
| --- | --- | --- | --- |
| Eligibility criteria | 441,859 | 853,536,518 | 1,154 |
| Brief summary | 393,576 | 280,076,095 | 484 |

**How fast an encoder runs here.** The forward pass of an encoder costs the same whatever its weights are, so the architectures were timed on this machine's CPU (10 threads) with random weights, without downloading a model. The open biomedical sentence models (PubMedBERT, BioLinkBERT and BioLORD derivatives) share the BERT-base architecture.

| Architecture | Sequences per second at 128 tokens | At 256 tokens |
| --- | --- | --- |
| BERT-base (12 layers, 768 wide) | 19.1 | 8.7 |
| MiniLM-L6 size (6 layers, 384 wide), not biomedical | 116.0 | 54.4 |

A median eligibility text is about 290 tokens, so it fills a 256-token window; a median summary is about 120 tokens.

| Model size | Eligibility (256 tokens) | Summary (128 tokens) | Total |
| --- | --- | --- | --- |
| BERT-base | 441,859 / 8.7 = 14.0 hours | 393,576 / 19.1 = 5.7 hours | **19.8 hours** |
| MiniLM-L6 size | 2.3 hours | 0.9 hours | **3.2 hours** |

Both are over the limit of about 2 hours, the biomedical one by a factor of ten.

## Decision

1. **The text components are TF-IDF reduced by truncated SVD,** 32 dimensions per text field, for the eligibility criteria and the brief summary in effect at the landmark.
2. **Words are hashed, once per distinct text.** Each text becomes word counts in 2^18 hashed columns (`features.text_hash_bits`). A word is a run of letters, digits and hyphens that starts with a letter and is at least two characters long, lowercased. Hashing has no fitted state: the same text gives the same counts whatever the other texts are. The counts are computed once per distinct text hash and cached, as Section 8 asks of the embeddings they replace.
3. **Everything fitted is fitted per origin.** The inverse document frequencies and the SVD are fitted for each walk-forward origin on the distinct texts of that origin's training rows only (Section 6), with sublinear term frequency, smoothed inverse document frequency and rows scaled to unit length. Hashed columns that no training text uses are left out of the fit: they are zero for every training text and can carry no component.
4. **The components are stored per origin and per distinct text** (`data/features/origin_<date>/<field>_components.parquet`), for the texts of that origin's training and evaluation rows, rounded to 6 decimals.
5. **The fit is reproducible on one machine.** It runs in double precision, on one BLAS thread, with the seed `seeds.default`. Two builds give identical components (checked by the build's checksums), whatever the number of cores the machine offers. Across processor types or BLAS libraries the last kept decimal may still differ, so a served model uses saved transforms, not a refit (see Consequences).
6. **`sentence-transformers` leaves the locked stack** (Aakrisht, 2026-10-10). It was listed in CLAUDE.md Section 15 for the embeddings this ADR replaces and was never added to the project's dependencies. `joblib` (the cache of word counts) and `threadpoolctl` (one thread for the fit), which the feature build imports and scikit-learn already installs, are declared as direct dependencies.

## Evidence

- The cost check above. Timing script and counts are reproducible; the timing used a throwaway environment and added no dependency to the project.
- Cost of the replacement, in Claude's shell: hashing all 835,435 distinct texts takes about 2 minutes, once; fitting and applying both fields takes 35 to 60 seconds per origin.
- `test_text_components_are_fitted_on_the_training_texts_only`: the same training texts give the same components; other training texts give others; a text's components do not depend on what else is transformed.
- Why one thread and double precision (found in the Step 7 review): the randomized SVD in single precision gave components that differed by up to 3.5e-05 between runs with different numbers of BLAS threads, more than the 6 decimals kept, so the build's checksums depended on the machine.
- `test_a_cache_that_cannot_be_trusted_is_not_used` and `test_the_build_recomputes_counts_it_cannot_trust_and_reuses_the_rest`: the cache of word counts is one file per field, written beside its place and renamed over it. It records the settings of the vectorizer and the scikit-learn version, and a digest of its contents. Counts made under other settings, a file cut short, and contents that do not match the digest are recomputed.
- `test_what_is_fitted_for_an_origin_depends_only_on_its_training_rows`: every version posted on or after an origin is rewritten and the cohort and features are rebuilt; the components of the texts of that origin's training rows are unchanged.

## Alternatives

- **Sentence embeddings anyway, as a long one-off job.** About 20 hours on this machine, and again for every dataset refresh. Section 3 says to stop and propose a cheaper plan for any job over about 1 hour.
- **A small general-purpose sentence model.** Still over the limit (3.2 hours), and not biomedical, which was the point of the embeddings.
- **Embed on a rented GPU and keep the vectors.** The vectors could not be rebuilt on the project's machine, and a 1 GB artifact would have to live outside the repository. It stays a possible later improvement, with its own ADR; the text components are one feature family and can be replaced without touching the others.
- **A fitted vocabulary (plain TF-IDF) per origin.** It would tokenize a gigabyte of text once per origin and field. Hashing tokenizes once and leaves only cheap matrix work per origin. The cost is hash collisions among 2^18 columns, which blur rare words slightly.

## Consequences

- `sentence-transformers` is no longer part of the locked stack (decision 6). Adopting sentence embeddings later needs a new ADR that puts it back.
- The word-count cache is saved with joblib, and the fit is held to one thread with threadpoolctl. scikit-learn requires and installs both, so declaring them (decision 6) installs nothing new.
- The Step 10 ablation ("plus text") measures these components. If they add nothing, the text family reduces to the eligibility statistics.
- Scoring a new text (Steps 13 and 14) needs the fitted inverse document frequencies and SVD of the champion's origin. The build writes the components, not the fitted transforms; Step 13 saves the transforms with the registered model.
- SHAP drivers named "component 7 of the eligibility text" are not readable to a user. Step 12 should group the 64 components into "eligibility text" and "summary text" when it reports drivers.
