"""The promotion gate of Section 14 (ADR 0080).

A challenger is promoted only if, on a window of matured outcomes after the champion's
training data, its AUC at 12 months is at least the champion's minus `auc_margin` and its
12-month calibration slope lies in [`slope_low`, `slope_high`] (config/project.yaml,
`retraining`). The 24-month slope is reported and tracked (ADR 0032), never gated.

A fair window holds predictions made after the champion's training cutoff whose 12-month
horizon has passed by the data date. Before one exists (about November 2027 for a champion
trained through 2026-09-25), the gate refuses with the reason below; it switches on by itself
once the window is not empty.
"""

import datetime as dt
from dataclasses import dataclass

from trialpulse.cohort.rules import add_months
from trialpulse.config import RetrainingConfig

NO_WINDOW = "not promoted: no window after the champion's training data with matured outcomes yet"


@dataclass(frozen=True)
class Window:
    start: dt.date  # the first prediction date, the day after the champion's training cutoff
    end: dt.date  # the last prediction date whose horizon has passed by the data date


@dataclass(frozen=True)
class Decision:
    promoted: bool
    reason: str


def fair_window(champion_cutoff: dt.date, data_date: dt.date, months: int) -> Window | None:
    """The predictions a fair comparison may grade, or None while there are none."""
    start = champion_cutoff + dt.timedelta(days=1)
    end = add_months(data_date, -months)
    return Window(start, end) if end >= start else None


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
