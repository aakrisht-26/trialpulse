"""score(as_of_date): the served scores of every open trial on a date (CLAUDE.md Step 13,
ADR 0034).

    uv run python -m trialpulse.scoring.score --as-of 2019-06-30

**The population on d** (ADR 0034, decision 1): every trial first posted on or after
2008-01-01 and on or before d whose version in effect on d is interventional, open
(SUSPENDED included) and not lapsed under ADR 0014. Only versions posted on or before d are
read. Among them:

- a trial with a status reversal visible by d (a version with an open or UNKNOWN status
  after its first terminal version, both posted by d) is not scored: reason `reversal`;
- a trial 120 months or more after registration (true landmark index 20 or more) is not
  scored: reason `beyond_120_months`;
- a trial between months 42 and 119 is scored and flagged "outside the validated range" if
  check 2 validated its band, otherwise not scored: reason `band_not_validated`;
- every other trial is scored as decision 1 says (option B), if check 1 chose it.

**What a score holds:** the risk of an early stop within 12 and within 24 months after d,
the five ADR 0033 drivers of each horizon's log-odds with their direction, the true and the
capped landmark index, the flag, and the content hash of the version scored (so a later run
can tell a record change). The output is two Parquet files per date (the scores and the
unscored trials), sorted by registry id, whose SHA-256 values are printed: scoring a date
twice gives the same files.

**What reaches the serving tables** (`trialpulse.scoring.serving`): a run with drivers, dated
on or after the data cutoff of the champion's training rows. A run without drivers, or of an
earlier date, is written to files only: the champion was fitted on outcomes after such a
date, so its scores there are in-sample, a check of the pipeline rather than predictions.
"""

import argparse
import datetime as dt
import hashlib
import json
import time as clock
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from trialpulse.cli import RefusedError, run
from trialpulse.cohort.outcomes import build_states as build_cohort_states
from trialpulse.cohort.rules import CohortRules, sql_list, submitted_status_sql
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.eval.rows import LandmarkRows
from trialpulse.explain import drivers
from trialpulse.features.frame import as_arrays
from trialpulse.features.transforms import SponsorPriors
from trialpulse.ingest.history import RAW_HISTORY_DIR, config_glob
from trialpulse.models.discrete_time import DiscreteTimeBoosting
from trialpulse.scoring import checks
from trialpulse.scoring import features as served_features
from trialpulse.scoring import rows as served
from trialpulse.warehouse.build import WAREHOUSE_PATH

SCORES_DIR = REPO_ROOT / "data" / "scoring" / "scores"
HORIZONS: tuple[int, ...] = (12, 24)
REVERSAL, OLDEST, BAND = "reversal", "beyond_120_months", "band_not_validated"
FLAG = "outside the validated range"


@dataclass
class Bundle:
    """A model, the sponsor priors fitted with it, and its name and version."""

    model: DiscreteTimeBoosting
    priors: SponsorPriors
    name: str
    version: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Scored:
    scores: pd.DataFrame
    unscored: pd.DataFrame
    seconds: dict[str, float]


def served_decisions(path: Path = checks.RESULT_PATH) -> dict[str, Any]:
    """The decisions of the two checks (ADR 0034), which the scoring follows."""
    if not path.is_file():
        raise RefusedError(
            f"{path} not found: run the checks of ADR 0034 first with: "
            "uv run python -m trialpulse.scoring.checks"
        )
    decisions: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))["decisions"]
    if decisions["between_landmarks"] != "B":
        raise RefusedError(
            "check 1 chose option A (the latest landmark row); this scoring code serves "
            "option B only, and option A needs the changes ADR 0034 decision 2 lists"
        )
    return decisions


def population(
    con: duckdb.DuckDBPyConnection,
    rules: CohortRules,
    day: dt.date,
    source: str = "wh.versions",
) -> pd.DataFrame:
    """The open trials on `day` (see the module text), with their first post, whether a
    status reversal is visible by `day`, and the sponsor class and content hash of the
    version in effect. Needs the `versions` view and `cohort_states`; the content hash, a
    bookkeeping column no feature reads, comes from `source` (the canonical versions)."""
    d = f"DATE '{day.isoformat()}'"
    terminal = sql_list(rules.terminal)
    reopened = sql_list((*rules.open_statuses, *rules.unknown_labels))
    frame: pd.DataFrame = con.execute(
        f"""WITH v AS (
          SELECT nct_id, nct_version, effective_date, {submitted_status_sql(rules)} AS status,
            study_first_post_date
          FROM versions v WHERE v.effective_date <= {d}
        ),
        trial AS (
          SELECT nct_id, arg_min(study_first_post_date, nct_version) AS t0,
            min(nct_version) FILTER (WHERE status IN {terminal}) AS first_terminal
          FROM v GROUP BY nct_id
        ),
        rev AS (
          SELECT t.nct_id, bool_or(v.status IN {reopened}) AS reversal
          FROM trial t JOIN v ON v.nct_id = t.nct_id AND v.nct_version > t.first_terminal
          GROUP BY t.nct_id
        ),
        now AS (
          SELECT t.nct_id, t.t0, coalesce(r.reversal, false) AS reversal, {d} AS day
          FROM trial t LEFT JOIN rev r USING (nct_id)
          WHERE t.t0 >= DATE '{rules.min_first_post_date.isoformat()}' AND t.t0 <= {d}
        )
        SELECT n.nct_id AS trial_id, n.t0, n.reversal, w.content_hash,
          coalesce(s.lead_sponsor_class, 'MISSING') AS sponsor_class
        FROM now n
        ASOF JOIN cohort_states s ON s.nct_id = n.nct_id AND n.day >= s.effective_date
        LEFT JOIN {source} w ON w.nct_id = s.nct_id AND w.nct_version = s.nct_version
        WHERE s.study_type = '{rules.study_type}'
          AND s.status IN {sql_list(rules.open_statuses)}
          AND NOT (s.lapse_from IS NOT NULL AND s.lapse_from <= {d})
        ORDER BY n.nct_id"""
    ).df()
    return frame


def classify(people: pd.DataFrame, day: dt.date, rules: CohortRules,
             decisions: Mapping[str, Any]) -> pd.DataFrame:  # fmt: skip
    """Each open trial with its true landmark index, k*, its band and whether it is scored
    (with the flag) or not (with the reason)."""
    out = people.copy()
    t0 = out["t0"].to_numpy(dtype="datetime64[D]")
    out["true_index"] = served.true_index(t0, day, rules.spacing_months)
    out["k_star"] = served.capped(out["true_index"].to_numpy(dtype=np.int64), rules.max_index)
    out["flagged"] = False
    out["reason"] = None
    for band, low, high in checks.BANDS:
        member = (out["true_index"] >= low) & (out["true_index"] <= high)
        if decisions["bands"][band]["served"]:
            out.loc[member, "flagged"] = True
        else:
            out.loc[member, "reason"] = BAND
    out.loc[out["true_index"] >= checks.OLDEST_INDEX, "reason"] = OLDEST
    out.loc[out["reversal"].astype(bool), "reason"] = REVERSAL
    return out


def _drivers_json(explanations: Mapping[int, drivers.Explanation],
                  values: Mapping[str, Any], row: int) -> str:  # fmt: skip
    payload = {
        str(months): [
            {"feature": d.feature, "value": d.value, "contribution": round(d.contribution, 6),
             "direction": d.direction}
            for d in drivers.top_drivers(explanation, values, row)
        ]
        for months, explanation in explanations.items()
    }  # fmt: skip
    return json.dumps(payload, separators=(",", ":"))


def predict(
    scored: pd.DataFrame,
    features: pd.DataFrame,
    bundle: Bundle,
    day: dt.date,
    with_drivers: bool = True,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """The scores of the trials in `scored` (one row per trial, with its true index, k*, flag
    and content hash) from their served features (`compute_served`, before the priors): the
    sponsor rate smoothed with the bundle's priors, the risk at each served horizon and, with
    `with_drivers`, the five drivers of each horizon, row for row in the order of `scored`."""
    seconds: dict[str, float] = {}
    tick = clock.perf_counter()
    frame = served_features.with_priors(features, bundle.priors)
    frame = scored[["trial_id", "true_index"]].merge(
        frame, on=["trial_id", "true_index"], how="left", validate="one_to_one", indicator=True
    )
    missing = int((frame.pop("_merge") != "both").sum())
    if missing:
        raise RuntimeError(f"{missing} scored trials have no served features")
    model = bundle.model
    columns = ["trial_id", "landmark_index", *[f for f in model.features if f != "landmark_index"]]
    values = as_arrays(frame[columns])
    stamp = np.full(len(frame), np.datetime64(day, "D"))
    landmark_rows = LandmarkRows(frame["trial_id"].to_numpy(dtype=object),
                                 scored["k_star"].to_numpy(dtype=np.int64), stamp,
                                 np.zeros(len(frame), dtype=np.int64), stamp, values)  # fmt: skip
    risk = {months: model.predict_months(landmark_rows, months) for months in HORIZONS}
    seconds["predict"] = clock.perf_counter() - tick
    out = pd.DataFrame(
        {
            "nct_id": scored["trial_id"].astype(str).to_numpy(),
            "as_of_date": np.datetime64(day, "D"),
            "true_index": scored["true_index"].to_numpy(dtype=np.int64),
            "landmark_index": scored["k_star"].to_numpy(dtype=np.int64),
            "flagged": scored["flagged"].to_numpy(dtype=bool),
            **{f"risk_{months}m": risk[months] for months in HORIZONS},
            "content_hash": scored["content_hash"].astype(str).to_numpy(),
            "model_name": bundle.name,
            "model_version": bundle.version,
        }
    )
    if with_drivers:
        tick = clock.perf_counter()
        explanations = drivers.explain_horizons(model, landmark_rows, HORIZONS)
        out["drivers"] = [_drivers_json(explanations, values, i) for i in range(len(out))]
        seconds["drivers"] = clock.perf_counter() - tick
    return out, seconds


def score(
    cfg: ProjectConfig,
    day: dt.date,
    bundle: Bundle,
    decisions: Mapping[str, Any],
    warehouse: Path = WAREHOUSE_PATH,
    with_drivers: bool = True,
    only: set[str] | None = None,
) -> Scored:
    """The served scores of every open trial on `day` (see the module text). With `only`,
    the scores of those trials alone (the population and its reasons are still decided on
    every trial; the parity test uses it to stay small)."""
    if tuple(cfg.horizons_months) != HORIZONS:
        raise RefusedError(
            f"the served scores hold the horizons {HORIZONS} (risk_12m and risk_24m); "
            f"config/project.yaml has {tuple(cfg.horizons_months)}"
        )
    seconds: dict[str, float] = {}
    started = clock.perf_counter()
    rules = CohortRules.from_config(cfg, cutoff=max(day, cfg.dataset.cutoff or day))
    interventions = config_glob(RAW_HISTORY_DIR, str(cfg.dataset.revision), "interventions")
    con = served_features.open_session(rules, warehouse, interventions)
    build_cohort_states(con, rules)
    people = classify(population(con, rules, day), day, rules, decisions)
    seconds["population"] = clock.perf_counter() - started
    scored = people[people["reason"].isna()]
    if only is not None:
        scored = scored[scored["trial_id"].isin(only)]
    scored = scored.sort_values("trial_id").reset_index(drop=True)
    keys = served_features.keys_for(
        scored["trial_id"], scored["t0"], scored["true_index"],
        np.full(len(scored), np.datetime64(day, "D")), rules, cfg.features.recent_versions_months,
    )  # fmt: skip
    served_features.register_keys(con, keys)
    features = served_features.compute_served(con, rules, cfg)
    con.close()
    seconds["features"] = clock.perf_counter() - started - seconds["population"]
    out, timing = predict(scored, features, bundle, day, with_drivers)
    seconds.update(timing)
    unscored = people[people["reason"].notna()][["trial_id", "true_index", "reason",
                                                  "content_hash"]].rename(
        columns={"trial_id": "nct_id"}
    ).sort_values("nct_id").reset_index(drop=True)  # fmt: skip
    unscored.insert(1, "as_of_date", np.datetime64(day, "D"))
    seconds["total"] = clock.perf_counter() - started
    return Scored(out, unscored, seconds)


def write(scored: Scored, day: dt.date, folder: Path = SCORES_DIR) -> dict[str, str]:
    """The scores and the unscored trials of a date as Parquet, with their SHA-256."""
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, frame in (("scores", scored.scores), ("unscored", scored.unscored)):
        path = folder / f"{name}_{day.isoformat()}.parquet"
        with duckdb.connect() as con:
            con.register("frame", frame)
            con.execute(f"COPY frame TO '{path.as_posix()}' (FORMAT parquet)")
        out[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def publish_refusal(day: dt.date, bundle: Bundle, with_drivers: bool) -> str | None:
    """Why a run must not reach the serving tables, or None. Scores without drivers are not
    served (CLAUDE.md Section 13, ADR 0033). Nor are the scores of a date before the data
    cutoff of the bundle's training rows: the served champion was fitted on outcomes after
    that date, so its scores there are in-sample, a check of the pipeline, not predictions."""
    if not with_drivers:
        return "a run without drivers is never served"
    cutoff = bundle.meta.get("data_cutoff")
    if cutoff and day < dt.date.fromisoformat(str(cutoff)):
        return (
            f"{bundle.version} was fitted on data through {cutoff}; its scores of {day} are "
            "in-sample and are written to files only"
        )
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score every open trial on a date (Step 13).")
    parser.add_argument("--as-of", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--no-drivers", action="store_true")
    parser.add_argument("--no-serving", action="store_true", help="do not write to Postgres")
    args = parser.parse_args(argv)
    import trialpulse.scoring.champion as champion
    import trialpulse.scoring.serving as serving

    cfg = load_project_config()
    decisions = served_decisions()
    bundle = champion.load_bundle(cfg)
    result = score(cfg, args.as_of, bundle, decisions, with_drivers=not args.no_drivers)
    hashes = write(result, args.as_of)
    print(f"{len(result.scores):,} trials scored, {len(result.unscored):,} open trials not "
          f"scored ({result.unscored['reason'].value_counts().to_dict()})")  # fmt: skip
    print("seconds: " + ", ".join(f"{k} {v:.0f}" for k, v in result.seconds.items()))
    for name, digest in hashes.items():
        print(f"{name}_{args.as_of}.parquet sha256:{digest}")
    held_back = publish_refusal(args.as_of, bundle, with_drivers=not args.no_drivers)
    if args.no_serving:
        print("serving: not written (--no-serving)")
    elif held_back:
        print(f"serving: not written ({held_back})")
    else:
        try:
            counts = serving.publish(result, cfg)
        except serving.EarlierDateError as exc:
            raise RefusedError(f"serving: not written ({exc})") from exc
        print(f"serving: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
