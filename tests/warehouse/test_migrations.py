"""The Alembic migrations, rendered offline (CI also applies them to a real Postgres)."""

import importlib.util
import re
from typing import Any

import pytest
from alembic import command
from alembic.config import Config

from trialpulse.config import REPO_ROOT
from trialpulse.contracts.versions import COLUMN_TYPES
from trialpulse.features.registry import model_columns
from trialpulse.live.contract import LIVE_FIELDS


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
        "serving.trials_not_scored",
        "serving.amendment_markers",
        "monitoring.pipeline_runs",
        "monitoring.graded_predictions",
        "monitoring.promotions",
        "monitoring.score_files",
        "live.sponsor_file",
        "serving.data_status",
        "serving.trial_display",
        "serving.sponsors",
        "serving.reason_counts",
        "serving.model_statement",
    }
    for word in ("investigator", "email", "contact", "phone", "affiliation"):
        assert word not in sql.lower()
    down = _render(capsys, "head", "base")
    for table in tables:
        assert f"DROP TABLE {table};" in down
    assert "ALTER TABLE live.study_versions DROP COLUMN last_known_status;" in down
    assert "ALTER TABLE serving.trial_score_history DROP COLUMN reasons;" in down


def _after(sql: str, revision: str) -> str:
    """The rendered SQL of one revision onward."""
    marker = f"-- Running upgrade {revision}"
    assert marker in sql, revision
    return sql[sql.index(marker) :]


POSTGRES: dict[str, Any] = {
    "VARCHAR": "TEXT",
    "DATE": "DATE",
    "BIGINT": "BIGINT",
    "DOUBLE": "DOUBLE PRECISION",
    "BOOLEAN": "BOOLEAN",
}


def test_the_live_versions_of_revision_0001_matched_the_canonical_schema(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The columns of revisions 0001 and 0002 (created, then added) equal the canonical
    contract; revision 0004 replaced them with compact rows."""
    sql = _render(capsys, "head")
    sql = sql[: sql.index("-- Running upgrade 0003 -> 0004")]
    columns = _create_table(sql, "live.study_versions")
    for name, kind in re.findall(
        r"ALTER TABLE live\.study_versions ADD COLUMN ([a-z_0-9]+) ([A-Z][A-Z ]*?);", sql
    ):
        columns[name] = kind
    expected = {c: POSTGRES[kind] for c, kind in COLUMN_TYPES.items()}
    expected.update({"ingested_at": "TIMESTAMP WITH TIME ZONE", "run_id": "BIGINT"})
    assert columns == expected


def test_the_compact_live_tables_match_the_live_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ADR 0037: `live.study_versions` and the latest-record part of `live.trial_state` hold
    exactly the compact fields of `trialpulse.live.contract`."""
    sql = _after(_render(capsys, "head"), "0003 -> 0004")
    kinds = {**POSTGRES, "VARCHAR[]": "TEXT[]"}
    expected = {c: kinds[kind] for c, kind in LIVE_FIELDS.items()}
    versions = _create_table(sql.replace("TEXT[]", "TEXTARRAY"), "live.study_versions")
    versions = {c: k.replace("TEXTARRAY", "TEXT[]") for c, k in versions.items()}
    compact = {c: k for c, k in versions.items() if c in LIVE_FIELDS}
    assert compact == expected
    assert set(versions) - set(LIVE_FIELDS) == {"nct_id", "feature_hash", "ingested_at", "run_id"}
    state = _create_table(sql.replace("TEXT[]", "TEXTARRAY"), "live.trial_state")
    assert set(LIVE_FIELDS) <= set(state)


def test_the_feature_numbers_of_the_compact_drivers_are_the_registry_order() -> None:
    spec = importlib.util.spec_from_file_location(
        "revision_0004", REPO_ROOT / "alembic" / "versions" / "0004_compact_live_state.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert model_columns() == module.FEATURE_NUMBERS
