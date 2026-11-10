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
    if source not in ("live", "replay"):
        raise ValueError("a grade's source is 'live' or 'replay'")
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
    or a lapse exit (censored at its date); else censored at `data_date`."""
    by_trial: dict[str, list[tuple[dt.date, int]]] = {}
    for e in events.sort_values("effective_date").itertuples(index=False):
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
    times, codes = [], []
    for nct, day in zip(predictions["nct_id"], predictions["prediction_date"], strict=True):
        day = pd.Timestamp(day).date()
        first = next(((d, c) for d, c in by_trial.get(str(nct), []) if d > day), None)
        end, code = first if first and first[0] <= data_date else (data_date, 0)
        times.append(_days(day, end))
        codes.append(code)
    return predictions.assign(time_days=times, event=codes)


def cohort_outcomes(predictions: pd.DataFrame, outcomes: pd.DataFrame) -> pd.DataFrame:
    """`predictions` with each one's outcome from the final cohort (the replay, ADR 0078):
    the trial's event and its date, or its censoring date, as follow-up from the prediction.
    A prediction whose trial has no outcome row is left out."""
    merged = predictions.merge(
        outcomes[["trial_id", "event", "event_date"]].rename(columns={"trial_id": "nct_id"}),
        on="nct_id", how="inner",
    )  # fmt: skip
    start = pd.to_datetime(merged["prediction_date"])
    end = pd.to_datetime(merged["event_date"])
    return merged.assign(time_days=(end - start).dt.days.astype(float),
                         event=merged["event"].astype(int))  # fmt: skip


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
