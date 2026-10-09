"""Landmark rows: what the walk-forward harness reads, and what a model is fitted on.

Input contract (written by the Step 4 cohort build; approved 2026-09-23): one row per
(trial, landmark) with trial_id, landmark_index, landmark_date, event (0 censored, 1 early
stop, 2 completion) and event_date, plus any feature columns a model needs (M0 uses
"stratum"). event_date holds the date of the event when event is 1 or 2, and the censoring
date when event is 0 (the end of observation, or the UNKNOWN censoring date of ADR 0014).

This module holds no model and no metric, so models and the harness can both import it.
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import numpy.typing as npt

from trialpulse.config import REPO_ROOT
from trialpulse.dates import DateArray, add_months, days_between

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
LANDMARKS_PATH = REPO_ROOT / "data" / "cohort" / "landmarks.parquet"
TRAINING_DIR = LANDMARKS_PATH.parent / "training"
REQUIRED_COLUMNS = ("trial_id", "landmark_index", "landmark_date", "event", "event_date")


def training_path(training_dir: Path, origin: dt.date) -> Path:
    """Where the cohort build puts the training landmark rows of an origin (ADR 0016)."""
    return training_dir / f"origin_{origin.isoformat()}" / LANDMARKS_PATH.name


@dataclass(frozen=True)
class LandmarkRows:
    trial_id: npt.NDArray[Any]
    landmark_index: IntArray
    landmark_date: DateArray
    event: IntArray
    event_date: DateArray
    features: Mapping[str, npt.NDArray[Any]]

    def __len__(self) -> int:
        return len(self.trial_id)

    def subset(self, mask: npt.NDArray[np.bool_]) -> "LandmarkRows":
        return LandmarkRows(
            self.trial_id[mask],
            self.landmark_index[mask],
            self.landmark_date[mask],
            self.event[mask],
            self.event_date[mask],
            {k: v[mask] for k, v in self.features.items()},
        )

    def with_features(self, features: Mapping[str, npt.NDArray[Any]]) -> "LandmarkRows":
        """The same rows with more feature columns (one value per row, in row order)."""
        for name, values in features.items():
            if len(values) != len(self):
                raise ValueError(f"{name}: {len(values)} values for {len(self)} rows")
        return LandmarkRows(
            self.trial_id,
            self.landmark_index,
            self.landmark_date,
            self.event,
            self.event_date,
            {**self.features, **features},
        )


def load_landmark_rows(path: Path) -> LandmarkRows:
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found: run the Step 4 cohort build first")
    with duckdb.connect() as con:
        data = con.execute(f"SELECT * FROM read_parquet('{path.as_posix()}')").fetchnumpy()
    missing = [c for c in REQUIRED_COLUMNS if c not in data]
    if missing:
        raise ValueError(f"{path} lacks required columns: {missing}")
    return LandmarkRows(
        np.asarray(data["trial_id"]),
        np.asarray(data["landmark_index"], dtype=np.int64),
        np.asarray(data["landmark_date"]).astype("datetime64[D]"),
        np.asarray(data["event"], dtype=np.int64),
        np.asarray(data["event_date"]).astype("datetime64[D]"),
        {k: np.asarray(v) for k, v in data.items() if k not in REQUIRED_COLUMNS},
    )


def horizon_days(landmark_dates: DateArray, months: int) -> FloatArray:
    """Each row's horizon in days: from L to L plus the horizon in calendar months."""
    return days_between(landmark_dates, add_months(landmark_dates, months))
