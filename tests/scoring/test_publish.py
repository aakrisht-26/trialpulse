"""`serving.publish` against a real Postgres (CLAUDE.md Step 13, ADR 0034 decision 5): a
second run on the same day changes nothing, and a later run writes history rows that say why
each score changed. The test makes a scratch database on the server of DATABASE_URL, applies
the migrations to it and drops it afterwards; it is skipped when no server answers (CI sets
DATABASE_URL to its Postgres service)."""

import datetime as dt
import hashlib
import json

import numpy as np
import pandas as pd
import pytest
from alembic import command
from alembic.config import Config

from trialpulse.config import REPO_ROOT, load_project_config
from trialpulse.scoring import serving
from trialpulse.scoring.score import Scored

psycopg = pytest.importorskip("psycopg")

DAY1, DAY2 = dt.date(2026, 9, 25), dt.date(2026, 9, 26)
DRIVERS = {
    "24": [{"feature": "status", "value": "RECRUITING", "contribution": -0.12345,
            "direction": "lowers"}],
    "12": [
        {"feature": "n_versions", "value": "3", "contribution": 0.5, "direction": "raises"},
        {"feature": "sponsor_class", "value": "OTHER", "contribution": -0.0204,
         "direction": "lowers"},
    ],
}  # fmt: skip


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _hex(content: str) -> str:
    """A stand-in content hash: 64 hex digits named by a short label."""
    return hashlib.sha256(content.encode()).hexdigest()


def _scored(day: dt.date, rows: list[tuple], unscored: list[tuple]) -> Scored:
    rows = [(*r[:-1], _hex(r[-1])) for r in rows]
    unscored = [(*u[:-1], _hex(u[-1])) for u in unscored]
    scores = pd.DataFrame(
        rows,
        columns=["nct_id", "true_index", "landmark_index", "flagged", "risk_12m", "risk_24m",
                 "content_hash"],
    )  # fmt: skip
    scores.insert(1, "as_of_date", np.datetime64(day, "D"))
    scores["model_name"] = "trialpulse-m4"
    scores["model_version"] = "trialpulse-m4/1"
    scores["drivers"] = json.dumps(DRIVERS)
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


def test_drivers_are_stored_compact_and_expand_back(scratch: str) -> None:
    """ADR 0037 decision 6: four arrays, horizons in increasing months, contributions to 3
    decimals whose sign is the direction; the API's expansion gives the drivers back."""
    serving.publish(_one(DAY1, 0.10), load_project_config(), scratch)
    with psycopg.connect(_libpq(scratch)) as con:
        row = con.execute(
            "SELECT driver_months, driver_features, driver_values, driver_contributions, "
            "content_hash FROM serving.trial_scores_latest"
        ).fetchone()
    assert row is not None
    months, features, values, contributions, content = row
    assert months == [12, 12, 24]
    assert values == ["3", "OTHER", "RECRUITING"]
    assert contributions == pytest.approx([0.5, -0.020, -0.123], abs=1e-6)
    assert bytes(content).hex() == _hex("a")
    back = serving.expanded_drivers(months, features, values, contributions)
    assert [d["feature"] for d in back["12"]] == ["n_versions", "sponsor_class"]
    assert [d["direction"] for d in back["12"] + back["24"]] == ["raises", "lowers", "lowers"]


def test_a_scoped_run_removes_nothing_outside_its_scope(scratch: str) -> None:
    """A live run rescores some trials (ADR 0045): trials outside its scope keep their rows,
    a trial in the scope without a new row loses it, and an empty scope writes nothing."""
    cfg = load_project_config()
    first = _scored(DAY1, [("NCT01", 2, 2, False, 0.10, 0.20, "a"),
                           ("NCT02", 2, 2, False, 0.30, 0.40, "b")], [])  # fmt: skip
    serving.publish(first, cfg, scratch, run_id=None)
    later = _scored(DAY2, [("NCT01", 2, 2, False, 0.20, 0.20, "a")], [])
    counts = serving.publish(later, cfg, scratch, scope={"NCT01"})
    assert counts["latest_removed"] == 0
    with psycopg.connect(_libpq(scratch)) as con:
        kept = con.execute(
            "SELECT nct_id, as_of_date FROM serving.trial_scores_latest ORDER BY nct_id"
        ).fetchall()
    assert kept == [("NCT01", DAY2), ("NCT02", DAY1)]
    gone = _scored(DAY2, [], [])
    assert serving.publish(gone, cfg, scratch, scope=set()) == {}
    counts = serving.publish(gone, cfg, scratch, scope={"NCT02"}, day=DAY2)
    assert counts["latest_removed"] == 1
    with pytest.raises(ValueError, match="outside its scope"):
        serving.publish(later, cfg, scratch, scope={"NCT02"})


def test_the_migration_converts_served_drivers_down_and_up(scratch: str) -> None:
    serving.publish(_one(DAY1, 0.10), load_project_config(), scratch)
    config = Config(str(REPO_ROOT / "alembic.ini"))
    command.downgrade(config, "0003")
    with psycopg.connect(_libpq(scratch)) as con:
        row = con.execute(
            "SELECT drivers, content_hash, model_name FROM serving.trial_scores_latest"
        ).fetchone()
    assert row is not None
    drivers, content, name = row
    assert content == _hex("a")
    assert name == "trialpulse-m4"
    assert [d["feature"] for d in drivers["12"]] == ["n_versions", "sponsor_class"]
    assert drivers["24"][0]["direction"] == "lowers"
    command.upgrade(config, "head")
    with psycopg.connect(_libpq(scratch)) as con:
        row = con.execute(
            "SELECT driver_months, driver_features, driver_values FROM serving.trial_scores_latest"
        ).fetchone()
    assert row is not None
    assert row[0] == [12, 12, 24]
    assert row[2] == ["3", "OTHER", "RECRUITING"]


def test_a_rerun_that_changes_nothing_keeps_the_run_that_wrote_the_history(scratch: str) -> None:
    """Review finding 9 (ADR 0045, decision 11): history rows carry the run that wrote them,
    and a same-day rerun with the same scores neither rewrites them nor takes them over."""
    with psycopg.connect(_libpq(scratch)) as con:
        first, second = (
            con.execute(
                "INSERT INTO monitoring.pipeline_runs (job, status) VALUES ('daily', 'succeeded') "
                "RETURNING run_id"
            ).fetchone()[0]
            for _ in range(2)
        )
        con.commit()
    cfg = load_project_config()
    serving.publish(_one(DAY1, 0.10), cfg, scratch, run_id=first)
    again = serving.publish(_one(DAY1, 0.10), cfg, scratch, run_id=second)
    assert again["history_written"] == 0
    with psycopg.connect(_libpq(scratch)) as con:
        runs = con.execute("SELECT run_id FROM serving.trial_score_history").fetchall()
    assert runs == [(first,)]


TRIALS = ("NCT01", "NCT02", "NCT03")


def _step13_cutoff(url: str) -> None:
    """The local serving tables as Step 13 left them: the cutoff published at revision 0003,
    every row a first score, content hashes of the canonical contract and JSON drivers; then
    migration 0004 converts them."""
    config = Config(str(REPO_ROOT / "alembic.ini"))
    command.downgrade(config, "0003")
    drivers = json.dumps({"12": [{"feature": "n_versions", "value": "3", "contribution": 0.5,
                                  "direction": "raises"}], "24": []})  # fmt: skip
    with psycopg.connect(_libpq(url)) as con:
        for i, trial in enumerate(TRIALS):
            canonical = _hex(f"canonical-{trial}")
            con.execute(
                """INSERT INTO serving.trial_scores_latest (nct_id, as_of_date, landmark_index,
                  true_landmark_index, flagged, model_name, model_version, risk_12m, risk_24m,
                  drivers, content_hash) VALUES (%s, %s, 2, 2, false, 'trialpulse-m4',
                  'trialpulse-m4/1', %s, %s, %s, %s)""",
                (trial, DAY1, 0.1 * (i + 1), 0.2 * (i + 1), drivers, canonical),
            )
            con.execute(
                """INSERT INTO serving.trial_score_history (nct_id, as_of_date, model_name,
                  model_version, risk_12m, risk_24m, reasons, landmark_index,
                  true_landmark_index, content_hash, flagged) VALUES (%s, %s, 'trialpulse-m4',
                  'trialpulse-m4/1', %s, %s, ARRAY['first_score'], 2, 2, %s, false)""",
                (trial, DAY1, 0.1 * (i + 1), 0.2 * (i + 1), canonical),
            )
        con.commit()
    command.upgrade(config, "head")


def _regenerated(day: dt.date, changed: str | None = None) -> Scored:
    """The cutoff's scores regenerated by the bootstrap: the same risks, with the feature
    hash of ADR 0037 as the content hash (one trial's record changed, if asked)."""
    rows = [
        (trial, 2, 2, False, 0.1 * (i + 1), 0.2 * (i + 1),
         f"feature-{trial}" + ("-new" if trial == changed else ""))
        for i, trial in enumerate(TRIALS)
    ]  # fmt: skip
    run = _scored(day, rows, [])
    run.scores["model_version"] = "trialpulse-m4/1"
    return run


def _reasons(url: str) -> list[tuple]:
    with psycopg.connect(_libpq(url)) as con:
        return con.execute(
            "SELECT nct_id, as_of_date, reasons FROM serving.trial_score_history "
            "ORDER BY nct_id, as_of_date"
        ).fetchall()


def test_regenerating_the_cutoff_over_converted_step13_rows_writes_no_record_change(
    scratch: str,
) -> None:
    """Item 3 of the last cloud task: the bootstrap regenerates the cutoff's scores with the
    feature hash (ADR 0037) over the Step 13 rows that migration 0004 converted, whose hashes
    are of the canonical contract. No history row may say record_change: the record did not
    change, only the hash's contract. A later run with the same records writes no history
    row, and a record that does change still shows as one."""
    cfg = load_project_config()
    _step13_cutoff(scratch)
    assert _reasons(scratch) == [(t, DAY1, ["first_score"]) for t in TRIALS]
    serving.publish(_regenerated(DAY1), cfg, scratch)
    history = _reasons(scratch)
    assert all("record_change" not in reasons for _, _, reasons in history)
    assert history == [(t, DAY1, ["first_score"]) for t in TRIALS]
    with psycopg.connect(_libpq(scratch)) as con:
        hashes = dict(con.execute(
            "SELECT nct_id, content_hash FROM serving.trial_scores_latest").fetchall())  # fmt: skip
    assert {t: bytes(h).hex() for t, h in hashes.items()} == {t: _hex(f"feature-{t}")
                                                               for t in TRIALS}  # fmt: skip
    later = DAY1 + dt.timedelta(days=67)  # go-live
    counts = serving.publish(_regenerated(later), cfg, scratch)
    assert counts["history_written"] == 0
    serving.publish(_regenerated(later + dt.timedelta(days=1), changed="NCT02"), cfg, scratch)
    changed = [r for r in _reasons(scratch) if r[1] > later]
    assert changed == [("NCT02", later + dt.timedelta(days=1), ["record_change"])]
