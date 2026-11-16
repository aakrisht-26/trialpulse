"""The promotion gate of Section 14 (ADR 0080).

A challenger is promoted only if, on a window of matured outcomes after the champion's
training data, its AUC at 12 months is at least the champion's minus `auc_margin` and its
12-month calibration slope lies in [`slope_low`, `slope_high`] (config/project.yaml,
`retraining`). The 24-month slope is reported and tracked (ADR 0032), never gated.

A fair window holds predictions made after the champion's training cutoff whose 12-month
horizon has passed by the data date. Before one exists (about November 2027 for a champion
trained through 2026-09-25), the gate refuses with the reason below; it switches on by itself
once the window is not empty.

The comparison (`window_dates`, `compare`): the window's latest monthly prediction dates
(`window_dates` in config/project.yaml, oldest first) are scored by the champion and by a
challenger fitted as of the first of them, so neither model has seen an outcome the window
grades (the retrain, `monitor.retrain`, fits it). Both are graded by `monitor.grading` with
source 'window' (never stored); the gate reads the mean over those dates of each model's
matured 12-month AUC and of the challenger's 12-month calibration slope, on the dates where
all three exist.
"""

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from trialpulse.cohort.rules import add_months
from trialpulse.config import RetrainingConfig

NO_WINDOW = "not promoted: no window after the champion's training data with matured outcomes yet"
CHAMPION, CHALLENGER = "champion", "challenger"  # the model_version of each model's grades


@dataclass(frozen=True)
class Window:
    start: dt.date  # the first prediction date, the day after the champion's training cutoff
    end: dt.date  # the last prediction date whose horizon has passed by the data date


@dataclass(frozen=True)
class Decision:
    promoted: bool
    reason: str


@dataclass(frozen=True)
class Comparison:
    """What the gate reads from the window (see the module text)."""

    champion_auc: float | None
    challenger_auc: float | None
    challenger_slope: float | None
    dates: int  # the prediction dates the means are taken over


def fair_window(champion_cutoff: dt.date, data_date: dt.date, months: int) -> Window | None:
    """The predictions a fair comparison may grade, or None while there are none."""
    start = champion_cutoff + dt.timedelta(days=1)
    end = add_months(data_date, -months)
    return Window(start, end) if end >= start else None


def window_dates(window: Window, count: int) -> list[dt.date]:
    """The prediction dates the comparison grades: the window's end and each whole month
    before it, at most `count` of them and none before the window's start, oldest first."""
    days: list[dt.date] = []
    while len(days) < count:
        day = add_months(window.end, -len(days))
        if day < window.start:
            break
        days.append(day)
    return sorted(days)


def compare(grades: pd.DataFrame, months: int) -> Comparison:
    """The means the gate reads, from the window's grades of both models (`grading.grade`
    rows whose model_version is CHAMPION or CHALLENGER): matured rows at `months`, all
    landmark indexes, all rows; only dates where both AUCs and the challenger's slope exist."""
    top = grades[(grades["horizon_months"] == months) & (grades["landmark_index"] == -1)
                 & (grades["row_group"] == "all") & grades["matured"].astype(bool)]  # fmt: skip
    champion = top[top["model_version"] == CHAMPION].set_index("prediction_date")[["auc"]]
    challenger = top[top["model_version"] == CHALLENGER].set_index("prediction_date")[
        ["auc", "calibration_slope"]
    ]
    both = champion.join(challenger, how="inner", lsuffix="_champion", rsuffix="_challenger")
    both = both.astype(float).dropna()
    if both.empty:
        return Comparison(None, None, None, 0)
    return Comparison(float(both["auc_champion"].mean()), float(both["auc_challenger"].mean()),
                      float(both["calibration_slope"].mean()), len(both))  # fmt: skip


def decide(
    cfg: RetrainingConfig,
    window: Window | None,
    champion_auc: float | None,
    challenger_auc: float | None,
    challenger_slope: float | None,
) -> Decision:
    """Section 14's rule, in order: a window, both AUCs and the slope, the AUC, the slope."""
    if window is None:
        return Decision(False, NO_WINDOW)
    if champion_auc is None or challenger_auc is None or challenger_slope is None:
        return Decision(False, "not promoted: the window gives no AUC or slope to compare")
    floor = champion_auc - cfg.auc_margin
    if challenger_auc < floor:
        return Decision(False, f"not promoted: AUC {challenger_auc:.4f} is below the champion's "
                               f"{champion_auc:.4f} minus {cfg.auc_margin}")  # fmt: skip
    if not cfg.slope_low <= challenger_slope <= cfg.slope_high:
        return Decision(False, f"not promoted: calibration slope {challenger_slope:.3f} is "
                               f"outside [{cfg.slope_low}, {cfg.slope_high}]")  # fmt: skip
    return Decision(True, f"promoted: AUC {challenger_auc:.4f} against {champion_auc:.4f}, "
                          f"calibration slope {challenger_slope:.3f}")  # fmt: skip
