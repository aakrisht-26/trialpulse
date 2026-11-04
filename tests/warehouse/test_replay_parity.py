"""The pieces of the parity layer 2 replay (`live.replay_parity`) on the synthetic warehouse:
the run days, the feed a delta pull would give, the version-limited offline view, the
counts of differences, and the refusals. The replay itself runs on the laptop."""

import datetime as dt
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from trialpulse.cli import RefusedError
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import ProjectConfig
from trialpulse.features.build import connect, prepare_sources
from trialpulse.ingest.ctgov_api import STUDIES_URL, VERSION_URL
from trialpulse.live import replay_parity

SECRET = b"a throwaway key for the replay tests"


def test_runs_are_the_weekdays_after_the_start_but_the_missed_ones() -> None:
    friday = dt.date(2026, 8, 28)
    days = replay_parity.run_days(friday, dt.date(2026, 9, 4))
    assert days == [dt.date(2026, 8, 31), *(dt.date(2026, 9, n) for n in range(1, 5))]
    missed = replay_parity.run_days(friday, dt.date(2026, 9, 4), [dt.date(2026, 9, 1)])
    assert dt.date(2026, 9, 1) not in missed
    assert len(missed) == 4


def test_the_feed_gives_each_trials_latest_version_posted_by_the_day(
    warehouse: Path, cfg: ProjectConfig
) -> None:
    rules = CohortRules.from_config(cfg)
    with duckdb.connect(str(warehouse), read_only=True) as con:
        feed = replay_parity.Feed(con, rules, SECRET)
        january = feed.fetch(dt.date(2016, 1, 1), dt.date(2016, 1, 31))
        assert [r["nct_id"] for r in january.converted.rows] == ["NCT00000001"]
        assert feed.fed == {("NCT00000001", dt.date(2016, 1, 10)): 1}
        # Two versions posted on one day: the delta pull shows the last.
        same_day = feed.fetch(dt.date(2017, 1, 30), dt.date(2017, 2, 5))
        assert [r["nct_id"] for r in same_day.converted.rows] == ["NCT00000002"]
        assert feed.fed[("NCT00000002", dt.date(2017, 2, 1))] == 2
        # A trial whose window version was later replaced gives the later one.
        wide = feed.fetch(dt.date(2015, 3, 1), dt.date(2017, 12, 31))
        dates = {r["nct_id"]: r["effective_date"] for r in wide.converted.rows}
        assert dates["NCT00000001"] == dt.date(2016, 1, 10)
        assert dates["NCT00000002"] == dt.date(2017, 2, 1)
        assert all(r["effective_date"] <= dt.date(2017, 12, 31) for r in wide.converted.rows)
    assert feed.sent == 1 + 1 + len(wide.converted.rows)
    assert wide.converted.quarantined == []
    assert wide.studies == []  # the display rows read raw records, which the feed has none of


def test_the_offline_view_can_be_held_to_the_feeds_versions(
    warehouse: Path, interventions: str, cfg: ProjectConfig
) -> None:
    rules = CohortRules.from_config(cfg)
    con = connect()
    try:
        con.execute(
            "CREATE TEMP TABLE keep AS SELECT * FROM (VALUES ('NCT00000002', 0), "
            "('NCT00000002', 2), ('NCT00000005', 0)) t(nct_id, nct_version)"
        )
        prepare_sources(con, warehouse, interventions, rules, only_versions="keep")
        shown = con.execute("SELECT nct_id, nct_version FROM versions ORDER BY 1, 2").fetchall()
    finally:
        con.close()
    assert shown == [("NCT00000002", 0), ("NCT00000002", 2), ("NCT00000005", 0)]


def test_differences_are_counted_exactly_with_missing_equal_to_missing() -> None:
    live = pd.DataFrame({"a": [1.0, np.nan, 3.0], "b": ["x", None, "z"], "c": [1, 2, 3]})
    offline = pd.DataFrame({"a": [1.0, np.nan, 3.0000001], "b": ["x", None, "y"], "c": [1, 2, 3]})
    assert replay_parity.frame_differences(live, offline) == {"a": 1, "b": 1}
    people = pd.DataFrame({
        "trial_id": ["NCT1", "NCT2", "NCT3"], "reversal": [False, False, True],
        "true_index": [1, 2, 3], "k_star": [1, 2, 3], "flagged": [False] * 3,
        "reason": [None, None, "REVERSAL"], "sponsor_class": ["OTHER"] * 3,
    })  # fmt: skip
    other = people.iloc[1:].copy()
    other.loc[2, "true_index"] = 4
    other = pd.concat([other, people.iloc[[0]].assign(trial_id="NCT9")])
    assert replay_parity.people_differences(people, other) == {
        "only_live": 1, "only_offline": 1, "true_index": 1
    }  # fmt: skip
    assert replay_parity.people_differences(people, people) == {}


def test_the_replay_never_calls_the_api_and_runs_on_local_postgres_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = replay_parity.DayApi(dt.date(2026, 9, 1))
    assert api.get_json(VERSION_URL)["dataTimestamp"].startswith("2026-09-01")
    with pytest.raises(RefusedError, match="never calls API v2"):
        api.get_json(STUDIES_URL, {})
    monkeypatch.setattr(replay_parity.daily, "_dsn",
                        lambda target: "postgresql://u:p@ep-x.neon.tech/db")  # fmt: skip
    with pytest.raises(RefusedError, match="local Postgres only"):
        replay_parity.local_dsn()
    monkeypatch.setattr(replay_parity.daily, "_dsn",
                        lambda target: "postgresql+psycopg://u:p@localhost:15432/db")  # fmt: skip
    assert replay_parity.local_dsn().endswith("localhost:15432/db")


def test_the_command_refuses_on_a_github_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(replay_parity, "run_replay", lambda *a, **k: pytest.fail("it ran"))
    with pytest.raises(RefusedError, match="ADR 0040"):
        replay_parity.main(["--start", "2026-08-28", "--end", "2026-09-04"])
