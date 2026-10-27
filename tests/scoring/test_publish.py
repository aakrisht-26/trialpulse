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


def _history(url: str) -> list[tuple]:
    with psycopg.connect(_libpq(url)) as con:
        return con.execute(
            "SELECT nct_id, as_of_date, risk_12m, reasons FROM serving.trial_score_history "
            "ORDER BY nct_id, as_of_date"
        ).fetchall()


def _one(day: dt.date, risk: float, content: str = "a", trial: str = "NCT01") -> Scored:
    return _scored(day, [(trial, 2, 2, False, risk, 0.20, content)], [])


def test_a_slow_drift_is_measured_against_the_last_history_row(scratch: str) -> None:
    cfg = load_project_config()
    for step in range(6):  # 0.100, 0.106, ..., 0.130, one day apart
        serving.publish(_one(DAY1 + dt.timedelta(days=step), 0.1 + 0.006 * step), cfg, scratch)
    days = [(day - DAY1).days for _, day, _, _ in _history(scratch)]
    # A row at the first score, then each time the score is 0.01 from the last row.
    assert days == [0, 2, 4]


def test_a_rerun_on_the_same_day_replaces_that_days_history(scratch: str) -> None:
    cfg = load_project_config()
    serving.publish(_one(DAY1, 0.10), cfg, scratch)
    counts = serving.publish(_one(DAY1, 0.30, "a2"), cfg, scratch)  # same day, changed
    assert counts["history_written"] == 1
    assert _history(scratch) == [("NCT01", DAY1, 0.30, ["first_score"])]
    serving.publish(_one(DAY2, 0.32), cfg, scratch)  # a record change against 0.30
    serving.publish(_one(DAY2, 0.30, "a2"), cfg, scratch)  # rerun: nothing to record now
    assert _history(scratch) == [("NCT01", DAY1, 0.30, ["first_score"])]


def test_a_trial_that_returns_is_not_a_first_score(scratch: str) -> None:
    cfg = load_project_config()
    serving.publish(_one(DAY1, 0.10), cfg, scratch)
    serving.publish(_one(DAY2, 0.05, trial="NCT02"), cfg, scratch)  # NCT01 left
    serving.publish(_one(DAY2 + dt.timedelta(days=1), 0.10, "a2"), cfg, scratch)  # it is back
    reasons = {(t, (d - DAY1).days): r for t, d, _, r in _history(scratch)}
    assert reasons[("NCT01", 2)] == ["record_change"]


def test_scores_without_drivers_are_refused(scratch: str) -> None:
    run = _one(DAY1, 0.10)
    run.scores = run.scores.drop(columns="drivers")
    with pytest.raises(ValueError, match="without drivers"):
        serving.publish(run, load_project_config(), scratch)


def test_the_migration_keeps_history_rows_down_and_up(scratch: str) -> None:
    cfg = load_project_config()
    serving.publish(_one(DAY1, 0.10), cfg, scratch)
    serving.publish(_one(DAY2, 0.13), cfg, scratch)  # calendar
    serving.publish(_one(DAY2 + dt.timedelta(days=1), 0.13, "a2"), cfg, scratch)  # record
    config = Config(str(REPO_ROOT / "alembic.ini"))
    command.downgrade(config, "0002")
    with psycopg.connect(_libpq(scratch)) as con:
        old = con.execute(
            "SELECT reason FROM serving.trial_score_history ORDER BY as_of_date"
        ).fetchall()
    assert old == [("first_score",), ("score_moved",), ("record_changed",)]
    command.upgrade(config, "head")
    assert [r for *_, r in _history(scratch)] == [["first_score"], ["calendar"],
                                                  ["record_change"]]  # fmt: skip
