"""The served row on a date (ADR 0034, decision 1): the last landmark passed, the cap, the
time features and the recent-versions window. No real data."""

import duckdb
import numpy as np
import pytest

from trialpulse.dates import add_months
from trialpulse.scoring import rows

D = np.datetime64


def _dates(*values: str) -> np.ndarray:
    return np.array(values, dtype="datetime64[D]")


@pytest.mark.parametrize(
    ("t0", "day", "expected"),
    [
        ("2017-08-31", "2018-02-28", 1),  # landmark 1 is clamped to 28 February
        ("2017-08-31", "2018-02-27", 0),  # the day before it
        ("2017-08-29", "2018-02-28", 1),  # 29, 30 and 31 August all reach 28 February
        ("2017-01-20", "2017-07-05", 0),  # the months differ by 6, landmark 1 is 20 July
        ("2017-01-20", "2017-07-20", 1),
        ("2016-02-29", "2018-02-28", 4),
        ("2017-01-31", "2017-01-31", 0),  # registration day
        ("2017-01-31", "2017-01-30", -1),  # before registration
        ("2010-03-10", "2026-09-25", 33),  # beyond the last landmark: the true index
    ],
)
def test_the_last_landmark_passed_is_computed_from_t0(t0: str, day: str, expected: int) -> None:
    k = rows.true_index(_dates(t0), _dates(day), 6)
    assert k.tolist() == [expected]
    if expected >= 0:
        assert add_months(_dates(t0), 6 * expected)[0] <= D(day)
        assert add_months(_dates(t0), 6 * (expected + 1))[0] > D(day)


def test_the_index_is_capped_and_the_time_features_follow_it() -> None:
    k = rows.capped(np.array([0, 3, 6, 7, 23]), 6)
    assert k.tolist() == [0, 3, 6, 6, 6]
    features = rows.time_features(k, 6)
    assert features["landmark_index"].tolist() == [0, 3, 6, 6, 6]
    assert features["months_since_t0"].tolist() == [0, 18, 36, 36, 36]


def test_on_a_landmark_day_the_window_is_the_offline_rule() -> None:
    """The offline rule of ADR 0021 item 8, in DuckDB as the feature code writes it: the
    first post plus 6k - 6 months, or 6 months before the landmark at k = 0."""
    t0 = np.repeat(_dates("2016-08-31", "2016-08-29", "2017-01-31", "2016-02-29", "2015-05-15"), 7)
    k = np.tile(np.arange(7), 5)
    landmark = add_months(t0, 6 * k)
    ours = rows.window_start(t0, k, landmark, 6, 6)
    with duckdb.connect() as con:
        offline = con.execute(
            """SELECT CAST(CASE WHEN k * 6 - 6 >= 0
                  THEN t0 + to_months(CAST(k * 6 - 6 AS INTEGER))
                  ELSE l - INTERVAL 6 MONTH END AS DATE)
            FROM (SELECT unnest(?) AS t0, unnest(?) AS k, unnest(?) AS l)""",
            [t0.astype(object).tolist(), k.tolist(), landmark.astype(object).tolist()],
        ).fetchall()
    assert ours.astype(object).tolist() == [row[0] for row in offline]


def test_between_landmarks_the_window_slides_the_last_gap() -> None:
    t0 = _dates("2016-08-31", "2015-03-10")
    k = rows.capped(rows.true_index(t0, _dates("2017-05-30", "2026-09-25"), 6), 6)
    assert k.tolist() == [1, 6]
    day = _dates("2017-05-30", "2026-09-25")
    start = rows.window_start(t0, k, day, 6, 6)
    landmark = rows.landmark_date(t0, k, 6)
    previous = add_months(t0, 6 * k - 6)
    # Same length as the gap from the previous landmark to the last one, ending on the day.
    assert ((day - start) == (landmark - previous)).all()
    assert start.tolist() == [D("2016-11-30"), D("2026-03-28")]
