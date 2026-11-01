"""Load the model statement of ADR 0048 into `serving.model_statement`.

    uv run python -m trialpulse.live.statement --target neon

The statement is `docs/model_statement.json`, held to docs/results.md and the model card by
tests/api/test_statement.py; this command loads it as it is, with its SHA-256.
"""

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from trialpulse.cli import run
from trialpulse.config import REPO_ROOT

STATEMENT_PATH = REPO_ROOT / "docs" / "model_statement.json"


def load(cur: Any, path: Path = STATEMENT_PATH) -> str:
    text = path.read_text(encoding="utf-8")
    json.loads(text)  # a statement that is not JSON is refused before anything is written
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    cur.execute(
        """INSERT INTO serving.model_statement (statement_id, statement, sha256)
        VALUES (1, %s::jsonb, %s)
        ON CONFLICT (statement_id) DO UPDATE SET statement = EXCLUDED.statement,
          sha256 = EXCLUDED.sha256, loaded_at = now()
        WHERE serving.model_statement.sha256 IS DISTINCT FROM EXCLUDED.sha256""",
        (text, digest),
    )
    return digest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the model statement (ADR 0048).")
    parser.add_argument("--target", choices=("local", "neon"), required=True)
    args = parser.parse_args(argv)
    import psycopg

    from trialpulse.live.daily import _dsn

    with psycopg.connect(_dsn(args.target), connect_timeout=10) as con, con.cursor() as cur:
        digest = load(cur)
        con.commit()
    print(f"model statement loaded, sha256:{digest[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
