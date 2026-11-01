"""Scores of served rows from their features (CLAUDE.md Step 13, ADR 0034): the population's
classes, the served keys, the priors, the risks and the drivers. Nothing here reads the
dataset, so the daily job of the live system uses it (ADR 0040); `scoring.score` re-exports
these names for the offline path.
"""

import datetime as dt
import json
import time as clock
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from trialpulse.cohort.rules import CohortRules
from trialpulse.eval.rows import LandmarkRows
from trialpulse.explain import drivers
from trialpulse.features.arrays import as_arrays
from trialpulse.features.transforms import SponsorPriors
from trialpulse.models.discrete_time import DiscreteTimeBoosting
from trialpulse.scoring import rows as served

HORIZONS: tuple[int, ...] = (12, 24)


REVERSAL, OLDEST, BAND = "reversal", "beyond_120_months", "band_not_validated"


FLAG = "outside the validated range"


@dataclass
class Bundle:
    """A model, the sponsor priors fitted with it, and its name and version."""

    model: DiscreteTimeBoosting
    priors: SponsorPriors
    name: str
    version: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Scored:
    scores: pd.DataFrame
    unscored: pd.DataFrame
    seconds: dict[str, float]


def classify(people: pd.DataFrame, day: dt.date, rules: CohortRules,
             decisions: Mapping[str, Any]) -> pd.DataFrame:  # fmt: skip
    """Each open trial with its true landmark index, k*, its band and whether it is scored
    (with the flag) or not (with the reason)."""
    out = people.copy()
    t0 = out["t0"].to_numpy(dtype="datetime64[D]")
    out["true_index"] = served.true_index(t0, day, rules.spacing_months)
    out["k_star"] = served.capped(out["true_index"].to_numpy(dtype=np.int64), rules.max_index)
    out["flagged"] = False
    out["reason"] = None
    for band, low, high in served.BANDS:
        member = (out["true_index"] >= low) & (out["true_index"] <= high)
        if decisions["bands"][band]["served"]:
            out.loc[member, "flagged"] = True
        else:
            out.loc[member, "reason"] = BAND
    out.loc[out["true_index"] >= served.OLDEST_INDEX, "reason"] = OLDEST
    out.loc[out["reversal"].astype(bool), "reason"] = REVERSAL
    return out


def _drivers_json(explanations: Mapping[int, drivers.Explanation],
                  values: Mapping[str, Any], row: int) -> str:  # fmt: skip
    payload = {
        str(months): [
            {"feature": d.feature, "value": d.value, "contribution": round(d.contribution, 6),
             "direction": d.direction}
            for d in drivers.top_drivers(explanation, values, row)
        ]
        for months, explanation in explanations.items()
    }  # fmt: skip
    return json.dumps(payload, separators=(",", ":"))


def predict(
    scored: pd.DataFrame,
    features: pd.DataFrame,
    bundle: Bundle,
    day: dt.date,
    with_drivers: bool = True,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """The scores of the trials in `scored` (one row per trial, with its true index, k*, flag
    and content hash) from their served features (`compute_served`, before the priors): the
    sponsor rate smoothed with the bundle's priors, the risk at each served horizon and, with
    `with_drivers`, the five drivers of each horizon, row for row in the order of `scored`."""
    seconds: dict[str, float] = {}
    tick = clock.perf_counter()
    frame = with_priors(features, bundle.priors)
    frame = scored[["trial_id", "true_index"]].merge(
        frame, on=["trial_id", "true_index"], how="left", validate="one_to_one", indicator=True
    )
    missing = int((frame.pop("_merge") != "both").sum())
    if missing:
        raise RuntimeError(f"{missing} scored trials have no served features")
    model = bundle.model
    columns = ["trial_id", "landmark_index", *[f for f in model.features if f != "landmark_index"]]
    values = as_arrays(frame[columns])
    stamp = np.full(len(frame), np.datetime64(day, "D"))
    landmark_rows = LandmarkRows(frame["trial_id"].to_numpy(dtype=object),
                                 scored["k_star"].to_numpy(dtype=np.int64), stamp,
                                 np.zeros(len(frame), dtype=np.int64), stamp, values)  # fmt: skip
    risk = {months: model.predict_months(landmark_rows, months) for months in HORIZONS}
    seconds["predict"] = clock.perf_counter() - tick
    out = pd.DataFrame(
        {
            "nct_id": scored["trial_id"].astype(str).to_numpy(),
            "as_of_date": np.datetime64(day, "D"),
            "true_index": scored["true_index"].to_numpy(dtype=np.int64),
            "landmark_index": scored["k_star"].to_numpy(dtype=np.int64),
            "flagged": scored["flagged"].to_numpy(dtype=bool),
            **{f"risk_{months}m": risk[months] for months in HORIZONS},
            "content_hash": scored["content_hash"].astype(str).to_numpy(),
            "model_name": bundle.name,
            "model_version": bundle.version,
        }
    )
    if with_drivers:
        tick = clock.perf_counter()
        explanations = drivers.explain_horizons(model, landmark_rows, HORIZONS)
        out["drivers"] = [_drivers_json(explanations, values, i) for i in range(len(out))]
        seconds["drivers"] = clock.perf_counter() - tick
    return out, seconds


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
