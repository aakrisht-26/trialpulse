"""The replay's dates and its model's identity (`monitor.replay`, ADR 0078). The fit and the
scores run on the laptop's data and are not part of the suite."""

import datetime as dt

import pytest

from trialpulse.cli import RefusedError
from trialpulse.monitor import replay


def test_the_replay_holds_78_fridays_in_six_quarters() -> None:
    every = replay.dates()
    assert len(every) == 78
    quarters = [replay.dates(f"{y}Q{q}") for y in (2023, 2024) for q in (1, 2, 3, 4)]
    assert sum(len(q) for q in quarters) == 78
    assert replay.dates("2024Q3") == []  # the window ends 2024-06-30
    assert replay.dates("2023Q1")[0] == dt.date(2023, 1, 6)
    assert all(len(q) <= 14 for q in quarters)


def test_a_quarter_is_written_like_2023q1() -> None:
    with pytest.raises(RefusedError, match="2023Q1"):
        replay.dates("2023Q5")


def test_the_replay_model_is_its_own_and_its_origin_is_2023() -> None:
    assert dt.date(2023, 1, 1) == replay.ORIGIN
    assert "replay" in replay.VERSION
    assert replay.MODEL_DIR != replay.REPO_ROOT / "data" / "scoring" / "champion"
