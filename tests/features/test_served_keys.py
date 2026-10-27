"""Features of served keys (Step 13, ADR 0034) on the scripted registry: on a landmark day
they are the offline features, and between landmarks the recent-versions window slides the
trial's last landmark gap. No real data."""

from collections.abc import Iterator

import numpy as np
import pandas as pd
import pytest

from trialpulse.cohort.rules import CohortRules
from trialpulse.config import ProjectConfig
from trialpulse.features.build import FEATURE_TABLE, compute_features
from trialpulse.features.states import KEYS_TABLE
from trialpulse.scoring import features as served_features
from trialpulse.scoring import rows as served

from .conftest import World

ELIGIBILITY = (
    "eligibility_n_inclusion",
    "eligibility_n_exclusion",
    "eligibility_length",
    "eligibility_n_thresholds",
)


@pytest.fixture
def saved_keys(world: World) -> Iterator[pd.DataFrame]:
    """The world's landmark keys with each trial's first post; restored afterwards."""
    keys = world.frame(
        f"""SELECT k.trial_id, k.landmark_index, k.landmark_date, t.t0
        FROM {KEYS_TABLE} k JOIN (
          SELECT nct_id, arg_min(study_first_post_date, nct_version) AS t0
          FROM all_versions GROUP BY nct_id) t ON t.nct_id = k.trial_id
        ORDER BY k.trial_id, k.landmark_index"""
    )
    yield keys
    frame = keys[["trial_id", "landmark_index", "landmark_date"]]  # noqa: F841
    world.con.execute(f"CREATE OR REPLACE TABLE {KEYS_TABLE} AS SELECT * FROM frame")


def _register(world: World, keys: pd.DataFrame) -> None:
    frame = keys[["trial_id", "landmark_index", "landmark_date", "window_start"]]  # noqa: F841
    world.con.execute(
        f"""CREATE OR REPLACE TABLE {KEYS_TABLE} AS SELECT trial_id,
          CAST(landmark_index AS BIGINT) AS landmark_index,
          CAST(landmark_date AS DATE) AS landmark_date,
          CAST(window_start AS DATE) AS window_start FROM frame"""
    )


def _with_window(keys: pd.DataFrame, day: np.ndarray, rules: CohortRules) -> pd.DataFrame:
    k_star = served.capped(keys["landmark_index"].to_numpy(dtype=np.int64), rules.max_index)
    out = keys.copy()
    out["landmark_date"] = day
    out["window_start"] = served.window_start(
        keys["t0"].to_numpy(dtype="datetime64[D]"), k_star, day, rules.spacing_months, 6
    )
    return out


@pytest.mark.slow  # recomputes every feature of the scripted registry; CI runs every test
def test_on_a_landmark_day_a_served_key_gets_the_offline_features(
    world: World, cfg: ProjectConfig, rules: CohortRules, saved_keys: pd.DataFrame
) -> None:
    day = saved_keys["landmark_date"].to_numpy(dtype="datetime64[D]")
    _register(world, _with_window(saved_keys, day, rules))
    compute_features(world.con, rules, cfg, text=False, explicit_window=True)
    columns = [
        row[0]
        for row in world.con.execute("DESCRIBE baseline_features").fetchall()
        if row[0] not in ELIGIBILITY
    ]
    listed = ", ".join(columns)
    differ = world.con.execute(
        f"""SELECT count(*) FROM (
          (SELECT {listed} FROM {FEATURE_TABLE} EXCEPT SELECT {listed} FROM baseline_features)
          UNION ALL
          (SELECT {listed} FROM baseline_features EXCEPT SELECT {listed} FROM {FEATURE_TABLE}))"""
    ).fetchone()
    assert differ == (0,)
    # Clamped first-post days are among the keys (31 August is a first-post day here).
    assert (pd.to_datetime(saved_keys["t0"]).dt.day == 31).any()
    # Without text, the eligibility statistics are left empty (ADR 0026).
    empty = world.con.execute(
        f"SELECT count(*) FROM {FEATURE_TABLE} WHERE eligibility_length IS NOT NULL"
    ).fetchone()
    assert empty == (0,)


@pytest.mark.slow
def test_between_landmarks_the_recent_window_slides_the_last_landmark_gap(
    world: World, cfg: ProjectConfig, rules: CohortRules, saved_keys: pd.DataFrame
) -> None:
    later = saved_keys[saved_keys["landmark_index"] >= 1].reset_index(drop=True)
    landmark = later["landmark_date"].to_numpy(dtype="datetime64[D]")
    day = landmark + np.timedelta64(91, "D")
    keys = _with_window(later, day, rules)
    _register(world, keys)
    compute_features(world.con, rules, cfg, text=False, explicit_window=True)
    got = world.frame(
        f"SELECT trial_id, landmark_index, n_versions_recent FROM {FEATURE_TABLE}"
    ).merge(keys, on=["trial_id", "landmark_index"])
    versions = world.frame("SELECT nct_id AS trial_id, effective_date FROM all_versions")
    joined = got.merge(versions, on="trial_id")
    posted = joined["effective_date"].to_numpy(dtype="datetime64[D]")
    joined["recent"] = (posted > joined["window_start"].to_numpy(dtype="datetime64[D]")) & (
        posted <= joined["landmark_date"].to_numpy(dtype="datetime64[D]")
    )
    expected = joined.groupby(["trial_id", "landmark_index"])["recent"].sum()
    observed = got.set_index(["trial_id", "landmark_index"])["n_versions_recent"]
    assert (observed.loc[expected.index].to_numpy() == expected.to_numpy()).all()
    # The window is the trial's last landmark gap, slid to end on the day.
    gap = (
        landmark
        - served.window_start(
            later["t0"].to_numpy(dtype="datetime64[D]"),
            served.capped(later["landmark_index"].to_numpy(dtype=np.int64), rules.max_index),
            landmark,
            rules.spacing_months,
            6,
        )
    ).astype(np.int64)
    slid = (day - keys["window_start"].to_numpy(dtype="datetime64[D]")).astype(np.int64)
    assert (gap == slid).all()
    assert set(np.unique(slid)) <= set(range(181, 185))


@pytest.mark.slow
def test_rows_beyond_the_last_landmark_keep_their_own_index(
    world: World, cfg: ProjectConfig, rules: CohortRules, saved_keys: pd.DataFrame
) -> None:
    first = saved_keys[saved_keys["landmark_index"] == 0].reset_index(drop=True)
    t0 = first["t0"].to_numpy(dtype="datetime64[D]")
    parts = []
    for index in (rules.max_index, rules.max_index + 1, rules.max_index + 4):
        k = np.full(len(first), index, dtype=np.int64)
        day = served.landmark_date(t0, k, rules.spacing_months) + np.timedelta64(30, "D")
        parts.append(first.assign(landmark_index=k, landmark_date=day))
    rows = pd.concat(parts, ignore_index=True)
    keys = _with_window(rows, rows["landmark_date"].to_numpy(dtype="datetime64[D]"), rules)
    _register(world, keys)
    frame = served_features.compute_served(world.con, rules, cfg)
    # One row per key, told apart by the row's own index ...
    assert len(frame) == len(keys)
    assert not frame.duplicated(["trial_id", "true_index"]).any()
    got = set(zip(frame["trial_id"], frame["true_index"], strict=True))
    assert got == set(zip(keys["trial_id"], keys["landmark_index"], strict=True))
    # ... while all three share the time features of the last landmark.
    assert (frame["landmark_index"] == rules.max_index).all()
    assert (frame["months_since_t0"] == rules.spacing_months * rules.max_index).all()
