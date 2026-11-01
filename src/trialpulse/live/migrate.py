"""Migrate the live database on Neon (ADR 0062).

    uv run python -m trialpulse.live.migrate

Asks for the database's direct URL at a prompt that does not echo it, so it never lands in
the shell's history, and runs `alembic upgrade head` on it. DATABASE_URL is never read: it
names the local Postgres only, and `alembic/env.py` refuses it when it names any other host.
Neon's pooled URL (a host with `-pooler`) is refused: a migration needs the direct URL, as
the daily job does (`NEON_DATABASE_URL`, ADR 0045). The URL is never printed; the revision
before and after is.
"""

import argparse
import getpass
from collections.abc import Sequence

from trialpulse.cli import RefusedError, run
from trialpulse.config import REPO_ROOT


def _sqlalchemy(url: str) -> str:
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def revision(url: str) -> str | None:
    """The database's Alembic revision, or None before the first migration."""
    import psycopg

    dsn = _sqlalchemy(url).replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn, connect_timeout=15) as con:
        exists = con.execute("SELECT to_regclass('public.alembic_version')").fetchone()
        if exists is None or exists[0] is None:
            return None
        row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        return None if row is None else str(row[0])


def upgrade(url: str, target: str = "head") -> tuple[str | None, str | None]:
    """Upgrade the database at `url` (given explicitly, never DATABASE_URL) to `target`;
    returns its revision before and after."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import make_url

    url = url.strip()
    try:
        host = make_url(_sqlalchemy(url)).host or ""
    except Exception as exc:
        raise RefusedError("that is not a Postgres URL") from exc
    if not url.startswith(("postgresql://", "postgres://", "postgresql+psycopg://")):
        raise RefusedError("that is not a Postgres URL")
    if "-pooler" in host:
        raise RefusedError("that is Neon's pooled URL; migrate with the direct URL (no -pooler)")
    before = revision(url)
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.attributes["url"] = _sqlalchemy(url)
    command.upgrade(config, target)
    return before, revision(url)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate the live database on Neon.")
    parser.parse_args(argv)
    url = getpass.getpass("Neon's direct URL (not echoed, never stored): ")
    before, after = upgrade(url)
    print(f"migrated from revision {before} to {after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
