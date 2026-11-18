"""Migration 0009 (ADR 0043, amended 2026-11-18) on a scratch database: the view of each live
trial's first-post date, and a clean downgrade."""

import datetime as dt

import psycopg
from alembic import command
from alembic.config import Config

from trialpulse.config import REPO_ROOT
from trialpulse.live import store


def _plain(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


STATE = """INSERT INTO live.trial_state (nct_id, effective_date, status, study_first_post_date,
    title_phase, intervention_types, feature_hash, n_versions, ever_suspended,
    recent_post_dates, reversal)
    VALUES (%s, '2026-11-12', 'RECRUITING', %s, 'NONE', '{}', '\\x00', 3, false, '{}', false)"""


def test_the_view_holds_each_live_trials_first_post_date(scratch: str) -> None:
    with psycopg.connect(_plain(scratch), autocommit=True) as con:
        con.execute(STATE, ("NCT01234567", dt.date(2024, 10, 28)))
        con.execute(STATE, ("NCT07654321", None))
        rows = con.execute(
            "SELECT nct_id, first_post_date FROM serving.trial_first_posts ORDER BY nct_id"
        ).fetchall()
    assert rows == [("NCT01234567", dt.date(2024, 10, 28)), ("NCT07654321", None)]


def test_the_migration_goes_down_and_up_again(scratch: str) -> None:
    alembic = Config(str(REPO_ROOT / "alembic.ini"))
    command.downgrade(alembic, "0008")
    with psycopg.connect(_plain(scratch), autocommit=True) as con:
        assert con.execute("SELECT to_regclass('serving.trial_first_posts')").fetchone() == (None,)
        assert con.execute("SELECT to_regclass('live.trial_state')").fetchone() != (None,)
    command.upgrade(alembic, "head")
    with psycopg.connect(_plain(scratch), autocommit=True) as con:
        assert con.execute("SELECT version_num FROM alembic_version").fetchone() == (store.HEAD,)
        assert con.execute("SELECT to_regclass('serving.trial_first_posts')").fetchone() != (None,)
