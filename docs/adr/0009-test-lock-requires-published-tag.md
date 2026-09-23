# 0009. Test lock: the prereg-v1 tag must also be on origin

- Date: 2026-09-23
- Status: Accepted (requested by Aakrisht in the review of PR #1 on 2026-09-23). Extends ADR 0004.

## Context

ADR 0004 unlocks the locked origins only with an annotated `prereg-v1` tag on a completed registration that is an ancestor of HEAD. A tag that exists only locally proves nothing to anyone else: it could be created, moved or deleted after a test result was seen, and nobody outside the machine could tell. The point of pre-registration is that the predictions are public before any locked result exists.

## Decision

The lock adds a fifth condition: `prereg-v1` must also exist on the remote `origin` and point at the same commit as the local tag. The harness checks it with `git ls-remote --tags origin`, taking the peeled `refs/tags/prereg-v1^{}` entry for an annotated tag. The run is refused, and the failed condition named, when:

- the tags on origin cannot be listed (no origin, or no network);
- origin has no `prereg-v1`;
- origin's `prereg-v1` points at a different commit.

The unlock record gains the remote's name, and it is stored with the results together with the tag and commit hashes (ADR 0004). The code only reads git state; it never pushes or creates tags. Tests use local bare repositories as origin.

## Alternatives

- **Local tag only (ADR 0004 as it was).** Simpler and works offline, but the registration is not provably public before the run.
- **Check the GitHub API for the tag.** Ties the lock to GitHub and needs a token; `git ls-remote` works with any remote.

## Consequences

- Unlocking needs network access to origin at evaluation time.
- Step 11 must push the tag (`git push origin prereg-v1`) before unlocking. The push timestamp on GitHub is public evidence of when the registration was made.
- When this branch is merged, ADR 0009 is added to the Amendments section of CLAUDE.md, next to ADR 0004.
