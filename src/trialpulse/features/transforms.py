"""Transforms fitted per walk-forward origin (CLAUDE.md Section 6: "Fitted transforms are
fit on each origin's training rows only").

Two things in the feature set are estimated from data and are therefore fitted once per
origin T, on the training rows of T (the cohort as of T, ADR 0016) and on nothing else:

- **The class rates of the sponsor track record.** The sponsor's early-stop rate is
  smoothed toward the rate of its sponsor class (empirical Bayes, prior weight 10). The
  class rate is the share of early stops among the training trials of that class that had
  ended by T, one row per trial, itself smoothed toward the rate of all classes with the
  same weight so that a class with a handful of ended trials stays stable.
- **The text components** (`trialpulse.features.text.TextComponents`).

The counts these are applied to are point-in-time already; only the fitted part depends on
the origin.
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from trialpulse.eval import EVENT_CENSORED, EVENT_STOP

MISSING_CLASS = "MISSING"


@dataclass(frozen=True)
class SponsorPriors:
    """Early-stop rates among ended trials: of all sponsor classes together, and per class."""

    weight: int
    overall: float
    classes: Mapping[str, float]
    ended: Mapping[str, int]
    stopped: Mapping[str, int]

    @classmethod
    def fit(
        cls,
        trial_id: npt.NDArray[Any],
        landmark_index: npt.NDArray[np.int64],
        landmark_date: npt.NDArray[np.datetime64],
        event: npt.NDArray[np.int64],
        event_date: npt.NDArray[np.datetime64],
        sponsor_class: npt.NDArray[Any],
        origin: dt.date,
        weight: int,
    ) -> "SponsorPriors":
        """From the training landmark rows of one origin. Rows dated on or after the origin
        are refused: they are not training rows of that origin."""
        t = np.datetime64(origin, "D")
        late = (
            (landmark_date >= t)
            | (event_date > t)
            | ((event != EVENT_CENSORED) & (event_date >= t))
        )
        if late.any():
            raise ValueError(
                f"{int(late.sum())} rows are dated on or after the origin {origin}: sponsor "
                "priors are fitted on the origin's training rows only"
            )
        order = np.lexsort((landmark_index, trial_id))
        first = np.ones(len(order), dtype=bool)
        first[1:] = trial_id[order][1:] != trial_id[order][:-1]
        rows = order[first]  # one row per trial: its first landmark
        done = event[rows] != EVENT_CENSORED
        stop = event[rows] == EVENT_STOP
        classes = np.asarray(sponsor_class[rows]).astype(str)
        n_ended, n_stopped = int(done.sum()), int(stop.sum())
        overall = n_stopped / n_ended if n_ended else 0.0
        ended: dict[str, int] = {}
        stopped: dict[str, int] = {}
        rates: dict[str, float] = {}
        for name in sorted(set(classes.tolist())):
            member = classes == name
            ended[name] = int((done & member).sum())
            stopped[name] = int((stop & member).sum())
            rates[name] = (stopped[name] + weight * overall) / (ended[name] + weight)
        return cls(weight, overall, rates, ended, stopped)

    def prior(self, sponsor_class: str | None) -> float:
        return self.classes.get(sponsor_class or MISSING_CLASS, self.overall)

    def rate(
        self,
        sponsor_class: npt.NDArray[Any],
        ended: npt.NDArray[np.float64],
        stopped: npt.NDArray[np.float64],
    ) -> npt.NDArray[np.float64]:
        """The smoothed early-stop rate of each row's sponsor. `ended` and `stopped` are NaN
        for a sponsor without an identity, which gets the class rate."""
        prior = np.array([self.prior(c) for c in np.asarray(sponsor_class, dtype=object)])
        known = ~np.isnan(ended)
        e = np.where(known, ended, 0.0)
        s = np.where(known, stopped, 0.0)
        result: npt.NDArray[np.float64] = (s + self.weight * prior) / (e + self.weight)
        return result

    def to_json(self) -> dict[str, Any]:
        return {
            "prior_weight": self.weight,
            "overall": self.overall,
            "classes": {
                name: {
                    "rate": self.classes[name],
                    "ended": self.ended[name],
                    "stopped": self.stopped[name],
                }
                for name in sorted(self.classes)
            },
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "SponsorPriors":
        classes = data["classes"]
        return cls(
            int(data["prior_weight"]),
            float(data["overall"]),
            {name: float(v["rate"]) for name, v in classes.items()},
            {name: int(v["ended"]) for name, v in classes.items()},
            {name: int(v["stopped"]) for name, v in classes.items()},
        )
