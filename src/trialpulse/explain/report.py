"""Explanations of the champion on the locked test origins (CLAUDE.md Step 12, ADR 0033).

    uv run python -m trialpulse.explain.report --unlock-test

One command writes docs/explanations.md and its figures:

1. **The test lock first** (ADR 0004): the rows of the test origins are read only with
   --unlock-test and the published registration tag, checked before any data is loaded.
2. **The champion of each test origin**, M4 fitted exactly as the locked run fitted it (the
   training rows as of the origin, the tuned settings, the seed), read from the cache in
   data/explain/ when it is there. A model is used only if it reproduces the scores the
   locked run saved for every evaluation row of its origin, to PARITY_TOLERANCE; a cached
   model that does not is fitted again, and a fitted one that does not is refused. So every
   explanation is of the model whose results are in docs/results.md.
3. **SHAP values** (`trialpulse.explain.drivers`) of a seeded sample of each origin's
   evaluation rows at 12 and 24 months: the mean absolute value of each feature, the
   direction of each number feature, and how closely the explained target ranks the rows as
   the served risk does.
4. **Three example timelines** (`trialpulse.explain.timelines`), with the five drivers of
   the 12-month risk at each landmark.

Nothing is logged to MLflow: the models are the locked run's, already logged, and the
command evaluates no metric. The document holds registry ids, dates, classes and scores; no
name or contact of anyone.
"""

import argparse
import datetime as dt
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from trialpulse.cli import RefusedError, run
from trialpulse.config import REPO_ROOT, Origin, ProjectConfig, load_project_config
from trialpulse.dates import add_months
from trialpulse.eval.lock import TestLockError, require_unlock
from trialpulse.eval.rows import (
    LANDMARKS_PATH,
    TRAINING_DIR,
    LandmarkRows,
    load_landmark_rows,
    training_path,
)
from trialpulse.eval.walkforward import (
    EVALUATION,
    FEATURES_DIR,
    PREDICTIONS_DIR,
    RESULTS_DIR,
    TRAINING,
    FeatureLoader,
    attach_features,
    evaluation_rows,
    feature_loader,
    select_origins,
    sha256_of,
    training_rows,
)
from trialpulse.explain import drivers, timelines
from trialpulse.features.registry import BY_NAME
from trialpulse.models import discrete_time
from trialpulse.models.discrete_time import DiscreteTimeBoosting
from trialpulse.reports import markdown_table, write_text_if_changed
from trialpulse.warehouse.build import WAREHOUSE_PATH

CHAMPION = "m4"
EXPLAIN_DIR = REPO_ROOT / "data" / "explain"
DOC_PATH = REPO_ROOT / "docs" / "explanations.md"
FIGURES_DIR = REPO_ROOT / "docs" / "figures"
SAMPLE_ROWS = 20_000  # evaluation rows explained per origin, by a seeded draw
TOP_FEATURES = 15  # features in the importance figure and table
PARITY_TOLERANCE = 1e-12  # largest difference from a saved score that counts as the same
SOURCE = "Source: ClinicalTrials.gov via brbk/clinical_trials_history; TrialPulse."


@dataclass
class OriginExplanation:
    """What the command computed for one test origin."""

    origin: str
    eval_rows: int
    sample_rows: int
    parity: float  # the largest difference from the saved scores, both horizons
    model_source: str  # "cache" or "fitted"
    rounds: int
    importance: dict[int, dict[str, float]] = field(default_factory=dict)
    direction: dict[str, str] = field(default_factory=dict)
    agreement: dict[int, float] = field(default_factory=dict)
    additivity: dict[int, float] = field(default_factory=dict)


@dataclass
class TimelineExplained:
    line: timelines.Timeline
    drivers: list[list[drivers.Driver]]  # per point, the drivers of its 12-month risk
    first_post: dt.date


# The saved scores and their provenance -----------------------------------------------------


def saved_scores(cfg: ProjectConfig, origin: Origin, unlock_commit: str) -> pd.DataFrame:
    """The scores the locked run saved for an origin's evaluation rows, after checking that
    its result file names the tagged commit and that the file's SHA-256 is the recorded one."""
    year = origin.date.year
    path = RESULTS_DIR / f"{CHAMPION}_{year}.json"
    if not path.is_file():
        raise RefusedError(f"{path} not found: the locked run of {CHAMPION} at {year} is missing")
    result = json.loads(path.read_text(encoding="utf-8"))
    tracking = result.get("tracking") or {}
    if tracking.get("git_commit") != unlock_commit or tracking.get("git_dirty") != "no":
        raise RefusedError(f"{path} was not made from the tagged commit with a clean tree")
    (record,) = [o for o in result["origins"] if o["origin"] == origin.date.isoformat()]
    scores = PREDICTIONS_DIR / record["predictions"]["path"]
    if sha256_of(scores) != record["predictions"]["sha256"]:
        raise RefusedError(f"{scores} is not the scores file the locked run recorded")
    frame = pd.read_parquet(scores)
    if len(frame) != record["n_eval_rows"]:
        raise RefusedError(f"{scores} holds {len(frame)} rows, not {record['n_eval_rows']}")
    return frame


def parity(model: DiscreteTimeBoosting, rows: LandmarkRows, saved: pd.DataFrame,
           months: Sequence[int]) -> float:  # fmt: skip
    """The largest difference between the model's scores of the rows and the saved ones,
    matched by registry id and landmark index."""
    keys = pd.MultiIndex.from_arrays(
        [rows.trial_id.astype(str), rows.landmark_index], names=["trial_id", "landmark_index"]
    )
    indexed = saved.set_index(["trial_id", "landmark_index"])
    if len(indexed) != len(keys) or not indexed.index.isin(keys).all():
        raise RefusedError("the evaluation rows are not the rows the locked run scored")
    worst = 0.0
    for m in months:
        theirs = indexed[f"score_{m}"].reindex(keys).to_numpy(dtype=np.float64)
        worst = max(worst, float(np.max(np.abs(model.predict_months(rows, m) - theirs))))
    return worst


# The champion of an origin: from the cache, or fitted ----------------------------------------


def _cache_paths(origin: dt.date, folder: Path) -> tuple[Path, Path]:
    return folder / f"{CHAMPION}_{origin.isoformat()}.txt", folder / f"{CHAMPION}_{origin}.json"


def load_cached(cfg: ProjectConfig, origin: dt.date, folder: Path) -> DiscreteTimeBoosting | None:
    booster_path, meta_path = _cache_paths(origin, folder)
    if not booster_path.is_file() or not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    model = discrete_time.build(CHAMPION, cfg)
    assert isinstance(model, DiscreteTimeBoosting)
    model.booster = lgb.Booster(model_file=str(booster_path))
    model.levels = {name: tuple(levels) for name, levels in meta["levels"].items()}
    model.summary = meta["summary"]
    return model


def save_cached(model: DiscreteTimeBoosting, origin: dt.date, folder: Path) -> None:
    assert model.booster is not None
    booster_path, meta_path = _cache_paths(origin, folder)
    folder.mkdir(parents=True, exist_ok=True)
    model.booster.save_model(str(booster_path))
    meta = {"levels": {k: list(v) for k, v in model.levels.items()}, "summary": model.summary}
    meta_path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")


def fit_champion(
    cfg: ProjectConfig, origin: dt.date, load_features: FeatureLoader, training_dir: Path
) -> DiscreteTimeBoosting:
    """M4 fitted on an origin's training rows exactly as the walk-forward run fits it."""
    t = np.datetime64(origin, "D")
    train, time, event = training_rows(load_landmark_rows(training_path(training_dir, origin)), t)
    train = attach_features(train, load_features(origin, TRAINING, None), "training rows")
    model = discrete_time.build(CHAMPION, cfg)
    assert isinstance(model, DiscreteTimeBoosting)
    model.fit_rows(train, time, event, origin)
    return model


# The explanations of an origin ---------------------------------------------------------------


def sample_rows(rows: LandmarkRows, size: int, generator: np.random.Generator) -> LandmarkRows:
    """A seeded draw of rows without replacement, kept in their order."""
    if size >= len(rows):
        return rows
    chosen = np.zeros(len(rows), dtype=bool)
    chosen[generator.choice(len(rows), size=size, replace=False)] = True
    return rows.subset(chosen)


def explain_origin(
    model: DiscreteTimeBoosting,
    sample: LandmarkRows,
    months: Sequence[int],
    record: OriginExplanation,
) -> None:
    for m in months:
        explanation = drivers.explain(model, sample, m)
        record.importance[m] = drivers.importance(explanation)
        record.agreement[m] = drivers.agreement(explanation.target, model.predict_months(sample, m))
        record.additivity[m] = float(
            np.max(np.abs(explanation.target - drivers.log_odds(model, sample, m)))
        )
        if m == min(months):
            record.direction = drivers.direction_of(explanation, sample.features)


def explain_timeline(
    line: timelines.Timeline,
    models: Mapping[str, DiscreteTimeBoosting],
    rows: Mapping[str, LandmarkRows],
    months: int,
    spacing_months: int,
) -> TimelineExplained:
    """The drivers of each point's risk, from its origin's model and its own row."""
    found: list[list[drivers.Driver]] = []
    for point in line.points:
        ev = rows[point.origin]
        mask = (ev.trial_id.astype(str) == line.trial_id) & (
            ev.landmark_index == point.landmark_index
        )
        if int(mask.sum()) != 1:
            raise RefusedError(f"{line.trial_id} has no single row at {point.landmark_date}")
        own = ev.subset(mask)
        explanation = drivers.explain(models[point.origin], own, months)
        found.append(drivers.top_drivers(explanation, own.features, 0))
    first = line.points[0]
    first_post = add_months(
        np.array([first.landmark_date], dtype="datetime64[D]"),
        -spacing_months * first.landmark_index,
    )[0]
    return TimelineExplained(line, found, pd.Timestamp(first_post).date())


# The document -------------------------------------------------------------------------------


def _year(date: dt.date) -> float:
    start = dt.date(date.year, 1, 1)
    length = (dt.date(date.year + 1, 1, 1) - start).days
    return date.year + (date - start).days / length


def _percent(value: float) -> str:
    return f"{100 * value:.1f}%"


def figure_paths(out_dir: Path) -> list[Path]:
    return [out_dir / "explain_importance_12m.png"] + [
        out_dir / f"explain_timeline_{i}.png" for i in range(1, len(timelines.KINDS) + 1)
    ]


def mean_importance(records: Sequence[OriginExplanation], months: int) -> dict[str, float]:
    """The mean over the origins of each feature's mean absolute SHAP value, largest first."""
    names = list(records[0].importance[months])
    mean = {n: float(np.mean([r.importance[months][n] for r in records])) for n in names}
    return dict(sorted(mean.items(), key=lambda item: (-item[1], names.index(item[0]))))


def draw_figures(
    records: Sequence[OriginExplanation],
    explained: Sequence[TimelineExplained],
    cutoff: dt.date,
    out_dir: Path,
) -> None:
    from trialpulse.eda import figures as fg

    paths = figure_paths(out_dir)
    top = list(mean_importance(records, 12).items())[:TOP_FEATURES]
    origins = " and ".join(r.origin[:4] for r in records)
    fg.ranked_bars(
        paths[0],
        top,
        title="What moves the champion's 12-month early-stop risk",
        subtitle=f"Mean absolute SHAP value of each feature over a seeded sample of the "
        f"evaluation rows of the {origins} origins, averaged over the two origins, in log-odds "
        "of an early stop against continuing. The 15 largest of 54 features.",
        source=SOURCE,
        x_label="Mean absolute SHAP value (log-odds)",
    )
    for path, item in zip(paths[1:], explained, strict=True):
        line = item.line
        x = np.array([_year(p.landmark_date) for p in line.points])
        series = [
            ("risk within 12 months", x, np.array([p.score_12 for p in line.points]), fg.BLUE),
            ("risk within 24 months", x, np.array([p.score_24 for p in line.points]), fg.ORANGE),
        ]
        end = _year(line.outcome_date)
        low, high = float(x[0]) - 0.3, max(float(x[-1]), end) + 0.3
        ticks = list(range(int(np.ceil(low)), int(np.floor(high)) + 1))
        outcome = (
            f"open at the data cutoff, {cutoff.isoformat()}"
            if line.kind == timelines.OPEN
            else f"{line.kind}, posted {line.outcome_date.isoformat()}"
        )
        fg.risk_timeline(
            path,
            series,
            title=f"Risk timeline of {line.trial_id} ({line.kind})",
            subtitle="The champion's early-stop risk at each of the trial's landmarks on the "
            "locked test origins, each score from its origin's model with the information "
            "posted by the landmark. Light lines mark registry updates (status, start, "
            "primary completion or enrollment); the dark line marks the outcome.",
            source=SOURCE,
            end_labels=["12 months", "24 months"],
            x_label="Calendar year",
            x_limits=(low, high),
            x_ticks=ticks,
            x_tick_labels=[str(t) for t in ticks],
            updates=[_year(u.date) for u in line.updates],
            outcome=(end, outcome),
        )


def document(
    cfg: ProjectConfig,
    records: Sequence[OriginExplanation],
    explained: Sequence[TimelineExplained],
    unlock_commit: str,
    figures: Sequence[Path],
) -> str:
    """The text of docs/explanations.md."""
    origins = " and ".join(r.origin[:4] for r in records)
    lines = [
        "# Explanations of the champion",
        "",
        "Research demo. Not medical advice. Not for patient decision-making.",
        "",
        "This file is generated by `uv run python -m trialpulse.explain.report --unlock-test` "
        "(CLAUDE.md Step 12, ADR 0033). Do not edit it by hand. An elevated early-stop risk is "
        "an operational statement about a registry record; it says nothing about whether a "
        "treatment works. The registered results are in [results.md](results.md); nothing here "
        "changes them.",
        "",
        f"- **Source:** ClinicalTrials.gov registry records, through the version-history "
        f"dataset `brbk/clinical_trials_history` (license CC-BY-NC-4.0), revision "
        f"{cfg.dataset.revision}. Data as of {cfg.dataset.cutoff}. TrialPulse normalizes the "
        "records and derives every score and driver here.",
        f"- **Model:** the champion, M4 (ADR 0026), of each locked test origin ({origins}), "
        f"fitted as the locked run fitted it from commit `{unlock_commit[:12]}`.",
        "",
        "## How the drivers are computed",
        "",
        "M4 predicts, for each 6-month interval after a landmark, whether a trial continues, "
        "stops early or completes. Its trees give, for each interval, a raw score per outcome, "
        "and TreeSHAP (LightGBM's own `pred_contrib`) splits each raw score exactly into one "
        "SHAP value per feature. A driver's value is its SHAP value for the log-odds of an "
        "early stop against continuing, averaged over the intervals of the horizon (two for 12 "
        "months, four for 24). A positive value raises the model's odds of an early stop "
        "against continuing; a negative one lowers them. The interval number itself is set by "
        "the horizon and is never listed as a driver.",
        "",
        "The explained log-odds is not the served risk, which also depends on the chance of "
        "completing first. The table below says how closely the two rank the same rows "
        "(Spearman's rank correlation), and that every model reproduces the scores the locked "
        "run saved.",
        "",
        *markdown_table(
            [
                "Origin",
                "Evaluation rows",
                "Rows explained",
                "Model",
                "Largest difference from the saved scores",
                "Rank agreement, 12 months",
                "Rank agreement, 24 months",
                "Largest additivity gap",
            ],
            [
                [
                    r.origin,
                    f"{r.eval_rows:,}",
                    f"{r.sample_rows:,}",
                    f"{r.rounds:,} rounds ({r.model_source})",
                    f"{r.parity:.1e}",
                    f"{r.agreement[12]:.3f}",
                    f"{r.agreement[24]:.3f}",
                    f"{max(r.additivity.values()):.1e}",
                ]
                for r in records
            ],
        ),
        "",
    ]
    lines += _importance_section(records, figures[0], origins)
    lines += _timeline_sections(explained, figures[1:], cutoff_of(cfg))
    return "\n".join(lines).rstrip("\n") + "\n"


def _importance_section(
    records: Sequence[OriginExplanation], figure: Path, origins: str
) -> list[str]:
    mean12, mean24 = mean_importance(records, 12), mean_importance(records, 24)
    direction = records[0].direction
    rows = []
    for name in list(mean12)[:TOP_FEATURES]:
        rows.append(
            [name, BY_NAME[name].family]
            + [f"{r.importance[12][name]:.3f}" for r in records]
            + [f"{mean12[name]:.3f}", f"{mean24[name]:.3f}", direction.get(name, "")]
        )
    return [
        "## What moves the risk",
        "",
        f"The mean absolute SHAP value of each feature over a seeded sample of the evaluation "
        f"rows of the {origins} origins. The direction says, for a number feature, whether "
        f"higher values go with higher SHAP values over the {records[0].origin[:4]} sample "
        "(Spearman's correlation of at least 0.1 in size; otherwise mixed); a category or "
        "flag has none here. A feature can matter a lot and still go either way.",
        "",
        f"![{figure.stem}](figures/{figure.name})",
        "",
        *markdown_table(
            ["Feature", "Family"]
            + [f"12 months, {r.origin[:4]}" for r in records]
            + ["12 months, mean", "24 months, mean", "Direction"],
            rows,
        ),
        "",
        "Every feature is described in [features.md](features.md).",
        "",
    ]


def _timeline_sections(
    explained: Sequence[TimelineExplained], figures: Sequence[Path], cutoff: dt.date
) -> list[str]:
    lines = [
        "## Example risk timelines",
        "",
        "Three trials drawn with the project's seed from the trials that have evaluation rows "
        "at both test origins: one that stopped early, one that completed and one still open "
        "at the data cutoff (ADR 0033). Each score is the one its origin's model gave with the "
        "information posted by the landmark, as the locked run saved it. The drivers are those "
        "of the 12-month risk. The sponsor is shown by class only.",
        "",
    ]
    for item, figure in zip(explained, figures, strict=True):
        line = item.line
        outcome = (
            f"still open at the data cutoff ({cutoff.isoformat()})"
            if line.kind == timelines.OPEN
            else f"{line.kind}: the registry posted it on {line.outcome_date.isoformat()}"
        )
        lines += [
            f"### {line.trial_id} ({line.kind})",
            "",
            f"First posted {item.first_post.isoformat()}; lead sponsor class "
            f"{line.sponsor_class or 'not given'}; {outcome}.",
            "",
            f"![{figure.stem}](figures/{figure.name})",
            "",
            *markdown_table(
                [
                    "Landmark",
                    "Date",
                    "Origin",
                    "Risk within 12 months",
                    "Risk within 24 months",
                    "Top drivers of the 12-month risk",
                ],
                [
                    [
                        str(p.landmark_index),
                        p.landmark_date.isoformat(),
                        p.origin[:4],
                        _percent(p.score_12),
                        _percent(p.score_24),
                        drivers.drivers_text(d),
                    ]
                    for p, d in zip(line.points, item.drivers, strict=True)
                ],
            ),
            "",
            "Registry updates up to the outcome:",
            "",
            *markdown_table(
                ["Posted", "What changed"],
                [[u.date.isoformat(), "; ".join(u.changes)] for u in line.updates],
            ),
            "",
        ]
        if line.updates_not_listed:
            lines += [f"{line.updates_not_listed} later updates are not listed.", ""]
    return lines


# The command --------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write docs/explanations.md (Step 12).")
    parser.add_argument("--unlock-test", action="store_true", help="see ADR 0004")
    parser.add_argument("--sample", type=int, default=SAMPLE_ROWS)
    parser.add_argument("--doc", type=Path, default=DOC_PATH)
    parser.add_argument("--figures", type=Path, default=FIGURES_DIR)
    parser.add_argument("--cache", type=Path, default=EXPLAIN_DIR)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    origins = select_origins(cfg, "test")
    try:  # before any data is read
        unlock = require_unlock({o.role for o in origins}, REPO_ROOT, args.unlock_test)
    except TestLockError as exc:
        raise RefusedError(str(exc)) from exc
    assert unlock is not None
    return explain_all(cfg, origins, unlock.commit, args)


def cutoff_of(cfg: ProjectConfig) -> dt.date:
    if cfg.dataset.cutoff is None:
        raise RefusedError("config/project.yaml records no data cutoff")
    return cfg.dataset.cutoff


def champion_for(
    cfg: ProjectConfig,
    origin: dt.date,
    rows: LandmarkRows,
    saved: pd.DataFrame,
    cache: Path,
    fit: Callable[[], DiscreteTimeBoosting],
) -> tuple[DiscreteTimeBoosting, str, float]:
    """The origin's champion from the cache if it reproduces the saved scores, else fitted
    and cached if it does; refused otherwise. Returns the model, where it came from and its
    largest difference from the saved scores."""
    months = list(cfg.horizons_months)
    cached = load_cached(cfg, origin, cache)
    if cached is not None:
        gap = parity(cached, rows, saved, months)
        if gap <= PARITY_TOLERANCE:
            return cached, "cache", gap
    model = fit()
    gap = parity(model, rows, saved, months)
    if gap > PARITY_TOLERANCE:
        raise RefusedError(
            f"M4 fitted for {origin} differs from the locked run's saved scores by up to "
            f"{gap:.2e}; its explanations would not be of the registered model"
        )
    save_cached(model, origin, cache)
    return model, "fitted", gap


def explain_all(
    cfg: ProjectConfig,
    origins: Sequence[Origin],
    unlock_commit: str,
    args: argparse.Namespace,
    load_rows: Callable[[], LandmarkRows] = lambda: load_landmark_rows(LANDMARKS_PATH),
    load_features: FeatureLoader | None = None,
    training_dir: Path = TRAINING_DIR,
    warehouse: Path = WAREHOUSE_PATH,
) -> int:
    loader = load_features or feature_loader(cfg, FEATURES_DIR, LANDMARKS_PATH.parent)
    months = list(cfg.horizons_months)
    cutoff = cutoff_of(cfg)
    generator = np.random.default_rng(cfg.seeds.default)
    every = load_rows()
    records: list[OriginExplanation] = []
    models: dict[str, DiscreteTimeBoosting] = {}
    rows: dict[str, LandmarkRows] = {}
    saved: dict[str, pd.DataFrame] = {}
    for origin in origins:
        key = origin.date.isoformat()
        saved[key] = saved_scores(cfg, origin, unlock_commit)
        t = np.datetime64(origin.date, "D")
        ev = evaluation_rows(every, t, cfg.walk_forward.eval_window_months)[0]
        ev = attach_features(ev, loader(origin.date, EVALUATION, None), "evaluation rows")
        day = origin.date
        model, source, gap = champion_for(
            cfg,
            day,
            ev,
            saved[key],
            args.cache,
            partial(fit_champion, cfg, day, loader, training_dir),
        )
        print(f"{key}: {source} model, largest difference from the saved scores {gap:.1e}")
        sample = sample_rows(ev, args.sample, generator)
        rounds = int(model.summary.get("rounds", 0))
        record = OriginExplanation(key, len(ev), len(sample), gap, source, rounds)
        explain_origin(model, sample, months, record)
        records.append(record)
        models[key], rows[key] = model, ev
    scored = timelines.scored_rows(saved)
    candidates = timelines.eligible(scored, cutoff, list(saved))
    chosen = timelines.draw(candidates, cfg.seeds.default)
    explained = []
    for kind in timelines.KINDS:
        line = timelines.timeline(scored, chosen[kind], kind)
        line = timelines.with_updates(line, timelines.read_versions(warehouse, line.trial_id))
        explained.append(
            explain_timeline(line, models, rows, min(months), cfg.landmarks.spacing_months)
        )
    figures = figure_paths(args.figures)
    text = document(cfg, records, explained, unlock_commit, figures)  # text first, as Step 11
    draw_figures(records, explained, cutoff, args.figures)
    changed = write_text_if_changed(args.doc, text)
    summary = {
        "unlock_commit": unlock_commit,
        "origins": [r.__dict__ for r in records],
        "candidates": {kind: len(ids) for kind, ids in candidates.items()},
        "chosen": chosen,
    }
    args.cache.mkdir(parents=True, exist_ok=True)
    (args.cache / "summary.json").write_text(json.dumps(summary, indent=2, default=str),
                                              encoding="utf-8")  # fmt: skip
    print(f"{args.doc}: {'written' if changed else 'up to date'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
