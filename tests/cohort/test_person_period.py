"""Person-period expansion and administrative censoring at an origin."""

import numpy as np

from trialpulse.cohort.person_period import PERSON_PERIOD_COLUMNS, expand, training_rows
from trialpulse.cohort.rules import CohortRules


def _landmarks(rows: list[tuple[str, str, int, str]]) -> dict[str, np.ndarray]:
    return {
        "trial_id": np.array([r[0] for r in rows]),
        "landmark_index": np.zeros(len(rows), dtype=np.int64),
        "landmark_date": np.array([r[1] for r in rows], dtype="datetime64[D]"),
        "event": np.array([r[2] for r in rows], dtype=np.int64),
        "event_date": np.array([r[3] for r in rows], dtype="datetime64[D]"),
        "stratum": np.array(["INDUSTRY"] * len(rows)),
    }


def _pairs(out: dict[str, np.ndarray], trial: str) -> list[tuple[int, int]]:
    mask = out["trial_id"] == trial
    return list(zip(out["interval"][mask].tolist(), out["outcome"][mask].tolist(), strict=True))


def test_censoring_on_an_interval_end_keeps_that_interval(rules: CohortRules) -> None:
    out = expand(
        _landmarks(
            [
                ("A", "2019-01-01", 0, "2020-01-01"),  # censored exactly at the end of j2
                ("B", "2019-01-01", 0, "2019-12-31"),  # one day earlier: j2 is dropped
                ("C", "2019-01-01", 0, "2030-01-01"),  # followed through all four
                ("D", "2019-01-01", 2, "2019-07-01"),  # completion on the end of j1
                ("E", "2019-01-01", 1, "2019-07-02"),  # early stop the day after
            ]
        ),
        rules,
    )
    assert set(out) == set(PERSON_PERIOD_COLUMNS)
    assert _pairs(out, "A") == [(1, 0), (2, 0)]
    assert _pairs(out, "B") == [(1, 0)]
    assert _pairs(out, "C") == [(1, 0), (2, 0), (3, 0), (4, 0)]
    assert _pairs(out, "D") == [(1, 2)]
    assert _pairs(out, "E") == [(1, 0), (2, 1)]
    ends = out["interval_end"][out["trial_id"] == "C"]
    assert ends.astype(str).tolist() == ["2019-07-01", "2020-01-01", "2020-07-01", "2021-01-01"]


def test_intervals_use_calendar_months_with_clamping(rules: CohortRules) -> None:
    out = expand(_landmarks([("A", "2019-08-31", 0, "2030-01-01")]), rules)
    assert out["interval_end"].astype(str).tolist() == [
        "2020-02-29",
        "2020-08-31",
        "2021-02-28",
        "2021-08-31",
    ]


def test_training_rows_censor_administratively_at_the_origin(rules: CohortRules) -> None:
    out = expand(
        _landmarks(
            [
                ("A", "2015-01-01", 1, "2016-03-01"),  # stop in j3, after the origin
                ("B", "2015-01-01", 1, "2015-10-01"),  # stop in j2, before the origin
                ("C", "2015-01-01", 2, "2016-01-01"),  # completion on the origin, end of j2
                ("D", "2016-02-01", 0, "2030-01-01"),  # landmark after the origin
            ]
        ),
        rules,
    )
    train = training_rows(out, np.datetime64("2016-01-01"))
    assert _pairs(train, "A") == [(1, 0), (2, 0)]  # j3 ends after T with an unknown event
    assert _pairs(train, "B") == [(1, 0), (2, 1)]
    assert _pairs(train, "C") == [(1, 0), (2, 0)]  # not known at T, but j2 fully observed
    assert _pairs(train, "D") == []
    assert set(train) == set(PERSON_PERIOD_COLUMNS)
