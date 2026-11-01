"""The API's read-only role (ADR 0049, decision 13 of Step 14).

    uv run python -m trialpulse.live.api_role --target neon

Creates (or updates the password of) a role that may connect and read the `serving` schema,
and nothing else: no other schema, no write, and every transaction read-only. Roles made in
Neon's console can read and write all data, which is why this one is made by SQL. The
password is asked for at the prompt and never printed; `API_DATABASE_URL` is then Neon's
pooled URL for this role.
"""

import argparse
import getpass
from collections.abc import Sequence
from typing import Any

from psycopg import sql

from trialpulse.cli import RefusedError, run

ROLE = "trialpulse_api"


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the API's read-only role.")
    parser.add_argument("--target", choices=("local", "neon"), required=True)
    args = parser.parse_args(argv)
    import psycopg

    from trialpulse.live.daily import _dsn

    password = getpass.getpass(f"password for {ROLE}: ")
    if len(password) < 16:
        raise RefusedError("choose a password of at least 16 characters")
    with psycopg.connect(_dsn(args.target), connect_timeout=10) as con, con.cursor() as cur:
        grant_read_only(cur, password)
        con.commit()
    print(f"{ROLE} may read the serving schema only; set API_DATABASE_URL to its pooled URL")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
