"""The bootstrap command (ADRs 0037, 0040, 0045): the streamed seed equals `seed` on the
synthetic registry, the command refuses on a GitHub runner and without SPONSOR_ID_KEY, and
the comparison of scores counts what differs."""

import datetime as dt
from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest

from trialpulse.cli import RefusedError
from trialpulse.live import bootstrap
from trialpulse.live.seed import seed
from trialpulse.scoring.predict import Scored

from .conftest import _FEATURES, CFG, KEEP, RULES, SECRET, compact_history_of, registry

ALIASES = (CFG.features.sponsor_alias_min_trials, CFG.features.sponsor_alias_wait_days)
DATA = registry(300, 7)


def _warehouse() -> duckdb.DuckDBPyConnection:
    """The synthetic registry in the warehouse's layout: `versions` and `texts`."""
    con = duckdb.connect()
    _FEATURES.load_versions(con, DATA.versions, "versions")
    _FEATURES.load_texts(con, DATA.texts)
    return con


@pytest.mark.parametrize(
    ("day", "batch_rows"),
    [
        (dt.date(2019, 6, 15), 37),  # batches end inside trials
        (dt.date(2019, 8, 31), 1),  # a posting day: its states are queued
        (dt.date(2026, 9, 25), 100_000),  # one batch
    ],
)
def test_the_streamed_seed_equals_seed(day: dt.date, batch_rows: int) -> None:
    expected = seed(compact_history_of(DATA.versions, DATA.texts, DATA.interventions), day,
                    RULES, KEEP, *ALIASES)  # fmt: skip
    with _warehouse() as con:
        streamed = bootstrap.stream_seed(con, day, RULES, SECRET, KEEP, *ALIASES,
                                         batch_rows=batch_rows)  # fmt: skip
    assert streamed.wides == expected.wides
    assert streamed.markers == expected.markers
    assert streamed.sponsors.to_bytes() == expected.sponsors.to_bytes()
    posted = [v for v in DATA.versions if v["effective_date"] <= day]
    assert streamed.counts["versions"] == len(posted)
    assert streamed.counts["trials"] == len({v["nct_id"] for v in posted})
    assert streamed.counts["wides"] == len(expected.wides) > 0


def test_the_stream_reads_only_the_trials_it_is_given() -> None:
    day = dt.date(2019, 6, 15)
    chosen = sorted({v["nct_id"] for v in DATA.versions})[::7]
    subset = [v for v in DATA.versions if v["nct_id"] in set(chosen)]
    expected = seed(compact_history_of(subset, DATA.texts, DATA.interventions), day, RULES,
                    KEEP, *ALIASES)  # fmt: skip
    with _warehouse() as con:
        con.execute("CREATE TEMP TABLE chosen (nct_id VARCHAR)")
        con.executemany("INSERT INTO chosen VALUES (?)", [(n,) for n in chosen])
        streamed = bootstrap.stream_seed(con, day, RULES, SECRET, KEEP, *ALIASES, batch_rows=50,
                                         trials="chosen")  # fmt: skip
    assert streamed.wides == expected.wides
    assert streamed.sponsors.to_bytes() == expected.sponsors.to_bytes()


def test_the_command_refuses_on_a_github_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(bootstrap, "run_bootstrap", lambda *a, **k: pytest.fail("it ran"))
    with pytest.raises(RefusedError, match="ADR 0040"):
        bootstrap.main(["--target", "local"])


def test_the_command_refuses_without_the_sponsor_id_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bootstrap, "Secrets", lambda: SimpleNamespace(sponsor_id_key=None))
    monkeypatch.setattr(bootstrap, "run_bootstrap", lambda *a, **k: pytest.fail("it ran"))
    with pytest.raises(RefusedError, match="SPONSOR_ID_KEY"):
        bootstrap.main(["--target", "local"])


def _scores(rows: list[tuple[str, float, str]]) -> pd.DataFrame:
    return pd.DataFrame({
        "nct_id": [r[0] for r in rows], "as_of_date": pd.Timestamp("2026-09-25"),
        "true_index": 2, "landmark_index": 2, "flagged": False,
        "risk_12m": [r[1] for r in rows], "risk_24m": [2 * r[1] for r in rows],
        "content_hash": [r[2] for r in rows], "model_name": "trialpulse-m4",
        "model_version": "trialpulse-m4/1", "drivers": '{"12":[],"24":[]}',
    })  # fmt: skip


def _unscored(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame({"nct_id": [r[0] for r in rows], "as_of_date": pd.Timestamp("2026-09-25"),
                         "true_index": 21, "reason": [r[1] for r in rows],
                         "content_hash": "h"})  # fmt: skip


def test_the_comparison_counts_what_differs_and_leaves_content_hashes_apart() -> None:
    reference = (_scores([("NCT1", 0.1, "a"), ("NCT2", 0.2, "b"), ("NCT3", 0.3, "c")]),
                 _unscored([("NCT9", "reversal")]))  # fmt: skip
    same = Scored(_scores([("NCT1", 0.1, "x"), ("NCT2", 0.2, "y"), ("NCT3", 0.3, "z")]),
                  _unscored([("NCT9", "reversal")]), {})  # fmt: skip
    compared = bootstrap.compare_scores(same, *reference)
    assert bootstrap.differences(compared) == []
    assert compared["scores"]["columns"]["content_hash"] == {"equal": 0, "different": 3}
    assert compared["scores"]["columns"]["risk_12m"]["max_abs_difference"] == 0.0
    other = Scored(_scores([("NCT1", 0.1, "a"), ("NCT2", 0.2000001, "b"), ("NCT4", 0.4, "d")]),
                   _unscored([("NCT9", "beyond_120_months")]), {})  # fmt: skip
    found = bootstrap.differences(bootstrap.compare_scores(other, *reference))
    assert found == [
        "scores: 1 rows only new, 1 only in the reference",
        "scores.risk_12m: 1 differ",
        "scores.risk_24m: 1 differ",
        "unscored.reason: 1 differ",
    ]


def test_the_neon_estimate_adds_the_transfer_to_the_local_write() -> None:
    estimate = bootstrap.neon_estimate(10_000_000, 4.0)
    assert estimate["10_mbps"] == {"transfer_seconds": 8.0, "with_local_write_seconds": 12.0}
    assert estimate["2_mbps"] == {"transfer_seconds": 40.0, "with_local_write_seconds": 44.0}
