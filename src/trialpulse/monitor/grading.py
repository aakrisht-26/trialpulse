"""Grades of served predictions as aggregates (ADRs 0039, 0077 and 0079).

Shared by the live grading (`monitor.grade`, from Neon's score files and outcome events) and
the replay (`monitor.replay`, from the dataset's final cohort). A prediction is a row of:
prediction_date, risk_12m, risk_24m, sponsor_class, landmark_index, flagged, model_version,
and its outcome as follow-up days from the prediction date (`time_days`) and an event code
(`event`: 0 censored, 1 early stop, 2 completion, as the evaluation harness).

For each prediction date and model, each horizon (12 and 24 months), each landmark index
(and -1 for all) and each group ('all', 'flagged'): counts and the harness's metrics, IPCW
AUC with censoring weights by sponsor class (ADR 0017, groups decided once per prediction
date), IPCW Brier score, calibration slope and intercept, lift at 10%. A prediction is
matured when the data date is at least its horizon after it; before that, the metrics are
partial-horizon, at the follow-up so far (Section 14). A metric that cannot be computed (no
case, no control, too few rows) is None.

A trial with a status reversal (a terminal status later replaced by an open one) is left out
of every grade, as Section 6 leaves it out of evaluation: on the live path, a `reopened`
event posted by the data date; on the replay, the final cohort's `reversal` flag.

A trial still open on the data date is known to be free of any event through the end of that
day, so it is censored half a day after it (`OPEN_THROUGH_THE_DAY`). The harness's weights
treat a row censored at or before the horizon as unknown, so a trial censored at the data date
itself would drop out of every grade whose horizon ends on the data date: every partial-horizon
grade, and a grade made on the horizon's last day (review finding N1, 2026-11-17).
"""

import datetime as dt
import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any

import numpy as np
import pandas as pd

from trialpulse.cohort.rules import add_months
from trialpulse.eval import EVENT_COMPLETE, EVENT_STOP
from trialpulse.eval.ipcw import censoring_groups
from trialpulse.eval.metrics import calibration_slope_intercept, ipcw_auc, ipcw_brier, lift_at

HORIZONS = (12, 24)
GROUPS = ("all", "flagged")
STOP_STATUSES = ("TERMINATED", "WITHDRAWN")
COMPLETE_STATUS = "COMPLETED"
REOPENED = "reopened"  # trialpulse.live.state.REOPENED: a closed trial shows an open status
STORED_SOURCES = ("live", "replay")  # what monitoring.graded_predictions holds (ADR 0079)
WINDOW = "window"  # the retrain's comparison of two models (ADR 0080), never stored
OPEN_THROUGH_THE_DAY = 0.5  # days after the data date at which a trial still open is censored
GRADE_COLUMNS = (
    "prediction_date", "horizon_months", "landmark_index", "row_group", "model_version",
    "source", "n_rows", "n_early_stop", "n_completed", "n_censored", "n_open", "mean_risk",
    "auc", "brier", "calibration_slope", "calibration_intercept", "lift_top_10", "matured",
)  # fmt: skip


def _days(start: dt.date, end: dt.date) -> float:
    return float((end - start).days)


def _metric(compute: Callable[[], Any]) -> float | None:
    try:
        value = compute()
    except (ValueError, FloatingPointError, ZeroDivisionError, np.linalg.LinAlgError):
        return None
    if isinstance(value, tuple):
        value = value[0]
    return None if value is None or not math.isfinite(float(value)) else float(value)


def grade(
    predictions: pd.DataFrame,
    data_date: dt.date,
    source: str,
    min_rows: int,
    top_fraction: float,
) -> pd.DataFrame:
    """The aggregate rows of `predictions` (see the module text)."""
    if source not in (*STORED_SOURCES, WINDOW):
        raise ValueError("a grade's source is 'live', 'replay' or 'window'")
    out: list[dict[str, Any]] = []
    for (day, model), rows in predictions.groupby(["prediction_date", "model_version"], sort=True):
        day = pd.Timestamp(day).date()
        classes = rows["sponsor_class"].fillna("MISSING").astype(str).to_numpy()
        groups_all = censoring_groups(classes, min_rows)  # decided once per prediction date
        follow_up = _days(day, data_date)
        for months in HORIZONS:
            horizon = _days(day, add_months(day, months))
            matured = follow_up >= horizon
            h = horizon if matured else max(follow_up, 0.0)
            risk = rows[f"risk_{months}m"].to_numpy(dtype=np.float64)
            for index in [-1, *sorted(int(k) for k in rows["landmark_index"].unique())]:
                at = np.ones(len(rows), bool) if index == -1 else (
                    rows["landmark_index"].to_numpy() == index)  # fmt: skip
                for group in GROUPS:
                    keep = at & (rows["flagged"].to_numpy(dtype=bool) if group == "flagged"
                                 else True)  # fmt: skip
                    if not keep.any():
                        continue
                    out.append(_row(rows[keep], risk[keep], groups_all[keep], day, months,
                                    index, group, str(model), source, h, matured,
                                    top_fraction))  # fmt: skip
    return pd.DataFrame(out, columns=list(GRADE_COLUMNS))


def _row(rows: pd.DataFrame, risk: np.ndarray, groups: np.ndarray, day: dt.date, months: int,
         index: int, group: str, model: str, source: str, h: float, matured: bool,
         top_fraction: float) -> dict[str, Any]:  # fmt: skip
    time = rows["time_days"].to_numpy(dtype=np.float64)
    event = rows["event"].to_numpy(dtype=np.int64)
    within = time <= h
    stop = int((within & (event == EVENT_STOP)).sum())
    done = int((within & (event == EVENT_COMPLETE)).sum())
    censored = int((within & (event == 0)).sum())
    usable = h > 0 and stop > 0 and (len(rows) - stop) > 0
    return {
        "prediction_date": day, "horizon_months": months, "landmark_index": index,
        "row_group": group, "model_version": model, "source": source,
        "n_rows": len(rows), "n_early_stop": stop, "n_completed": done, "n_censored": censored,
        "n_open": int((~within).sum()), "mean_risk": float(risk.mean()),
        "auc": _metric(lambda: ipcw_auc(time, event, risk, h, groups)) if usable else None,
        "brier": _metric(lambda: ipcw_brier(time, event, risk, h, groups)) if h > 0 else None,
        "calibration_slope": _metric(lambda: calibration_slope_intercept(
            time, event, risk, h, groups)) if usable else None,
        "calibration_intercept": _metric(lambda: calibration_slope_intercept(
            time, event, risk, h, groups)[1]) if usable else None,
        "lift_top_10": _metric(lambda: lift_at(time, event, risk, h, top_fraction, groups))
        if usable else None,
        "matured": bool(matured),
    }  # fmt: skip


def live_outcomes(
    predictions: pd.DataFrame, events: pd.DataFrame, data_date: dt.date
) -> pd.DataFrame:
    """`predictions` with each one's outcome from `live.outcome_events` (ADR 0077): the first
    status change after the prediction date to TERMINATED or WITHDRAWN (1) or COMPLETED (2),
    or a lapse exit (censored at its date); else censored at `data_date`. A trial with a
    `reopened` event (a status reversal) posted by `data_date` is left out (Section 6)."""
    by_trial: dict[str, list[tuple[dt.date, int]]] = {}
    reversed_trials: set[str] = set()
    for e in events.sort_values("effective_date").itertuples(index=False):
        if e.kind == REOPENED and pd.Timestamp(e.effective_date).date() <= data_date:
            reversed_trials.add(str(e.nct_id))
        code = None
        if e.kind == "status" and e.to_status in STOP_STATUSES:
            code = EVENT_STOP
        elif e.kind == "status" and e.to_status == COMPLETE_STATUS:
            code = EVENT_COMPLETE
        elif e.kind == "lapsed":
            code = 0
        if code is not None:
            by_trial.setdefault(str(e.nct_id), []).append(
                (pd.Timestamp(e.effective_date).date(), code)
            )
    kept = predictions[~predictions["nct_id"].astype(str).isin(reversed_trials)]
    times, codes = [], []
    for nct, day in zip(kept["nct_id"], kept["prediction_date"], strict=True):
        day = pd.Timestamp(day).date()
        first = next(((d, c) for d, c in by_trial.get(str(nct), []) if d > day), None)
        if first and first[0] <= data_date:
            times.append(_days(day, first[0]))
            codes.append(first[1])
        else:  # open through the end of the data date
            times.append(_days(day, data_date) + OPEN_THROUGH_THE_DAY)
            codes.append(0)
    return kept.assign(time_days=times, event=codes)


def cohort_outcomes(
    predictions: pd.DataFrame, outcomes: pd.DataFrame, data_date: dt.date
) -> pd.DataFrame:
    """`predictions` with each one's outcome from a final cohort observed through
    `data_date` (the replay, ADR 0078, and the retrain's window, ADR 0080): the trial's event
    and its date, or its censoring date, as follow-up from the prediction; a trial open at the
    data date is censored at the end of that day (see the module text). Left out: a prediction
    whose trial has no outcome row, a status reversal (`reversal`, Section 6), and a trial
    that ended or was censored on or before the prediction date (a lapse the final cohort
    dates at an earlier status-verified date, ADR 0014), as the landmark rows leave such a
    trial out (review finding N2, 2026-11-17). The number left out for that last reason is in
    `attrs["ended_by_the_prediction"]`."""
    kept = outcomes.loc[~outcomes["reversal"].fillna(False).astype(bool),
                        ["trial_id", "event", "event_date"]]  # fmt: skip
    merged = predictions.merge(
        kept.rename(columns={"trial_id": "nct_id"}), on="nct_id", how="inner"
    )
    start = pd.to_datetime(merged["prediction_date"])
    end = pd.to_datetime(merged["event_date"])
    after = (end > start).to_numpy()
    open_at_the_end = ((merged["event"] == 0) & (end >= pd.Timestamp(data_date))).to_numpy()
    days = (end - start).dt.days.astype(float) + np.where(open_at_the_end,
                                                         OPEN_THROUGH_THE_DAY, 0.0)  # fmt: skip
    out = merged.assign(time_days=days, event=merged["event"].astype(int))[after]
    out.attrs["ended_by_the_prediction"] = int((~after).sum())
    return out.reset_index(drop=True)


def _plain(value: Any) -> Any:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def as_records(grades: pd.DataFrame) -> list[tuple[Any, ...]]:
    """Rows for COPY or executemany, NaN as None."""
    out = []
    for values in grades[list(GRADE_COLUMNS)].itertuples(index=False, name=None):
        out.append(tuple(_plain(v) for v in values))
    return out


def upsert(cur: Any, grades: pd.DataFrame) -> int:
    """Write grades to `monitoring.graded_predictions`, replacing rows with the same key."""
    if grades.empty:
        return 0
    if not set(grades["source"]) <= set(STORED_SOURCES):
        raise ValueError("only live and replay grades are stored (ADR 0079)")
    columns = ", ".join(GRADE_COLUMNS)
    marks = ", ".join(["%s"] * len(GRADE_COLUMNS))
    key = "prediction_date, horizon_months, landmark_index, row_group, model_version, source"
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in GRADE_COLUMNS[6:])
    cur.executemany(
        f"""INSERT INTO monitoring.graded_predictions ({columns}) VALUES ({marks})
            ON CONFLICT ({key}) DO UPDATE SET {updates}, graded_at = now()""",
        as_records(grades),
    )
    return len(grades)


def summary(grades: pd.DataFrame) -> Mapping[str, Any]:
    """Counts of what was graded, for the command's output."""
    top = grades[(grades["landmark_index"] == -1) & (grades["row_group"] == "all")]
    return {"rows": len(grades), "prediction_dates": int(grades["prediction_date"].nunique()),
            "matured_rows": int(grades["matured"].sum()),
            "with_auc": int(top["auc"].notna().sum())}  # fmt: skip


def iter_dates(start: dt.date, end: dt.date, weekday: int = 4) -> Iterable[dt.date]:
    """Every `weekday` (Friday by default) from start through end."""
    day = start + dt.timedelta(days=(weekday - start.weekday()) % 7)
    while day <= end:
        yield day
        day += dt.timedelta(days=7)
