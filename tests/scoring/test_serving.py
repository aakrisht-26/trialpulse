"""Why a score-history row is written (ADR 0034 decision 5). No database."""

import pandas as pd

from trialpulse.scoring import serving


def _frame(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["nct_id", "risk_12m", "risk_24m", "model_version", "true_landmark_index",
                 "content_hash"],
    )  # fmt: skip


def test_each_history_row_says_why_the_score_changed() -> None:
    previous = _frame(
        [
            ("A", 0.10, 0.20, "m/1", 2, "h"),
            ("B", 0.10, 0.20, "m/1", 2, "h"),
            ("C", 0.10, 0.20, "m/1", 2, "h"),
            ("D", 0.10, 0.20, "m/1", 2, "h"),
            ("E", 0.10, 0.20, "m/1", 2, "h"),
            ("F", 0.10, 0.20, "m/1", 2, "h"),
        ]
    )
    new = _frame(
        [
            ("A", 0.10, 0.20, "m/1", 2, "h"),  # nothing changed: no row
            ("B", 0.10, 0.20, "m/1", 2, "h2"),  # another version scored
            ("C", 0.13, 0.20, "m/1", 3, "h"),  # a landmark passed (and the score moved)
            ("D", 0.10, 0.20, "m/2", 2, "h"),  # a new model
            ("E", 0.111, 0.20, "m/1", 2, "h"),  # moved by 0.011 with nothing else: calendar
            ("F", 0.109, 0.209, "m/1", 2, "h"),  # moved by less than 0.01: no row
            ("G", 0.05, 0.08, "m/1", 0, "g"),  # first score
        ]
    )
    rows = serving.history_rows(new, previous).set_index("nct_id")["reasons"].to_dict()
    assert rows == {
        "B": ["record_change"],
        "C": ["landmark_passed"],
        "D": ["new_model"],
        "E": ["calendar"],
        "G": ["first_score"],
    }


def test_a_record_change_on_a_landmark_day_with_a_new_model_lists_every_reason() -> None:
    previous = _frame([("A", 0.10, 0.20, "m/1", 2, "h")])
    new = _frame([("A", 0.30, 0.40, "m/2", 3, "h2")])
    rows = serving.history_rows(new, previous)
    assert rows["reasons"].tolist() == [["record_change", "landmark_passed", "new_model"]]


def test_a_move_counts_at_either_horizon_from_exactly_one_hundredth() -> None:
    previous = _frame([("A", 0.10, 0.20, "m/1", 2, "h"), ("B", 0.01, 0.20, "m/1", 2, "h"),
                       ("C", 0.01, 0.20, "m/1", 2, "h")])  # fmt: skip
    new = _frame(
        [
            ("A", 0.10, 0.211, "m/1", 2, "h"),  # only the 24-month risk moved
            ("B", 0.02, 0.20, "m/1", 2, "h"),  # moved by exactly 0.01 (0.02 - 0.01 == 0.01)
            ("C", 0.0199, 0.20, "m/1", 2, "h"),  # just under 0.01
        ]
    )
    assert serving.MOVE == 0.02 - 0.01
    rows = serving.history_rows(new, previous).set_index("nct_id")["reasons"].to_dict()
    assert rows == {"A": ["calendar"], "B": ["calendar"]}
