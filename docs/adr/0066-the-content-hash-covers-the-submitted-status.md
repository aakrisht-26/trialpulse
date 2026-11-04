# 0066. The content hash covers the submitted status

- Date: 2026-11-04
- Status: **Accepted.** Decided by Aakrisht: guidance item 10 of 2026-10-07 (review of Steps 4 and 5) asked for it at the start of Step 14, with an ADR, because it changes the Step 3 contract and needs a warehouse rebuild; his instruction for phase A of 2026-11-04 (item 1) ordered it built with its rebuild and an old-against-new guard.
- Changes the canonical version contract (CLAUDE.md Step 3, `trialpulse.contracts.versions`). Refines ADR 0014 (decision 1, the submitted status) and ADR 0037 (decision 2, the identity of a record). Warehouse schema version 4, together with ADR 0067.

## Context

The canonical contract of Step 3 gives every version a `content_hash`: a SHA-256 over every content column, the registry's `overall_status` and `last_known_status` among them. Both paths use the same function, so an unchanged record gets the same hash from the dataset and from API v2.

The registry computes UNKNOWN and sets it without a new version (ADR 0014): a record whose submitted status is RECRUITING is shown as UNKNOWN, with `lastKnownStatus` RECRUITING, once its verification has lapsed. Under the Step 3 hash the same record then hashes as a different one. The parity command of Step 3 counted such records as differing rows (its known cause "UNKNOWN set by the registry without a new version"), and any comparison by content hash would see a new version where the sponsor posted none.

Guidance item 10 set the rule: the content hash covers the submitted status, not the registry's computed UNKNOWN. The live path already follows it: its compact rows hash the submitted status (ADR 0037, decision 2), and `serving.content_hash` is that feature hash from the bootstrap on. The warehouse's own `content_hash` was left as it was until this rebuild (docs/progress.md, Step 14, "Left for the laptop").

## Decision

1. **The hash reads the submitted status in the place of the two status columns.** The submitted status is `last_known_status` where `overall_status` is UNKNOWN and `last_known_status` is set, and `overall_status` otherwise: the rule of ADR 0014 (decision 1), of `cohort.rules.submitted_status` and of the view the feature queries read (`features.states.expose_versions`). A test holds the contract's rule equal to the cohort rule under config/project.yaml.
2. **Both columns stay in the canonical row as they are.** The cohort's `registry_unknown` flag, the data audit and the parity command read the registry's label. Only the input of the hash changes.
3. **One function on both paths, as before** (`contracts.versions.content_hashes`). A record from the dataset and the same record from API v2 hash alike, and two records that differ only in the registry's UNKNOWN label, with the same submitted status, hash alike. Tests hold both.
4. **Warehouse schema version 4, one rebuild, and its guard.** The warehouse is rebuilt once for this ADR and ADR 0067, and the rebuild must preserve everything else:
   - an old-against-new comparison of the two warehouse files (`trialpulse.warehouse.compare`), table by table and column by column with rows matched by their keys, must find every column of every table identical except `content_hash` and ADR 0067's `intervention_types`, apart from the build's own record (`build_info`: the schema version and the new statistics of the interventions config);
   - a second build must report "Identical to the previous build: yes";
   - the cohort build and the feature build, and their UNKNOWN variants (ADR 0029), must report "Identical to the previous build: yes";
   - no registered number may move: the tests that hold docs/results.md, docs/results_dev.md, the model card and docs/model_statement.json to their records, and the checks that recompute numbers from `data/`, must pass unchanged.

## Alternatives

- **Keep the Step 3 hash.** A relabel by the registry would count as a changed record. Guidance item 10 rejects it.
- **Write the submitted status into `overall_status` and drop `last_known_status`.** One column fewer, but the warehouse would lose the registry's label, which `registry_unknown` and the data audit read, and the contract of ADR 0014 (warehouse schema version 2) would change with it.
- **Hash only the fields a served feature reads.** That is the compact row's feature hash (ADR 0037). The canonical hash identifies the whole canonical record, texts included, for the warehouse and the parity command.

## Consequences

- Every stored content hash changes, since the input changed for every row (and ADR 0067 adds a column to it). No feature reads `content_hash` (it is outside the feature whitelist) and the cohort build reads none, so no cohort or feature output can move; the guard and the builds' own comparisons check it.
- Step 13's score files (`data/scoring/scores/`) and the local serving tables keep the schema-3 hashes. They are not regenerated here: the bootstrap regenerates the cutoff's scores, dated the cutoff, with the feature hash as `content_hash` (ADR 0037, decision 2), and that replaces the local rows. Neon starts empty, so no hash there changes.
- The parity command now counts a record the registry relabeled UNKNOWN without a new version as an identical row. Its column report still shows the two status columns differing, with their cause.
- The cohort and feature builds accept the new warehouse unchanged (they require schema version 3 or later) and read neither status column through the hash.
