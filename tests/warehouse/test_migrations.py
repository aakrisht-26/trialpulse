"""The Alembic migrations, rendered offline (CI also applies them to a real Postgres)."""

import re
from typing import Any

import pytest
from alembic import command
from alembic.config import Config

from trialpulse.config import REPO_ROOT
from trialpulse.contracts.versions import COLUMN_TYPES


def _render(capsys: pytest.CaptureFixture[str], *revisions: str) -> str:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    if revisions == ("head",):
        command.upgrade(config, "head", sql=True)
    else:
        command.downgrade(config, ":".join(revisions), sql=True)
    return capsys.readouterr().out


def _create_table(sql: str, name: str) -> dict[str, str]:
    body = re.search(rf"CREATE TABLE {re.escape(name)} \((.*?)\n\);", sql, re.DOTALL)
    assert body, name
    columns: dict[str, str] = {}
    for line in body.group(1).splitlines():
        match = re.match(r"\s+([a-z_0-9]+) ([A-Z][A-Z ]*?)(?: NOT NULL| DEFAULT .*)?,?\s*$", line)
        if match:
            columns[match.group(1)] = match.group(2).strip()
    return columns


def test_migration_creates_every_schema_and_table(capsys: pytest.CaptureFixture[str]) -> None:
    sql = _render(capsys, "head")
    for schema in ("live", "serving", "monitoring"):
        assert f"CREATE SCHEMA IF NOT EXISTS {schema};" in sql
    tables = set(re.findall(r"CREATE TABLE ([a-z_]+\.[a-z_]+) \(", sql))
    assert tables == {
        "live.study_versions",
        "live.texts",
        "live.trial_state",
        "live.outcome_events",
        "live.quarantine",
        "serving.trial_scores_latest",
        "serving.trial_score_history",
        "monitoring.pipeline_runs",
        "monitoring.graded_predictions",
        "monitoring.promotions",
    }
    for word in ("investigator", "email", "contact", "phone", "affiliation"):
        assert word not in sql.lower()
    down = _render(capsys, "head", "base")
    for table in tables:
        assert f"DROP TABLE {table};" in down
    assert "ALTER TABLE live.study_versions DROP COLUMN last_known_status;" in down


def test_live_versions_match_the_canonical_schema(capsys: pytest.CaptureFixture[str]) -> None:
    """The columns after every migration (created, then added) equal the canonical contract."""
    sql = _render(capsys, "head")
    columns = _create_table(sql, "live.study_versions")
    for name, kind in re.findall(
        r"ALTER TABLE live\.study_versions ADD COLUMN ([a-z_0-9]+) ([A-Z][A-Z ]*?);", sql
    ):
        columns[name] = kind
    postgres: dict[str, Any] = {
        "VARCHAR": "TEXT",
        "DATE": "DATE",
        "BIGINT": "BIGINT",
        "DOUBLE": "DOUBLE PRECISION",
        "BOOLEAN": "BOOLEAN",
    }
    expected = {c: postgres[kind] for c, kind in COLUMN_TYPES.items()}
    expected.update({"ingested_at": "TIMESTAMP WITH TIME ZONE", "run_id": "BIGINT"})
    assert columns == expected
