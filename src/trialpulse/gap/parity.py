"""Parity layer 5b of ADR 0038: the gap's real API pull against the release's gap rows (ADR
0041, decision 2; item 6 of the gap read).

    uv run python -m trialpulse.gap.parity

Laptop only (ADR 0040). It reads the kept API pull (data/gap/api/<data date>/), the gap store
and the release (ADR 0073's rules), and writes counts to data/gap/parity_5b.json. In order:

1. **The API rows** of the kept records, converted again by the live path's code
   (`canonical_from_api_v2`, ADR 0073's rule for interventions without a name) in worker
   processes.
2. **What the API never showed.** A pull dated the release shows one version per trial: the
   latest public one. Every other gap version is hidden, by cause, in this order: failed
   review (never public, ADR 0073), the same post date as a later-numbered version,
   superseded by a later post date. A trial's latest version the real pull did not show is
   counted apart: the pull holds a later record (posted after the release), or another one.
3. **The API records the release lacks,** by post date: a record posted in the gap whose trial
   has no release version that day (how complete the release's last days are, which the
   seed date of ADR 0074 rests on).
4. **Field differences** on the versions both hold (the API record and the release's latest
   version of that post date), per canonical column, the columns a served feature reads
   marked, the titles compared by the phase they state.
5. **What the hidden versions change:** Step 13's population and served features on the
   release date, on every version (pinned and gap rows through the release date, combined as
   the rebootstrap combines them) against the API-shaped feed (the pinned rows and each
   trial's latest public gap version): served trials whose features differ, per feature, and
   the population's differences. Beside them, the analog of ADR 0041 scaled to this gap.

It prints and writes counts only, never a trial id, a name or a text.
"""

import argparse
import datetime as dt
import gzip
import json
import os
import time as clock
from collections.abc import Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from trialpulse.cli import RefusedError, run
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.features.design import resolved_title_phase
from trialpulse.gap import rebootstrap
from trialpulse.gap.compare import STORE_PATH
from trialpulse.gap.pull import GAP_DIR
from trialpulse.gap.release import release_glob
from trialpulse.ingest.history import refuse_on_a_runner
from trialpulse.warehouse import census
from trialpulse.warehouse.build import TEMP_DIR, WAREHOUSE_PATH, interventions_glob

RESULT_PATH = GAP_DIR / "parity_5b.json"
PARITY_DIR = REPO_ROOT / "data" / "gap" / "parity"
WORKERS = max(1, min(6, (os.cpu_count() or 2) - 2))
CHUNK = 500
# ADR 0041's analog: at 67 days, 17,748 hidden versions and 4,932 scored rows apart.
ANALOG = {"days": 67, "hidden_versions": 17_748, "scored_rows_apart": 4_932}
TITLES = tuple(census.TITLE_COLUMNS)


# 1: the API rows ----------------------------------------------------------------------------


def _records(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


def _convert(studies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from trialpulse.contracts.versions import canonical_from_api_v2

    out = []
    for study in studies:
        try:
            row, texts = canonical_from_api_v2(study)
        except Exception:  # the job's conversion quarantines these; counted by the pull
            continue
        row["official_title"] = texts.get(row["official_title_hash"] or "")
        row["brief_title"] = texts.get(row["brief_title_hash"] or "")
        out.append(row)
    return out


def api_rows(day_dir: Path, workers: int = WORKERS) -> pd.DataFrame:
    """The canonical rows of the kept records, with their two titles' texts."""
    studies = list(_records(day_dir / "studies.jsonl.gz"))
    chunks = [studies[i : i + CHUNK] for i in range(0, len(studies), CHUNK)]
    if workers <= 1:
        parts = [_convert(c) for c in chunks]
    else:
        with ProcessPoolExecutor(workers, initializer=census._worker_start) as pool:
            parts = list(pool.map(_convert, chunks))
    return pd.DataFrame([row for part in parts for row in part])


# 2 to 4: hidden versions, missing records, field differences --------------------------------


def hidden(con: duckdb.DuckDBPyConnection, cutoff: dt.date, release: dt.date) -> dict[str, Any]:
    """Needs `gap_versions` (nct_id, nct_version, effective_date, failed) and `api`
    (nct_id, effective_date)."""
    d0, d1 = f"DATE '{cutoff.isoformat()}'", f"DATE '{release.isoformat()}'"
    con.execute(f"""CREATE OR REPLACE TEMP TABLE gap_ranked AS
        SELECT g.*,
          EXISTS (SELECT 1 FROM gap_versions h WHERE h.nct_id = g.nct_id
                  AND h.effective_date = g.effective_date AND h.nct_version > g.nct_version)
            AS same_day_later,
          EXISTS (SELECT 1 FROM gap_versions h WHERE h.nct_id = g.nct_id
                  AND h.effective_date > g.effective_date) AS later_day,
          row_number() OVER (PARTITION BY g.nct_id ORDER BY g.failed, g.effective_date DESC,
                             g.nct_version DESC) = 1 AND NOT g.failed AS latest_public
        FROM gap_versions g WHERE g.effective_date > {d0} AND g.effective_date <= {d1}""")
    causes = dict(con.execute(
        """SELECT CASE WHEN failed THEN 'failed review'
                       WHEN same_day_later THEN 'same post date as a later-numbered version'
                       WHEN later_day THEN 'superseded by a later post date'
                       ELSE 'other: an earlier public version' END, count(*)
        FROM gap_ranked WHERE NOT latest_public GROUP BY 1""").fetchall())  # fmt: skip
    after = "the pull holds a record posted after the release"
    within = "other: the pull holds a later record within the release"
    shown = dict(con.execute(
        f"""SELECT CASE WHEN a.nct_id IS NULL THEN 'other: no API record of the trial'
                       WHEN a.effective_date = g.effective_date THEN 'shown by the pull'
                       WHEN a.effective_date > {d1} THEN '{after}'
                       WHEN a.effective_date > g.effective_date THEN '{within}'
                       ELSE 'other: the pull holds an earlier record' END, count(*)
        FROM gap_ranked g LEFT JOIN api a USING (nct_id)
        WHERE g.latest_public GROUP BY 1""").fetchall())  # fmt: skip
    totals = con.execute(
        "SELECT count(*), count(DISTINCT nct_id), count(*) FILTER (WHERE NOT latest_public), "
        "count(DISTINCT nct_id) FILTER (WHERE NOT latest_public) FROM gap_ranked"
    ).fetchone()
    assert totals is not None
    return {
        "gap_versions": int(totals[0]),
        "gap_trials": int(totals[1]),
        "hidden_versions": int(totals[2]),
        "trials_with_a_hidden_version": int(totals[3]),
        "hidden_by_cause": {str(k): int(v) for k, v in sorted(causes.items())},
        "latest_public_versions": {str(k): int(v) for k, v in sorted(shown.items())},
    }


def lacking(con: duckdb.DuckDBPyConnection, cutoff: dt.date, release: dt.date) -> dict[str, Any]:
    """API records posted in the gap whose trial has no release version of that post date."""
    d0, d1 = f"DATE '{cutoff.isoformat()}'", f"DATE '{release.isoformat()}'"
    rows = con.execute(
        f"""SELECT a.effective_date, count(*),
              count(*) FILTER (WHERE NOT EXISTS (
                SELECT 1 FROM gap_versions g WHERE g.nct_id = a.nct_id
                  AND g.effective_date = a.effective_date))
        FROM api a WHERE a.effective_date > {d0} AND a.effective_date <= {d1}
        GROUP BY 1 ORDER BY 1"""
    ).fetchall()
    later = con.execute(f"SELECT count(*) FROM api WHERE effective_date > {d1}").fetchone()
    return {
        "by_post_date": {
            str(d): {"records": int(n), "not_in_the_release": int(m)} for d, n, m in rows
        },
        "records_posted_after_the_release": int(later[0]) if later else 0,
    }


def field_differences(con: duckdb.DuckDBPyConnection, columns: Sequence[str]) -> dict[str, Any]:
    """Needs `api` (canonical rows with titles) and `rel_versions` (the release's canonical
    rows with titles): the API record against the release's last version of its post date."""
    flags = ", ".join(f"NOT (a.{c} IS NOT DISTINCT FROM r.{c}) AS d_{c}" for c in columns)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE pair_flags AS
        SELECT a.nct_id, a.official_title AS ao, a.brief_title AS ab, r.official_title AS ro,
               r.brief_title AS rb, a.content_hash = r.content_hash AS same_hash, {flags}
        FROM api a JOIN (
          SELECT * FROM rel_versions
          QUALIFY row_number() OVER (PARTITION BY nct_id, effective_date
                                     ORDER BY nct_version DESC) = 1
        ) r ON r.nct_id = a.nct_id AND r.effective_date = a.effective_date""")
    sums = ", ".join(f"count(*) FILTER (WHERE d_{c})" for c in columns)
    found = con.execute(f"SELECT count(*), count(*) FILTER (WHERE same_hash), {sums} "
                        "FROM pair_flags").fetchone()  # fmt: skip
    assert found is not None
    by_column = {c: int(n) for c, n in zip(columns, found[2:], strict=True) if n}
    phase_moved = 0
    titles = " OR ".join(f"d_{c}" for c in TITLES if c in columns) or "false"
    for ao, ab, ro, rb in con.execute(
        f"SELECT ao, ab, ro, rb FROM pair_flags WHERE {titles}"
    ).fetchall():
        phase_moved += resolved_title_phase(ao, ab) != resolved_title_phase(ro, rb)
    feature = [c for c in columns if c in census.FEATURE_COLUMNS and c not in TITLES]
    apart = " OR ".join(f"d_{c}" for c in feature) or "false"
    in_features = con.execute(f"SELECT count(*) FROM pair_flags WHERE {apart}").fetchone()
    return {
        "versions_both_hold": int(found[0]),
        "content_hash_equal": int(found[1]),
        "by_column": by_column,
        "feature_columns": sorted(c for c in by_column if c in census.FEATURE_COLUMNS),
        "versions_apart_in_a_feature_column_but_titles": int(in_features[0]) if in_features else 0,
        "versions_whose_titles_state_another_phase": int(phase_moved),
    }


# 5: what the hidden versions change ---------------------------------------------------------


def feature_effect(cfg: ProjectConfig, day: dt.date, combined: Path, interventions: str,
                   api_feed: pd.DataFrame) -> dict[str, Any]:  # fmt: skip
    """Served features on `day` on every combined version against the API-shaped feed."""
    from trialpulse.features.registry import model_columns
    from trialpulse.live.features import served_keys
    from trialpulse.live.replay_parity import frame_differences, offline_on, people_differences
    from trialpulse.scoring import score as offline

    decisions = offline.served_decisions()
    seeded = rebootstrap.seeded_cfg(cfg, day)
    rules = CohortRules.from_config(seeded, cutoff=day)
    empty = pd.DataFrame({"trial_id": pd.Series(dtype="object"),
                          "landmark_index": pd.Series(dtype="int64"),
                          "landmark_date": pd.Series(dtype="datetime64[ns]"),
                          "window_start": pd.Series(dtype="datetime64[ns]")})  # fmt: skip
    first = offline_on(seeded, combined, interventions, day, empty, decisions, None, "6GB")
    scored = first.people[first.people["reason"].isna()]
    keys = served_keys(scored, day, rules, seeded)
    on_all = offline_on(seeded, combined, interventions, day, keys, decisions, None, "6GB")
    on_api = offline_on(seeded, combined, interventions, day, keys, decisions, api_feed, "6GB")
    served = [c for c in model_columns() if c in on_all.features.columns]
    a = on_all.features[served].reset_index(drop=True)
    b = on_api.features[served].reset_index(drop=True)
    same = pd.DataFrame({c: (a[c] == b[c]).fillna(False).astype(bool) | (a[c].isna() & b[c].isna())
                         for c in served})  # fmt: skip
    return {
        "day": day.isoformat(),
        "scored_trials": len(scored),
        "scored_rows_with_a_feature_apart": int((~same.all(axis=1)).sum()),
        "by_feature": dict(sorted(frame_differences(a, b).items(), key=lambda kv: -kv[1])),
        "population": people_differences(on_all.people, on_api.people),
        "seconds": {"all": round(on_all.seconds, 1), "api_feed": round(on_api.seconds, 1)},
    }


def scaled_analog(days: int) -> dict[str, Any]:
    share = days / ANALOG["days"]
    return {
        "analog": ANALOG,
        "this_gap_days": days,
        "scaled_hidden_versions": round(ANALOG["hidden_versions"] * share),
        "scaled_scored_rows_apart": round(ANALOG["scored_rows_apart"] * share),
    }


def seed_summary(cfg: ProjectConfig, release: dt.date, found: dict[str, Any]) -> dict[str, Any]:
    """The seed date of ADR 0074 (decision 5), and the API records the release lacks that are
    posted before the first daily run's pull starts (the seed minus the overlap days): those
    the catch-up would not fetch again."""
    seed = rebootstrap.seed_date(release, found["by_post_date"])
    starts = seed - dt.timedelta(days=cfg.live.overlap_days)
    before = sum(v["not_in_the_release"] for d, v in found["by_post_date"].items()
                 if dt.date.fromisoformat(d) < starts)  # fmt: skip
    return {"seed": seed.isoformat(), "first_pull_starts": starts.isoformat(),
            "lacking_records_before_the_first_pull": before}  # fmt: skip


# The command ------------------------------------------------------------------------------


def latest_pull(root: Path = GAP_DIR / "api") -> Path:
    folders = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    if not folders:
        raise RefusedError("no kept API pull; run: uv run python -m trialpulse.gap.pull")
    return folders[-1]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parity layer 5b of the gap (ADR 0038).")
    parser.add_argument("--skip-features", action="store_true",
                        help="leave out step 5 (the served features on two feeds)")  # fmt: skip
    args = parser.parse_args(argv)
    refuse_on_a_runner()
    cfg = load_project_config()
    if cfg.dataset.cutoff is None or not STORE_PATH.is_file():
        raise RefusedError("needs the pinned cutoff and the gap store (trialpulse.gap.compare)")
    cutoff, release = cfg.dataset.cutoff, rebootstrap.release_date(cfg)
    started = clock.perf_counter()
    pulled = latest_pull()
    api = api_rows(pulled)
    result: dict[str, Any] = {
        "api_pull": pulled.name,
        "api_records_converted": len(api),
        "cutoff": cutoff.isoformat(),
        "release": release.isoformat(),
        "seconds": {"api_rows": round(clock.perf_counter() - started, 1)},
    }
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as con:
        con.execute("SET memory_limit = '6GB'")
        con.execute(f"SET temp_directory = '{TEMP_DIR.as_posix()}'")
        con.execute(f"ATTACH '{STORE_PATH.as_posix()}' AS gap (READ_ONLY)")
        con.register("api_frame", api)
        con.execute("CREATE TEMP TABLE api AS SELECT * FROM api_frame")
        con.unregister("api_frame")
        failed = release_glob(cfg, "core")
        con.execute(f"""CREATE TEMP TABLE gap_versions AS
            SELECT v.nct_id, v.nct_version, v.effective_date, f.nct_id IS NOT NULL AS failed
            FROM gap.versions v LEFT JOIN (
              SELECT nct_id, nct_version FROM read_parquet('{failed}', union_by_name=true)
              WHERE last_update_post_date IS NULL) f USING (nct_id, nct_version)""")
        result["hidden"] = hidden(con, cutoff, release)
        result["api_records_the_release_lacks"] = lacking(con, cutoff, release)
        result["seed"] = seed_summary(cfg, release, result["api_records_the_release_lacks"])
        con.execute("""CREATE TEMP TABLE rel_versions AS
            SELECT v.*, o.text AS official_title, b.text AS brief_title
            FROM gap.versions v LEFT JOIN gap.texts o ON o.text_hash = v.official_title_hash
            LEFT JOIN gap.texts b ON b.text_hash = v.brief_title_hash""")
        columns = [c for c in census.COMPARED if c in api.columns]
        result["fields"] = field_differences(con, columns)
        feed = con.execute(f"""SELECT nct_id, nct_version FROM gap_ranked WHERE latest_public
            AND effective_date <= DATE '{release.isoformat()}'""").df()  # fmt: skip
    result["seconds"]["counts"] = round(clock.perf_counter() - started, 1)
    result["analog"] = scaled_analog((release - cutoff).days)
    if not args.skip_features:
        with duckdb.connect() as con:
            con.execute(f"ATTACH '{WAREHOUSE_PATH.as_posix()}' AS pinned (READ_ONLY)")
            con.execute(f"ATTACH '{STORE_PATH.as_posix()}' AS gap (READ_ONLY)")
            result["combined_through_the_release"] = rebootstrap.combine(
                con, release, interventions_glob(cfg), PARITY_DIR, gap_label="parity 5b"
            )
            pinned_keys = con.execute(
                "SELECT nct_id, nct_version FROM pinned.versions").df()  # fmt: skip
        api_feed = pd.concat([pinned_keys, feed], ignore_index=True)
        result["features"] = feature_effect(
            cfg, release, PARITY_DIR / "warehouse.duckdb",
            rebootstrap.combined_interventions(PARITY_DIR), api_feed)  # fmt: skip
    result["seconds"]["total"] = round(clock.perf_counter() - started, 1)
    RESULT_PATH.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
