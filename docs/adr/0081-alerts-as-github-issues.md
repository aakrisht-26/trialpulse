# 0081. Alerts as GitHub issues

- Date: 2026-11-10
- Status: **Accepted.** Decided by Aakrisht on 2026-11-10 (the Step 17 plan, docs/step17_plan.md, decision 8).

## Decision

Each alert kind opens or reuses one GitHub issue, from the workflows: watermark lag over 3 days, quarantined records over 1% of a run's records, drift on over 20% of the features of a week, and a matured 12-month AUC below the lower bound of the registered interval. The thresholds are in config/project.yaml (`alerts`). A dispatch of `grade.yml` with `fail_on_purpose` proves the path; its test issue is closed after it is confirmed.

## Alternatives

- **E-mail:** a new secret and a third-party action.
- **The page only:** easy to miss.

## Amendment of 2026-11-17: the top features

Decided by Aakrisht on 2026-11-17. Realistic changes flag only a few features (ADR 0076: all four of its realistic changes at once flag 3 of 52), so the 20% share alone would never alert on a real shift in the most important feature. **The drift alert also fires when any of the 5 most important features drifts**, at any share. The 5 are the largest of the "12 months, mean" column of the importance table in [explanations.md](../explanations.md): `enrollment_count`, `sponsor_stop_rate`, `planned_duration_months`, `landmark_index`, `sponsor_class`, listed in config/project.yaml (`alerts.drift_top_features`). A test reads the table and fails when the list stops matching its top 5; others test both rules.

`sponsor_stop_rate` has no drift row of its own: it is computed after the features from `sponsor_class`, `sponsor_prior_ended` and `sponsor_prior_stopped`, which are checked (ADR 0076). It counts as drifted when one of those three drifted. The alert stays one kind, `drift`, so the workflows are unchanged; its log line names the top features that drifted.

Alternative considered for the sponsor rate: give it a drift row of its own, which needs a new reference made on the laptop from the gated data and changes the step's 52 columns. Watching its inputs covers it without either.

**ADR 0076's realistic cases through the alert logic** (the drift step's own comparison on the 88,759 trials served on 2026-11-09 against the 2026-11-03 reference, then `monitor.alerts.check`; the PSI values are those of ADR 0076):

| Case | Features flagged | Share rule (over 20%) | Top-feature rule | Alert |
| --- | --- | --- | --- | --- |
| Unchanged | none | no | none | none |
| `enrollment_count` times 1.2 | `enrollment_count` | no (1 of 52) | `enrollment_count` | **drift** |
| `n_versions` plus 1 | `n_versions` | no (1 of 52) | none (not a top feature) | none |
| `planned_duration_months` plus 3 | `planned_duration_months` | no (1 of 52) | `planned_duration_months` | **drift** |
| `risk_12m` times 1.2 | none (PSI 0.0219) | no | none | none |
| `risk_24m` times 1.2 | none (PSI 0.0247) | no | none | none |
| All four at once | the three features | no (3 of 52) | `enrollment_count`, `planned_duration_months` | **drift** |

The risks are not features, so neither rule reads them; a uniform rise in the risks stays the grading's to see (ADRs 0077 and 0080).
