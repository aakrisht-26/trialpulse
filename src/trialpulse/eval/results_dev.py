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
"""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from trialpulse.cli import RefusedError, run
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.reports import markdown_table, write_text_if_changed

DOC_PATH = REPO_ROOT / "docs" / "results_dev.md"
RESULTS_ROOT = REPO_ROOT / "data" / "results"
DEV = "dev"
MODELS: dict[str, str] = {
    "m0": "Aalen-Johansen curve per sponsor class: base rates only",
    "m1": "static LightGBM classifier at registration (landmark 0), all feature families",
    "m2": "discrete-time logistic regression, design features",
    "m3": "M2 plus amendment signals",
    "m4": "discrete-time LightGBM, all feature families",
}
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


def _by_group_section(results: dict[str, dict[str, Any]], months: str) -> list[str]:
    """Predicted against observed risk by sponsor class at landmark 0, for every model."""
    lines: list[str] = []
    origins = [o["origin"] for o in next(iter(results.values()))["origins"]]
    for day in origins:
        tables = {}
        for model, result in results.items():
            s = _first_landmark_slice(result, day, months)
            if s is not None and "calibration_by_group" in s:
                tables[model] = {row["group"]: row for row in s["calibration_by_group"]}
        if not tables:
            continue
        first = next(iter(tables.values()))
        headers = ["Sponsor class", "Rows", "Observed (IPCW)", "Censored before the horizon"]
        headers += [f"Predicted, {model.upper()}" for model in tables]
        rows = []
        for group, row in first.items():
            rows.append(
                [
                    group,
                    f"{row['n']:,}",
                    _pct(row["observed"]),
                    _pct(row["censored_before_horizon"], 1),
                    *(_pct(table[group]["mean_predicted"]) for table in tables.values()),
                ]
            )
        lines += [f"Origin {day[:4]}, {months} months:", "", *markdown_table(headers, rows), ""]
    return lines


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
    results: dict[str, dict[str, Any]], cox_path: Path, diagnosis_path: Path
) -> list[str]:
    rows = []
    entries: list[tuple[str, dict[str, Any] | None]] = [
        (f"{model.upper()}, development origins", result.get("tracking"))
        for model, result in results.items()
    ]
    for what, path in (
        ("Cox models at landmark 0", cox_path),
        ("Calibration diagnosis of M0", diagnosis_path),
    ):
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


def document(cfg: ProjectConfig, results: dict[str, dict[str, Any]], root: Path) -> str:
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
        *(f"  - **{model.upper()}:** {MODELS[model]}." for model in results),
        "",
    ]
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
        "rows of one origin can be compared across models. The intervals are per model: the "
        "difference between two models has no interval of its own yet.",
        "",
        "## Predicted against observed risk by sponsor class, landmark 0",
        "",
        "A calibration slope pooled over classes cannot say which class a model overpredicts. "
        "Mean predicted risk and the IPCW early-stop rate, by the censoring groups of ADR "
        "0017 (classes too small for a curve of their own are pooled):",
        "",
    ]
    for months in (str(m) for m in cfg.horizons_months):
        lines += _by_group_section(results, months)
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
    lines += _runs_section(results, cox_path, diagnosis_path)
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write docs/results_dev.md.")
    parser.add_argument("--results", type=Path, default=RESULTS_ROOT)
    parser.add_argument("--doc", type=Path, default=DOC_PATH)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    results = load_results(args.results)
    changed = write_text_if_changed(args.doc, document(cfg, results, args.results))
    print(f"models: {', '.join(model.upper() for model in results)}")
    print(f"{args.doc}: {'written' if changed else 'up to date'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
