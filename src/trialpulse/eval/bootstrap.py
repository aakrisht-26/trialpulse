"""Cluster bootstrap confidence intervals (CLAUDE.md Section 6: resample trials).

A trial can contribute several landmark rows, and those rows are not independent.
Resampling whole trials keeps each trial's rows together, so the intervals reflect the
real amount of independent information.
"""

from collections.abc import Callable
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
    uniq, inverse = np.unique(np.asarray(clusters), return_inverse=True)
    n_rows = len(inverse)
    all_rows = np.arange(n_rows, dtype=np.int64)
    point = statistic(all_rows)
    rng = np.random.default_rng(seed)
    values: list[float] = []
    for _ in range(n_resamples):
        drawn = rng.integers(0, len(uniq), size=len(uniq))
        multiplicity = np.bincount(drawn, minlength=len(uniq))
        rows = np.repeat(all_rows, multiplicity[inverse])
        value = statistic(rows)
        if np.isfinite(value):
            values.append(value)
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
