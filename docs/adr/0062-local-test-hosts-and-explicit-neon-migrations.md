# 0062. Tests touch local databases only; Neon is migrated through an explicit URL

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (review of Steps 14 to 16, finding 10).
- Refines CLAUDE.md Section 17 (DATABASE_URL and NEON_DATABASE_URL) and ADR 0045 (migrations run by hand).

## Context

Alembic read only DATABASE_URL, and no document said how to migrate Neon. The natural way, setting DATABASE_URL to Neon's URL in a shell, has a trap: the test fixtures create and drop scratch databases on the server of DATABASE_URL, so a later `uv run pytest` in the same shell would create and drop databases on Neon. `.env.example` also did not say that NEON_DATABASE_URL must be the direct URL, while Neon's console may offer the pooled one first.

## Decision

1. **The test fixtures refuse any host but `localhost` and `127.0.0.1`** (the laptop's Docker Compose Postgres, and CI's service, which `ci.yml` publishes at 127.0.0.1): a test fails, never skips, and touches nothing. A test holds `ci.yml`'s DATABASE_URL to those hosts, so moving CI's service forces a decision here.
2. **`alembic/env.py` refuses a DATABASE_URL that names any other host**, and says which command to use instead.
3. **Neon is migrated by `uv run python -m trialpulse.live.migrate`**, which asks for the direct URL at a prompt that does not echo it (so it stays out of shell history), refuses a pooled host (`-pooler`), passes the URL to Alembic explicitly, never reads DATABASE_URL, and prints the revision before and after.
4. **`.env.example` says** that NEON_DATABASE_URL is Neon's direct URL, how to copy it, and how Neon is migrated.

## Alternatives

- **`alembic -x url=...`:** the URL, with its password, lands in the shell's history, and a Neon URL's `&` needs quoting in PowerShell.
- **Read NEON_DATABASE_URL in the migration command:** one fewer paste, but the command would then run against whatever the environment holds; asking makes the target explicit every time.

## Consequences

- Migrating Neon is one documented command. The local and CI flows are unchanged.
- A DATABASE_URL left pointing at a remote host fails loudly in tests and in Alembic instead of acting on it.
