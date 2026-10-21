"""Write docs/results.md from the result files of the locked run (Step 11).

    uv run python -m trialpulse.eval.results

**One commit.** Every result file of the locked run (the walk-forward runs of the test and
stress origins, the UNKNOWN sensitivity runs, the comparisons of the test origins and H3 and
H5) must name the same commit with a clean tree, be unlocked by the same tag object, and
name as its commit the commit the tag points at (the run rule of the review of Step 10 and
`docs/preregistration.md`). A file that breaks this refuses the document, by name.

**The registered predictions** are read from `docs/preregistration.md` in the tagged commit,
not from the working tree: the machine-readable block of the registration (a fenced block
opened by "```json predictions") gives each prediction's value and acceptable range, and the
document holds each observed value against them. The conditions of H1 to H5 are written
here as the registration states them. Every falsified prediction and failed hypothesis is
named.

Reading a locked result file evaluates nothing: the files exist only after an unlocked run
(ADR 0004), and each carries its unlock. Before the unlock the command finds no file and
says which runs make them.
"""

import argparse
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from trialpulse.cli import RefusedError, run
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.eval.lock import PREREG_PATH
from trialpulse.eval.rows import VARIANT
from trialpulse.reports import markdown_table, write_text_if_changed

RESULTS_ROOT = REPO_ROOT / "data" / "results"
DOC_PATH = REPO_ROOT / "docs" / "results.md"
FIGURES_DIR = REPO_ROOT / "docs" / "figures"
CHAMPION = "m4"
MODELS: tuple[str, ...] = ("m0", "m1", "m2", "m3", "m4", "m4-text")
TEST_YEARS: tuple[str, ...] = ("2018", "2019")
STRESS_YEAR = "2020"
REQUIRED_PAIRS: tuple[tuple[str, str], ...] = (("m4", "m1"), ("m3", "m2"))
H4_BOUNDS = (0.8, 1.2)
H1_BAND = 0.02
PRIMARY = ("auc_12_2018", "auc_12_2019", "h2_slope_12")
DIFFERENCES = ("h1_", "h2_", "h3_", "h5_")
BLOCK = re.compile(r"```json predictions\n(.*?)\n```", re.DOTALL)


@dataclass(frozen=True)
class Prediction:
    id: str
    statement: str
    predicted: float
    low: float | None
    high: float | None
    low_exclusive: bool = False

    def holds(self, value: float) -> bool:
        if self.low is not None and (value <= self.low if self.low_exclusive else value < self.low):
            return False
        return self.high is None or value <= self.high

    @property
    def difference(self) -> bool:
        """A difference (H1, H2, H3, H5) is shown with its sign; a level is not."""
        return self.id.startswith(DIFFERENCES)

    def number(self, value: float) -> str:
        return f"{value:+g}" if self.difference and value != 0 else f"{value:g}"

    def range_text(self) -> str:
        low = "" if self.low is None else self.number(self.low)
        if low and self.low_exclusive:
            low = f"above {low}"
        high = "" if self.high is None else self.number(self.high)
        return f"{low} to {high}" if low and high else (low or f"up to {high}")


def registered_predictions(text: str) -> dict[str, Prediction]:
    """The predictions of the machine-readable block of the registration."""
    match = BLOCK.search(text)
    if match is None:
        raise RefusedError(
            f"{PREREG_PATH} at the tagged commit holds no ```json predictions block, so the "
            "observed values cannot be held against the registered ranges"
        )
    out = {}
    for item in json.loads(match.group(1))["predictions"]:
        prediction = Prediction(**item)
        if prediction.id in out:
            raise RefusedError(f"the prediction {prediction.id} is registered twice")
        out[prediction.id] = prediction
    return out


def _read(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return result


def _walkforward(root: Path, model: str, year: str, variant: bool = False) -> Path | None:
    """The result of a model at a locked origin: from a run of that origin, or of the test
    (or stress) origins together; both at once are refused."""
    folder = root / "walkforward" / (VARIANT if variant else "")
    spec = "stress" if year == STRESS_YEAR else "test"
    found = [
        p
        for p in (folder / f"{model}_{year}.json", folder / f"{model}_{spec}.json")
        if p.is_file() and any(o["origin"].startswith(year) for o in _read(p)["origins"])
    ]
    if len(found) > 1:
        raise RefusedError(f"two results of {model} for {year}: {found[0]} and {found[1]}")
    return found[0] if found else None


def _origin(result: Mapping[str, Any], year: str) -> dict[str, Any]:
    (origin,) = [o for o in result["origins"] if o["origin"].startswith(year)]
    return dict(origin)


@dataclass
class Run:
    """Every result file of the locked run, read."""

    walkforward: dict[tuple[str, str], dict[str, Any]]  # (model, year) -> the origin's record
    variant: dict[str, dict[str, Any]]  # year -> the champion's origin record on the variant
    pairs: dict[tuple[str, str], dict[str, Any]]  # (model, baseline) -> the pair
    h3: dict[str, Any]
    h5: dict[str, Any]
    files: dict[Path, dict[str, Any]]  # every file, for the commit check and the runs list
    root: Path


def load_run(root: Path) -> Run:
    files: dict[Path, dict[str, Any]] = {}
    walkforward: dict[tuple[str, str], dict[str, Any]] = {}
    missing: list[str] = []
    for model in MODELS:
        for year in (*TEST_YEARS, STRESS_YEAR):
            path = _walkforward(root, model, year)
            if path is None:
                if model == CHAMPION or (model != "m4-text" and year in TEST_YEARS):
                    missing.append(
                        f"uv run python -m trialpulse.eval.walkforward --model {model} "
                        f"--origins {year} --unlock-test"
                    )
                continue
            files[path] = files.get(path) or _read(path)
            walkforward[model, year] = _origin(files[path], year)
    variant = {}
    for year in TEST_YEARS:
        path = _walkforward(root, CHAMPION, year, variant=True)
        if path is None:
            missing.append(
                f"uv run python -m trialpulse.eval.walkforward --model {CHAMPION} --origins "
                f"{year} --unlock-test --variant {VARIANT}"
            )
            continue
        files[path] = files.get(path) or _read(path)
        variant[year] = _origin(files[path], year)
    pairs = {}
    for path in sorted((root / "compare" / "test").glob("*.json")):
        files[path] = _read(path)
        for pair in files[path]["pairs"]:
            pairs[pair["model"], pair["baseline"]] = pair
    for model, baseline in REQUIRED_PAIRS:
        if (model, baseline) not in pairs:
            missing.append(
                f"uv run python -m trialpulse.eval.compare --pairs {model}:{baseline} "
                "--origins test --unlock-test"
            )
    hypothesis: dict[str, dict[str, Any]] = {}
    for name, command in (("h3-test", "h3 --origins test"), ("h5", "h5")):
        path = root / "hypotheses" / f"{name}.json"
        if not path.is_file():
            missing.append(f"uv run python -m trialpulse.eval.hypotheses {command} --unlock-test")
            continue
        files[path] = hypothesis[name] = _read(path)
    if missing:
        raise RefusedError(
            "the locked run is incomplete; make the missing results with:\n  "
            + "\n  ".join(missing)
        )
    return Run(walkforward, variant, pairs, hypothesis["h3-test"], hypothesis["h5"], files, root)


def check_one_commit(files: Mapping[Path, Mapping[str, Any]]) -> dict[str, str]:
    """Every file names one clean commit, unlocked by one tag object, and that commit is the
    one the tag points at. Returns the commit and the unlock."""
    problems = []
    commits, tags = set(), set()
    for path, result in files.items():
        tracking, unlock = result.get("tracking"), result.get("unlock")
        if not tracking:
            problems.append(f"{path}: not logged (no tracking record)")
            continue
        if not unlock:
            problems.append(f"{path}: no unlock record")
            continue
        if tracking.get("git_dirty") != "no":
            problems.append(f"{path}: run with uncommitted changes ({tracking.get('git_dirty')})")
        if tracking["git_commit"] != unlock["commit"]:
            problems.append(
                f"{path}: run from {tracking['git_commit'][:12]}, not from the tagged commit "
                f"{unlock['commit'][:12]}"
            )
        commits.add(tracking["git_commit"])
        tags.add((unlock["tag_object"], unlock["commit"], unlock["registered"]))
    if len(commits) > 1:
        problems.append(f"the files name {len(commits)} commits: {sorted(c[:12] for c in commits)}")
    if len(tags) > 1:
        problems.append(f"the files name {len(tags)} unlocks")
    if problems:
        raise RefusedError("the locked run is not one clean commit:\n  " + "\n  ".join(problems))
    (tag_object, commit, registered) = tags.pop()
    return {"commit": commit, "tag_object": tag_object, "registered": registered}


def statistic_ids() -> frozenset[str]:
    """Every statistic a registered prediction can name: the keys of `observed`."""
    ids = {"h1_delta_auc_12", "h1_delta_auc_24", "h2_slope_12", "h3_difference_12", "h5_difference"}
    for year in TEST_YEARS:
        for months in ("12", "24"):
            ids.add(f"h4_slope_{months}_{year}")
            ids.update(f"{metric}_{months}_{year}" for metric in ("auc", "brier", "lift"))
    return frozenset(ids)


def observed(run: Run) -> dict[str, dict[str, float]]:
    """Every statistic a prediction can name, with its interval where it has one."""

    def interval(d: Mapping[str, Any]) -> dict[str, float]:
        return {k: float(d[k]) for k in ("estimate", "ci_low", "ci_high")}

    def point(value: float) -> dict[str, float]:
        return {"estimate": float(value)}

    out = {
        "h1_delta_auc_12": interval(run.pairs["m4", "m1"]["horizons"]["12"]["0"]["delta_auc"]),
        "h1_delta_auc_24": interval(run.pairs["m4", "m1"]["horizons"]["24"]["0"]["delta_auc"]),
        "h2_slope_12": interval(run.pairs["m3", "m2"]["trend"]["12"]["slope"]),
        "h3_difference_12": interval(run.h3["horizons"]["12"]["difference"]),
        "h5_difference": interval(run.h5["difference"]),
    }
    for year in TEST_YEARS:
        record = run.walkforward[CHAMPION, year]
        for months in ("12", "24"):
            pooled = record["horizons"][months]["pooled"]
            out[f"h4_slope_{months}_{year}"] = point(pooled["calibration_slope"])
            for metric in ("auc", "brier", "lift"):
                out[f"{metric}_{months}_{year}"] = interval(pooled[metric])
    return out


def hypotheses(values: Mapping[str, Mapping[str, float]]) -> dict[str, tuple[bool, str]]:
    """The condition of each hypothesis, as the registration states it."""
    h1_12, h1_24 = values["h1_delta_auc_12"]["estimate"], values["h1_delta_auc_24"]["estimate"]
    slopes = [values[f"h4_slope_{m}_{y}"]["estimate"] for m in ("12", "24") for y in TEST_YEARS]
    low, high = H4_BOUNDS
    return {
        "H1": (
            abs(h1_12) < H1_BAND and h1_24 > 0,
            f"absolute difference {abs(h1_12):.4f} < {H1_BAND} at 12 months, and {h1_24:+.4f} > 0 "
            "at 24 months",
        ),
        "H2": (
            values["h2_slope_12"]["ci_low"] > 0,
            f"the interval of the slope starts at {values['h2_slope_12']['ci_low']:+.4f}",
        ),
        "H3": (
            values["h3_difference_12"]["ci_low"] > 0,
            f"the interval of the difference starts at {values['h3_difference_12']['ci_low']:+.4f}",
        ),
        "H4": (
            all(low <= s <= high for s in slopes),
            "slopes " + ", ".join(f"{s:.3f}" for s in slopes) + f" against [{low}, {high}]",
        ),
        "H5": (
            values["h5_difference"]["estimate"] < 0,
            f"{values['h5_difference']['estimate']:+.4f} < 0",
        ),
    }


def _ci(d: Mapping[str, Any], digits: int = 3) -> str:
    text = f"{d['estimate']:.{digits}f}"
    if "ci_low" in d:
        text += f" ({d['ci_low']:.{digits}f} to {d['ci_high']:.{digits}f})"
    return text


def _shown(d: Mapping[str, Any], difference: bool) -> str:
    """An observed value: a difference with its sign, a level without; the interval if any."""
    if "ci_low" not in d:
        return f"{d['estimate']:+.4f}" if difference else f"{d['estimate']:.4f}"
    return _signed(d) if difference else _ci(d, 4)


def _signed(d: Mapping[str, Any], digits: int = 4) -> str:
    return f"{d['estimate']:+.{digits}f} ({d['ci_low']:+.{digits}f} to {d['ci_high']:+.{digits}f})"


def calibration_figures(run: Run, cfg: ProjectConfig, out_dir: Path) -> list[Path]:
    """Observed against predicted risk by decile for the champion, one line per test origin,
    with the diagonal of perfect calibration."""
    from trialpulse.eda import figures as fg

    paths = []
    for months in (str(m) for m in cfg.horizons_months):
        series = []
        top = 0.0
        for year, color in zip(TEST_YEARS, (fg.BLUE, fg.ORANGE), strict=True):
            table = run.walkforward[CHAMPION, year]["horizons"][months]["pooled"][
                "calibration_table"
            ]
            x = np.array([row["mean_predicted"] for row in table])
            y = np.array([row["observed_cif"] for row in table])
            top = max(top, float(x.max()), float(y.max()))
            series.append((f"origin {year}", x, y, color))
        series.append(
            ("perfect calibration", np.array([0.0, top]), np.array([0.0, top]), fg.NEUTRAL)
        )
        path = out_dir / f"results_calibration_{months}m.png"
        ticks = [round(top * i / 4, 3) for i in range(5)]
        fg.line_chart(
            path,
            series,
            title=f"Predicted against observed early-stop risk, {months} months",
            subtitle="The champion (M4) on the locked test origins: the rows of each origin cut "
            "into ten groups by predicted risk; observed risk is the Aalen-Johansen cumulative "
            "incidence of each group. Points on the diagonal are calibrated.",
            source="Source: ClinicalTrials.gov via brbk/clinical_trials_history; TrialPulse.",
            end_labels=[f"{year}" for year in TEST_YEARS] + ["diagonal"],
            x_label="Mean predicted risk",
            x_ticks=ticks,
            x_tick_labels=[f"{100 * t:.0f}%" for t in ticks],
            markers=True,
        )
        paths.append(path)
    return paths


def document(run: Run, registration: str, cfg: ProjectConfig, figures: Sequence[Path] = ()) -> str:
    """The text of docs/results.md."""
    unlock = check_one_commit(run.files)
    predictions = registered_predictions(registration)
    values = observed(run)
    unknown = sorted(set(predictions) - statistic_ids())
    if unknown:
        raise RefusedError(
            f"the registration names predictions this code cannot compute: {unknown}"
        )
    verdicts = hypotheses(values)
    rows, falsified = [], []
    for p in predictions.values():
        value = values[p.id]
        ok = p.holds(value["estimate"])
        if not ok:
            falsified.append(p.id)
        rows.append(
            [
                f"**{p.id}**" if p.id in PRIMARY else p.id,
                p.statement,
                p.number(p.predicted),
                p.range_text(),
                _shown(value, p.difference),
                "within the range" if ok else "**falsified**",
            ]
        )
    failed = [name for name, (holds, _) in verdicts.items() if not holds]
    lines = [
        "# Results on the locked origins",
        "",
        "Research demo. Not medical advice. Not for patient decision-making.",
        "",
        "This file is generated by `uv run python -m trialpulse.eval.results` from the result "
        "files of the locked run (CLAUDE.md Step 11). Do not edit it by hand. An elevated "
        "early-stop risk is an operational statement about a registry record; it says nothing "
        "about whether a treatment works.",
        "",
        f"- **Source:** ClinicalTrials.gov registry records, through the version-history "
        f"dataset `brbk/clinical_trials_history` (license CC-BY-NC-4.0), revision "
        f"{cfg.dataset.revision}. Data as of {cfg.dataset.cutoff}. TrialPulse normalizes the "
        "records and derives every number here.",
        f"- **Registration:** `{PREREG_PATH}` registered on {unlock['registered']}, tag "
        f"`prereg-v1` (tag object `{unlock['tag_object'][:12]}`) on commit "
        f"`{unlock['commit'][:12]}`. Every result file below was made from that commit with a "
        "clean tree.",
        f"- **Origins:** {', '.join(f'{y}-01-01' for y in TEST_YEARS)} (the locked test) and "
        f"{STRESS_YEAR}-01-01 (the COVID stress test, reported separately).",
        "",
        "## The registered predictions against the observed values",
        "",
        "Primary claims in bold: the champion's AUC at 12 months on each test origin, and H2. "
        "Everything else is secondary. A value outside its registered range falsifies the "
        "prediction.",
        "",
        *markdown_table(
            ["Prediction", "What", "Predicted", "Acceptable range", "Observed", "Verdict"], rows
        ),
        "",
        *markdown_table(
            ["Hypothesis", "Condition met", "How it was read"],
            [[name, "yes" if holds else "**no**", how] for name, (holds, how) in verdicts.items()],
        ),
        "",
        (
            "Falsified predictions: " + ", ".join(falsified) + "."
            if falsified
            else "No prediction fell outside its registered range."
        ),
        "",
        (
            "Hypotheses whose condition failed: " + ", ".join(failed) + "."
            if failed
            else "Every hypothesis's condition held."
        ),
        "",
    ]
    lines += _headline(run)
    lines += _by_index(run)
    lines += _calibration(run, figures)
    lines += _pairs(run)
    lines += _cause_specific(run)
    lines += _unknown(run)
    lines += _stress(run)
    lines += _runs(run)
    return "\n".join(lines).rstrip("\n") + "\n"


def _headline(run: Run) -> list[str]:
    rows = []
    for (model, year), record in sorted(run.walkforward.items()):
        if year == STRESS_YEAR:
            continue
        h12, h24 = (record["horizons"][m]["pooled"] for m in ("12", "24"))
        rows.append(
            [
                model.upper(),
                year,
                f"{h12['n_rows']:,}",
                _ci(h12["auc"]),
                _ci(h24["auc"]),
                _ci(h12["brier"], 4),
                _ci(h12["lift"], 2),
                f"{h12['calibration_slope']:.3f}",
                f"{h24['calibration_slope']:.3f}",
            ]
        )
    return [
        "## Every model on the test origins",
        "",
        "Pooled over the landmark indices each model scores (M1 scores landmark 0 only), with "
        "95% cluster-bootstrap intervals over trials.",
        "",
        *markdown_table(
            [
                "Model",
                "Origin",
                "Rows",
                "AUC, 12 months",
                "AUC, 24 months",
                "Brier, 12 months",
                "Lift at 10%, 12 months",
                "Slope, 12 months",
                "Slope, 24 months",
            ],
            rows,
        ),
        "",
    ]


def _by_index(run: Run) -> list[str]:
    out = ["## The champion's AUC by landmark index", ""]
    for months in ("12", "24"):
        indices = sorted(
            run.walkforward[CHAMPION, TEST_YEARS[0]]["horizons"][months]["by_landmark_index"],
            key=int,
        )
        rows = [
            [
                k,
                *(
                    _ci(
                        run.walkforward[CHAMPION, y]["horizons"][months]["by_landmark_index"][k][
                            "auc"
                        ]
                    )
                    for y in TEST_YEARS
                ),
            ]
            for k in indices
        ]
        out += [
            f"At {months} months:",
            "",
            *markdown_table(["Landmark index", *TEST_YEARS], rows),
            "",
        ]
    return out


def _calibration(run: Run, figures: Sequence[Path]) -> list[str]:
    rows = []
    for year in TEST_YEARS:
        for months in ("12", "24"):
            pooled = run.walkforward[CHAMPION, year]["horizons"][months]["pooled"]
            rows.append(
                [
                    year,
                    months,
                    f"{pooled['calibration_slope']:.3f}",
                    f"{pooled['calibration_intercept']:+.3f}",
                ]
            )
    out = [
        "## Calibration of the champion",
        "",
        *markdown_table(["Origin", "Horizon (months)", "Slope", "Intercept"], rows),
        "",
    ]
    for path in figures:
        out += [f"![{path.stem}](figures/{path.name})", ""]
    return out


def _pairs(run: Run) -> list[str]:
    rows = []
    for (model, baseline), pair in run.pairs.items():
        cells = [f"{model.upper()} minus {baseline.upper()}"]
        for months in ("12", "24"):
            part = pair["horizons"][months]
            cells += [
                _signed(part["all"]["delta_auc"]) if "all" in part else "",
                _signed(part["0"]["delta_auc"]) if "0" in part else "",
            ]
        trend = pair.get("trend", {}).get("12")
        cells.append(_signed(trend["slope"]) if trend else "")
        rows.append(cells)
    return [
        "## Paired differences on the test origins",
        "",
        "The mean over 2018 and 2019 of the difference in AUC, with paired cluster-bootstrap "
        "intervals (ADR 0025).",
        "",
        *markdown_table(
            [
                "Difference",
                "12 months, all indices",
                "12 months, landmark 0",
                "24 months, all indices",
                "24 months, landmark 0",
                "Slope per landmark index, 12 months",
            ],
            rows,
        ),
        "",
    ]


def _cause_specific(run: Run) -> list[str]:
    rows = []
    for months, part in run.h3["horizons"].items():
        for day, by in part["by_origin"].items():
            rows.append(
                [
                    day[:4],
                    months,
                    f"{by['cases']['operational']:,}",
                    f"{by['operational']:.3f}",
                    f"{by['cases']['scientific']:,}",
                    f"{by['scientific']:.3f}",
                    f"{by['operational'] - by['scientific']:+.3f}",
                ]
            )
    return [
        "## Cause-specific results: operational and scientific stops (H3)",
        "",
        "The champion's all-cause score held against operational and against scientific early "
        "stops (ADR 0028), with the censoring weights of all evaluation rows. Errors in the reason "
        "labels push the difference toward zero, as long as they do not depend on the risk score.",
        "",
        *markdown_table(
            [
                "Origin",
                "Horizon (months)",
                "Operational stops",
                "AUC, operational",
                "Scientific stops",
                "AUC, scientific",
                "Difference",
            ],
            rows,
        ),
        "",
    ]


def _unknown(run: Run) -> list[str]:
    rows = []
    for year in TEST_YEARS:
        for months in ("12", "24"):
            main = run.walkforward[CHAMPION, year]["horizons"][months]["pooled"]
            alt = run.variant[year]["horizons"][months]["pooled"]
            rows.append(
                [
                    year,
                    months,
                    f"{main['n_rows']:,}",
                    f"{alt['n_rows']:,}",
                    _ci(main["auc"]),
                    _ci(alt["auc"]),
                    _ci(main["brier"], 4),
                    _ci(alt["brier"], 4),
                    f"{main['calibration_slope']:.3f}",
                    f"{alt['calibration_slope']:.3f}",
                ]
            )
    return [
        "## Sensitivity: UNKNOWN as an early stop",
        "",
        "The champion, trained as in the main run, scores a cohort in which a trial lapsed at "
        "the data cutoff stops early at its lapse date (Section 6, ADR 0014).",
        "",
        *markdown_table(
            [
                "Origin",
                "Horizon (months)",
                "Rows, main",
                "Rows, variant",
                "AUC, main",
                "AUC, variant",
                "Brier, main",
                "Brier, variant",
                "Slope, main",
                "Slope, variant",
            ],
            rows,
        ),
        "",
    ]


def _stress(run: Run) -> list[str]:
    record = run.walkforward[CHAMPION, STRESS_YEAR]
    h12, h24 = (record["horizons"][m]["pooled"] for m in ("12", "24"))
    return [
        "## The COVID stress test (origin 2020), reported separately",
        "",
        f"The champion at {STRESS_YEAR}-01-01: AUC at 12 months {_ci(h12['auc'])}, at 24 months "
        f"{_ci(h24['auc'])}; Brier score at 12 months {_ci(h12['brier'], 4)}; calibration slope "
        f"{h12['calibration_slope']:.3f} and {h24['calibration_slope']:.3f}. H5: the AUC at 12 "
        f"months minus the mean of 2018 and 2019 is {_signed(run.h5['difference'])}.",
        "",
    ]


def _runs(run: Run) -> list[str]:
    rows = [
        [
            path.relative_to(run.root).as_posix(),
            result["tracking"]["run_name"],
            result["tracking"]["run_id"][:8],
        ]
        for path, result in sorted(run.files.items())
    ]
    return ["## Runs", "", *markdown_table(["File", "Run", "Run id"], rows), ""]


def registration_at(commit: str, repo: Path = REPO_ROOT) -> str:
    shown = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{PREREG_PATH}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if shown.returncode != 0:
        raise RefusedError(f"{PREREG_PATH} is not in commit {commit[:12]}")
    return shown.stdout


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write docs/results.md (Step 11).")
    parser.add_argument("--results", type=Path, default=RESULTS_ROOT)
    parser.add_argument("--doc", type=Path, default=DOC_PATH)
    parser.add_argument("--figures", type=Path, default=FIGURES_DIR)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    loaded = load_run(args.results)
    unlock = check_one_commit(loaded.files)
    figures = calibration_figures(loaded, cfg, args.figures)
    text = document(loaded, registration_at(unlock["commit"]), cfg, figures)
    changed = write_text_if_changed(args.doc, text)
    print(f"{args.doc}: {'written' if changed else 'up to date'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
