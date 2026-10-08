# 0020. Intervention types from the dataset's `interventions` config

- Date: 2026-10-07
- Status: **Accepted on the condition Aakrisht set** on 2026-10-07 for Step 7: "Evaluate the interventions config; adopt intervention type only through an ADR, and only if it is truly per version." The evaluation below shows the config is per version. Listed for his confirmation in the Step 7 report.
- Refines CLAUDE.md Section 7 (item 1: "if they are published later, adopting them requires an ADR") and Section 8 (design family).

## Context

Step 2 found that the `core` config holds no list of interventions, and that the current record's interventions fail the stability rule of Section 8: they changed after version 0 in 20 of 150 sampled trials (13.3%). So the type of intervention (drug, device, behavioral and so on) could not be a feature.

The pinned dataset revision (`v2026.09.26`) also publishes a config named `interventions`: 19 Parquet files, 91,548,915 bytes. The dataset card describes it as "one row per (nct_id, nct_version, intervention_name, intervention_type)".

## Evaluation

| Check | Result |
| --- | --- |
| Columns | `nct_id`, `nct_version`, `intervention_name`, `intervention_type`, `description` |
| Rows | 9,401,447, for 3,843,841 distinct (trial, version) pairs |
| Pairs that are not in `core` | 0 |
| Interventional versions of `core` without a row | 221 of 3,483,392 (217 trials): versions that list no intervention |
| Missing types or names | 0 |
| Intervention types | 11 values, the registry's own: DRUG, PROCEDURE, OTHER, BIOLOGICAL, DEVICE, BEHAVIORAL, RADIATION, DIETARY_SUPPLEMENT, DIAGNOSTIC_TEST, GENETIC, COMBINATION_PRODUCT |
| Trials whose set of types differs between two of their versions | 23,726 of 544,285 (4.4%) |

**Is it truly per version?** Step 2 part f fetched every version of 150 seeded trials from the registry's own version history (973 versions) and kept each version's interventions as type and name. Those cached versions were compared with the config, with no new request:

- The set of (type, name) pairs is identical in **973 of 973 versions**. Types are compared without regard to case: the registry's history writes "Drug" where the config has DRUG.
- All 20 trials whose interventions change across versions change at the same versions in the config. In 10 of them the list of types changes (an intervention of some type is added or removed), and in 6 of those the set of distinct types changes. Both change at the same versions in the config.

The config reproduces the registry's history exactly on this sample, including the changes that made the current record unusable.

## Decision

1. **The `interventions` config of the pinned revision is a second offline source,** read by the feature build from `data/raw/history/<revision>/interventions/`. `uv run python -m trialpulse.ingest.history --config interventions` downloads it.
2. **Only `intervention_type` is read,** joined to the version in effect at the landmark by (trial, version). The free-text names and descriptions are not read.
3. **Thirteen design features:** the number of interventions listed, the number of distinct types, and one flag per type. A version that lists no intervention has 0 and all flags false.
4. `intervention_type` is on the feature whitelist (`registry.INTERVENTION_FIELDS`).

## Alternatives

- **Leave intervention type out.** The registry's design fields say how a trial is run, not what is tested. Whether a trial tests a drug, a device or a behavioral program is basic design information that the versioned record now provides.
- **Use the current record's types** (Step 2's bulk snapshot). Not point-in-time: 4.4% of trials change their set of types between versions, and Section 8 allows a current-record field only under 3%.
- **Parse types from the versioned titles,** as ADR 0006 does for phase. Titles rarely say "device" or "behavioral".

## Consequences

- A second download (92 MB) is needed before the feature build. The raw files are never committed or redistributed (license CC-BY-NC-4.0), like `core`.
- **The canonical version schema does not carry intervention types yet.** The warehouse of Step 3 is unchanged; the feature build joins the raw config directly. For the live path (Step 14), API v2 gives the same values at `protocolSection.armsInterventionsModule.interventions.type` (path confirmed in Step 2 part g). The canonical contract should gain a list of intervention types at the Step 14 warehouse rebuild, which is already planned for the content hash, with a parity test between the two paths. Until then serving parity for these 13 features is not tested.
- If a later dataset revision drops the config, the features are dropped with it and the models retrained; they are one block of the design family.
- The data card (Step 12) cites the config beside `core`.
