"""Migration 0007 (ADRs 0076, 0077 and 0079) on a scratch database: grades keyed by source,
the drift table, the serving views, and a clean downgrade."""

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from trialpulse.config import REPO_ROOT

ROW = ("2023-01-06", 12, -1, "all", "m/1")


def _plain(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def test_live_and_replay_grades_of_one_key_sit_side_by_side(scratch: str) -> None:
    with psycopg.connect(_plain(scratch), autocommit=True) as con:
        for source in ("live", "replay"):
            con.execute(
                """INSERT INTO monitoring.graded_predictions (prediction_date, horizon_months,
                   landmark_index, row_group, model_version, source, n_rows, n_early_stop,
                   n_completed, n_censored, n_open, matured)
                   VALUES (%s, %s, %s, %s, %s, %s, 10, 1, 2, 3, 4, true)""",
                (*ROW, source),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            con.execute(
                """INSERT INTO monitoring.graded_predictions (prediction_date, horizon_months,
                   landmark_index, row_group, model_version, source, n_rows, n_early_stop,
                   n_completed, n_censored, n_open, matured)
                   VALUES (%s, %s, %s, %s, %s, 'test', 1, 0, 0, 0, 1, false)""",
                ROW,
            )
        seen = con.execute(
            "SELECT source FROM serving.monitoring_grades ORDER BY source"
        ).fetchall()
        assert seen == [("live",), ("replay",)]
        con.execute(
            """INSERT INTO monitoring.drift (week_date, column_name, kind, method, statistic,
               threshold, drifted, n_current) VALUES ('2026-11-13', 'enrollment_count',
               'feature', 'psi', 0.02, 0.1, false, 90000)"""
        )
        assert con.execute("SELECT count(*) FROM serving.monitoring_drift").fetchone() == (1,)
        assert con.execute(
            "SELECT count(*) FROM serving.monitoring_promotions").fetchone() == (0,)  # fmt: skip


def test_the_migration_goes_down_and_up_again(scratch: str) -> None:
    alembic = Config(str(REPO_ROOT / "alembic.ini"))
    command.downgrade(alembic, "0006")
    with psycopg.connect(_plain(scratch), autocommit=True) as con:
        assert con.execute("SELECT to_regclass('serving.monitoring_grades')").fetchone() == (None,)
        assert con.execute("SELECT to_regclass('monitoring.drift')").fetchone() == (None,)
    command.upgrade(alembic, "head")
    with psycopg.connect(_plain(scratch), autocommit=True) as con:
        assert con.execute("SELECT version_num FROM alembic_version").fetchone() == ("0007",)
