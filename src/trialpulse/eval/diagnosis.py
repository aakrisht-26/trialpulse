"""Why M0's calibration slope moved when the training labels became "as of each origin".

    uv run python -m trialpulse.eval.diagnosis

The first task of Step 9: a diagnosis, on the development origins only. Nothing is fixed
here and no model beyond M0 is fitted.

**The question.** M0 is one Aalen-Johansen curve per sponsor class. With training labels
that knew what was posted after the origin ("hindsight": the final cohort's rows before
the origin, cut off at the origin), its 24-month calibration slope was about 1.2 to 1.3.
With labels built as of the origin (ADR 0016) it is about 1.46 and 1.49. A slope above 1
says the predicted risks of the classes lie closer together than the observed ones.

**The method.** Every training row of an origin exists in up to two forms, the as-of one
and the hindsight one. Each row is put in one category by how the two forms differ:

- `same`: both forms agree;
- `lapsed_as_of_origin`: the registry's UNKNOWN rule (ADR 0014) applies to the trial on the
  origin, so the as-of cohort censors it at its status-verified date and drops its later
  landmarks. A version posted after the origin resolved the lapse, so hindsight keeps the
  trial under observation up to the origin;
- `lapsed_later`: the opposite. The trial's record lapsed after the origin and was never
  resolved, so hindsight censors it at a status-verified date before the origin, while on
  the origin itself the trial was still under observation;
- `reversal_later`: hindsight excludes the trial for a reversal posted after the origin;
- `other`: anything else.

M0 is then fitted on mixed label sets (hindsight labels, with the as-of labels swapped in
for one category at a time) and scored on the same evaluation rows with the harness's own
metrics. The step from one mix to the next is the share of the move that the category
explains.

The command writes docs/calibration_diagnosis.md and data/results/diagnosis/m0_calibration.json.
"""

import argparse
import datetime as dt
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import numpy.typing as npt

from trialpulse.cli import RefusedError, run
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.dates import days_between
from trialpulse.eval import EVENT_COMPLETE, EVENT_STOP
from trialpulse.eval.bootstrap import cluster_bootstrap
from trialpulse.eval.ipcw import FloatArray, IntArray, censoring_groups, horizon_labels_and_weights
from trialpulse.eval.metrics import calibration_slope_intercept, ipcw_auc, ipcw_brier
from trialpulse.eval.rows import (
    LANDMARKS_PATH,
    TRAINING_DIR,
    LandmarkRows,
    horizon_days,
    load_landmark_rows,
    training_path,
)
from trialpulse.eval.walkforward import CENSORING_COLUMN, evaluation_rows
from trialpulse.models.aalen_johansen import AalenJohansenModel
from trialpulse.reports import markdown_table, write_text_if_changed

DOC_PATH = REPO_ROOT / "docs" / "calibration_diagnosis.md"
RESULTS_PATH = REPO_ROOT / "data" / "results" / "diagnosis" / "m0_calibration.json"
OUTCOMES_NAME = "outcomes.parquet"
DEV_ROLE = "dev"

SAME = "same"
LAPSED_AS_OF_ORIGIN = "lapsed_as_of_origin"
LAPSED_LATER = "lapsed_later"
REVERSAL_LATER = "reversal_later"
OTHER = "other"
CATEGORIES: tuple[str, ...] = (SAME, LAPSED_AS_OF_ORIGIN, LAPSED_LATER, REVERSAL_LATER, OTHER)
# The label sets M0 is fitted on: a name, and the categories whose rows take the as-of form.
# Every other row takes the hindsight form. The first is all hindsight, the last all as-of.
MIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hindsight", ()),
    ("hindsight, lapsed as of the origin from as-of", (LAPSED_AS_OF_ORIGIN,)),
    ("hindsight, lapsed later from as-of", (LAPSED_LATER,)),
    ("hindsight, both lapse categories from as-of", (LAPSED_AS_OF_ORIGIN, LAPSED_LATER)),
    ("as of the origin", CATEGORIES),
)
HINDSIGHT, AS_OF = MIXES[0][0], MIXES[-1][0]
BOOTSTRAP_RESAMPLES = 200


@dataclass(frozen=True)
class LabelTable:
    """Every training row of one origin in its two forms. A form the row does not have (the
    cohort has no such landmark row) is marked by `has_as_of` or `has_hindsight` false."""

    trial_id: npt.NDArray[Any]
    landmark_date: npt.NDArray[np.datetime64]
    stratum: npt.NDArray[Any]
    category: npt.NDArray[Any]
    has_as_of: npt.NDArray[np.bool_]
    as_of_event: IntArray
    as_of_date: npt.NDArray[np.datetime64]
    has_hindsight: npt.NDArray[np.bool_]
    hindsight_event: IntArray
    hindsight_date: npt.NDArray[np.datetime64]

    def __len__(self) -> int:
        return len(self.trial_id)

    def mix(self, from_as_of: Sequence[str]) -> tuple[FloatArray, IntArray, npt.NDArray[Any]]:
        """Follow-up time, event and stratum of the rows of one label set: the rows of the
        categories in `from_as_of` in their as-of form, every other row in its hindsight
        form. A row without the form asked for is not in the set."""
        swapped = np.isin(self.category, list(from_as_of))
        keep = np.where(swapped, self.has_as_of, self.has_hindsight)
        event = np.where(swapped, self.as_of_event, self.hindsight_event)[keep]
        end = np.where(swapped, self.as_of_date, self.hindsight_date)[keep]
        return days_between(self.landmark_date[keep], end), event, self.stratum[keep]


def label_table(
    final_landmarks: Path,
    final_outcomes: Path,
    as_of_landmarks: Path,
    as_of_outcomes: Path,
    origin: dt.date,
) -> LabelTable:
    """Join the as-of and the hindsight form of every training row of an origin and put
    each row in a category. The hindsight form is what training used before ADR 0016: the
    final cohort's rows before the origin, with anything dated on or after the origin cut
    off (an event dated on the origin is not known on the origin, ADR 0015)."""
    for path in (final_landmarks, final_outcomes, as_of_landmarks, as_of_outcomes):
        if not path.is_file():
            raise RefusedError(
                f"{path} is missing. Create it with: uv run python -m trialpulse.cohort.build"
            )
    day = f"DATE '{origin.isoformat()}'"
    lapse = "event = 0 AND censor_reason = 'unknown'"
    with duckdb.connect() as con:
        data = con.execute(
            f"""WITH hindsight AS (
              SELECT trial_id, landmark_index, landmark_date, stratum,
                CASE WHEN event_date >= {day} THEN 0 ELSE event END AS h_event,
                least(event_date, {day}) AS h_date
              FROM read_parquet('{final_landmarks.as_posix()}') WHERE landmark_date < {day}
            ),
            as_of AS (
              SELECT trial_id, landmark_index, landmark_date, stratum, event AS a_event,
                event_date AS a_date
              FROM read_parquet('{as_of_landmarks.as_posix()}')
            ),
            joined AS (
              SELECT trial_id, landmark_index,
                coalesce(a.landmark_date, h.landmark_date) AS landmark_date,
                coalesce(a.stratum, h.stratum) AS stratum,
                a.a_event, a.a_date, h.h_event, h.h_date
              FROM as_of a FULL JOIN hindsight h USING (trial_id, landmark_index)
            ),
            ao AS (SELECT trial_id, ({lapse}) AS lapsed, event_date AS censored
                   FROM read_parquet('{as_of_outcomes.as_posix()}')),
            fo AS (SELECT trial_id, ({lapse}) AS lapsed, event_date AS censored, reversal
                   FROM read_parquet('{final_outcomes.as_posix()}'))
            SELECT j.trial_id, j.landmark_date, j.stratum,
              j.a_event IS NOT NULL AS has_as_of, coalesce(j.a_event, 0) AS a_event,
              coalesce(j.a_date, j.landmark_date) AS a_date,
              j.h_event IS NOT NULL AS has_hindsight, coalesce(j.h_event, 0) AS h_event,
              coalesce(j.h_date, j.landmark_date) AS h_date,
              CASE
                WHEN j.a_event = j.h_event AND j.a_date = j.h_date THEN '{SAME}'
                WHEN j.a_event = 0 AND j.h_event IS NOT NULL AND j.a_date < j.h_date
                  AND coalesce(ao.lapsed, false) THEN '{LAPSED_AS_OF_ORIGIN}'
                WHEN j.a_event IS NULL AND coalesce(ao.lapsed, false)
                  AND ao.censored <= j.landmark_date THEN '{LAPSED_AS_OF_ORIGIN}'
                WHEN j.h_event = 0 AND j.a_event IS NOT NULL AND j.h_date < j.a_date
                  AND coalesce(fo.lapsed, false) THEN '{LAPSED_LATER}'
                WHEN j.h_event IS NULL AND coalesce(fo.lapsed, false)
                  AND fo.censored <= j.landmark_date THEN '{LAPSED_LATER}'
                WHEN j.h_event IS NULL AND coalesce(fo.reversal, false) THEN '{REVERSAL_LATER}'
                ELSE '{OTHER}' END AS category
            FROM joined j LEFT JOIN ao USING (trial_id) LEFT JOIN fo USING (trial_id)
            ORDER BY j.trial_id, j.landmark_index"""
        ).fetchnumpy()

    def dates(name: str) -> npt.NDArray[np.datetime64]:
        return np.asarray(data[name]).astype("datetime64[D]")

    return LabelTable(
        np.asarray(data["trial_id"]),
        dates("landmark_date"),
        np.asarray(data["stratum"]).astype(str),
        np.asarray(data["category"]).astype(str),
        np.asarray(data["has_as_of"], dtype=bool),
        np.asarray(data["a_event"], dtype=np.int64),
        dates("a_date"),
        np.asarray(data["has_hindsight"], dtype=bool),
        np.asarray(data["h_event"], dtype=np.int64),
        dates("h_date"),
    )


def category_counts(table: LabelTable) -> dict[str, dict[str, int]]:
    """Per category: rows, trials, and how many rows exist in each form."""
    out: dict[str, dict[str, int]] = {}
    for category in CATEGORIES:
        member = table.category == category
        out[category] = {
            "rows": int(member.sum()),
            "trials": len(np.unique(table.trial_id[member])),
            "rows_as_of": int((member & table.has_as_of).sum()),
            "rows_hindsight": int((member & table.has_hindsight).sum()),
        }
    return out


def followup_years(table: LabelTable, category: str) -> dict[str, float]:
    """Years under observation that the rows of a category carry in each form."""
    member = table.category == category
    as_of = member & table.has_as_of
    hindsight = member & table.has_hindsight
    return {
        "as_of": float(
            days_between(table.landmark_date[as_of], table.as_of_date[as_of]).sum() / 365.25
        ),
        "hindsight": float(
            days_between(table.landmark_date[hindsight], table.hindsight_date[hindsight]).sum()
            / 365.25
        ),
    }


def later_fate(final_outcomes: Path, as_of_outcomes: Path, origin: dt.date) -> list[dict[str, Any]]:
    """What became of the trials that the as-of cohort censors: those lapsed on the origin
    (censored under the UNKNOWN rule at their status-verified date) and those still open on
    the origin (censored at the origin). The fate is the trial's outcome in the final
    cohort, with the median days from the origin to a later stop or completion."""
    day = f"DATE '{origin.isoformat()}'"
    with duckdb.connect() as con:
        rows = con.execute(
            f"""WITH ao AS (
              SELECT trial_id, CASE WHEN censor_reason = 'unknown' THEN 'lapsed on the origin'
                ELSE 'open on the origin' END AS state
              FROM read_parquet('{as_of_outcomes.as_posix()}')
              WHERE in_window AND ever_in_population AND NOT reversal AND event = 0
            ),
            fo AS (
              SELECT trial_id, CASE
                WHEN reversal THEN 'excluded for a later reversal'
                WHEN event = {EVENT_STOP} THEN 'stopped early'
                WHEN event = {EVENT_COMPLETE} THEN 'completed'
                WHEN censor_reason = 'unknown' THEN 'lapsed, never resolved'
                ELSE 'open at the data cutoff' END AS fate,
                event, event_date
              FROM read_parquet('{final_outcomes.as_posix()}')
            )
            SELECT ao.state, coalesce(fo.fate, 'not in the final cohort') AS fate, count(*) AS n,
              median(date_diff('day', {day}, fo.event_date))
                FILTER (WHERE fo.event IN ({EVENT_STOP}, {EVENT_COMPLETE})) AS median_days
            FROM ao LEFT JOIN fo USING (trial_id) GROUP BY 1, 2 ORDER BY 1, 3 DESC"""
        ).fetchall()
    totals: dict[str, int] = {}
    for state, _, n, _ in rows:
        totals[state] = totals.get(state, 0) + int(n)
    return [
        {
            "state": state,
            "fate": fate,
            "trials": int(n),
            "share": int(n) / totals[state],
            "median_days_after_origin": None if days is None else float(days),
        }
        for state, fate, n, days in rows
    ]


def class_rates(
    time: FloatArray, event: IntArray, horizon: FloatArray, groups: npt.NDArray[Any]
) -> dict[str, dict[str, float]]:
    """The IPCW early-stop rate at the horizon within each censoring group of the evaluation
    rows: what a prediction for that class is compared with."""
    y, w = horizon_labels_and_weights(time, event, horizon, groups)
    out: dict[str, dict[str, float]] = {}
    for name in np.unique(groups):
        member = groups == name
        total = float(w[member].sum())
        out[str(name)] = {
            "rows": int(member.sum()),
            "observed": float((w[member] * y[member]).sum() / total) if total > 0 else float("nan"),
            "censored_before_horizon": float((w[member] == 0).mean()),
        }
    return out


def diagnose_origin(
    cfg: ProjectConfig,
    origin: dt.date,
    final: LandmarkRows,
    table: LabelTable,
    fates: list[dict[str, Any]],
    n_resamples: int = BOOTSTRAP_RESAMPLES,
) -> dict[str, Any]:
    """M0 on every label mix, scored on the evaluation rows of one origin."""
    t = np.datetime64(origin, "D")
    ev, ev_time, ev_event = evaluation_rows(final, t, cfg.walk_forward.eval_window_months)
    classes = np.asarray(ev.features[CENSORING_COLUMN]).astype(str)
    groups = censoring_groups(classes, cfg.evaluation.censoring_min_rows)
    horizons: dict[str, Any] = {}
    scores: dict[tuple[str, int], FloatArray] = {}
    predicted: dict[str, dict[str, dict[str, float]]] = {}
    for name, from_as_of in MIXES:
        time, event, stratum = table.mix(from_as_of)
        model = AalenJohansenModel().fit(time, event, {"stratum": stratum})
        for months in cfg.horizons_months:
            h = horizon_days(ev.landmark_date, months)
            score = model.predict_cif(h, ev.features)
            scores[name, months] = score
            slope, intercept = calibration_slope_intercept(ev_time, ev_event, score, h, groups)
            single_slope, _ = calibration_slope_intercept(ev_time, ev_event, score, h)
            horizons.setdefault(str(months), {})[name] = {
                "training_rows": len(time),
                "calibration_slope": slope,
                "calibration_intercept": intercept,
                "calibration_slope_single_curve": single_slope,
                "auc": ipcw_auc(ev_time, ev_event, score, h, groups),
                "brier": ipcw_brier(ev_time, ev_event, score, h, groups),
            }
            by_class = predicted.setdefault(str(months), {}).setdefault(name, {})
            for label in np.unique(classes):
                by_class[str(label)] = float(score[classes == label].mean())
    result: dict[str, Any] = {
        "origin": origin.isoformat(),
        "n_eval_rows": len(ev),
        "categories": category_counts(table),
        "followup_years": {c: followup_years(table, c) for c in CATEGORIES if c != SAME},
        "horizons": horizons,
        "predicted_by_class": predicted,
        "observed_by_group": {},
        "class_rows": {
            str(c): int(n) for c, n in zip(*np.unique(classes, return_counts=True), strict=True)
        },
        "group_of_class": {str(c): str(groups[classes == c][0]) for c in np.unique(classes)},
        "later_fate": fates,
        "slope_move": {},
        "slope_with_one_class_from_as_of": {},
    }
    for months in cfg.horizons_months:
        h = horizon_days(ev.landmark_date, months)
        result["observed_by_group"][str(months)] = class_rates(ev_time, ev_event, h, groups)
        as_of, hindsight = scores[AS_OF, months], scores[HINDSIGHT, months]

        def move(idx: npt.NDArray[np.int64], h: FloatArray = h, as_of: FloatArray = as_of,
                 hindsight: FloatArray = hindsight) -> float:  # fmt: skip
            new, _ = calibration_slope_intercept(
                ev_time[idx], ev_event[idx], as_of[idx], h[idx], groups[idx]
            )
            old, _ = calibration_slope_intercept(
                ev_time[idx], ev_event[idx], hindsight[idx], h[idx], groups[idx]
            )
            return new - old

        result["slope_move"][str(months)] = cluster_bootstrap(
            ev.trial_id, move, n_resamples, cfg.seeds.default, cfg.evaluation.confidence_level
        )
        # Which class carries the move: the hindsight predictions, with the rows of one
        # class taking the prediction of the as-of fit.
        by_class = result["slope_with_one_class_from_as_of"].setdefault(str(months), {})
        for label in np.unique(classes):
            mixed = np.where(classes == label, as_of, hindsight)
            by_class[str(label)], _ = calibration_slope_intercept(
                ev_time, ev_event, mixed, h, groups
            )
    return result


# The document --------------------------------------------------------------------------------


def _f(value: float, digits: int = 3) -> str:
    return "n/a" if value != value else f"{value:.{digits}f}"


def _pct(value: float, digits: int = 1) -> str:
    return "n/a" if value != value else f"{100 * value:.{digits}f}%"


def _signed(value: float) -> str:
    return "n/a" if value != value else f"{value:+.3f}"


def _origin_section(cfg: ProjectConfig, r: dict[str, Any]) -> list[str]:
    lines = [f"## Origin {r['origin']}", ""]
    lines += [
        f"{r['n_eval_rows']:,} evaluation rows (landmarks in the 12 months from the origin). "
        "Training rows by how their two forms differ:",
        "",
    ]
    rows = []
    for category in CATEGORIES:
        c = r["categories"][category]
        years = r["followup_years"].get(category)
        rows.append(
            [
                f"`{category}`",
                f"{c['rows']:,}",
                f"{c['trials']:,}",
                f"{c['rows_as_of']:,}",
                f"{c['rows_hindsight']:,}",
                "" if years is None else f"{years['as_of']:,.0f}",
                "" if years is None else f"{years['hindsight']:,.0f}",
            ]
        )
    lines += markdown_table(
        ["Category", "Rows", "Trials", "Rows in the as-of cohort", "Rows in hindsight",
         "Years observed, as of", "Years observed, hindsight"],
        rows,
    )  # fmt: skip
    for months in cfg.horizons_months:
        key = str(months)
        lines += ["", f"**M0 at {months} months, by training labels.**", ""]
        base = r["horizons"][key][HINDSIGHT]["calibration_slope"]
        rows = []
        for name, _ in MIXES:
            m = r["horizons"][key][name]
            rows.append(
                [
                    name,
                    f"{m['training_rows']:,}",
                    _f(m["calibration_slope"]),
                    _signed(m["calibration_slope"] - base),
                    _f(m["calibration_intercept"]),
                    _f(m["calibration_slope_single_curve"]),
                    _f(m["auc"], 4),
                    _f(m["brier"], 5),
                ]
            )
        lines += markdown_table(
            ["Training labels", "Training rows", "Calibration slope", "Against hindsight",
             "Intercept", "Slope, single censoring curve", "AUC", "Brier score"],
            rows,
        )  # fmt: skip
        move = r["slope_move"][key]
        lines += [
            "",
            f"The slope moves by {_signed(move['estimate'])} from hindsight labels to as-of "
            f"labels ({int(100 * cfg.evaluation.confidence_level)}% interval "
            f"{_signed(move['ci_low'])} to {_signed(move['ci_high'])}, {move['resamples']} "
            "resamples of the evaluation trials with the two sets of predictions held fixed).",
            "",
            f"Predicted against observed early-stop risk at {months} months, by sponsor class:",
            "",
        ]
        observed = r["observed_by_group"][key]
        rows = []
        for label, n in sorted(r["class_rows"].items(), key=lambda kv: -kv[1]):
            group = r["group_of_class"][label]
            rows.append(
                [
                    label,
                    f"{n:,}",
                    _pct(r["predicted_by_class"][key][HINDSIGHT][label], 2),
                    _pct(r["predicted_by_class"][key][AS_OF][label], 2),
                    _pct(observed[group]["observed"], 2) + ("" if group == label else " (pooled)"),
                    _pct(observed[group]["censored_before_horizon"]),
                    _signed(r["slope_with_one_class_from_as_of"][key][label] - base),
                ]
            )
        lines += markdown_table(
            ["Sponsor class", "Evaluation rows", "Predicted, hindsight labels",
             "Predicted, as-of labels", "Observed (IPCW)", "Censored before the horizon",
             "Slope move if only this class is predicted from as-of labels"],
            rows,
        )  # fmt: skip
    lines += [
        "",
        "**What became of the trials that were censored on the origin** (their outcome in the "
        "final cohort):",
        "",
    ]
    lines += markdown_table(
        ["State on the origin", "Later", "Trials", "Share", "Median days after the origin"],
        [
            [
                f["state"],
                f["fate"],
                f"{f['trials']:,}",
                _pct(f["share"]),
                ""
                if f["median_days_after_origin"] is None
                else f"{f['median_days_after_origin']:,.0f}",
            ]
            for f in r["later_fate"]
        ],
    )
    return [*lines, ""]


def _summary_section(cfg: ProjectConfig, results: list[dict[str, Any]]) -> list[str]:
    """The calibration slope of every label mix at every horizon and origin, and the share
    of the trials censored on the origin that stopped early later, by their state."""
    lines = ["## Summary", ""]
    headers = ["Training labels"]
    headers += [
        f"Slope at {months} months, origin {r['origin'][:4]}"
        for months in cfg.horizons_months
        for r in results
    ]
    rows = []
    for name, _ in MIXES:
        row = [name]
        for months in cfg.horizons_months:
            for r in results:
                by_mix = r["horizons"][str(months)]
                slope = by_mix[name]["calibration_slope"]
                against = slope - by_mix[HINDSIGHT]["calibration_slope"]
                row.append(_f(slope) if name == HINDSIGHT else f"{_f(slope)} ({_signed(against)})")
        rows.append(row)
    lines += markdown_table(headers, rows)
    lines += [
        "",
        "In brackets: the difference from hindsight labels. The step from the first row to the "
        "second is what censoring the trials lapsed on the origin does by itself; the step to "
        "the third row is what the opposite category does; the fourth row has both, and what "
        "is left between it and the last row comes from trials that hindsight excludes for a "
        "later reversal.",
        "",
        "Trials censored on the origin, by their state there, and what the final cohort shows "
        "for them:",
        "",
    ]
    fate_rows = []
    for r in results:
        for state in ("lapsed on the origin", "open on the origin"):
            fates = {f["fate"]: f for f in r["later_fate"] if f["state"] == state}
            total = sum(f["trials"] for f in fates.values())

            def share(fate: str, fates: dict[str, Any] = fates) -> str:
                return _pct(fates[fate]["share"]) if fate in fates else _pct(0.0)

            fate_rows.append(
                [
                    r["origin"],
                    state,
                    f"{total:,}",
                    share("stopped early"),
                    share("completed"),
                    share("lapsed, never resolved"),
                ]
            )
    lines += markdown_table(
        ["Origin", "State on the origin", "Trials", "Stopped early later", "Completed later",
         "Lapsed, never resolved"],
        fate_rows,
    )  # fmt: skip
    return [*lines, ""]


def document(cfg: ProjectConfig, results: list[dict[str, Any]]) -> str:
    lines = [
        "# Calibration diagnosis of M0",
        "",
        "Research demo. Not medical advice. Not for patient decision-making.",
        "",
        "This file is generated by `uv run python -m trialpulse.eval.diagnosis` (CLAUDE.md "
        "Step 9, first task). Do not edit it by hand. It is a diagnosis on the development "
        "origins: nothing is changed by it, and no locked origin is read.",
        "",
        "- **Source:** ClinicalTrials.gov registry records, through the version-history "
        f"dataset `{cfg.dataset.repo_id}` (license CC-BY-NC-4.0), revision "
        f"{cfg.dataset.revision}. Data as of {cfg.dataset.cutoff}. TrialPulse normalizes the "
        "records and derives every number here.",
        "- **M0** is one Aalen-Johansen curve of early-stop risk per lead sponsor class, fitted "
        "on the training rows of an origin and scored on the landmarks of the following 12 "
        "months. A calibration slope above 1 means its predictions for the classes lie closer "
        "together than the observed risks.",
        "- **Hindsight labels** are the final cohort's rows before the origin, cut off at the "
        "origin: what training used before ADR 0016. **As-of labels** come from the cohort "
        "built from versions posted before the origin.",
        "- **Categories.** `lapsed_as_of_origin`: under the UNKNOWN rule (ADR 0014) the trial's "
        "record had lapsed on the origin, so the as-of cohort censors it at its "
        "status-verified date; a later version resolved the lapse, so hindsight observes it "
        "up to the origin. `lapsed_later`: the record lapsed after the origin and was never "
        "resolved, so hindsight censors the trial at a status-verified date before the origin, "
        "while the as-of cohort observes it up to the origin. `reversal_later`: excluded in "
        "hindsight for a reversal posted after the origin.",
        "- **Mixes.** M0 is fitted on hindsight labels with the as-of form swapped in for one "
        "category at a time. All mixes are scored on the same evaluation rows, with the "
        "censoring weights by sponsor class of ADR 0017.",
        "",
    ]
    lines += _summary_section(cfg, results)
    for r in results:
        lines += _origin_section(cfg, r)
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose M0's calibration slope (dev origins).")
    parser.add_argument("--input", type=Path, default=LANDMARKS_PATH)
    parser.add_argument("--training-dir", type=Path, default=TRAINING_DIR)
    parser.add_argument("--doc", type=Path, default=DOC_PATH)
    parser.add_argument("--out", type=Path, default=RESULTS_PATH)
    parser.add_argument("--resamples", type=int, default=BOOTSTRAP_RESAMPLES)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    # Development origins only: the locked origins are never read here (Section 10).
    origins = [o.date for o in cfg.walk_forward.origins if o.role == DEV_ROLE]
    if not origins:
        raise RefusedError("config/project.yaml names no development origin")
    final_landmarks: Path = args.input
    final_outcomes = final_landmarks.parent / OUTCOMES_NAME
    final = load_landmark_rows(final_landmarks)
    results = []
    for origin in origins:
        as_of_landmarks = training_path(args.training_dir, origin)
        as_of_outcomes = as_of_landmarks.parent / OUTCOMES_NAME
        table = label_table(
            final_landmarks, final_outcomes, as_of_landmarks, as_of_outcomes, origin
        )
        fates = later_fate(final_outcomes, as_of_outcomes, origin)
        results.append(diagnose_origin(cfg, origin, final, table, fates, args.resamples))
        slopes = results[-1]["horizons"][str(max(cfg.horizons_months))]
        print(
            f"{origin}: {len(table):,} training rows; slope at {max(cfg.horizons_months)} months "
            f"{slopes[HINDSIGHT]['calibration_slope']:.3f} with hindsight labels, "
            f"{slopes[AS_OF]['calibration_slope']:.3f} with as-of labels"
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    changed = write_text_if_changed(args.doc, document(cfg, results))
    print(f"wrote {args.out}")
    print(f"{args.doc}: {'written' if changed else 'up to date'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
