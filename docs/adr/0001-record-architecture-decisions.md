# 0001. Record architecture decisions

- Date: 2026-09-23
- Status: Accepted

## Context

TrialPulse involves many decisions with real tradeoffs: data sources, locked outcome definitions, modeling methods, evaluation protocol and tooling. CLAUDE.md requires every accepted decision to be recorded with its reasons, and the locked definitions in Section 6 may change only through an ADR that Aakrisht approves. Without a written record, the reasons behind a choice are lost, and a reviewer cannot tell a considered decision from an accident.

## Decision

Use Architecture Decision Records in the format described by Michael Nygard.

- Each ADR is a Markdown file in `docs/adr/` named `NNNN-short-title.md`, numbered sequentially and never renumbered.
- Each ADR has a date, a status (Proposed, Accepted, Superseded by NNNN) and the sections Context, Decision, Alternatives and Consequences.
- Flow: Claude presents 2 to 3 options with a recommendation, Aakrisht decides, and the accepted decision is recorded in the same roadmap step.
- An accepted ADR is not rewritten. A new ADR supersedes it, and both are marked.

## Alternatives

- **Decisions only in commit messages.** Scattered and hard to find, with no natural place for rejected alternatives.
- **One running decisions log.** Grows long and makes it awkward to supersede a single decision.
- **A wiki outside the repository.** Drifts from the code and is not versioned with it.

## Consequences

- Small writing overhead per decision.
- Every major choice, including every change to a locked definition, has a traceable record for reviewers and interviewers.
