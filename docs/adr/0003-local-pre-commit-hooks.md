# 0003. Run ruff and mypy in pre-commit as local hooks through uv

- Date: 2026-09-23
- Status: Accepted

## Context

Step 1 requires pre-commit hooks for ruff, ruff format and mypy, and CI runs the same tools. If the hooks and CI use different tool versions or different environments, a commit can pass locally and fail in CI, or the reverse.

## Decision

`ruff check --fix`, `ruff format` and `mypy src` are `repo: local` hooks with `language: unsupported` (pre-commit 4.x's name for the former `system`), each invoked through `uv run`. Tool versions therefore come only from `uv.lock`, and mypy checks against the real installed packages, including the pydantic plugin. The file hygiene hooks (end-of-file-fixer, trailing-whitespace, check-added-large-files) come from `pre-commit/pre-commit-hooks`, pinned to the commit SHA of v6.0.0. Approved by Aakrisht on 2026-09-23.

## Alternatives

- **Remote hook repositories (`ruff-pre-commit`, `mirrors-mypy`).** They work without uv, but their versions are pinned separately from `uv.lock` and can drift. mypy would run in an isolated environment that cannot see pydantic unless it is listed again under `additional_dependencies`, so its results could differ from `uv run mypy src`.

## Consequences

- One source of truth for tool versions: `uv.lock`.
- Any machine running the hooks needs uv on PATH.
- The mypy hook checks all of `src/` whenever a Python file changes. That is fast at the current size; revisit if it becomes slow.
