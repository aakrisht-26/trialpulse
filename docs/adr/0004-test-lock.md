# 0004. Test lock: an annotated prereg-v1 tag on a completed preregistration

- Date: 2026-09-23
- Status: Accepted (specified by Aakrisht on 2026-09-23). Implemented in Step 8.

## Context

CLAUDE.md Section 10 allows evaluating the locked origins (2018 and 2019 test, 2020 COVID stress) only with the `--unlock-test` flag and a `docs/preregistration.md` committed before the run. A header stub of that file has been in git history since Step 1, so a check of the form "the file was committed before the run" is already satisfied, and the lock would protect nothing. The lock needs a condition that a stub or a half-written draft cannot meet by accident, and that leaves an auditable record of exactly what was registered.

## Decision

The evaluation harness evaluates a locked origin only when every condition below holds. Otherwise it refuses and names the failed condition.

1. The run was started with `--unlock-test`.
2. A git tag named `prereg-v1` exists and is an **annotated** tag (`git cat-file -t prereg-v1` returns `tag`). Lightweight tags are rejected because they carry no tagger, date or message.
3. In the commit the tag points to, `docs/preregistration.md`:
   - contains a line of the form `Registered: YYYY-MM-DD` with a valid ISO date, and
   - contains no placeholder text. Placeholder text is any case-insensitive match of: `not yet registered`, `TODO`, `TBD`, `TBC`, `FIXME`, `XXX`, `placeholder`, `fill in`, `lorem ipsum`, or an unfilled template token in angle brackets such as `<prediction>`. The list lives in the Step 8 code and tests. Empty results blocks, as the protocol requires, are written as a heading or label with nothing after it, never with placeholder words.
4. The tagged commit is an ancestor of `HEAD` (`git merge-base --is-ancestor prereg-v1^{commit} HEAD`), so the registration came before the code that is being run.
5. The tag object hash and the tagged commit hash are recorded with the results files and as MLflow tags on every locked-origin run.

The Step 1 header stub must never satisfy the lock: it has no `Registered:` line and it contains `not yet registered`. Step 8 includes a test that builds a temporary repository with that stub, tags it `prereg-v1`, and asserts the lock refuses.

## Alternatives

- **Any committed version of `docs/preregistration.md` (the literal Section 10 wording).** Already satisfied by the Step 1 stub, so it provides no protection.
- **Record a content hash of the registration in `config/project.yaml`.** Works, but it needs a config edit after registration, and the hash alone does not show when the registration was made.
- **A GPG-signed tag.** Stronger proof of authorship, but it needs key setup on Aakrisht's machine and in CI. It can be added later without changing the rest of this design.
- **A lightweight tag.** Cannot carry a tagger, a date or a message, so it gives a weaker audit trail.

## Consequences

- Step 11 must finish the preregistration, commit it, create the annotated tag (`git tag -a prereg-v1 -m "..."`) and push the tag before unlocking. The pushed tag gives a public timestamp.
- A tag can be moved with a force update. Because every locked run records the tag and commit hashes, a move is detectable by comparing against the recorded hashes. Moving the tag is not allowed; a new registration uses `prereg-v2` and a new ADR.
- The placeholder list may reject a legitimate word in rare cases (for example "XXX" in a quoted string). The fix is to reword the registration, never to weaken the check.
