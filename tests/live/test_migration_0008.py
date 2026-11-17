"""Migration 0008 (ADR 0084) on a scratch database: the memo table, the memo runs, and a
clean downgrade."""

import json

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from trialpulse.config import REPO_ROOT
from trialpulse.live import store


def _plain(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


INSERT = """INSERT INTO serving.trial_memos (nct_id, payload_hash, payload_version, payload,
    prompt_version, model, chosen_as, paragraphs, valid_as_of)
    VALUES (%s, %s, 1, %s, 'memo_v1', 'openai/gpt-oss-120b', %s, %s, '2026-11-16')"""


def test_a_memo_has_three_paragraphs_and_a_run_has_defaults(scratch: str) -> None:
    with psycopg.connect(_plain(scratch), autocommit=True) as con:
        con.execute(INSERT, ("NCT01234567", b"\x01" * 32, json.dumps({"nct_id": "NCT01234567"}),
                             ["top_100"], ["a", "b", "c"]))  # fmt: skip
        with pytest.raises(psycopg.errors.CheckViolation):
            con.execute(INSERT, ("NCT07654321", b"\x02" * 32, "{}", ["moved"], ["a", "b"]))
        run = con.execute(
            """INSERT INTO monitoring.memo_runs (status, model, prompt_version)
               VALUES ('running', 'm', 'memo_v1')
               RETURNING n_called, rejections, rejected_samples, total_tokens"""
        ).fetchone()
        assert run == (0, {}, [], 0)
        with pytest.raises(psycopg.errors.CheckViolation):
            con.execute("""INSERT INTO monitoring.memo_runs (status, model, prompt_version)
                           VALUES ('maybe', 'm', 'memo_v1')""")  # fmt: skip


def test_the_migration_goes_down_and_up_again(scratch: str) -> None:
    alembic = Config(str(REPO_ROOT / "alembic.ini"))
    command.downgrade(alembic, "0007")
    with psycopg.connect(_plain(scratch), autocommit=True) as con:
        assert con.execute("SELECT to_regclass('serving.trial_memos')").fetchone() == (None,)
        assert con.execute("SELECT to_regclass('monitoring.memo_runs')").fetchone() == (None,)
    command.upgrade(alembic, "head")
    with psycopg.connect(_plain(scratch), autocommit=True) as con:
        assert con.execute("SELECT version_num FROM alembic_version").fetchone() == (store.HEAD,)
        assert con.execute("SELECT to_regclass('serving.trial_memos')").fetchone() is not None
