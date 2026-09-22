# 0002. Load locked constants from project.yaml and secrets from the environment

- Date: 2026-09-23
- Status: Accepted

## Context

CLAUDE.md Section 17 requires `config/project.yaml` to hold every tunable constant from Section 6 and a gitignored `.env` to hold secrets, loaded with pydantic-settings (Step 1). Reading YAML needs a parser, and PyYAML is not in the locked stack (Section 15), so it needed approval. The locked definitions must not change by accident, and a commit should fully determine the constants a run used.

## Decision

- **ProjectConfig** is a frozen pydantic-settings model filled from `config/project.yaml` through the pydantic-settings YAML source (PyYAML via the `pydantic-settings[yaml]` extra, approved by Aakrisht on 2026-09-23). Its environment and `.env` sources are switched off, so no shell variable can override a locked definition. Unknown keys are errors at every level. Validators check that status and stop-reason groups are disjoint, walk-forward origins are unique and ordered, and every horizon lands on the discrete-time interval grid.
- **load_project_config()** raises `FileNotFoundError` for a missing file, because the pydantic-settings YAML source skips missing files without an error.
- **Secrets** is a separate pydantic-settings model read from the environment or `.env`. Credentials are `SecretStr`, so they are masked in reprs and logs. Every field is optional, empty values count as unset, and environment variables take precedence over `.env`.
- A smoke test asserts the committed values equal CLAUDE.md Section 6, so changing a locked value fails CI until the test is updated alongside an approved ADR.

## Alternatives

- **TOML read with the standard library (`tomllib`).** No new dependency, but it departs from the file names in CLAUDE.md Sections 16 and 17.
- **Calling `yaml.safe_load` directly into a pydantic model.** Needs the `types-PyYAML` stub package for mypy strict, which is one more dependency outside the stack.
- **One settings class where environment variables override everything.** Convenient, but any stray shell variable could silently change a locked definition, and a commit would no longer determine a run's constants.

## Consequences

- PyYAML is a runtime dependency (it was already a transitive dependency of pre-commit).
- Changing a constant means editing a committed file, and changing a locked one also needs an ADR.
- Code that needs a secret must check for `None` and fail with a clear message.
