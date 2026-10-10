"""Cause-specific Cox models at registration (CLAUDE.md Section 9): for interpretation only.

    uv run python -m trialpulse.models.cox

Two proportional-hazards models on landmark 0, the day a trial is first posted, with the
same registration-time covariates: one for the hazard of an early stop (a completion ends
the follow-up as censoring) and one for the hazard of completion (an early stop does). A
hazard ratio says how much faster trials with a characteristic reach that end, everything
else in the model equal. The two are read together: the cumulative incidence of an early
stop depends on both, because a trial that completes sooner has less time left to stop.

This is not a predictive competitor (Section 9). No prediction is scored here, and nothing
is read from a locked origin: the rows are the landmark 0 rows of the cohort as of the
later development origin, with labels as of that origin (ADR 0016).

**Proportional hazards.** A Cox model assumes each hazard ratio is the same at every time
since registration. Two checks are reported per covariate: the test on scaled Schoenfeld
residuals against the rank of the event times, and, because with this many trials the test
rejects for differences too small to matter, the hazard ratio fitted separately on the
first year after registration and on the time after it. A ratio that changes sides or size
between the two windows is an average over time, not a constant.

The test is computed here (`schoenfeld_test`), with the statistic lifelines uses, because
the run time of `lifelines.statistics.proportional_hazard_test` grows about with the
square of the number of trials: seconds for 20,000, minutes per cause for the 118,000 of
this analysis, against a fraction of a second here. The tests compare the two on smaller
data. One difference: follow-up is in whole days, so many trials end on the same day, and
here they share one rank (lifelines ranks them in row order, which lets the order of the
rows move the statistic).

The three hazard ratios of a covariate (whole follow-up, first year, later) come from
three fits with all covariates. With correlated covariates the overall ratio need not lie
between the two window ratios.

The command writes docs/cox_report.md and data/results/cox/cox_l0.json, then logs the run
to MLflow (`trialpulse.tracking`). If the run cannot be logged, both files stay and the
command exits with code 5.
"""

import argparse
import datetime as dt
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from lifelines import CoxPHFitter

from trialpulse import tracking
from trialpulse.cli import RefusedError, run
from trialpulse.cohort.build import COHORT_DIR
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.dates import days_between
from trialpulse.eval import EVENT_COMPLETE, EVENT_STOP
from trialpulse.eval.rows import FloatArray, IntArray, load_landmark_rows, training_path
from trialpulse.features import frame
from trialpulse.features.build import FEATURES_DIR
from trialpulse.reports import markdown_table, write_text_if_changed

DOC_PATH = REPO_ROOT / "docs" / "cox_report.md"
RESULTS_PATH = REPO_ROOT / "data" / "results" / "cox" / "cox_l0.json"
DEV_ROLE = "dev"
CAUSES: tuple[tuple[int, str], ...] = ((EVENT_STOP, "early stop"), (EVENT_COMPLETE, "completion"))
SPLIT_DAYS = 365  # the two windows of the proportional-hazards check
MIN_LEVEL_ROWS = 500  # a sponsor class with fewer rows joins the other small classes
MIN_MISSING_SHARE = 0.01  # a number missing more often than this gets a "not given" flag
PHASE_GROUP = {
    "EARLY_PHASE1": "phase 1",
    "PHASE1": "phase 1",
    "PHASE1_PHASE2": "phase 2",
    "PHASE2": "phase 2",
    "PHASE2_PHASE3": "phase 3",
    "PHASE3": "phase 3",
    "PHASE4": "phase 4",
}
INTERVENTION_FLAGS = ("drug", "biological", "device", "procedure", "behavioral")


@dataclass(frozen=True)
class Term:
    """One column of the design: its name in the model, how the report words it, and the
    reference it is compared with (empty for a number)."""

    column: str
    label: str
    reference: str = ""


def _text(values: npt.NDArray[Any]) -> npt.NDArray[Any]:
    return np.array(
        ["" if v is None or v != v else str(v) for v in np.asarray(values, dtype=object)],
        dtype=object,
    )


def _flag(values: npt.NDArray[Any]) -> FloatArray:
    """A flag as 1.0 or 0.0; a missing value counts as 0."""
    out = np.asarray(values, dtype=np.float64)
    return np.where(np.isnan(out), 0.0, out)


def covariates(features: Mapping[str, npt.NDArray[Any]]) -> tuple[pd.DataFrame, list[Term]]:
    """The design matrix of the Cox models from the features at landmark 0, and what each
    column means. Categories are compared with a fixed reference level (the most common
    one, except for status, where RECRUITING is the reference); numbers are scaled so that a
    hazard ratio reads per doubling, per year or per 10 points."""
    columns: dict[str, FloatArray] = {}
    terms: list[Term] = []

    def add(column: str, values: npt.NDArray[Any], label: str, reference: str = "") -> None:
        columns[column] = np.asarray(values, dtype=np.float64)
        terms.append(Term(column, label, reference))

    def levels(name: str, source: npt.NDArray[Any], wanted: Mapping[str, str], reference: str,
               rest: str | None) -> None:  # fmt: skip
        """One 0/1 column per level in `wanted` (value -> label); every other value that is
        not the reference goes to `rest`, if a label for it is given."""
        text = _text(source)
        covered = np.isin(text, [*wanted, reference])
        for value, label in wanted.items():
            add(f"{name}_{value.lower()}", text == value, label, reference)
        if rest is not None:
            add(f"{name}_rest", ~covered, rest, reference)

    sponsor = _text(features["sponsor_class"])
    names, counts = np.unique(sponsor, return_counts=True)
    large = {
        str(n): f"sponsor class {n}"
        for n, c in sorted(zip(names, counts, strict=True), key=lambda nc: -nc[1])
        if n not in ("OTHER", "") and c >= MIN_LEVEL_ROWS
    }
    levels("sponsor_class", sponsor, large, "OTHER", "sponsor class: any smaller class")
    phase = np.array([PHASE_GROUP.get(v, "none") for v in _text(features["title_phase"])])
    for group in ("phase 1", "phase 2", "phase 3", "phase 4"):
        add(f"title_{group.replace(' ', '')}", phase == group, f"title states {group}",
            "no phase in the title")  # fmt: skip
    levels("allocation", features["allocation"], {"NON_RANDOMIZED": "not randomized"},
           "RANDOMIZED", "allocation not applicable or not given")  # fmt: skip
    masking = _text(features["masking"])
    add("masked", np.isin(masking, ["SINGLE", "DOUBLE", "TRIPLE", "QUADRUPLE"]),
        "masked (single to quadruple)", "open label")  # fmt: skip
    add("masking_not_given", masking == "", "masking not given", "open label")
    levels("model", features["intervention_model"],
           {"SINGLE_GROUP": "single group", "CROSSOVER": "crossover"},
           "PARALLEL", "another intervention model, or none given")  # fmt: skip
    levels("purpose", features["primary_purpose"], {"PREVENTION": "purpose: prevention"},
           "TREATMENT", "another purpose, or none given")  # fmt: skip
    levels("status", features["status"], {"NOT_YET_RECRUITING": "not yet recruiting"},
           "RECRUITING", "another open status")  # fmt: skip
    add("healthy_volunteers", _flag(features["healthy_volunteers"]), "accepts healthy volunteers",
        "does not")  # fmt: skip
    minimum_age = np.asarray(features["minimum_age_years"], dtype=np.float64)
    add("children_eligible", np.isnan(minimum_age) | (minimum_age < 18),
        "open to participants under 18", "adults only")  # fmt: skip
    add("registered_after_start", _flag(features["registered_after_start"]),
        "registered after the start month", "registered before or in it")  # fmt: skip
    for kind in INTERVENTION_FLAGS:
        add(f"intervention_{kind}", _flag(features[f"intervention_{kind}"]),
            f"lists a {kind} intervention", "does not")  # fmt: skip

    def number(column: str, values: FloatArray, label: str) -> None:
        missing = np.isnan(values)
        fill = float(np.nanmedian(values)) if (~missing).any() else 0.0
        add(column, np.where(missing, fill, values), label)
        if missing.mean() > MIN_MISSING_SHARE:
            add(f"{column}_not_given", missing, f"{label.split(',')[0]} not given", "given")

    enrollment = np.asarray(features["enrollment_count"], dtype=np.float64)
    number("enrollment_log2", np.log2(enrollment + 1.0), "enrollment target, per doubling")
    duration = np.asarray(features["planned_duration_months"], dtype=np.float64) / 12.0
    number("planned_years", np.clip(duration, 0.0, 15.0), "planned duration, per year")
    prior = np.asarray(features["sponsor_prior_registrations"], dtype=np.float64)
    add("sponsor_registrations_log2", np.log2(np.where(np.isnan(prior), 0.0, prior) + 1.0),
        "sponsor's earlier registrations, per doubling")  # fmt: skip
    rate = np.asarray(features["sponsor_stop_rate"], dtype=np.float64)
    number("sponsor_stop_rate_10", rate * 10.0, "sponsor's smoothed early-stop rate, per 10 points")
    year = np.asarray(features["registration_year"], dtype=np.float64)
    number("registration_year", year - float(np.nanmin(year)), "registration year, per year")
    design = pd.DataFrame(columns)
    constant = [name for name in design.columns if design[name].nunique() < 2]
    return design.drop(columns=constant), [t for t in terms if t.column not in constant]


def _summary(fitter: CoxPHFitter) -> dict[str, dict[str, float]]:
    table = fitter.summary
    return {
        str(name): {
            "hazard_ratio": float(row["exp(coef)"]),
            "ci_low": float(row["exp(coef) lower 95%"]),
            "ci_high": float(row["exp(coef) upper 95%"]),
            "p": float(row["p"]),
        }
        for name, row in table.iterrows()
    }


def schoenfeld_test(
    x: FloatArray,
    time: FloatArray,
    event: npt.NDArray[np.bool_],
    coef: FloatArray,
    variance: FloatArray,
) -> tuple[FloatArray, FloatArray]:
    """The test of proportional hazards on scaled Schoenfeld residuals, per covariate.

    The Schoenfeld residual of an event is its covariates minus their mean over the trials
    still at risk on that day, each weighted by its fitted relative hazard. Under
    proportional hazards the residuals, scaled by the variance of the coefficients, have no
    trend over time. The statistic is the squared covariance of the scaled residuals with
    the rank of the event among all events, standardized: chi-squared with one degree of
    freedom per covariate (Grambsch and Therneau; the form lifelines uses with its "rank"
    transform). Trials that end on the same day share one risk set (Breslow) and one rank,
    the mean of their positions, so the order of the rows cannot move the statistic."""
    order = np.argsort(time, kind="stable")
    xs, ts, es = x[order], time[order], np.asarray(event, dtype=bool)[order]
    score = xs @ coef
    weight = np.exp(score - score.max())
    # Sums over the rows from each position to the end: the trials at risk from that day on.
    at_risk = np.cumsum(weight[::-1])[::-1]
    at_risk_x = np.cumsum((weight[:, None] * xs)[::-1], axis=0)[::-1]
    first = np.searchsorted(ts, ts, side="left")  # the first row of each row's day
    residual = (xs - at_risk_x[first] / at_risk[first][:, None])[es]
    n_events = len(residual)
    scaled = n_events * residual @ variance
    position = np.arange(1, n_events + 1, dtype=np.float64)
    _, day, per_day = np.unique(ts[es], return_inverse=True, return_counts=True)
    rank = (np.bincount(day, weights=position) / per_day)[day]
    centered = rank - rank.mean()
    statistic: FloatArray = (centered @ scaled) ** 2 / (
        n_events * np.diag(variance) * (centered**2).sum()
    )
    p_value = np.array([math.erfc(math.sqrt(max(s, 0.0) / 2.0)) for s in statistic])
    return statistic, p_value


def fit_cause(
    design: pd.DataFrame, time: FloatArray, event: IntArray, cause: int
) -> dict[str, Any]:
    """The cause-specific model for one cause: every other end of follow-up is censoring.
    Returns the hazard ratios with 95% intervals, the Schoenfeld-residual test per
    covariate, and the hazard ratios of the first year and of the time after it."""
    data = design.copy()
    data["time"] = time
    data["event"] = (event == cause).astype(int)
    fitter = CoxPHFitter().fit(data, duration_col="time", event_col="event")
    ratios = _summary(fitter)
    names = list(design.columns)
    statistic, p_value = schoenfeld_test(
        design.to_numpy(dtype=np.float64),
        time,
        event == cause,
        fitter.params_[names].to_numpy(dtype=np.float64),
        fitter.variance_matrix_.loc[names, names].to_numpy(dtype=np.float64),
    )
    for j, name in enumerate(names):
        ratios[name]["schoenfeld_statistic"] = float(statistic[j])
        ratios[name]["schoenfeld_p"] = float(p_value[j])
    early = data.copy()
    early["event"] = ((event == cause) & (time <= SPLIT_DAYS)).astype(int)
    early["time"] = np.minimum(time, float(SPLIT_DAYS))
    # The later window: the trials still under observation after the split, on the clock
    # that starts at the split. They all enter at the same moment, so this is the model
    # with late entry at the split, without the cost of fitting it as one.
    late = data[data["time"] > SPLIT_DAYS].copy()
    late["time"] = late["time"] - float(SPLIT_DAYS)
    windows = {
        "first_year": CoxPHFitter().fit(early, duration_col="time", event_col="event"),
        "later": CoxPHFitter().fit(late, duration_col="time", event_col="event"),
    }
    for window, fitted in windows.items():
        for name, values in _summary(fitted).items():
            ratios[name][f"hazard_ratio_{window}"] = values["hazard_ratio"]
    return {
        "rows": len(data),
        "events": int(data["event"].sum()),
        "events_first_year": int(early["event"].sum()),
        "events_later": int(late["event"].sum()),
        "concordance": float(fitter.concordance_index_),
        "ratios": ratios,
    }


def analyze(
    features: Mapping[str, npt.NDArray[Any]], time: FloatArray, event: IntArray
) -> dict[str, Any]:
    """Both cause-specific models on rows with a positive follow-up time."""
    usable = time > 0
    design, terms = covariates({name: values[usable] for name, values in features.items()})
    return {
        "rows": int(usable.sum()),
        "rows_without_follow_up": int((~usable).sum()),
        "censored": int((event[usable] == 0).sum()),
        "terms": [{"column": t.column, "label": t.label, "reference": t.reference} for t in terms],
        "causes": {
            label: fit_cause(design, time[usable], event[usable], cause) for cause, label in CAUSES
        },
    }


# The document --------------------------------------------------------------------------------


def _ratio(value: float) -> str:
    return "n/a" if value != value else f"{value:.2f}"


def _p(value: float) -> str:
    if value != value:
        return "n/a"
    return "<0.001" if value < 0.001 else f"{value:.3f}"


def _cause_section(
    label: str, result: dict[str, Any], terms: Sequence[dict[str, str]]
) -> list[str]:
    cause = result["causes"][label]
    lines = [
        f"## Hazard of {label}",
        "",
        f"{cause['events']:,} trials reached this end among {cause['rows']:,}: "
        f"{cause['events_first_year']:,} in the first year after registration and "
        f"{cause['events_later']:,} later. Concordance on the same rows: "
        f"{cause['concordance']:.3f} (a description of fit, not a test of prediction).",
        "",
    ]
    rows = []
    for term in terms:
        r = cause["ratios"][term["column"]]
        rows.append(
            [
                term["label"],
                term["reference"] or "(a number)",
                _ratio(r["hazard_ratio"]),
                f"{_ratio(r['ci_low'])} to {_ratio(r['ci_high'])}",
                _p(r["p"]),
                _ratio(r["hazard_ratio_first_year"]),
                _ratio(r["hazard_ratio_later"]),
                f"{r['schoenfeld_statistic']:.1f}",
                _p(r["schoenfeld_p"]),
            ]
        )
    lines += markdown_table(
        ["Characteristic", "Compared with", "Hazard ratio", "95% interval", "p",
         "Hazard ratio, first year", "Hazard ratio, later", "Schoenfeld statistic",
         "Schoenfeld p"],
        rows,
    )  # fmt: skip
    return [*lines, ""]


def document(cfg: ProjectConfig, origin: dt.date, result: dict[str, Any]) -> str:
    lines = [
        "# Cause-specific Cox models at registration",
        "",
        "Research demo. Not medical advice. Not for patient decision-making.",
        "",
        "This file is generated by `uv run python -m trialpulse.models.cox` (CLAUDE.md Step 9). "
        "Do not edit it by hand.",
        "",
        "- **Source:** ClinicalTrials.gov registry records, through the version-history "
        f"dataset `{cfg.dataset.repo_id}` (license CC-BY-NC-4.0), revision "
        f"{cfg.dataset.revision}. Data as of {cfg.dataset.cutoff}. TrialPulse normalizes the "
        "records and derives every number here.",
        f"- **Rows:** the {result['rows']:,} interventional trials with a landmark on the day "
        f"they were first posted, in the cohort as of {origin} (the later development origin): "
        "what the registry showed before that day, and nothing after it (ADR 0016). "
        f"{result['censored']:,} of them had reached neither end by then and are censored. No "
        "locked origin is read.",
        "- **For interpretation only.** These models are not scored as predictions and do not "
        "compete with M0 to M4 (Section 9). A hazard ratio is an association in registry "
        "records, not a cause, and says nothing about whether a treatment works.",
        "- **Two models, one set of covariates.** The first table is the hazard of an early "
        "stop, with completion as censoring; the second the hazard of completion, with an "
        "early stop as censoring. The two are read together: the share of trials that stop "
        "early is higher where the first hazard is higher or the second is lower.",
        "- **Reading a row.** A hazard ratio of 1.25 means that end is reached at 1.25 times "
        "the rate of the comparison group, the other characteristics equal. Numbers are scaled "
        "as the row says (per doubling, per year, per 10 points).",
        f"- **Proportional hazards.** The two right-hand pairs of columns check the model's "
        f"assumption that a ratio is constant over time: the ratio in the first {SPLIT_DAYS} "
        "days after registration against the ratio afterwards, and the test on scaled "
        "Schoenfeld residuals. With this many trials the test rejects for small differences, "
        "so the two ratios are the practical reading: where they differ, the single ratio "
        "summarizes a ratio that changes over time. The three ratios of a row come from three "
        "fits with all characteristics, so the overall ratio can lie slightly outside the two "
        "others. Trials that end on the same day share one rank in the test.",
        "",
    ]
    for _, label in CAUSES:
        lines += _cause_section(label, result, result["terms"])
    return "\n".join(lines).rstrip("\n") + "\n"


def load_rows(
    cfg: ProjectConfig, origin: dt.date, features_dir: Path, cohort_dir: Path
) -> tuple[dict[str, npt.NDArray[Any]], FloatArray, IntArray]:
    """The landmark 0 rows of the cohort as of an origin: features, follow-up time in days
    and event."""
    matrix = frame.load(cfg, origin, "training", features_dir, cohort_dir, landmark_indices=(0,))
    rows = load_landmark_rows(training_path(cohort_dir / "training", origin))
    rows = rows.subset(rows.landmark_index == 0)
    if list(rows.trial_id) != list(matrix["trial_id"]):
        raise RefusedError(
            "the features and the cohort as of the origin hold different landmark 0 rows; "
            "rebuild both: uv run python -m trialpulse.cohort.build, then "
            "uv run python -m trialpulse.features.build"
        )
    time = days_between(rows.landmark_date, rows.event_date)
    return frame.as_arrays(matrix), time, rows.event


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cause-specific Cox models at landmark 0.")
    parser.add_argument("--features-dir", type=Path, default=FEATURES_DIR)
    parser.add_argument("--cohort-dir", type=Path, default=COHORT_DIR)
    parser.add_argument("--doc", type=Path, default=DOC_PATH)
    parser.add_argument("--out", type=Path, default=RESULTS_PATH)
    tracking.add_arguments(parser)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    started = tracking.git_state()
    # The later development origin: the most data that no locked origin's training set adds.
    dev = [o.date for o in cfg.walk_forward.origins if o.role == DEV_ROLE]
    if not dev:
        raise RefusedError("config/project.yaml names no development origin")
    origin = max(dev)
    features, time, event = load_rows(cfg, origin, args.features_dir, args.cohort_dir)
    result = {"origin": origin.isoformat(), **analyze(features, time, event)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    changed = write_text_if_changed(args.doc, document(cfg, origin, result))
    for _, label in CAUSES:
        cause = result["causes"][label]
        print(f"{label}: {cause['events']:,} events among {cause['rows']:,} rows")
    print(f"wrote {args.out}")
    print(f"{args.doc}: {'written' if changed else 'up to date'}")
    if not args.no_track:
        metrics = {
            f"{label.replace(' ', '_')}_{name}": float(result["causes"][label][name])
            for _, label in CAUSES
            for name in ("concordance", "events")
        }
        try:
            result["tracking"] = tracking.log_run(
                "cox-l0",
                {"model": "cause-specific Cox at landmark 0", "origin": origin.isoformat(),
                 "rows": result["rows"], "covariates": len(result["terms"])},
                metrics,
                [args.out, args.doc],
                cfg,
                tags={"step": "9", "kind": "interpretation"},
                local=args.local_tracking,
                state=started,
            )  # fmt: skip
        except tracking.TrackingError as exc:  # both files are written: say so and fail
            print(f"failed: the run was not logged to MLflow ({exc}); its results are in "
                  f"{args.out}", file=sys.stderr)  # fmt: skip
            return tracking.FAILED_EXIT_CODE
        args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"logged to {result['tracking']['store_description']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
