"""Features of served rows (ADR 0034): the offline feature code, run on keys of our own.

The keys are (trial_id, landmark_index, landmark_date, window_start): a row's own index
(the true landmark index, which keeps two rows of one trial apart), the date it is scored
on, and the start of its recent-versions window (`trialpulse.scoring.rows`). The feature
code computes every feature as of that date, exactly as the offline build computes a
landmark row; then the two time features are set to the last landmark passed, capped at the
model's last landmark, and the sponsor rate is smoothed with the given priors. No text
feature is computed (ADR 0026).
"""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from trialpulse.cohort.rules import CohortRules
from trialpulse.config import ProjectConfig
from trialpulse.features.build import FEATURE_TABLE, compute_features, connect, prepare_sources
from trialpulse.features.states import KEYS_TABLE
from trialpulse.features.transforms import SponsorPriors
from trialpulse.scoring import rows as served

KEY_COLUMNS: tuple[str, ...] = ("trial_id", "landmark_index", "landmark_date", "window_start")


def open_session(
    rules: CohortRules, warehouse: Path, interventions: str
) -> duckdb.DuckDBPyConnection:
    """A database with what the feature queries read (versions, texts, interventions)."""
    con = connect()
    prepare_sources(con, warehouse, interventions, rules)
    return con


def register_keys(con: duckdb.DuckDBPyConnection, keys: pd.DataFrame) -> None:
    """`feature_keys` from a frame of served keys; one row per (trial, index)."""
    missing = [c for c in KEY_COLUMNS if c not in keys.columns]
    if missing:
        raise ValueError(f"the served keys lack {missing}")
    frame = keys[list(KEY_COLUMNS)].copy()  # noqa: F841 (read by DuckDB below)
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE {KEYS_TABLE} AS
        SELECT CAST(trial_id AS VARCHAR) AS trial_id,
          CAST(landmark_index AS BIGINT) AS landmark_index,
          CAST(landmark_date AS DATE) AS landmark_date,
          CAST(window_start AS DATE) AS window_start
        FROM frame"""
    )
    clash = con.execute(
        f"""SELECT count(*) FROM (SELECT trial_id, landmark_index FROM {KEYS_TABLE}
        GROUP BY 1, 2 HAVING count(*) > 1)"""
    ).fetchone()
    if clash and clash[0]:
        raise ValueError(f"{clash[0]} served keys repeat a (trial, index) pair")


def compute_served(
    con: duckdb.DuckDBPyConnection, rules: CohortRules, cfg: ProjectConfig
) -> pd.DataFrame:
    """The features of the registered keys as of their dates, sorted by trial and index.
    The row's own index moves to `true_index`, the key of the frame: the time features,
    `landmark_index` among them, are held at k* (that index capped at the last landmark), so
    two rows of one trial beyond the last landmark share them."""
    compute_features(con, rules, cfg, text=False, explicit_window=True)
    frame = con.execute(f"SELECT * FROM {FEATURE_TABLE} ORDER BY trial_id, landmark_index").df()
    frame = frame.rename(columns={"landmark_index": "true_index"})
    k_star = served.capped(frame["true_index"].to_numpy(dtype=np.int64), rules.max_index)
    for name, values in served.time_features(k_star, rules.spacing_months).items():
        frame[name] = values
    return frame


def with_priors(frame: pd.DataFrame, priors: SponsorPriors) -> pd.DataFrame:
    """The frame with the sponsor's early-stop rate smoothed toward the class rates of
    `priors` (the origin's for a development check, the served champion's when serving)."""
    out = frame.copy()
    out["sponsor_stop_rate"] = priors.rate(
        out["sponsor_class"].to_numpy(dtype=object),
        out["sponsor_prior_ended"].to_numpy(dtype=np.float64, na_value=np.nan),
        out["sponsor_prior_stopped"].to_numpy(dtype=np.float64, na_value=np.nan),
    )
    return out


def keys_for(
    trial_id: np.ndarray,
    t0: np.ndarray,
    index: np.ndarray,
    day: np.ndarray,
    rules: CohortRules,
    recent_months: int,
) -> pd.DataFrame:
    """Served keys for rows scored on `day` with their own index (the true landmark index
    for rows beyond the last landmark): the window slides the gap of k*, the index capped."""
    k_star = served.capped(np.asarray(index, dtype=np.int64), rules.max_index)
    start = served.window_start(
        np.asarray(t0, dtype="datetime64[D]"),
        k_star,
        np.asarray(day, dtype="datetime64[D]"),
        rules.spacing_months,
        recent_months,
    )
    return pd.DataFrame(
        {
            "trial_id": np.asarray(trial_id).astype(str),
            "landmark_index": np.asarray(index, dtype=np.int64),
            "landmark_date": np.asarray(day, dtype="datetime64[D]"),
            "window_start": start,
        }
    )
