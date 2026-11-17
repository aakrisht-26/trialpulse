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

## Amendment of 2026-11-17: a comment only on a change

Decided by Aakrisht on 2026-11-17. The alerts job commented on an alert's open issue at every run that raised it, about two a day with the second cron. It now writes only when the alert's state changes (raised, cleared, raised again after clearing) or its details change, and never twice for the same state:

- The alerts step writes `alert_states`: every kind, raised or not, with a key of the details that matter (`monitor.alerts.states`). The key leaves out what moves every day: for the watermark lag it is the stale data date (not today's date); for drift, the rules that fired and the drifted top features (not the share or the week); for the matured AUC, the graded prediction date; a quarantine has no key, since its counts belong to one run.
- `monitor.alert_issues` (standard library and the GitHub CLI only, so the job installs nothing) compares each kind with the last state recorded on its open issue, a hidden marker in the issue or comments written by GitHub Actions (a marker written by anyone else is ignored). It opens an issue on a raise with none open, comments on a change, and does nothing otherwise. A cleared alert gets a comment and its issue stays open for a person to close; a later raise is recorded on it while it is open, and opens a new issue once it is closed. An issue from before this change, which has no marker, counts as raised with an unknown key, so it gets one comment and then stays quiet.
- The job runs whenever the alerts step answered, raised or not, so a clear is recorded. A step that could not read the monitoring tables writes no states, and the job then changes nothing: it never reads as a cleared alert.

Tests: the decision table, a simulation of many runs on an in-memory GitHub (a lasting alert opens one issue and is never commented on again; a changed top feature, a clear and a raise after it are one comment each), separate issues per kind, closed issues, the issues opened before the change, foreign markers, missing or unreadable states, and that the helper imports the standard library only.

Alternatives: the previous state in a Neon table (a write path from the alerts job, which has no database secret) or in the Actions cache (evicted after 7 days unused, so a quiet week would forget a raised alert). The issue itself holds the state where a person reads it.
