"""The pieces of the parity layer 2 replay (`live.replay_parity`) on the synthetic warehouse:
the run days, the feed a delta pull would give, the version-limited offline view, the
counts of differences, and the refusals. The replay itself runs on the laptop."""

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pytest

from trialpulse.cli import RefusedError
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import ProjectConfig
from trialpulse.features.build import connect, prepare_sources
from trialpulse.features.registry import model_columns
from trialpulse.ingest.ctgov_api import STUDIES_URL, VERSION_URL
from trialpulse.live import bootstrap, daily, replay_parity

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
        # A trial touched in the window gives its version of that day, not a later one.
        early = feed.fetch(dt.date(2015, 3, 1), dt.date(2015, 3, 5))
        first = {r["nct_id"]: r["effective_date"] for r in early.converted.rows}
        assert first["NCT00000001"] == dt.date(2015, 3, 2)
        assert feed.fed[("NCT00000001", dt.date(2015, 3, 2))] == 0
    assert feed.sent == 1 + 1 + len(wide.converted.rows) + len(early.converted.rows)
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
    missing_one_side = pd.DataFrame({"a": [np.nan, 2.0], "b": [None, "y"]})
    against = pd.DataFrame({"a": [1.0, np.nan], "b": ["x", None]})
    assert replay_parity.frame_differences(missing_one_side, against) == {"a": 2, "b": 2}
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


# The replay's own logic, with the job, the live read and the offline path stood in for: the
# real ones run in the replay on the laptop and in their own tests.

START, END = dt.date(2016, 1, 8), dt.date(2016, 1, 12)  # a Friday; runs on Jan 11 and 12
TOUCHED = ("NCT00000001", dt.date(2016, 1, 10))  # the one version the feed sends


def _people(ids: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"trial_id": ids, "reversal": False, "true_index": 1, "k_star": 1,
                         "flagged": False, "reason": None, "sponsor_class": "OTHER"})  # fmt: skip


class Stubs:
    """Stand-ins for what the replay calls, recording what it was given."""

    def __init__(self, keep_all: bool = True, offline_apart: bool = False,
                 scored: int = 2) -> None:  # fmt: skip
        self.keep_all, self.offline_apart, self.scored = keep_all, offline_apart, scored
        self.fetched: dict[int, list[tuple[str, dt.date]]] = {}
        self.versions: list[pd.DataFrame] = []
        ids = [f"NCT0000000{n}" for n in range(1, scored + 1)]
        columns = list(model_columns()[:3])
        self.features = pd.DataFrame({c: [0.5] * scored for c in columns})
        self.live = SimpleNamespace(people=_people(ids), scored=pd.DataFrame({"trial_id": ids}),
                                    features=self.features.copy())  # fmt: skip

    def run_daily(self, cfg: Any, dsn: str, secret: bytes, api: Any, model: Any,
                  fetch: Any) -> daily.Report:  # fmt: skip
        run_id = len(self.fetched) + 1
        fetched = fetch(api.day - dt.timedelta(days=3), api.day)
        self.fetched[run_id] = [(r["nct_id"], r["effective_date"]) for r in fetched.converted.rows]
        return daily.Report("succeeded", api.day, run_id, {"details": {"full": True}})

    def kept_by_run(self, dsn: str, run_id: int) -> list[tuple[str, dt.date]]:
        sent = [(n, d) for n, d in self.fetched[run_id] if d > START]
        return sent if self.keep_all else []

    def offline_on(self, cfg: Any, warehouse: Path, interventions: str, day: dt.date,
                   keys: Any, decisions: Any, versions: pd.DataFrame | None,
                   memory_limit: str) -> replay_parity.Offline:  # fmt: skip
        features = self.features.copy()
        if versions is not None:
            self.versions.append(versions)
            if self.offline_apart:
                features.iloc[0, 0] = 0.25
        return replay_parity.Offline(self.live.people.copy(), features, 0.0)


def _replay(stubs: Stubs, monkeypatch: pytest.MonkeyPatch, warehouse: Path, cfg: ProjectConfig,
            checkpoints: list[dt.date] | None = None) -> dict[str, Any]:  # fmt: skip
    monkeypatch.setattr(bootstrap, "run_bootstrap",
                        lambda *a, **k: {"seconds": {}, "counts": {}})  # fmt: skip
    monkeypatch.setattr(bootstrap, "open_warehouse",
                        lambda path: duckdb.connect(str(path), read_only=True))  # fmt: skip
    monkeypatch.setattr(daily, "champion_model", lambda *a: SimpleNamespace(decisions={}))
    monkeypatch.setattr(daily, "run_daily", stubs.run_daily)
    monkeypatch.setattr(replay_parity, "kept_by_run", stubs.kept_by_run)
    monkeypatch.setattr(replay_parity, "live_on", lambda *a: stubs.live)
    monkeypatch.setattr(replay_parity.live_features, "served_keys", lambda *a: pd.DataFrame())
    monkeypatch.setattr(replay_parity, "offline_on", stubs.offline_on)
    inputs = bootstrap.Inputs(warehouse, warehouse, warehouse, warehouse, warehouse)
    return replay_parity.run_replay(cfg, START, END, [], [END] if checkpoints is None
                                    else checkpoints, "postgresql://localhost/x", inputs, "",
                                    say=lambda _: None)  # fmt: skip


def test_the_replay_compares_on_every_version_before_the_start_and_every_record_sent(
    warehouse: Path, cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    stubs = Stubs()
    result = _replay(stubs, monkeypatch, warehouse, cfg)
    assert result["passes"]
    assert [r["day"] for r in result["runs"]] == ["2016-01-11", "2016-01-12"]
    assert result["feed"]["sent_after_the_start_not_kept"] == 0
    (versions,) = stubs.versions  # offline(F) of the one checkpoint
    with duckdb.connect(str(warehouse), read_only=True) as con:
        query = "SELECT nct_id, nct_version FROM versions WHERE effective_date <= DATE '2016-01-08'"
        before = set(con.execute(query).fetchall())
    shown = set(versions.itertuples(index=False, name=None))
    assert shown == before | {(TOUCHED[0], 1)}
    assert len(versions) == len(shown)
    entry = result["checkpoints"][0]
    assert (entry["scored"], entry["served_features"], entry["differences"]) == (2, 3, False)


def test_a_record_sent_but_not_kept_fails_and_stays_in_the_feeds_versions(
    warehouse: Path, cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    stubs = Stubs(keep_all=False)
    result = _replay(stubs, monkeypatch, warehouse, cfg)
    assert result["feed"]["sent_after_the_start_not_kept"] == 1
    assert (TOUCHED[0], 1) in set(stubs.versions[0].itertuples(index=False, name=None))
    assert not result["passes"]


def test_a_feature_apart_or_a_checkpoint_with_nothing_scored_fails(
    warehouse: Path, cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    apart = _replay(Stubs(offline_apart=True), monkeypatch, warehouse, cfg)
    assert apart["checkpoints"][0]["features"] == {model_columns()[0]: 1}
    assert apart["checkpoints"][0]["differences"]
    assert not apart["passes"]
    empty = _replay(Stubs(scored=0), monkeypatch, warehouse, cfg)
    assert empty["checkpoints"][0]["differences"]
    assert not empty["passes"]
    with pytest.raises(RefusedError, match="at least one --checkpoint"):
        _replay(Stubs(), monkeypatch, warehouse, cfg, checkpoints=[])


def test_the_live_side_is_read_as_a_full_run_reads_it(
    cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import psycopg

    seen: dict[str, Any] = {}

    @contextmanager
    def fake_connect(dsn: str, **kwargs: Any) -> Iterator[Any]:
        cursor = object()

        @contextmanager
        def cursor_cm() -> Iterator[Any]:
            yield cursor

        yield SimpleNamespace(cursor=cursor_cm)

    def fake_day_features(*args: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        return "the day's features"

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    monkeypatch.setattr(replay_parity.store, "load_file", lambda cur: "sponsors")
    monkeypatch.setattr(daily, "day_features", fake_day_features)
    rules = CohortRules.from_config(cfg)
    got = replay_parity.live_on("postgresql://localhost/x", cfg, rules, END, None)  # type: ignore[arg-type]
    assert got == "the day's features"
    assert seen == {"full": True}
