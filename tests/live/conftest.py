"""Synthetic histories for the live path: the feature tests' scripted registry as compact
rows, a feed of API-shaped records after a seed date, and the offline path on the same
feed (Parity C, ADR 0038)."""

import datetime as dt
import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from trialpulse.cohort.outcomes import build_states as build_cohort_states
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import load_project_config
from trialpulse.live import contract, engine, export
from trialpulse.live.seed import seed
from trialpulse.scoring import features as served_features
from trialpulse.scoring.score import population as offline_population


def _feature_registry() -> Any:
    """The feature tests' scripted registry, loaded under a name of its own."""
    path = Path(__file__).resolve().parent.parent / "features" / "conftest.py"
    spec = importlib.util.spec_from_file_location("feature_registry_for_live_tests", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_FEATURES = _feature_registry()
registry = _FEATURES.registry
version = _FEATURES.version

CFG = load_project_config()
RULES = CohortRules.from_config(CFG)
SECRET = b"a test key for the sponsor ids"
KEEP = CFG.live.recent_post_days
DECISIONS: dict[str, Any] = {
    "between_landmarks": "B",
    "bands": {"42 to 59 months": {"served": True}, "60 to 119 months": {"served": True}},
}


def compact_history_of(
    versions: list[dict[str, Any]],
    texts: dict[str, str] | None = None,
    interventions: dict[tuple[str, int], list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Canonical versions as compact rows, each with its version number."""
    texts, interventions = texts or {}, interventions or {}
    rows = []
    for v in versions:
        row = contract.compact_row(
            v,
            texts.get(v["official_title_hash"] or ""),
            texts.get(v["brief_title_hash"] or ""),
            interventions.get((v["nct_id"], v["nct_version"])),
            RULES,
            SECRET,
        )
        row["nct_version"] = v["nct_version"]
        rows.append(row)
    return rows


def compact_history(n_trials: int = 300, seed_number: int = 7) -> list[dict[str, Any]]:
    """Every version of the scripted registry as a compact row with its version number."""
    data = registry(n_trials, seed_number)
    return compact_history_of(data.versions, data.texts, data.interventions)


def api_view(rows: list[dict[str, Any]], day: dt.date, since: dt.date) -> list[dict[str, Any]]:
    """What a delta pull on `day` from `since` returns: the latest record of each trial,
    posted on or before `day`, if it was posted on or after `since` (of several versions
    on one day, the API shows the last)."""
    latest: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["effective_date"] <= day:
            best = latest.get(r["nct_id"])
            if best is None or (r["effective_date"], r["nct_version"]) > (
                best["effective_date"], best["nct_version"]
            ):  # fmt: skip
                latest[r["nct_id"]] = r
    return [
        {k: v for k, v in r.items() if k != "nct_version"}
        for r in latest.values()
        if r["effective_date"] >= since
    ]


@dataclass
class Replay:
    memory: engine.Memory
    fed: list[dict[str, Any]]  # every record the live path kept, with its run
    runs: list[dt.date]


def replay(
    history: list[dict[str, Any]],
    start: dt.date,
    run_days: list[dt.date],
    on_run: Callable[[dt.date, engine.Memory, "Replay"], None] | None = None,
) -> Replay:
    """Seed at `start`, then one run on each of `run_days`, each pulling from the previous
    run's date minus the overlap, as the daily job does."""
    seeded = seed(history, start, RULES, KEEP, CFG.features.sponsor_alias_min_trials,
                  CFG.features.sponsor_alias_wait_days)  # fmt: skip
    memory = engine.Memory(seeded.wides, seeded.sponsors, markers=seeded.markers)
    out = Replay(memory, [], [])
    previous = start
    for run, day in enumerate(run_days, start=1):
        records = api_view(history, day, previous - dt.timedelta(days=CFG.live.overlap_days))
        done = engine.ingest(memory, records, RULES, day, KEEP)
        engine.advance(memory, done.versions, day, RULES, run)
        out.fed += [{**v, "run": run} for v in done.versions]
        out.runs.append(day)
        previous = day
        if on_run is not None:
            on_run(day, memory, out)
    return out


def feed_rows(history: list[dict[str, Any]], start: dt.date, fed: list[dict[str, Any]]) -> list[
    dict[str, Any]
]:  # fmt: skip
    """The feed F of Parity C: every version posted by the seed date, then the records the
    live path kept (numbered after them)."""
    before = [r for r in history if r["effective_date"] <= start]
    after = [{k: v for k, v in r.items() if k != "run"} | {"nct_version": None} for r in fed]
    return [*before, *after]


def offline_on(
    rows: list[dict[str, Any]], day: dt.date, keys: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """offline(F) on `day`: the population and the served features of `keys`, computed by
    the Step 13 code on the export of the feed's rows posted by `day`."""
    known = [r for r in rows if r["effective_date"] <= day]
    with duckdb.connect() as con:
        export.register_export(con, known, RULES)
        build_cohort_states(con, RULES)
        people = offline_population(con, RULES, day, source=export.EXPORT_TABLE)
        served_features.register_keys(con, keys)
        frame = served_features.compute_served(con, RULES, CFG)
    return people, frame
