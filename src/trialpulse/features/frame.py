"""The feature matrix of one walk-forward origin: what a model of Steps 9 and 10 reads.

`load` joins the point-in-time features of an origin's training or evaluation rows with
what was fitted on that origin's training rows: the text components of each row's texts,
and the sponsor's early-stop rate smoothed toward the class rates of that origin. It returns
the key columns and every main feature of the registry (`registry.matrix_columns`). Labels are not
part of it; they stay in the cohort's landmark files.

Training rows are the landmark rows of the cohort as of the origin (ADR 0016); evaluation
rows are the final cohort's landmarks with T <= L < T + 12 months (Section 6).

Reading the features of a locked origin's evaluation rows scores nothing: no outcome is
read here. The test lock (ADR 0004) sits in the walk-forward harness, which is the only
place predictions meet outcomes.
"""

import datetime as dt
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import duckdb
import numpy as np
import numpy.typing as npt
import pandas as pd

from trialpulse.cli import RefusedError
from trialpulse.cohort.build import COHORT_DIR
from trialpulse.cohort.rules import add_months
from trialpulse.config import ProjectConfig
from trialpulse.features.build import (
    COMPONENT_COLUMNS,
    FEATURES_DIR,
    FEATURES_NAME,
    PRIORS_NAME,
    TEXT_KEYS_NAME,
    cohort_key_files,
    components_name,
    origin_source,
)
from trialpulse.features.registry import BY_NAME, CATEGORY, KEY_COLUMNS, TEXT_FIELDS, matrix_columns
from trialpulse.features.transforms import SponsorPriors

Role = Literal["training", "evaluation"]


def _rows_sql(
    cfg: ProjectConfig,
    origin: dt.date,
    role: Role,
    cohort_dir: Path,
    landmark_indices: Sequence[int] | None,
) -> str:
    files = cohort_key_files(cohort_dir, [origin])
    only = (
        ""
        if landmark_indices is None
        else f"landmark_index IN ({', '.join(str(int(k)) for k in landmark_indices)})"
    )
    if role == "training":
        return (
            "SELECT trial_id, landmark_index FROM "
            f"read_parquet('{files[origin_source(origin)].as_posix()}')"
            + (f" WHERE {only}" if only else "")
        )
    end = add_months(origin, cfg.walk_forward.eval_window_months)
    return (
        f"SELECT trial_id, landmark_index FROM read_parquet('{files['final'].as_posix()}') "
        f"WHERE landmark_date >= DATE '{origin.isoformat()}' "
        f"AND landmark_date < DATE '{end.isoformat()}'" + (f" AND {only}" if only else "")
    )


def load(
    cfg: ProjectConfig,
    origin: dt.date,
    role: Role,
    features_dir: Path = FEATURES_DIR,
    cohort_dir: Path = COHORT_DIR,
    landmark_indices: Sequence[int] | None = None,
) -> pd.DataFrame:
    """The key columns and every main feature for an origin's training or evaluation rows,
    sorted by trial and landmark index. With `landmark_indices`, only the rows of those
    landmark indices (M1 reads landmark 0 only)."""
    folder = features_dir / origin_source(origin)
    needed = [features_dir / FEATURES_NAME, features_dir / TEXT_KEYS_NAME, folder / PRIORS_NAME]
    needed += [folder / components_name(field) for field in TEXT_FIELDS]
    for path in needed:
        if not path.is_file():
            raise RefusedError(
                f"{path} is missing. Create it with: uv run python -m trialpulse.features.build"
            )
    components = ", ".join(
        f"{field}.{column} AS {field}_svd_{column[1:]}"
        for field in TEXT_FIELDS
        for column in COMPONENT_COLUMNS
    )
    joins = " ".join(
        f"LEFT JOIN read_parquet('{(folder / components_name(field)).as_posix()}') {field} "
        f"ON {field}.text_hash = k.{field}_hash"
        for field in TEXT_FIELDS
    )
    with duckdb.connect() as con:
        frame: pd.DataFrame = con.execute(
            f"""WITH wanted AS ({_rows_sql(cfg, origin, role, cohort_dir, landmark_indices)})
            SELECT f.*, {components}, f.trial_id IS NULL AS no_features
            FROM wanted w
            LEFT JOIN read_parquet('{(features_dir / FEATURES_NAME).as_posix()}') f
              USING (trial_id, landmark_index)
            LEFT JOIN read_parquet('{(features_dir / TEXT_KEYS_NAME).as_posix()}') k
              USING (trial_id, landmark_index)
            {joins}
            ORDER BY w.trial_id, w.landmark_index"""
        ).df()
    if bool(frame["no_features"].any()):
        raise RefusedError(
            f"{int(frame['no_features'].sum())} {role} rows of origin {origin} have no features: "
            "rebuild them with: uv run python -m trialpulse.features.build"
        )
    priors = SponsorPriors.from_json(json.loads((folder / PRIORS_NAME).read_text(encoding="utf-8")))
    frame["sponsor_stop_rate"] = priors.rate(
        frame["sponsor_class"].to_numpy(dtype=object),
        frame["sponsor_prior_ended"].to_numpy(dtype=np.float64, na_value=np.nan),
        frame["sponsor_prior_stopped"].to_numpy(dtype=np.float64, na_value=np.nan),
    )
    return frame[list(matrix_columns())]


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
