# 0055. How a flagged score appears on the dashboard

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 16 decision 4: option a).
- Refines CLAUDE.md Section 13, ADR 0034 and guidance item 24 (flagged scores ran above the observed risk in development).

## Context

Trials 42 to 119 months after registration are scored as at landmark 6 and flagged "outside the validated range" (ADR 0034); 21% of served scores were flagged at the data cutoff. In development their scores ran above the observed risk, so a flagged score may overstate it.

## Decision

1. **The number stays, with a badge.** A flagged score shows its risk with the badge "Outside the validated range" and the API's note beside it, every time it appears.
2. **Rankings keep flagged trials in place,** with a flag column. Every list that can hold one carries the note in its caption and a "Hide flagged" switch that starts off.
3. **On the timeline**, flagged points have their own marker and legend entry.

## Alternatives

- **Flagged trials in their own section below the unflagged ones:** a ranking never puts an overstated score first, but every page gets two lists and a risky older trial is easy to miss.
- **No number for a flagged trial, only the badge behind an expander:** the most cautious, but it withholds a score that the API serves and the model card explains.

## Consequences

- A flagged trial can rise in a ranking; the badge, the caption and the switch are the guard.
