"""Simulated landmark rows with every model feature, for the model tests. No real data."""

import datetime as dt
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest

from trialpulse.config import ProjectConfig, load_project_config
from trialpulse.eval.rows import LandmarkRows
from trialpulse.features.registry import CATEGORY, FEATURES, FLAG

ORIGIN = dt.date(2016, 1, 1)
CLASSES = np.array(["INDUSTRY", "OTHER", "NIH"])
LEVELS: dict[str, tuple[str, ...]] = {
    "allocation": ("RANDOMIZED", "NON_RANDOMIZED"),
    "intervention_model": ("PARALLEL", "SINGLE_GROUP", "CROSSOVER"),
    "primary_purpose": ("TREATMENT", "PREVENTION", "DIAGNOSTIC"),
    "masking": ("NONE", "DOUBLE", "QUADRUPLE"),
    "sex": ("ALL", "FEMALE", "MALE"),
    "title_phase": ("NONE", "PHASE1", "PHASE2", "PHASE3", "PHASE4"),
    "status": ("RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"),
    "sponsor_class": tuple(CLASSES.tolist()),
    "organization_class": tuple(CLASSES.tolist()),
}


@pytest.fixture(scope="session")
def cfg() -> ProjectConfig:
    return load_project_config()


def simulated_rows(
    n: int,
    seed: int,
    first: dt.date = dt.date(2010, 1, 1),
    last: dt.date = ORIGIN,
    end: dt.date | None = ORIGIN,
    lapse_share: float = 0.0,
) -> tuple[LandmarkRows, npt.NDArray[np.float64], npt.NDArray[np.int64]]:
    """Landmark 0 rows of trials first posted between `first` and `last`. A trial stops
    early more often when it is not yet recruiting, when its enrollment target is small and
    when its sponsor is a company; it completes sooner when its planned duration is short.
    With `end`, outcomes are known up to that day only (the cohort as of an origin); without,
    every outcome is known. With `lapse_share`, that share of the trials of class OTHER is
    censored early at a random day, as a lapsed record is. Returns the rows, the follow-up
    time in days and the event."""
    rng = np.random.default_rng(seed)
    days = (last - first).days
    t0 = np.datetime64(first, "D") + rng.integers(0, days, n).astype("timedelta64[D]")
    features: dict[str, npt.NDArray[Any]] = {}
    for feature in FEATURES:
        if not feature.main:
            continue
        if feature.kind == CATEGORY:
            values = rng.choice(np.array(LEVELS[feature.name], dtype=object), n)
            values[rng.random(n) < 0.03] = None
            features[feature.name] = values
        elif feature.kind == FLAG:
            flags = (rng.random(n) < 0.3).astype(np.float64)
            flags[rng.random(n) < 0.02] = np.nan
            features[feature.name] = flags
        else:
            numbers = rng.normal(0.0, 1.0, n)
            numbers[rng.random(n) < 0.05] = np.nan
            features[feature.name] = numbers
    features["enrollment_count"] = np.exp(rng.normal(4.5, 1.0, n)).round()
    features["planned_duration_months"] = rng.uniform(3, 60, n).round()
    features["sponsor_prior_registrations"] = rng.poisson(20, n).astype(np.float64)
    features["sponsor_stop_rate"] = rng.uniform(0.02, 0.3, n)
    features["registration_year"] = t0.astype("datetime64[Y]").astype(np.int64) + 1970.0
    features["minimum_age_years"] = rng.choice([18.0, 0.5, np.nan], n)
    features["landmark_index"] = np.zeros(n)
    features["months_since_t0"] = np.zeros(n)
    waiting = features["status"] == "NOT_YET_RECRUITING"
    company = features["sponsor_class"] == "INDUSTRY"
    small = np.log(features["enrollment_count"]) < 4.0
    stop_rate = 0.0002 * np.exp(1.0 * waiting + 0.7 * company + 0.6 * small)
    complete_rate = 0.0012 * np.exp(-0.02 * (features["planned_duration_months"] - 30))
    stop = rng.exponential(1.0 / stop_rate)
    complete = rng.exponential(1.0 / complete_rate)
    time = np.floor(np.minimum(stop, complete)) + 1.0
    event = np.where(stop < complete, 1, 2).astype(np.int64)
    lapsed = (features["sponsor_class"] == "OTHER") & (rng.random(n) < lapse_share)
    lapse_day = np.floor(rng.uniform(1.0, 700.0, n))
    cut = lapsed & (lapse_day < time)
    event = np.where(cut, 0, event).astype(np.int64)
    time = np.where(cut, lapse_day, time)
    if end is not None:
        limit = (np.datetime64(end, "D") - t0).astype(np.int64).astype(np.float64)
        event = np.where(time >= limit, 0, event).astype(np.int64)
        time = np.minimum(time, limit)
    event_date = t0 + time.astype(np.int64).astype("timedelta64[D]")
    rows = LandmarkRows(
        np.array([f"NCT{seed:02d}{i:06d}" for i in range(n)]),
        np.zeros(n, dtype=np.int64),
        t0,
        event,
        event_date,
        {**features, "stratum": features["sponsor_class"].astype(str)},
    )
    return rows, time, event
