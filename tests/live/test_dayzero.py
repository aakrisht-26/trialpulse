"""The day-0 check (ADR 0038, layer 3) on a real Postgres: after a bootstrap of the synthetic
registry, the live path's scores of the cutoff, computed as the daily job computes them,
are compared with a reference; with a stand-in for the champion. Skipped without Postgres
(failed in CI)."""

import datetime as dt
import json
from typing import Any

import numpy as np
import pandas as pd
import pytest

from trialpulse.live import daily, dayzero, engine, store
from trialpulse.live.seed import seed
from trialpulse.scoring.predict import Scored

from .conftest import CFG, DECISIONS, KEEP, RULES, compact_history_of, registry

ALIASES = (CFG.features.sponsor_alias_min_trials, CFG.features.sponsor_alias_wait_days)
DATA = registry(300, 7)


def _scorer(scored: pd.DataFrame, features: pd.DataFrame, day: dt.date) -> pd.DataFrame:
    merged = scored.merge(features[["trial_id", "true_index", "n_versions"]],
                          on=["trial_id", "true_index"], how="left")  # fmt: skip
    risk = merged["n_versions"].astype(float) / 100
    drivers = [json.dumps({"12": [{"feature": "n_versions", "value": str(n), "contribution": 0.1,
                                   "direction": "raises"}], "24": []}, separators=(",", ":"))
               for n in merged["n_versions"]]  # fmt: skip
    return pd.DataFrame({
        "nct_id": merged["trial_id"].astype(str), "as_of_date": np.datetime64(day, "D"),
        "true_index": merged["true_index"], "landmark_index": merged["k_star"],
        "flagged": merged["flagged"].astype(bool), "risk_12m": risk, "risk_24m": risk * 2,
        "content_hash": merged["content_hash"], "model_name": "stand-in",
        "model_version": "stand-in/1", "drivers": drivers,
    })  # fmt: skip


MODEL = daily.Model("stand-in/1", _scorer, DECISIONS)


def _reference(day: dt.date) -> tuple[pd.DataFrame, pd.DataFrame, Any]:
    """The cutoff's scores of the synthetic registry from the seeded state in memory (the
    live engine with the stand-in), as Step 13's files hold them, and the seed."""
    seeded = seed(compact_history_of(DATA.versions, DATA.texts, DATA.interventions), day,
                  RULES, KEEP, *ALIASES)  # fmt: skip
    memory = engine.Memory(seeded.wides, seeded.sponsors, markers=seeded.markers)
    people, features = engine.features_on(memory, day, RULES, CFG, DECISIONS)
    scored = people[people["reason"].isna()].sort_values("trial_id").reset_index(drop=True)
    scores = _scorer(scored, features, day)
    # The unscored rows made here, not by the code under test (`dayzero.live_unscored`).
    out = people[people["reason"].notna()]
    unscored = pd.DataFrame({
        "nct_id": out["trial_id"].astype(str).to_numpy(), "as_of_date": pd.Timestamp(day),
        "true_index": out["true_index"].to_numpy(), "reason": out["reason"].to_numpy(),
        "content_hash": out["content_hash"].to_numpy(),
    }).sort_values("nct_id").reset_index(drop=True)  # fmt: skip
    assert len(scores) > 0
    assert len(unscored) > 0
    return scores, unscored, seeded


def test_the_day0_check_compares_the_live_path_with_the_reference(scratch: str) -> None:
    psycopg = pytest.importorskip("psycopg")
    dsn = scratch.replace("postgresql+psycopg://", "postgresql://", 1)
    assert CFG.dataset.cutoff is not None
    day = CFG.dataset.cutoff
    scores, unscored, seeded = _reference(day)
    served = Scored(scores.copy(), unscored.copy(), {})
    with psycopg.connect(dsn, autocommit=True) as con, con.cursor() as cur:
        with con.transaction():
            run_id = store.start_bootstrap(cur, day, 120)
        rows = store.prepare_bootstrap(cur, run_id, day, seeded.wides, seeded.sponsors,
                                       seeded.markers, served)  # fmt: skip
        with con.transaction():
            store.load_bootstrap(cur, rows)
    result = dayzero.check_cutoff(CFG, dsn, MODEL, (scores, unscored))
    assert result["differences"] == []
    assert result["live_scored"] == len(scores)
    assert result["content_hash_against_served_rows"]["different"] == 0
    moved = scores.copy()
    moved.loc[0, "risk_24m"] += 1e-12
    moved.loc[1, "drivers"] = '{"12":[],"24":[]}'
    result = dayzero.check_cutoff(CFG, dsn, MODEL, (moved.iloc[:-1], unscored))
    assert result["differences"] == [
        "scores: 1 rows only new, 0 only in the reference",
        "scores.risk_24m: 1 differ",
        "scores.drivers: 1 differ",
    ]
    # Every compared column, a trial the live path lacks, and the unscored trials' reason.
    changed = scores.copy()
    changed["flagged"] = changed["flagged"].astype(bool)
    rows = [i % len(changed) for i in range(6)]  # one changed value per column
    changed.loc[rows[0], "flagged"] = not changed.loc[rows[0], "flagged"]
    changed.loc[rows[1], "landmark_index"] += 1
    changed.loc[rows[2], "true_index"] += 1
    changed.loc[rows[3], "model_version"] = "stand-in/2"
    changed.loc[rows[4], "risk_12m"] += 1e-12
    changed.loc[rows[5], "as_of_date"] = np.datetime64(day - dt.timedelta(days=1), "D")
    extra = changed.iloc[[0]].assign(nct_id="NCT99999999")
    other = unscored.copy()
    other.loc[0, "reason"] = "another reason"
    result = dayzero.check_cutoff(CFG, dsn, MODEL, (pd.concat([changed, extra]), other))
    assert result["differences"] == [
        "scores: 0 rows only new, 1 only in the reference",
        "scores.as_of_date: 1 differ",
        "scores.true_index: 1 differ",
        "scores.landmark_index: 1 differ",
        "scores.flagged: 1 differ",
        "scores.risk_12m: 1 differ",
        "scores.model_version: 1 differ",
        "unscored.reason: 1 differ",
    ]
    # A live path that loses every unscored trial is caught.
    result = dayzero.check_cutoff(CFG, dsn, MODEL, (scores, pd.concat([unscored, unscored.iloc[
        [0]].assign(nct_id="NCT99999998")])))  # fmt: skip
    assert result["differences"] == ["unscored: 0 rows only new, 1 only in the reference"]
