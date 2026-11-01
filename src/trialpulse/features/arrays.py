"""Feature columns as plain arrays for a model (used by the harness and by serving)."""

from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from trialpulse.features.registry import BY_NAME, CATEGORY, KEY_COLUMNS


def as_arrays(frame: pd.DataFrame) -> dict[str, npt.NDArray[Any]]:
    """The feature columns of a matrix as plain arrays, one per feature: numbers and flags
    as floats with NaN for a missing value (a flag is 1.0 or 0.0), categories as objects
    with None for a missing value. The key columns are left out, except `landmark_index`,
    which is a feature too."""
    out: dict[str, npt.NDArray[Any]] = {}
    for name in frame.columns:
        if name in KEY_COLUMNS and name not in BY_NAME:
            continue
        column = frame[name]
        if BY_NAME[name].kind == CATEGORY:
            out[name] = column.astype(object).where(column.notna(), None).to_numpy(dtype=object)
        else:
            out[name] = column.astype("Float64").to_numpy(dtype=np.float64, na_value=np.nan)
    return out
