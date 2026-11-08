"""The rebootstrap of ADR 0041 (decision 3), seeded from the pinned rows and the gap rows,
and its reference check (ADR 0074).

    uv run python -m trialpulse.gap.rebootstrap combine --seed 2026-11-02
    uv run python -m trialpulse.gap.rebootstrap reference --seed 2026-11-02
    uv run python -m trialpulse.gap.rebootstrap bootstrap --seed 2026-11-02 --target local --rebuild
    uv run python -m trialpulse.gap.rebootstrap check --seed 2026-11-02 --target local

Laptop only (ADR 0040): every step reads the dataset's rows and refuses under GitHub Actions.
The steps, in order:

1. **combine:** data/gap/combined/warehouse.duckdb, the pinned warehouse's versions with the
   gap store's versions posted on or before the seed date (ADR 0073's rules on both), their
   texts and the pinned warehouse's stop reasons; and data/gap/combined/interventions/, the
   pinned interventions config with the gap store's rows of those versions. It refuses a gap
   version that is not after every pinned version of its trial, by number and by post date,
   or a seed outside the tail of 1 to 7 days before the release date. Nothing pinned is
   modified.
2. **reference:** Step 13's code (`scoring.score.score`) on the combined rows, dated the seed:
   the reference files `scores_<seed>.parquet` and `unscored_<seed>.parquet` in
   data/gap/reference/, with their SHA-256 values. They take the place Step 13's files take
   for the bootstrap at the cutoff, since Step 13 wrote none for a later date.
3. **bootstrap:** `live.bootstrap.run_bootstrap` dated the seed, on the combined rows, held to
   the reference files (the same rows regenerated), with the reason counts of the pinned
   cutoff (`labeled_through` stays 2026-09-25, ADR 0050).
4. **check:** the day-0 check of `live.dayzero` at the seed date against the reference files:
   the live path's scores from the seeded state, computed as the daily job computes them,
   must equal the reference on every trial and column (risks, drivers, flags, reasons, the
   true and the landmark index, the model version), and the content hashes of the served
   rows must equal the live path's.

It prints counts and hashes only, never a trial id, a name or a text.
"""

import argparse
import datetime as dt
import json
import re
import time as clock
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb

from trialpulse.cli import RefusedError, run
from trialpulse.config import REPO_ROOT, ProjectConfig, Secrets, load_project_config
from trialpulse.gap.compare import STORE_PATH
from trialpulse.gap.release import gap_config
from trialpulse.ingest.history import refuse_on_a_runner
from trialpulse.warehouse.build import TEMP_DIR, WAREHOUSE_PATH, interventions_glob

COMBINED_DIR = REPO_ROOT / "data" / "gap" / "combined"
COMBINED_WAREHOUSE = COMBINED_DIR / "warehouse.duckdb"
COMBINED_INTERVENTIONS = COMBINED_DIR / "interventions"
REFERENCE_DIR = REPO_ROOT / "data" / "gap" / "reference"
TAIL_DAYS = (1, 7)  # the seed is the release date minus 1 to 7 days (ADR 0041, decision 3)
MEMORY_LIMIT = "6GB"


def release_date(cfg: ProjectConfig) -> dt.date:
    """The release's date, from its tag (vYYYY.MM.DD)."""
    tag = gap_config(cfg).tag
    found = re.fullmatch(r"v(\d{4})\.(\d{2})\.(\d{2})", tag)
    if found is None:
        raise RefusedError(f"the release tag {tag} does not name a date")
    return dt.date(*(int(g) for g in found.groups()))


def check_seed(cfg: ProjectConfig, seed: dt.date) -> None:
    released = release_date(cfg)
    first, last = (released - dt.timedelta(days=d) for d in reversed(TAIL_DAYS))
    if not first <= seed <= last:
        raise RefusedError(f"the seed {seed} is outside the tail of 1 to 7 days before the "
                           f"release of {released} ({first} to {last})")  # fmt: skip
    if cfg.dataset.cutoff is None or seed <= cfg.dataset.cutoff:
        raise RefusedError("the seed must come after the pinned cutoff")


def seed_date(released: dt.date, lacking_by_day: dict[str, dict[str, int]]) -> dt.date:
    """ADR 0074, decision 5: the latest day, 1 to 7 days before the release, on which the API
    pull holds no record the release lacks (`lacking_by_day` as parity layer 5b writes it,
    keyed by ISO date; a day without records lacks none)."""
    for days in range(TAIL_DAYS[0], TAIL_DAYS[1] + 1):
        day = released - dt.timedelta(days=days)
        if lacking_by_day.get(day.isoformat(), {}).get("not_in_the_release", 0) == 0:
            return day
    raise RefusedError(f"every day of the tail before the release of {released} has API "
                       "records the release lacks: no seed date (ADR 0074)")  # fmt: skip


def seeded_cfg(cfg: ProjectConfig, seed: dt.date) -> ProjectConfig:
    """The configuration of the rebootstrap: the data reach the seed date."""
    return cfg.model_copy(update={"dataset": cfg.dataset.model_copy(update={"cutoff": seed})})


# 1: combine ---------------------------------------------------------------------------------


def combine(
    con: duckdb.DuckDBPyConnection,
    seed: dt.date,
    pinned_interventions: str,
    out_dir: Path = COMBINED_DIR,
    gap_label: str = "",
) -> dict[str, Any]:
    """The combined warehouse and interventions config (step 1), from a connection with the
    pinned warehouse attached as `pinned` and the gap store as `gap`."""
    refuse_on_a_runner()
    day = f"DATE '{seed.isoformat()}'"
    clash = con.execute(
        f"""SELECT count(*) FROM gap.versions g
        JOIN (SELECT nct_id, max(nct_version) AS v, max(effective_date) AS d
              FROM pinned.versions GROUP BY nct_id) p USING (nct_id)
        WHERE g.effective_date <= {day} AND (g.nct_version <= p.v OR g.effective_date <= p.d)"""
    ).fetchone()
    if clash and clash[0]:
        raise RefusedError(f"{clash[0]} gap versions are not after every pinned version of "
                           "their trial; the combined rows would not be one history")  # fmt: skip
    out_dir.mkdir(parents=True, exist_ok=True)
    warehouse = out_dir / "warehouse.duckdb"
    building = warehouse.with_name(warehouse.name + ".building")
    building.unlink(missing_ok=True)
    con.execute(f"ATTACH '{building.as_posix()}' AS comb")
    con.execute(f"""CREATE TABLE comb.versions AS
        SELECT * FROM pinned.versions
        UNION ALL SELECT * FROM gap.versions WHERE effective_date <= {day}
        ORDER BY nct_id, nct_version""")
    con.execute("""CREATE TABLE comb.texts AS
        SELECT * FROM pinned.texts
        UNION ALL SELECT * FROM gap.texts g
        WHERE NOT EXISTS (SELECT 1 FROM pinned.texts p WHERE p.text_hash = g.text_hash)
        ORDER BY text_hash""")
    con.execute("CREATE TABLE comb.stop_reasons AS SELECT * FROM pinned.stop_reasons")
    con.execute("CREATE TABLE comb.build_info AS SELECT * FROM pinned.build_info")
    con.execute("""INSERT INTO comb.build_info VALUES ('combined.gap', ?), ('combined.seed', ?)""",
                [gap_label, seed.isoformat()])  # fmt: skip
    counts = {
        name: int((con.execute(f"SELECT count(*) FROM comb.{name}").fetchone() or (0,))[0])
        for name in ("versions", "texts")
    }
    gap_rows = con.execute(f"SELECT count(*), count(DISTINCT nct_id) FROM gap.versions "
                           f"WHERE effective_date <= {day}").fetchone()  # fmt: skip
    assert gap_rows is not None
    counts.update(gap_versions=int(gap_rows[0]), gap_trials=int(gap_rows[1]))
    con.execute("DETACH comb")
    building.replace(warehouse)
    folder = out_dir / "interventions"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.parquet"):
        old.unlink()
    target = (folder / "part-0.parquet").as_posix()
    con.execute(f"""COPY (
        SELECT nct_id, nct_version, intervention_name, intervention_type, description
        FROM read_parquet('{pinned_interventions}', union_by_name=true)
        UNION ALL
        SELECT i.nct_id, i.nct_version, i.intervention_name, i.intervention_type, i.description
        FROM gap.interventions i JOIN gap.versions v USING (nct_id, nct_version)
        WHERE v.effective_date <= {day}
        ORDER BY nct_id, nct_version, intervention_name, intervention_type
    ) TO '{target}' (FORMAT parquet, ROW_GROUP_SIZE 200000)""")
    iv = con.execute(f"SELECT count(*) FROM read_parquet('{target}')").fetchone()
    counts["intervention_rows"] = int(iv[0]) if iv else 0
    return counts


def combined_interventions(out_dir: Path = COMBINED_DIR) -> str:
    return (out_dir / "interventions" / "*.parquet").as_posix()


# The steps --------------------------------------------------------------------------------


def _connect() -> duckdb.DuckDBPyConnection:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{MEMORY_LIMIT}'")
    con.execute(f"SET temp_directory = '{TEMP_DIR.as_posix()}'")
    return con


def step_combine(cfg: ProjectConfig, seed: dt.date) -> dict[str, Any]:
    if not STORE_PATH.is_file():
        raise RefusedError(f"no gap store at {STORE_PATH}; run: uv run python -m "
                           "trialpulse.gap.compare")  # fmt: skip
    gap = gap_config(cfg)
    with _connect() as con:
        con.execute(f"ATTACH '{WAREHOUSE_PATH.as_posix()}' AS pinned (READ_ONLY)")
        con.execute(f"ATTACH '{STORE_PATH.as_posix()}' AS gap (READ_ONLY)")
        return combine(con, seed, interventions_glob(cfg), gap_label=f"{gap.tag}@{gap.commit}")


def inputs() -> Any:
    """The bootstrap's inputs on the combined rows, held to the reference files (named by
    the seed date, as Step 13's are named by theirs)."""
    from trialpulse.cohort.build import OUTCOMES_PATH
    from trialpulse.live.bootstrap import Inputs
    from trialpulse.scoring import checks
    from trialpulse.scoring.bundle import CHAMPION_DIR

    return Inputs(COMBINED_WAREHOUSE, CHAMPION_DIR, checks.RESULT_PATH, OUTCOMES_PATH,
                  REFERENCE_DIR, interventions=combined_interventions(),
                  reasons_warehouse=WAREHOUSE_PATH)  # fmt: skip


def step_reference(cfg: ProjectConfig, seed: dt.date) -> dict[str, Any]:
    from trialpulse.scoring import score as offline
    from trialpulse.scoring.bundle import load_bundle

    if not COMBINED_WAREHOUSE.is_file():
        raise RefusedError("no combined warehouse; run the combine step first")
    found = inputs()
    bundle = load_bundle(cfg, found.bundle)
    decisions = offline.served_decisions(found.decisions)
    scored = offline.score(
        seeded_cfg(cfg, seed), seed, bundle, decisions,
        warehouse=COMBINED_WAREHOUSE, interventions=found.interventions,
    )  # fmt: skip
    hashes = offline.write(scored, seed, REFERENCE_DIR)
    return {
        "scored": len(scored.scores),
        "unscored": len(scored.unscored),
        "reasons": scored.unscored["reason"].value_counts().to_dict(),
        "flagged": int(scored.scores["flagged"].sum()),
        "sha256": hashes,
        "seconds": {k: round(v, 1) for k, v in scored.seconds.items()},
    }


def step_bootstrap(cfg: ProjectConfig, seed: dt.date, target: str, rebuild: bool) -> dict[str, Any]:
    from trialpulse.live import bootstrap
    from trialpulse.live.daily import _dsn

    key = Secrets().sponsor_id_key
    if key is None:
        raise RefusedError("SPONSOR_ID_KEY is not set (environment or .env)")
    assert cfg.dataset.cutoff is not None
    return bootstrap.run_bootstrap(
        seeded_cfg(cfg, seed), _dsn(target), key.get_secret_value().encode("utf-8"), rebuild,
        inputs(), say=lambda line: print(line, flush=True), day=seed,
        labeled_through=cfg.dataset.cutoff,
    )  # fmt: skip


def step_check(cfg: ProjectConfig, seed: dt.date, target: str) -> dict[str, Any]:
    from trialpulse.live import daily, dayzero
    from trialpulse.live.daily import _dsn

    found = inputs()
    reference = found.step13_files(seed)
    model = daily.champion_model(cfg, found.bundle, found.decisions)
    return dayzero.check_cutoff(seeded_cfg(cfg, seed), _dsn(target), model, reference)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The rebootstrap of ADR 0041 (ADR 0074).")
    parser.add_argument("step", choices=("combine", "reference", "bootstrap", "check"))
    parser.add_argument("--seed", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--target", choices=("local", "neon"))
    parser.add_argument("--rebuild", action="store_true",
                        help="clear the live state and start it again (bootstrap)")  # fmt: skip
    args = parser.parse_args(argv)
    refuse_on_a_runner()
    cfg = load_project_config()
    check_seed(cfg, args.seed)
    if args.step in ("bootstrap", "check") and args.target is None:
        raise RefusedError(f"the {args.step} step needs --target local or --target neon")
    started = clock.perf_counter()
    if args.step == "combine":
        result = step_combine(cfg, args.seed)
    elif args.step == "reference":
        result = step_reference(cfg, args.seed)
    elif args.step == "bootstrap":
        result = step_bootstrap(cfg, args.seed, str(args.target), args.rebuild)
    else:
        result = step_check(cfg, args.seed, str(args.target))
    result["step_seconds"] = round(clock.perf_counter() - started, 1)
    print(json.dumps(result, indent=2, default=str))
    if args.step == "check":
        apart = result["content_hash_against_served_rows"]["different"]
        if result["differences"] or apart:
            print(f"failed: {len(result['differences'])} differences, {apart} served rows "
                  "with another content hash")  # fmt: skip
            return 1
    if args.step == "check":
        print("reference check: the live path equals the reference on every trial, risk "
              "and driver")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
