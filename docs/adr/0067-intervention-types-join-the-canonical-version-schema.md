# 0067. Intervention types join the canonical version schema

- Date: 2026-11-04
- Status: **Accepted.** Decided by Aakrisht: guidance item 13 of 2026-10-10 (review of Step 7, confirming ADR 0020) asked for intervention types in the canonical version schema at the start of Step 14, with a parity test between the dataset path and the API v2 path; his instruction for phase A of 2026-11-04 (item 1) ordered it built with its rebuild and an old-against-new guard.
- Changes the canonical version contract (CLAUDE.md Step 3, `trialpulse.contracts.versions`). Refines ADR 0020 (its consequence "The canonical version schema does not carry intervention types yet") and ADR 0037 (the compact row's intervention types). Warehouse schema version 4, together with ADR 0066.

## Context

ADR 0020 found the dataset's `interventions` config to be per version (973 of 973 sampled versions agree with the registry's own history) and made intervention types the source of 13 design features: the number of interventions, the number of distinct types and one flag per type. The feature build joins the raw config directly. The canonical schema did not carry the types, so the two paths could not be compared on those features until the Step 14 rebuild.

The live path already reads `protocolSection.armsInterventionsModule.interventions` (each intervention's name and type) from API v2 into its compact row (ADR 0037): one type per distinct (name, type) pair, compared as exact strings, sorted (`distinct_intervention_types`), because API v2 repeats a pair on some records.

The pinned revision's config (v2026.09.26), counted on 2026-11-04:

| Check | Result |
| --- | --- |
| Rows, versions | 9,401,447 rows for 3,843,841 versions |
| Rows with the same version, name and type | none: every row is a distinct pair |
| Missing or empty names, missing types | none |
| Types | the registry's 11, all in upper case, none changed by trimming and upper-casing |
| Names in one version that differ only in letter case | 2,664 groups, kept as separate rows |
| Versions not in `core` | none |
| Versions of `core` without a row | 600,701 |
| Versions where the feature build's list (every row) differs from the list of distinct pairs | none |

## Decision

1. **The column.** The canonical row gains `intervention_types` (`VARCHAR[]`): one type per distinct (name, type) pair of the version, sorted. Names and types are compared as exact strings, as on the live path. A type is first put in the contract's canonical form like every enum (`canonical_enum`: trimmed, upper case, older spellings mapped), and a pair without a type is left out. A version that lists no intervention has an empty list, never NULL. Because a type repeats once per intervention of that type, the list holds both the number of interventions and the set of types, which is what the 13 features read.
2. **The dataset path.** The warehouse build reads the interventions config of the pinned revision (`data/raw/history/<revision>/interventions/`) and joins its list to each version by (nct_id, nct_version), by the same rule in SQL; a test holds the SQL rule equal to the Python one.
3. **The API v2 path.** `canonical_from_api_v2` reads `armsInterventionsModule.interventions` by the same rule. A list that is not a JSON array, or a name or type that is not a JSON string, fails the record's contract (`FieldTypeError`), as the live pull already required. The contract's `fields` parameter (`api_v2_fields`) requests each intervention's name and type, so the parity command compares the column too, and the live pull takes its types from the canonical row.
4. **Validation.** Every type must be one of the registry's 11 (`features.registry.INTERVENTION_TYPES`) and the list must be sorted; a row that fails is quarantined on either path. This replaces the live pull's own check of unknown types.
5. **The content hash covers the column,** rendered as the types joined by "|", as the compact row's feature hash renders it.
6. **What reads it.** The design features keep reading the config itself (ADR 0020), joined by version. The canonical copy stays out of the view the feature queries read: it is listed with the fields that view leaves out, because no feature names it as a source. On the pinned revision both give the same list for every version (the last row of the table above).
7. **No Alembic migration.** Since revision 0004, no Postgres table holds canonical rows (ADR 0037): `live.study_versions` holds compact rows, which already carry intervention types.
8. **Warehouse schema version 4, one rebuild and its guard,** shared with ADR 0066 (its decision 4).

## Alternatives

- **The set of distinct types.** It loses the number of interventions, which `n_interventions` reads, and it would differ from the compact row's list.
- **Every row of the config, repeated pairs kept.** The same on the pinned revision, which holds no repeated pair, but API v2 repeats pairs on some records, so the two paths would disagree there.
- **Names compared without regard to letter case.** The config keeps names that differ only in case as separate interventions (2,664 groups), so the dataset path would no longer match the config the features read.
- **Keep the types out of the canonical schema,** as ADR 0020 left them. The two paths could not be compared on these 13 features.

## Consequences

- Every stored content hash changes (ADR 0066).
- The warehouse build needs the interventions download (`uv run python -m trialpulse.ingest.history --config interventions`), already on the laptop for the feature build, and refuses without it. If a later revision drops the config, the warehouse build refuses, as the feature build does (ADR 0020).
- The data audit's count of versions identical to the previous one "in every loaded column" now includes intervention types, so it falls by the versions that changed only their interventions.
- The feature whitelist test classifies the new column; the export adapter (ADR 0037) fills it from the compact row.
- The 13 features get a dataset-to-API parity test on fixtures now, and the census of ADR 0042 can compare the column on real records.
