"""docs/results_dev.md: the development results (CLAUDE.md Step 9, extended in Step 10).

    uv run python -m trialpulse.eval.results_dev

The document is written from the result files of the walk-forward runs on the development
origins (data/results/walkforward/<model>_dev.json), of the Cox analysis and of the
calibration diagnosis. It computes nothing itself and reads no cohort or feature file, so it
cannot touch a locked origin; it refuses a result file that holds one.

Models are compared at two levels. Pooled over all landmark indices, where only models of
every landmark are comparable; and at landmark 0, registration, where M1 lives and where
every model can be read (Step 11's "M4 beats M1 at L0"). Section 6 asks for the metrics
per horizon, per landmark index and pooled, and for calibration by risk decile: the
document holds all of them.

From Step 10 on it also names the champion by the selection rule of Section 9, gives the
difference between two models with the paired interval `trialpulse.eval.compare` computed,
the ablation of M4, calibration by registration year and by sponsor class over all
landmark indices, the run time and size of each model, and the settings the tuning chose.
"""

import argparse
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from trialpulse.cli import RefusedError, run
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.models import discrete_time
from trialpulse.reports import markdown_table, write_text_if_changed

DOC_PATH = REPO_ROOT / "docs" / "results_dev.md"
RESULTS_ROOT = REPO_ROOT / "data" / "results"
DEV = "dev"
MODELS: dict[str, str] = {
    "m0": "Aalen-Johansen curve per sponsor class: base rates only",
    "m1": "static LightGBM classifier at registration (landmark 0), all feature families",
    "m2": "discrete-time logistic regression on the sponsor class and the design features",
    "m3": "M2 plus the amendment signals",
    "m4": "discrete-time LightGBM, all feature families",
    "m4-design": "ablation of M4: its learner on the sponsor class and the design features",
    "m4-amendments": "ablation of M4: plus the amendment signals",
    "m4-sponsor": "ablation of M4: plus the sponsor record and competition",
}
# The models of the main tables; the ablation rungs have a section of their own.
MAIN: tuple[str, ...] = ("m0", "m1", "m2", "m3", "m4")
ABLATION: tuple[str, ...] = ("m4-design", "m4-amendments", "m4-sponsor", "m4")
TUNED: tuple[str, ...] = ("m2", "m3", "m4")
PRIMARY_MONTHS = "12"  # the horizon of the selection rule (Section 9)
FIRST_LANDMARK = "0"
TOP_RATIOS = 8


def _f(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None or value != value else f"{value:.{digits}f}"


def _pct(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None or value != value else f"{100 * value:.{digits}f}%"


def _interval(metric: dict[str, Any], digits: int = 3) -> str:
    return (
        f"{_f(metric['estimate'], digits)} ({_f(metric['ci_low'], digits)} to "
        f"{_f(metric['ci_high'], digits)})"
    )


def load_results(root: Path) -> dict[str, dict[str, Any]]:
    """The development result of every model that has one, in ladder order."""
    found: dict[str, dict[str, Any]] = {}
    for model in MODELS:
        path = root / "walkforward" / f"{model}_{DEV}.json"
        if not path.is_file():
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        roles = {origin["role"] for origin in result["origins"]}
        if roles != {DEV} or result.get("unlock"):
            raise RefusedError(
                f"{path} holds origins with roles {sorted(roles)}: this document is for the "
                "development origins only"
            )
        found[model] = result
    if not found:
        raise RefusedError(
            f"no development result under {root / 'walkforward'}; run for example: "
            "uv run python -m trialpulse.eval.walkforward --model m0 --origins dev"
        )
    return found


def _slice(origin: dict[str, Any], months: str, landmark: str | None) -> dict[str, Any] | None:
    """The pooled slice of an origin and horizon, or the slice of one landmark index. A
    model of landmark 0 only has the same rows in both."""
    by = origin["horizons"][months]
    if landmark is None:
        return by["pooled"] if origin.get("landmark_indices", "all") == "all" else None
    found: dict[str, Any] | None = by["by_landmark_index"].get(landmark)
    return found


def _metric_rows(
    results: dict[str, dict[str, Any]], months: str, landmark: str | None
) -> list[list[str]]:
    rows = []
    for model, result in results.items():
        for origin in result["origins"]:
            s = _slice(origin, months, landmark)
            if s is None:
                continue
            rows.append(
                [
                    model.upper(),
                    origin["origin"][:4],
                    f"{s['n_rows']:,}",
                    _interval(s["auc"]),
                    _interval(s["brier"], 4),
                    _interval(s["lift"], 2),
                    _f(s["calibration_slope"]),
                    _f(s["calibration_intercept"]),
                    _f(s["single_censoring_curve"]["auc"]),
                ]
            )
    return rows


METRIC_HEADERS = [
    "Model", "Origin", "Rows", "AUC (95% interval)", "Brier score", "Lift at 10%",
    "Calibration slope", "Intercept", "AUC, single censoring curve",
]  # fmt: skip


def _by_index_section(results: dict[str, dict[str, Any]], months: str) -> list[str]:
    """Every landmark index of the models that score them all."""
    rows = []
    for model, result in results.items():
        for origin in result["origins"]:
            if origin.get("landmark_indices", "all") != "all":
                continue
            by_index = origin["horizons"][months]["by_landmark_index"]
            for index in sorted(by_index, key=int):
                s = by_index[index]
                rows.append(
                    [
                        model.upper(),
                        origin["origin"][:4],
                        index,
                        f"{s['n_rows']:,}",
                        _interval(s["auc"]),
                        _interval(s["brier"], 4),
                        _interval(s["lift"], 2),
                        _f(s["calibration_slope"]),
                        _f(s["calibration_intercept"]),
                    ]
                )
    if not rows:
        return []
    headers = [METRIC_HEADERS[0], METRIC_HEADERS[1], "Landmark index", *METRIC_HEADERS[2:8]]
    return [f"## By landmark index, {months} months", "", *markdown_table(headers, rows), ""]


def _first_landmark_slice(result: dict[str, Any], day: str, months: str) -> dict[str, Any] | None:
    """The landmark 0 slice of a model at one origin and horizon, or nothing if the model
    has no result for that origin."""
    origin = next((o for o in result["origins"] if o["origin"] == day), None)
    return None if origin is None else _slice(origin, months, FIRST_LANDMARK)


def _decile_section(results: dict[str, dict[str, Any]], months: str) -> list[str]:
    """Predicted against observed risk by decile of each model's own predictions, at
    landmark 0."""
    lines: list[str] = []
    origins = [o["origin"] for o in next(iter(results.values()))["origins"]]
    for day in origins:
        tables = {}
        for model, result in results.items():
            s = _first_landmark_slice(result, day, months)
            if s is not None and s.get("calibration_table"):
                tables[model] = {row["bin"]: row for row in s["calibration_table"]}
        if not tables:
            continue
        headers = ["Decile of predicted risk"]
        for model in tables:
            name = model.upper()
            headers += [f"Rows, {name}", f"Predicted, {name}", f"Observed, {name}"]
        rows = []
        for decile in sorted({b for table in tables.values() for b in table}):
            row = [str(decile)]
            for table in tables.values():
                b = table.get(decile)
                row += (
                    ["", "", ""]
                    if b is None
                    else [f"{b['n']:,}", _pct(b["mean_predicted"]), _pct(b["observed_cif"])]
                )
            rows.append(row)
        lines += [f"Origin {day[:4]}, {months} months:", "", *markdown_table(headers, rows), ""]
    return lines


def _mean_auc(results: dict[str, dict[str, Any]], months: str, landmark: str | None) -> list[str]:
    lines = []
    for model, result in results.items():
        values = [
            s["auc"]["estimate"]
            for origin in result["origins"]
            if (s := _slice(origin, months, landmark)) is not None
        ]
        if values:
            lines.append(f"{model.upper()} {_f(sum(values) / len(values), 4)}")
    return lines


def _calibration_section(
    results: dict[str, dict[str, Any]],
    months: str,
    landmark: str | None,
    key: str = "calibration_by_group",
    label: str = "Sponsor class",
) -> list[str]:
    """Predicted against observed risk by a label (the sponsor class, or the registration
    year), for every model that has the slice: landmark 0, or all landmark indices."""
    lines: list[str] = []
    origins = [o["origin"] for o in next(iter(results.values()))["origins"]]
    for day in origins:
        tables = {}
        for model, result in results.items():
            origin = next((o for o in result["origins"] if o["origin"] == day), None)
            s = None if origin is None else _slice(origin, months, landmark)
            if s is not None and s.get(key):
                tables[model] = {row["group"]: row for row in s[key]}
        if not tables:
            continue
        first = next(iter(tables.values()))
        headers = [label, "Rows", "Observed (IPCW)", "Censored before the horizon"]
        headers += [f"Predicted, {model.upper()}" for model in tables]
        rows = []
        for group, row in first.items():
            rows.append(
                [
                    group,
                    f"{row['n']:,}",
                    _pct(row["observed"]),
                    _pct(row["censored_before_horizon"], 1),
                    *(
                        _pct(table[group]["mean_predicted"]) if group in table else ""
                        for table in tables.values()
                    ),
                ]
            )
        lines += [f"Origin {day[:4]}, {months} months:", "", *markdown_table(headers, rows), ""]
    return lines


def _mean(results: dict[str, Any], months: str, landmark: str | None, key: str) -> float | None:
    """The mean over the development origins of one number of a model's slice."""
    values = []
    for origin in results["origins"]:
        s = _slice(origin, months, landmark)
        if s is None:
            return None
        value = s[key]
        values.append(value["estimate"] if isinstance(value, dict) else value)
    return float(sum(values) / len(values)) if values else None


def champion(results: dict[str, dict[str, Any]]) -> str | None:
    """Section 9: the model with the best mean AUC at 12 months over the development
    origins, among the models that score every landmark index; ties by the Brier score at
    12 months, then by the calibration slope closest to 1."""
    ranked = []
    for model in MAIN:
        if model not in results:
            continue
        auc = _mean(results[model], PRIMARY_MONTHS, None, "auc")
        brier = _mean(results[model], PRIMARY_MONTHS, None, "brier")
        slope = _mean(results[model], PRIMARY_MONTHS, None, "calibration_slope")
        if auc is None or brier is None or slope is None:
            continue
        ranked.append(((-auc, brier, abs(slope - 1.0)), model))
    return min(ranked)[1] if len(ranked) > 1 else None


def _ladder_section(results: dict[str, dict[str, Any]], horizons: Sequence[str]) -> list[str]:
    rows = []
    for model in MAIN:
        if model not in results:
            continue
        cells = [model.upper()]
        for landmark in (None, FIRST_LANDMARK):
            for months in horizons:
                cells.append(_f(_mean(results[model], months, landmark, "auc"), 4))
        cells.append(_f(_mean(results[model], PRIMARY_MONTHS, None, "brier"), 5))
        cells.append(_f(_mean(results[model], PRIMARY_MONTHS, None, "calibration_slope")))
        rows.append(cells)
    best = champion(results)
    if best is None:
        return []
    headers = ["Model"]
    headers += [f"AUC, {m} months, all landmark indices" for m in horizons]
    headers += [f"AUC, {m} months, landmark 0" for m in horizons]
    headers += ["Brier score, 12 months, all", "Calibration slope, 12 months, all"]
    return [
        "## The ladder and the champion",
        "",
        "Means over the development origins. A model of landmark 0 only has no number over "
        "all landmark indices.",
        "",
        *markdown_table(headers, rows),
        "",
        "By the selection rule declared in Section 9 (the best mean AUC at 12 months over the "
        "development origins, among the models that score every landmark index; ties by the "
        f"Brier score, then by the calibration slope closest to 1), the champion is "
        f"**{best.upper()}**. The paired differences below say which steps of the ladder the "
        "evaluation trials can tell from no difference.",
        "",
    ]


def _delta(part: dict[str, Any] | None, key: str, digits: int = 4) -> str:
    if part is None:
        return ""
    d = part[key]
    return f"{d['estimate']:+.{digits}f} ({d['ci_low']:+.{digits}f} to {d['ci_high']:+.{digits}f})"


SAME_SCORES = 1e-9  # two computations of one metric from the same scores


def _reported(
    everything: dict[str, dict[str, Any]], model: str, day: str, months: str, part: str
) -> dict[str, Any] | None:
    """The slice of a model's result file that a slice of the comparison stands beside."""
    origins = everything.get(model, {}).get("origins", [])
    origin = next((o for o in origins if o["origin"] == day), None)
    return None if origin is None else _slice(origin, months, None if part == "all" else part)


def _same(compared: float, reported: float) -> bool:
    """Two computations of one metric from the same scores. Two values that are both not a
    number are the same; one that is not a number beside one that is, never."""
    if math.isnan(compared) or math.isnan(reported):
        return math.isnan(compared) and math.isnan(reported)
    return abs(compared - reported) <= SAME_SCORES


def _compared_cells(pair: dict[str, Any]) -> list[tuple[str, str, str | None, Any]]:
    """Every (horizon, slice, origin, numbers of that origin) of one pair of the comparison.
    A slice without numbers per origin gives one entry without an origin."""
    cells: list[tuple[str, str, str | None, Any]] = []
    for months, slices in pair["horizons"].items():
        for part, values in slices.items():
            by_origin = values.get("by_origin") or {None: None}
            cells += [(months, part, day, here) for day, here in by_origin.items()]
    return cells


def check_compared(path: Path, everything: dict[str, dict[str, Any]]) -> None:
    """The paired differences must come from the scores behind the result files. A
    prediction file does not say which run wrote it, so a model run again after the
    comparison would leave differences between new and old scores in this document. For
    every pair, origin, horizon and slice (each landmark index too), the AUC and the Brier
    score of each model as the comparison computed them are held against that model's
    result file. A cell that cannot be checked (no numbers per origin, no result file, no
    such slice) refuses the document as a cell that differs does."""
    if not path.is_file():
        return
    again = "Run the comparison again: uv run python -m trialpulse.eval.compare"
    for pair in json.loads(path.read_text(encoding="utf-8"))["pairs"]:
        name = f"{pair['model'].upper()} minus {pair['baseline'].upper()}"
        for months, part, day, here in _compared_cells(pair):
            where = "all landmark indices" if part == "all" else f"landmark {part}"
            if day is None:
                raise RefusedError(
                    f"{path} holds no numbers per origin for {name} ({months} months, {where}), "
                    f"so it cannot be held against the result files. {again}"
                )
            for role in ("model", "baseline"):
                s = _reported(everything, pair[role], day, months, part)
                if s is None:
                    raise RefusedError(
                        f"{path} compares {pair[role].upper()} at origin {day} ({months} months, "
                        f"{where}), and no result file holds that slice. Run that model again, "
                        "then the comparison"
                    )
                for metric in ("auc", "brier"):
                    compared, reported = here[f"{metric}_{role}"], s[metric]["estimate"]
                    if not _same(compared, reported):
                        raise RefusedError(
                            f"{path} was not computed from the scores behind the result file of "
                            f"{pair[role].upper()} (origin {day}, {months} months, {where}: "
                            f"{metric} {compared:.12g} in the comparison, {reported:.12g} in "
                            f"the result file). {again}"
                        )


def _compare_section(path: Path, horizons: Sequence[str]) -> list[str]:
    if not path.is_file():
        return []
    result = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for pair in result["pairs"]:
        cells = [f"{pair['model'].upper()} minus {pair['baseline'].upper()}", pair["what"]]
        for months in horizons:
            slices = pair["horizons"][months]
            cells += [_delta(slices.get("all"), "delta_auc"), _delta(slices.get("0"), "delta_auc")]
        cells.append(_delta(pair["horizons"][PRIMARY_MONTHS].get("all"), "delta_brier", 5))
        rows.append(cells)
    headers = ["Difference", "What it shows"]
    for months in horizons:
        headers += [
            f"AUC, {months} months, all landmark indices",
            f"AUC, {months} months, landmark 0",
        ]
    headers.append("Brier score, 12 months, all landmark indices")
    lines = [
        "## Differences between models, with paired intervals",
        "",
        "The interval of a model's AUC does not say whether another model is better: both are "
        "scored on the same trials. Here the trials are resampled once and both models are "
        "scored on each resample, so the interval is that of the difference "
        f"(`trialpulse.eval.compare`, {result['bootstrap_resamples']:,} resamples). The number "
        "is the mean of the difference over the development origins. A higher AUC is better "
        "and a lower Brier score is better. An interval that excludes 0 is a difference the "
        "evaluation trials can tell from none.",
        "",
        *markdown_table(headers, rows),
        "",
    ]
    by_index = [
        p
        for p in result["pairs"]
        if len(p["landmark_indices"]) > 1 and "1" in p["horizons"][PRIMARY_MONTHS]
    ]
    for months in horizons:
        if not by_index:
            break
        indices = sorted(
            {k for p in by_index for k in p["horizons"][months] if k != "all"}, key=int
        )
        table = [
            [k, *(_delta(p["horizons"][months].get(k), "delta_auc") for p in by_index)]
            for k in indices
        ]
        lines += [
            f"Difference in AUC at {months} months, by landmark index:",
            "",
            *markdown_table(
                [
                    "Landmark index",
                    *(f"{p['model'].upper()} minus {p['baseline'].upper()}" for p in by_index),
                ],
                table,
            ),
            "",
        ]
    return lines


def _ablation_section(
    results: dict[str, dict[str, Any]], compare_path: Path, horizons: Sequence[str]
) -> list[str]:
    rungs = [m for m in ABLATION if m in results]
    if len(rungs) < 2:
        return []
    pairs = {}
    if compare_path.is_file():
        for pair in json.loads(compare_path.read_text(encoding="utf-8"))["pairs"]:
            pairs[pair["model"], pair["baseline"]] = pair
    rows = []
    for position, model in enumerate(rungs):
        summary = results[model]["origins"][0].get("model_summary") or {}
        cells = [model.upper(), MODELS[model], str(summary.get("features", ""))]
        cells += [_f(_mean(results[model], months, None, "auc"), 4) for months in horizons]
        cells.append(_f(_mean(results[model], PRIMARY_MONTHS, None, "brier"), 5))
        previous = pairs.get((model, rungs[position - 1])) if position else None
        cells.append(
            ""
            if previous is None
            else _delta(previous["horizons"][PRIMARY_MONTHS].get("all"), "delta_auc")
        )
        rows.append(cells)
    headers = ["Rung", "What it is", "Features"]
    headers += [f"AUC, {m} months" for m in horizons]
    headers += ["Brier score, 12 months", "AUC at 12 months against the rung above it"]
    return [
        "## Ablation of M4",
        "",
        "The same learner and settings as M4, with the feature families added one at a time "
        "(ADR 0024), so the gain of a family is not mixed with a change of learner. Means "
        "over the development origins, all landmark indices; the last column is the paired "
        "difference to the rung in the row above.",
        "",
        *markdown_table(headers, rows),
        "",
    ]


def _size(n: int | None) -> str:
    if not n:
        return ""
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} kB"


def _runtime_section(results: dict[str, dict[str, Any]]) -> list[str]:
    rows = []
    for model, result in results.items():
        for origin in result["origins"]:
            s = origin.get("model_summary") or {}
            if "fit_seconds" not in s:
                continue
            rows.append(
                [
                    model.upper(),
                    origin["origin"][:4],
                    str(s.get("features", "")),
                    f"{s['landmark_rows']:,}",
                    f"{s['person_period_rows']:,}",
                    f"{s['fit_seconds']:,.0f}",
                    f"{s.get('predict_seconds', 0):,.0f}",
                    _size(s.get("model_bytes")),
                    str(s.get("rounds", s.get("iterations", "")))
                    + (" (the limit: not converged)" if s.get("converged") is False else ""),
                ]
            )
    if not rows:
        return []
    return [
        "## Run time and size",
        "",
        "Fitting one model on the training rows of an origin and scoring that origin's "
        "evaluation rows at every interval, on the project's laptop (no GPU). The size is "
        "that of the fitted model as it would be stored.",
        "",
        *markdown_table(
            ["Model", "Origin", "Features", "Training landmark rows", "Person-period rows",
             "Fit (seconds)", "Scoring (seconds)", "Model size", "Boosting rounds or iterations"],
            rows,
        ),
        "",
    ]  # fmt: skip


def _tuning_section(tuned_dir: Path) -> list[str]:
    rows = []
    for model in TUNED:
        path = tuned_dir / f"{model}.json"
        if not path.is_file():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        settings = ", ".join(
            f"`{name}` {value:.4g}" if isinstance(value, float) else f"`{name}` {value}"
            for name, value in record["settings"].items()
        )
        rows.append(
            [
                model.upper(),
                str(record["trials"]),
                _pct(record["trial_sample_share"], 0),
                _f(record["default_auc"], 4),
                _f(record["auc"], 4),
                settings,
            ]
        )
    if not rows:
        return []
    return [
        "## Tuning",
        "",
        "Optuna, a fixed budget of trials per model, development origins only "
        "(`trialpulse.models.tuning`). A trial is fitted on a share of the training trials and "
        "scored on every evaluation row; the objective is the selection rule of Section 9. "
        "The two AUC columns are means at 12 months over the development origins from those "
        "sampled fits, so they are lower than the numbers of the full fits above. The ablation "
        "rungs run with M4's settings. The chosen settings are committed in `config/tuned/`.",
        "",
        *markdown_table(
            ["Model", "Trials", "Share of training trials per trial",
             "AUC with the default settings", "AUC with the chosen settings", "Chosen settings"],
            rows,
        ),
        "",
    ]  # fmt: skip


def _m1_section(result: dict[str, Any]) -> list[str]:
    lines = ["## M1 in detail", ""]
    rows = []
    for origin in result["origins"]:
        for months, info in origin["model_summary"]["horizons"].items():
            rows.append(
                [
                    origin["origin"][:4],
                    months,
                    f"{info['rows_at_landmark_0']:,}",
                    f"{info['training_rows']:,}",
                    f"{info['labeled_rows']:,}",
                    f"{info['cases']:,}",
                    f"{info['validation_from']} to {info['validation_before']}",
                    f"{info['validation_rows']:,}",
                    str(info["rounds"]),
                ]
            )
    lines += markdown_table(
        ["Origin", "Horizon (months)", "Landmark 0 rows as of the origin",
         "Of those, horizon passed by the origin", "With a label", "Early stops among them",
         "Last training year (first post from, before)", "Labeled rows in that year", "Trees"],
        rows,
    )  # fmt: skip
    lines += [
        "",
        "The share of the total gain of each fitted model that its strongest features carry "
        "(a description of the trees, not an effect size; the explanations of Step 12 use "
        "SHAP):",
        "",
    ]
    rows = []
    for origin in result["origins"]:
        for months, info in origin["model_summary"]["horizons"].items():
            top = ", ".join(
                f"`{f['feature']}` {100 * f['share_of_gain']:.1f}%"
                for f in info["top_features_by_gain"][:TOP_RATIOS]
            )
            rows.append([origin["origin"][:4], months, top])
    lines += markdown_table(["Origin", "Horizon (months)", "Features by share of gain"], rows)
    return [*lines, ""]


def _diagnosis_section(path: Path) -> list[str]:
    if not path.is_file():
        return []
    saved = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(saved, dict) or "origins" not in saved:
        raise RefusedError(
            f"{path} was written by an older version of the diagnosis; write it again with: "
            "uv run python -m trialpulse.eval.diagnosis"
        )
    results = saved["origins"]
    lines = [
        "## Calibration of M0 and the training labels",
        "",
        "M0's calibration slope depends on how the training labels treat trials whose record "
        "had lapsed on the origin. The full diagnosis is in "
        "[calibration_diagnosis.md](calibration_diagnosis.md); its headline, the slope at 24 "
        "months by training labels:",
        "",
    ]
    names = list(next(iter(results))["horizons"]["24"])
    rows = [
        [name, *(_f(r["horizons"]["24"][name]["calibration_slope"]) for r in results)]
        for name in names
    ]
    lines += markdown_table(
        ["Training labels", *(f"Origin {r['origin'][:4]}" for r in results)], rows
    )
    return [*lines, ""]


def _cox_section(path: Path) -> list[str]:
    if not path.is_file():
        return []
    result = json.loads(path.read_text(encoding="utf-8"))
    labels = {term["column"]: term for term in result["terms"]}
    lines = [
        "## Cause-specific Cox models (interpretation only)",
        "",
        f"On the {result['rows']:,} landmark 0 rows of the cohort as of {result['origin']}. The "
        "full tables, with the checks of proportional hazards, are in "
        "[cox_report.md](cox_report.md). The characteristics with the largest hazard ratios "
        "for an early stop, in either direction, among those whose 95% interval excludes 1:",
        "",
    ]
    stop = result["causes"]["early stop"]["ratios"]
    complete = result["causes"]["completion"]["ratios"]
    clear = [
        (name, r)
        for name, r in stop.items()
        if (r["ci_low"] > 1.0 or r["ci_high"] < 1.0) and name in labels
    ]
    clear.sort(key=lambda item: -abs(_log(item[1]["hazard_ratio"])))
    rows = []
    for name, r in clear[:TOP_RATIOS]:
        other = complete[name]
        rows.append(
            [
                labels[name]["label"],
                labels[name]["reference"] or "(a number)",
                f"{_f(r['hazard_ratio'], 2)} ({_f(r['ci_low'], 2)} to {_f(r['ci_high'], 2)})",
                f"{_f(r['hazard_ratio_first_year'], 2)} and {_f(r['hazard_ratio_later'], 2)}",
                f"{_f(other['hazard_ratio'], 2)} ({_f(other['ci_low'], 2)} to "
                f"{_f(other['ci_high'], 2)})",
            ]
        )
    lines += markdown_table(
        ["Characteristic", "Compared with", "Hazard ratio of early stop (95% interval)",
         "First year and later", "Hazard ratio of completion"],
        rows,
    )  # fmt: skip
    return [*lines, ""]


def _log(value: float) -> float:
    import math

    return math.log(value) if value > 0 else float("inf")


def _runs_section(
    results: dict[str, dict[str, Any]], cox_path: Path, diagnosis_path: Path, root: Path
) -> list[str]:
    rows = []
    entries: list[tuple[str, dict[str, Any] | None]] = [
        (f"{model.upper()}, development origins", result.get("tracking"))
        for model, result in results.items()
    ]
    others = [
        ("Cox models at landmark 0", cox_path),
        ("Calibration diagnosis of M0", diagnosis_path),
        ("Paired differences between models", root / "compare" / "dev.json"),
        *((f"Tuning of {model.upper()}", root / "tuning" / f"{model}.json") for model in TUNED),
    ]
    for what, path in others:
        if path.is_file():
            saved = json.loads(path.read_text(encoding="utf-8"))
            entries.append((what, saved.get("tracking") if isinstance(saved, dict) else None))
    for what, info in entries:
        if info is None:
            rows.append([what, "not logged", "", "", ""])
        else:
            rows.append(
                [
                    what,
                    info["store_description"],
                    f"`{info['run_name']}` in `{info['experiment']}`, id `{info['run_id']}`",
                    f"`{info['git_commit'][:7]}`",
                    "yes" if info["git_dirty"] == "yes" else "no",
                ]
            )
    lines = [
        "## Runs",
        "",
        "A run is logged to MLflow with the commit it started from, the dataset revision, the "
        "configuration, its metrics and its result files (`trialpulse.tracking`). The table "
        "says where each run of this document went.",
        "",
        *markdown_table(
            ["Run", "Logged to", "MLflow run", "Commit", "Uncommitted changes at the time"], rows
        ),
        "",
    ]
    stores = [info.get("store") if info else None for _, info in entries]
    if any(store != "remote" for store in stores):
        on_server = sum(store == "remote" for store in stores)
        how_many = "None of the runs above is" if on_server == 0 else "Not every run above is"
        lines += [
            f"**{how_many} on the project's MLflow server (DagsHub).** A run in a "
            "local store exists only on the machine that produced it, and a run that is not "
            "logged exists nowhere. The numbers of this document do not depend on where a "
            "run is logged; the runs are logged to the server again once it takes them.",
            "",
        ]
    return lines


def document(
    cfg: ProjectConfig,
    everything: dict[str, dict[str, Any]],
    root: Path,
    tuned_dir: Path | None = None,
) -> str:
    horizons = [str(m) for m in cfg.horizons_months]
    results = {model: result for model, result in everything.items() if model in MAIN}
    if not results:
        raise RefusedError("no development result of a model of the ladder (M0 to M4)")
    compare_path = root / "compare" / "dev.json"
    check_compared(compare_path, everything)
    first = next(iter(results.values()))
    origins = ", ".join(o["origin"] for o in first["origins"])
    lines = [
        "# Development results",
        "",
        "Research demo. Not medical advice. Not for patient decision-making.",
        "",
        "This file is generated by `uv run python -m trialpulse.eval.results_dev` from the "
        "result files of the walk-forward runs (CLAUDE.md Steps 9 and 10). Do not edit it by "
        "hand.",
        "",
        "- **Source:** ClinicalTrials.gov registry records, through the version-history "
        f"dataset `{cfg.dataset.repo_id}` (license CC-BY-NC-4.0), revision "
        f"{cfg.dataset.revision}. Data as of {cfg.dataset.cutoff}. TrialPulse normalizes the "
        "records and derives every number here.",
        f"- **Development origins only:** {origins}. The origins 2018, 2019 and 2020 are "
        "locked (Section 10) and appear nowhere in this file.",
        "- **What is predicted:** the probability that an open interventional trial stops "
        "early (terminated or withdrawn) within 12 and 24 months of a landmark. An elevated "
        "early-stop risk is an operational statement about a registry record; it says nothing "
        "about whether a treatment works.",
        "- **How it is scored (Section 6):** for origin T a model is trained on what the "
        "registry showed before T (ADR 0016) and scored on the landmarks of the following 12 "
        "months, against outcomes through the data cutoff. AUC, Brier score, lift at 10% and "
        "calibration use inverse probability of censoring weights by sponsor class (ADR "
        "0017); the last column repeats the AUC with one censoring curve for all rows. "
        f"Intervals are {int(100 * cfg.evaluation.confidence_level)}% cluster-bootstrap "
        f"intervals over trials, {first['bootstrap_resamples']:,} resamples.",
        "- **Models:**",
        *(f"  - **{model.upper()}:** {MODELS[model]}." for model in everything),
        "",
    ]
    lines += _ladder_section(results, horizons)
    lines += _compare_section(compare_path, horizons)
    lines += _ablation_section(everything, compare_path, horizons)
    for months in (str(m) for m in cfg.horizons_months):
        rows = _metric_rows(results, months, None)
        lines += [f"## All landmark indices, {months} months", ""]
        lines += markdown_table(METRIC_HEADERS, rows)
        means = _mean_auc(results, months, None)
        lines += ["", f"Mean AUC over the development origins: {'; '.join(means)}.", ""]
    lines += [
        "A model of landmark 0 only (M1) has no row here: it does not score the later landmarks.",
        "",
    ]
    for months in (str(m) for m in cfg.horizons_months):
        lines += _by_index_section(results, months)
    for months in (str(m) for m in cfg.horizons_months):
        rows = _metric_rows(results, months, FIRST_LANDMARK)
        lines += [f"## Landmark 0 (registration), {months} months", ""]
        lines += markdown_table(METRIC_HEADERS, rows)
        means = _mean_auc(results, months, FIRST_LANDMARK)
        lines += ["", f"Mean AUC over the development origins: {'; '.join(means)}.", ""]
    lines += [
        "Every model is scored on the same rows here, with the same censoring weights, so the "
        "rows of one origin can be compared across models. The intervals are per model; the "
        "interval of a difference between two models is in the section on differences.",
        "",
        "## Predicted against observed risk by sponsor class, landmark 0",
        "",
        "A calibration slope pooled over classes cannot say which class a model overpredicts. "
        "Mean predicted risk and the IPCW early-stop rate, by the censoring groups of ADR "
        "0017 (classes too small for a curve of their own are pooled):",
        "",
    ]
    for months in (str(m) for m in cfg.horizons_months):
        lines += _calibration_section(results, months, FIRST_LANDMARK)
    pooled: list[str] = []
    for months in horizons:
        pooled += _calibration_section(results, months, None)
    if pooled:
        lines += [
            "## Predicted against observed risk by sponsor class, all landmark indices",
            "",
            "The same table over every landmark index, for the models that score them all. "
            "The small classes are where a model can be far off without moving a pooled "
            "slope: they hold few rows.",
            "",
            *pooled,
        ]
    by_year: list[str] = []
    for months in horizons:
        by_year += _calibration_section(
            results, months, None, "calibration_by_registration_year", "Registration year"
        )
    if by_year:
        lines += [
            "## Predicted against observed risk by registration year, all landmark indices",
            "",
            "By the calendar year in which a trial was first posted. The evaluation rows of "
            "an origin hold landmarks 0 to 6, so they reach back three years. A model that is "
            "right on average and wrong by registration year has learned something about "
            "calendar time that does not carry over (ADR 0023 names one such selection).",
            "",
            *by_year,
        ]
    deciles: list[str] = []
    for months in (str(m) for m in cfg.horizons_months):
        deciles += _decile_section(results, months)
    if deciles:
        lines += [
            "## Predicted against observed risk by decile, landmark 0",
            "",
            "The rows of each model are sorted by its own predicted risk and cut into ten "
            "groups of equal size; the observed risk of a group is its Aalen-Johansen "
            "cumulative incidence at the horizon (Section 6). A model with few distinct "
            "predictions (M0 has one per sponsor class) puts equal predictions in neighboring "
            "groups.",
            "",
            *deciles,
        ]
    if "m1" in results:
        lines += _m1_section(results["m1"])
    diagnosis_path = root / "diagnosis" / "m0_calibration.json"
    lines += _diagnosis_section(diagnosis_path)
    cox_path = root / "cox" / "cox_l0.json"
    lines += _cox_section(cox_path)
    lines += _runtime_section(everything)
    lines += _tuning_section(discrete_time.TUNED_DIR if tuned_dir is None else tuned_dir)
    lines += _runs_section(everything, cox_path, diagnosis_path, root)
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write docs/results_dev.md.")
    parser.add_argument("--results", type=Path, default=RESULTS_ROOT)
    parser.add_argument("--doc", type=Path, default=DOC_PATH)
    parser.add_argument("--tuned-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    results = load_results(args.results)
    text = document(cfg, results, args.results, args.tuned_dir)
    changed = write_text_if_changed(args.doc, text)
    print(f"models: {', '.join(model.upper() for model in results)}")
    print(f"{args.doc}: {'written' if changed else 'up to date'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
