"""Example risk timelines (Step 12, ADR 0033): the seeded draw and the registry updates, on
hand-built rows and versions. No real data."""

import datetime as dt
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from trialpulse.explain import timelines as tl

CUTOFF = dt.date(2026, 9, 25)
ORIGINS = ["2018-01-01", "2019-01-01"]


def _saved(rows: list[tuple[str, int, str, float, int]]) -> pd.DataFrame:
    """Saved scores: registry id, landmark index, landmark date, follow-up days, event."""
    return pd.DataFrame(
        {
            "trial_id": [r[0] for r in rows],
            "landmark_index": [r[1] for r in rows],
            "landmark_date": [dt.date.fromisoformat(r[2]) for r in rows],
            "time": [r[3] for r in rows],
            "event": [r[4] for r in rows],
            "censoring_group": ["OTHER"] * len(rows),
            "score_12": [0.01 * (i + 1) for i in range(len(rows))],
            "score_24": [0.02 * (i + 1) for i in range(len(rows))],
        }
    )


def _rows() -> pd.DataFrame:
    to_cutoff = (CUTOFF - dt.date(2018, 3, 1)).days
    first = _saved(
        [
            ("NCT00000001", 2, "2018-03-01", 400.0, 1),  # stops early
            ("NCT00000002", 2, "2018-03-01", 900.0, 2),  # completes
            ("NCT00000003", 2, "2018-03-01", float(to_cutoff), 0),  # open at the cutoff
            ("NCT00000004", 2, "2018-03-01", 300.0, 0),  # lapsed: censored before the cutoff
            ("NCT00000005", 2, "2018-03-01", 200.0, 1),  # at one origin only
            ("NCT00000006", 2, "2018-03-01", 500.0, 1),  # stops early
        ]
    )
    second = _saved(
        [
            ("NCT00000001", 4, "2019-03-01", 35.0, 1),
            ("NCT00000002", 4, "2019-03-01", 535.0, 2),
            ("NCT00000003", 4, "2019-03-01", float(to_cutoff - 365), 0),
            ("NCT00000004", 4, "2019-03-01", 10.0, 0),
            ("NCT00000006", 4, "2019-03-01", 135.0, 1),
        ]
    )
    return tl.scored_rows({ORIGINS[0]: first, ORIGINS[1]: second})


def test_the_outcome_of_a_trial_is_its_event_through_the_cutoff() -> None:
    assert tl.outcome_kind(1, dt.date(2019, 1, 1), CUTOFF) == tl.STOPPED
    assert tl.outcome_kind(2, dt.date(2019, 1, 1), CUTOFF) == tl.COMPLETED
    assert tl.outcome_kind(0, CUTOFF, CUTOFF) == tl.OPEN
    assert tl.outcome_kind(0, dt.date(2019, 1, 1), CUTOFF) is None  # the lapse rule


def test_eligible_trials_have_rows_at_both_origins_and_a_known_kind() -> None:
    rows = _rows()
    assert rows.loc[rows["trial_id"] == "NCT00000001", "event_date"].nunique() == 1
    pools = tl.eligible(rows, CUTOFF, ORIGINS)
    assert pools == {
        tl.STOPPED: ["NCT00000001", "NCT00000006"],
        tl.COMPLETED: ["NCT00000002"],
        tl.OPEN: ["NCT00000003"],
    }


def test_the_draw_follows_the_declared_rule_exactly() -> None:
    """One generator seeded once, used in the order stopped, completed, open, on sorted
    pools (ADR 0033). The fixed triple relies on numpy's PCG64 stream for
    Generator.integers; re-seeding per kind gives S0, C0, O0 and the reverse order S4, C3, O0."""
    pools = {
        tl.STOPPED: [f"S{i}" for i in (3, 0, 6, 1, 5, 2, 4)],
        tl.COMPLETED: [f"C{i}" for i in (4, 1, 0, 3, 2)],
        tl.OPEN: [f"O{i}" for i in (2, 0, 3, 1)],
    }
    assert tl.draw(pools, 42) == {tl.STOPPED: "S0", tl.COMPLETED: "C3", tl.OPEN: "O2"}
    generator = np.random.default_rng(42)
    expected = {}
    for kind in tl.KINDS:
        pool = sorted(pools[kind])
        expected[kind] = pool[int(generator.integers(len(pool)))]
    assert tl.draw(pools, 42) == expected


def test_the_draw_is_seeded_and_takes_one_of_each_kind() -> None:
    pools = {tl.STOPPED: [f"NCT{i:08d}" for i in range(50)], tl.COMPLETED: ["B"], tl.OPEN: ["C"]}
    first, again = tl.draw(pools, 42), tl.draw(pools, 42)
    assert first == again
    assert first[tl.STOPPED] in pools[tl.STOPPED]
    assert (first[tl.COMPLETED], first[tl.OPEN]) == ("B", "C")
    shuffled = {**pools, tl.STOPPED: list(reversed(pools[tl.STOPPED]))}
    assert tl.draw(shuffled, 42) == first  # sorted before the draw
    assert len({tl.draw(pools, seed)[tl.STOPPED] for seed in range(10)}) > 1
    with pytest.raises(ValueError, match="no eligible trial completed"):
        tl.draw({**pools, tl.COMPLETED: []}, 42)


def test_a_timeline_holds_the_saved_scores_in_time_order() -> None:
    line = tl.timeline(_rows(), "NCT00000001", tl.STOPPED)
    assert [p.landmark_index for p in line.points] == [2, 4]
    assert [p.origin for p in line.points] == ORIGINS
    assert line.outcome_date == dt.date(2019, 4, 5)
    assert line.points[0].score_12 == pytest.approx(0.01)
    with pytest.raises(ValueError, match="no saved score"):
        tl.timeline(_rows(), "NCT09999999", tl.STOPPED)


def _versions() -> pd.DataFrame:
    rows = [
        (1, "2016-01-10", "NOT_YET_RECRUITING", None, "2016-03-01", "2018-06-01", 120, "ESTIMATED"),
        (2, "2016-05-01", "RECRUITING", None, "2016-03-01", "2018-06-01", 120, "ESTIMATED"),
        (3, "2016-09-01", "RECRUITING", None, "2016-03-01", "2018-06-01", 120, "ESTIMATED"),
        (4, "2017-02-01", "RECRUITING", None, "2016-03-01", "2019-12-01", 80, "ESTIMATED"),
        (5, "2019-02-01", "UNKNOWN", "RECRUITING", "2016-03-01", "2019-12-01", 80, "ESTIMATED"),
        (6, "2019-04-05", "TERMINATED", None, "2016-03-01", "2019-03-01", 31, "ACTUAL"),
        (7, "2020-01-01", "TERMINATED", None, "2016-03-01", "2019-03-01", 31, "ACTUAL"),
    ]
    return pd.DataFrame(
        rows,
        columns=["nct_version", "effective_date", "overall_status", "last_known_status",
                 "start_date", "primary_completion_date", "enrollment_count",
                 "enrollment_type"],
    ).assign(
        effective_date=lambda f: pd.to_datetime(f["effective_date"]).dt.date,
        # Version 5 is the last on or before the last landmark (2019-03-01); 6 and 7 differ.
        lead_sponsor_class=["OTHER"] * 4 + ["INDUSTRY"] + ["NIH"] * 2,
        study_first_post_date=dt.date(2016, 1, 10),
    )  # fmt: skip


def test_the_updates_are_the_versions_that_changed_what_a_timeline_shows() -> None:
    updates = tl.updates_of(_versions(), dt.date(2019, 4, 5))
    assert [(u.date.isoformat(), u.changes) for u in updates] == [
        ("2016-01-10", ("registered as NOT_YET_RECRUITING",)),
        ("2016-05-01", ("status NOT_YET_RECRUITING to RECRUITING",)),
        ("2017-02-01", ("primary completion 2018-06 to 2019-12",
                        "enrollment 120 (estimated) to 80 (estimated)")),
        ("2019-04-05", ("status RECRUITING to TERMINATED",
                        "primary completion 2019-12 to 2019-03",
                        "enrollment 80 (estimated) to 31 (actual)")),
    ]  # fmt: skip
    # Version 3 changed nothing shown, version 5 shows the registry's UNKNOWN as the last
    # known status (ADR 0014), and version 7 is after the end.


def test_the_updates_listed_are_capped_and_the_rest_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tl, "MAX_UPDATES", 2)
    line = tl.timeline(_rows(), "NCT00000001", tl.STOPPED)
    line = tl.with_updates(line, _versions())
    assert len(line.updates) == 2
    assert line.updates_not_listed == 2
    assert line.sponsor_class == "INDUSTRY"  # the class in effect at the last landmark
    assert line.first_post == dt.date(2016, 1, 10)


def test_the_first_post_date_is_the_records_own() -> None:
    """Landmarks clamp the day to the month's length (2017-08-31 plus 6 months is
    2018-02-28), so t0 is read from the record, not worked back from a landmark."""
    point = tl.Point(ORIGINS[0], 1, dt.date(2018, 2, 28), 0.01, 0.02)
    line = tl.Timeline(tl.OPEN, "NCT00000009", [point], CUTOFF)
    versions = _versions().assign(study_first_post_date=dt.date(2017, 8, 31))
    assert tl.with_updates(line, versions).first_post == dt.date(2017, 8, 31)


def test_the_versions_are_read_without_names(tmp_path: Path) -> None:
    for column in tl.VERSION_COLUMNS:
        assert "name" not in column, column
        assert "contact" not in column, column
        assert "official" not in column, column
    warehouse = tmp_path / "w.duckdb"
    frame = _versions().assign(nct_id="NCT00000001", lead_sponsor_name="A Person")  # noqa: F841
    with duckdb.connect(str(warehouse)) as con:
        con.execute("CREATE TABLE versions AS SELECT * FROM frame")
    read = tl.read_versions(warehouse, "NCT00000001")
    assert list(read.columns) == list(tl.VERSION_COLUMNS)
    assert len(read) == 7
    assert tl.read_versions(warehouse, "NCT00000002").empty
