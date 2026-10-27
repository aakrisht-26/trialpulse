"""`serving.publish` against a real Postgres (CLAUDE.md Step 13, ADR 0034 decision 5): a
second run on the same day changes nothing, and a later run writes history rows that say why
each score changed. The test makes a scratch database on the server of DATABASE_URL, applies
the migrations to it and drops it afterwards; it is skipped when no server answers (CI sets
DATABASE_URL to its Postgres service)."""

import datetime as dt
import json
import uuid
from collections.abc import Iterator
from urllib.parse import quote

import numpy as np
import pandas as pd
import pytest
from alembic import command
from alembic.config import Config

from trialpulse.config import REPO_ROOT, Secrets, load_project_config
from trialpulse.scoring import serving
from trialpulse.scoring.score import Scored

psycopg = pytest.importorskip("psycopg")
from psycopg import conninfo  # noqa: E402 (after the import check)

DAY1, DAY2 = dt.date(2026, 9, 25), dt.date(2026, 9, 26)


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture
def scratch(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A migrated scratch database; its SQLAlchemy URL, also set as DATABASE_URL."""
    secret = Secrets().database_url
    if secret is None:
        pytest.skip("DATABASE_URL is not set")
    base = _libpq(secret.get_secret_value())
    admin = conninfo.make_conninfo(base, dbname="postgres", connect_timeout=3)
    name = f"trialpulse_test_{uuid.uuid4().hex[:12]}"
    try:
        with psycopg.connect(admin, autocommit=True) as con:
            con.execute(f"CREATE DATABASE {name}")
    except psycopg.OperationalError as exc:  # the address is not printed: it holds a password
        pytest.skip(f"no Postgres answers at DATABASE_URL ({type(exc).__name__})")
    parts = conninfo.conninfo_to_dict(base)
    url = (
        f"postgresql+psycopg://{quote(str(parts.get('user', '')), safe='')}:"
        f"{quote(str(parts.get('password', '')), safe='')}@{parts.get('host', 'localhost')}:"
        f"{parts.get('port', 5432)}/{name}"
    )
    monkeypatch.setenv("DATABASE_URL", url)
    try:
        command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
        yield url
    finally:
        with psycopg.connect(admin, autocommit=True) as con:
            con.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def _scored(day: dt.date, rows: list[tuple], unscored: list[tuple]) -> Scored:
    scores = pd.DataFrame(
        rows,
        columns=["nct_id", "true_index", "landmark_index", "flagged", "risk_12m", "risk_24m",
                 "content_hash"],
    )  # fmt: skip
    scores.insert(1, "as_of_date", np.datetime64(day, "D"))
    scores["model_name"] = "trialpulse-m4"
    scores["model_version"] = "trialpulse-m4/1"
    scores["drivers"] = json.dumps({"12": [], "24": []})
    missing = pd.DataFrame(unscored, columns=["nct_id", "true_index", "reason", "content_hash"])
    missing.insert(1, "as_of_date", np.datetime64(day, "D"))
    return Scored(scores, missing, {})


def test_a_second_run_changes_nothing_and_a_later_run_says_why(scratch: str) -> None:
    cfg = load_project_config()
    first = _scored(
        DAY1,
        [
            ("NCT01", 2, 2, False, 0.10, 0.20, "a"),
            ("NCT02", 8, 6, True, 0.30, 0.40, "b"),
            ("NCT03", 1, 1, False, 0.05, 0.09, "c"),
        ],
        [("NCT09", 21, "beyond_120_months", "z")],
    )
    counts = serving.publish(first, cfg, scratch)
    assert (counts["latest_written"], counts["history_written"]) == (3, 3)
    assert counts["not_scored_written"] == 1
    digest = serving.table_digest(scratch)
    again = serving.publish(first, cfg, scratch)
    assert set(again.values()) == {0}, again
    assert serving.table_digest(scratch) == digest

    later = _scored(
        DAY2,
        [
            ("NCT01", 2, 2, False, 0.125, 0.20, "a"),  # moved by 0.025 alone: calendar
            ("NCT02", 9, 6, True, 0.30, 0.40, "b2"),  # another version, and landmark 9 passed
            ("NCT04", 0, 0, False, 0.02, 0.04, "d"),  # first score; NCT03 closed
        ],
        [],
    )
    counts = serving.publish(later, cfg, scratch)
    assert (counts["latest_removed"], counts["not_scored_removed"]) == (1, 1)
    with psycopg.connect(_libpq(scratch)) as con:
        history = con.execute(
            "SELECT nct_id, reasons, flagged FROM serving.trial_score_history "
            "WHERE as_of_date = %s ORDER BY nct_id",
            (DAY2,),
        ).fetchall()
        latest = con.execute(
            "SELECT nct_id, as_of_date, true_landmark_index FROM serving.trial_scores_latest "
            "ORDER BY nct_id"
        ).fetchall()
        not_scored = con.execute("SELECT count(*) FROM serving.trials_not_scored").fetchone()
    assert history == [
        ("NCT01", ["calendar"], False),
        ("NCT02", ["record_change", "landmark_passed"], True),
        ("NCT04", ["first_score"], False),
    ]
    assert latest == [("NCT01", DAY2, 2), ("NCT02", DAY2, 9), ("NCT04", DAY2, 0)]
    assert not_scored == (0,)
    # The tables move forward in time: the first day cannot be published again.
    before = serving.table_digest(scratch)
    with pytest.raises(serving.EarlierDateError):
        serving.publish(first, cfg, scratch)
    assert serving.table_digest(scratch) == before
