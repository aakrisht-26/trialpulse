"""Cluster bootstrap confidence intervals (CLAUDE.md Section 6: resample trials).

A trial can contribute several landmark rows, and those rows are not independent.
Resampling whole trials keeps each trial's rows together, so the intervals reflect the
real amount of independent information.
"""

from collections.abc import Callable, Iterator, Mapping
from typing import Any

import numpy as np
import numpy.typing as npt

# Above this share of invalid resamples the interval would describe a selected subset of
# resamples, not the sampling distribution, so the bootstrap fails instead.
MAX_INVALID_SHARE = 0.01


class BootstrapError(RuntimeError):
    """Too many resamples gave a statistic that is not finite."""


def cluster_bootstrap(
    clusters: npt.NDArray[Any],
    statistic: Callable[[npt.NDArray[np.int64]], float],
    n_resamples: int,
    seed: int,
    confidence: float,
) -> dict[str, float | int]:
    """Point estimate and percentile interval of statistic(row_indices).

    Each resample draws as many clusters as there are, with replacement, and passes the
    row indices of the drawn clusters (with repeats) to statistic. A resample whose
    statistic is not finite (for example, no cases drawn) is invalid. Invalid resamples are
    always counted and reported; if more than 1% of them are invalid, BootstrapError is
    raised with the count.
    """
    all_rows = np.arange(len(np.asarray(clusters)), dtype=np.int64)
    point = statistic(all_rows)
    values: list[float] = []
    for rows in resamples(clusters, n_resamples, seed):
        value = statistic(rows)
        if np.isfinite(value):
            values.append(value)
    return _interval(point, values, n_resamples, confidence)


def resamples(
    clusters: npt.NDArray[Any], n_resamples: int, seed: int
) -> Iterator[npt.NDArray[np.int64]]:
    """The row indices of each resample: as many clusters as there are, drawn with
    replacement, every row of a drawn cluster as often as the cluster was drawn."""
    uniq, inverse = np.unique(np.asarray(clusters), return_inverse=True)
    all_rows = np.arange(len(inverse), dtype=np.int64)
    rng = np.random.default_rng(seed)
    for _ in range(n_resamples):
        drawn = rng.integers(0, len(uniq), size=len(uniq))
        multiplicity = np.bincount(drawn, minlength=len(uniq))
        yield np.repeat(all_rows, multiplicity[inverse])


def cluster_bootstrap_many(
    clusters: npt.NDArray[Any],
    statistics: Callable[[npt.NDArray[np.int64]], Mapping[str, float]],
    n_resamples: int,
    seed: int,
    confidence: float,
) -> dict[str, dict[str, float | int]]:
    """Several statistics from the same resamples: `statistics` returns a value per name,
    and each name gets the point estimate and interval `cluster_bootstrap` would give it.
    A difference between two models is bootstrapped this way, with both models scored on
    every resample (a paired bootstrap), and so are two metrics that share their weights."""
    all_rows = np.arange(len(np.asarray(clusters)), dtype=np.int64)
    point = dict(statistics(all_rows))
    values: dict[str, list[float]] = {name: [] for name in point}
    for rows in resamples(clusters, n_resamples, seed):
        for name, value in statistics(rows).items():
            if np.isfinite(value):
                values[name].append(float(value))
    return {name: _interval(point[name], values[name], n_resamples, confidence) for name in point}


def _interval(
    point: float, values: list[float], n_resamples: int, confidence: float
) -> dict[str, float | int]:
    invalid = n_resamples - len(values)
    if invalid > MAX_INVALID_SHARE * n_resamples:
        raise BootstrapError(
            f"{invalid} of {n_resamples} bootstrap resamples ({invalid / n_resamples:.1%}) gave "
            f"a statistic that is not finite, above the {MAX_INVALID_SHARE:.0%} limit; the "
            "subset is too small or has too few cases for a reliable interval"
        )
    alpha = (1.0 - confidence) / 2.0
    lo, hi = (
        (float(np.quantile(values, alpha)), float(np.quantile(values, 1.0 - alpha)))
        if values
        else (float("nan"), float("nan"))
    )
    return {
        "estimate": float(point),
        "ci_low": lo,
        "ci_high": hi,
        "resamples": n_resamples,
        "valid_resamples": len(values),
        "invalid_resamples": invalid,
    }
