"""The API's read-only role (ADR 0049, decision 13 of Step 14).

    uv run python -m trialpulse.live.api_role --target neon
    uv run python -m trialpulse.live.api_role --target neon --env-file .env

Creates (or updates the password of) a role that may connect and read the `serving` schema,
and nothing else: no other schema, no write, and every transaction read-only. Roles made in
Neon's console can read and write all data, which is why this one is made by SQL.

The command makes the password itself: 32 random bytes from `secrets` (256 bits, far above
Neon's minimum of 60 bits for a role made by SQL), never typed and never stored by the
command. It prints `API_DATABASE_URL` once, with Neon's pooled host for `--target neon`, to
paste into `.env` and the Hugging Face Space's secrets; every run makes a new password, so
running it again rotates the password and the old URL stops working. With `--env-file`, the URL
is written into that file's `API_DATABASE_URL` line (added if missing, every other line kept)
and never printed.
"""

import argparse
import secrets
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from psycopg import conninfo, sql

from trialpulse.cli import run

ROLE = "trialpulse_api"
ENV_NAME = "API_DATABASE_URL"
PASSWORD_BYTES = 32  # 256 bits of entropy (at least 128 asked, ADR 0064)


def new_password() -> str:
    """A password of PASSWORD_BYTES random bytes, URL-safe, so it needs no quoting."""
    return secrets.token_urlsafe(PASSWORD_BYTES)


def api_url(direct: str, password: str, role: str = ROLE) -> str:
    """The API's URL for `role`: the database of `direct`, through Neon's pooled host (the
    first label of a Neon host gains `-pooler`) and with TLS required; any other host is
    kept as it is (the local Postgres)."""
    parts = conninfo.conninfo_to_dict(direct)
    host = str(parts.get("host") or "localhost")
    if host.endswith(".neon.tech") and "-pooler" not in host:
        first, rest = host.split(".", 1)
        host = f"{first}-pooler.{rest}"
    port = f":{parts['port']}" if parts.get("port") else ""
    query = "?sslmode=require" if host.endswith(".neon.tech") else ""
    return f"postgresql://{role}:{password}@{host}{port}/{parts.get('dbname', '')}{query}"


def grant_read_only(cur: Any, password: str, role: str = ROLE) -> None:
    """Make `role` able to read `serving` only, in the caller's transaction."""
    name = sql.Identifier(role)
    exists = cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    verb = "ALTER" if exists else "CREATE"
    cur.execute(sql.SQL(verb + " ROLE {} LOGIN PASSWORD {}").format(name, sql.Literal(password)))
    database = cur.execute("SELECT current_database()").fetchone()[0]
    statements = [
        "REVOKE ALL ON DATABASE {db} FROM {r}",
        "GRANT CONNECT ON DATABASE {db} TO {r}",
        "REVOKE ALL ON SCHEMA public FROM {r}",
        "REVOKE ALL ON ALL TABLES IN SCHEMA live, monitoring FROM {r}",
        "REVOKE ALL ON SCHEMA live, monitoring FROM {r}",
        "GRANT USAGE ON SCHEMA serving TO {r}",
        "GRANT SELECT ON ALL TABLES IN SCHEMA serving TO {r}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA serving GRANT SELECT ON TABLES TO {r}",
        "ALTER ROLE {r} SET default_transaction_read_only = on",
    ]
    for text in statements:
        cur.execute(sql.SQL(text).format(db=sql.Identifier(database), r=name))


def write_env(path: Path, url: str) -> None:
    """Set `ENV_NAME` in the env file at `path` to `url`: its line replaced (or added), every
    other line kept as it was, nothing printed."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    entry = f"{ENV_NAME}={url}"
    kept = [entry if line.split("=", 1)[0].strip() == ENV_NAME else line for line in lines]
    if entry not in kept:
        kept.append(entry)
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the API's read-only role.")
    parser.add_argument("--target", choices=("local", "neon"), required=True)
    parser.add_argument("--env-file", type=Path,
                        help=f"write {ENV_NAME} into this file instead of printing it")  # fmt: skip
    args = parser.parse_args(argv)
    import psycopg

    from trialpulse.live.daily import _dsn

    direct = _dsn(args.target)
    password = new_password()
    with psycopg.connect(direct, connect_timeout=10) as con, con.cursor() as cur:
        grant_read_only(cur, password)
        con.commit()
    if args.env_file is not None:
        write_env(args.env_file, api_url(direct, password))
        print(f"{ROLE} may read the serving schema only; {ENV_NAME} with its new password "
              f"is written to {args.env_file} (not shown). Running this again makes a new "
              "one.")  # fmt: skip
        return 0
    print(f"{ROLE} may read the serving schema only. Its new password is in this URL, shown "
          "once: set it as API_DATABASE_URL in .env and in the Space's secrets, and never "
          "commit it. Running this again makes a new password.")  # fmt: skip
    print(api_url(direct, password))
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
