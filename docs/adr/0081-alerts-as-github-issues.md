# 0081. Alerts as GitHub issues

- Date: 2026-11-10
- Status: **Accepted.** Decided by Aakrisht on 2026-11-10 (the Step 17 plan, docs/step17_plan.md, decision 8).

## Decision

Each alert kind opens or reuses one GitHub issue, from the workflows: watermark lag over 3 days, quarantined records over 1% of a run's records, drift on over 20% of the features of a week, and a matured 12-month AUC below the lower bound of the registered interval. The thresholds are in config/project.yaml (`alerts`). A dispatch of `grade.yml` with `fail_on_purpose` proves the path; its test issue is closed after it is confirmed.

## Alternatives

- **E-mail:** a new secret and a third-party action.
- **The page only:** easy to miss.
