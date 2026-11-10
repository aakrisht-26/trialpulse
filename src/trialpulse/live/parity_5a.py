"""Parity layer 5a of ADR 0038: the offline path on the pinned, gap and exported live rows,
against the live risks on Neon for the latest run date.

    uv run python -m trialpulse.live.parity_5a

Laptop only (ADR 0040: it reads the dataset's rows), and read-only on Neon. In order:

1. **Neon, one read-only snapshot:** the latest succeeded daily run's date D (API v2's data
   date), every row of `live.study_versions` (the versions the live path added since go-live),
   and the served rows dated D: `serving.trial_scores_latest` and `serving.trials_not_scored`.
2. **The 5a warehouse** in data/parity_5a/: a copy of the rebootstrap's combined warehouse
   (the pinned rows and the gap rows through the seed, ADR 0074), with the live versions
   appended through the export adapter (ADR 0037, `live.export.export_frame`): canonical
   rows numbered after each trial's last version, the stand-in title of each stated phase,
   and the intervention types as rows of the interventions source. The live rows carry a
   keyed sponsor id (ADR 0044); it is mapped back to the sponsor key whose HMAC it is, over
   every key the combined rows hold, so both paths see one identity per sponsor (an id with
   no such key, a sponsor first seen live, stays as its id).
3. **The offline path:** Step 13's code (`scoring.score.score`) on that warehouse, dated D,
   written in the serving tables' compact form (`scoring.serving.latest_frame`).
4. **The comparison,** on the trials Neon served on D (a daily run rescores only the trials
   it touched, so the other served rows keep an earlier date and are not compared): every
   column of the served rows (risks, flags, indexes, model, the four driver arrays) and of
   the unscored rows (index, reason). Equal on every column is the bar, as at the reference
   check. A trial on one side only is counted by which side. Content hashes are counted
   apart: the live rows' are feature hashes, the offline rows' canonical hashes (ADR 0037).

Nothing is changed to make it pass. It prints and writes counts only (data/parity_5a/), never
a trial id, a name or a connection string.
"""

import argparse
import datetime as dt
import hashlib
import hmac
import json
import shutil
import time as clock
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from trialpulse.cli import RefusedError, run
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import REPO_ROOT, ProjectConfig, Secrets, load_project_config
from trialpulse.contracts.sponsor import has_identity
from trialpulse.contracts.versions import COLUMN_TYPES
from trialpulse.live import export

OUT_DIR = REPO_ROOT / "data" / "parity_5a"
EXPORT_DIR = REPO_ROOT / "data" / "live_exports"  # the retention's exports (ADR 0069)
COMPARED = ("as_of_date", "landmark_index", "true_landmark_index", "flagged", "model_version",
            "risk_12m", "risk_24m", "driver_months", "driver_features", "driver_values",
            "driver_contributions")  # fmt: skip
UNSCORED_COMPARED = ("as_of_date", "true_landmark_index", "reason")
CONTRIBUTION_TOLERANCE = 5e-4  # Neon stores contributions as float4 of 3 decimals
# ADR 0038, amended 2026-11-10: the risks within ADR 0034's reproduction tolerance, since the
# live risks come from a Linux runner and 5a runs on Windows (platform math, last bits only)
RISK_TOLERANCE = 1e-12


def read_neon(dsn: str) -> dict[str, Any]:
    """The latest run's date, the live versions and the served rows of that date."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True, connect_timeout=30) as con, con.transaction():
        con.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        row = con.execute(
            """SELECT max(data_timestamp) FROM monitoring.pipeline_runs
                    WHERE job = 'daily' AND status = 'succeeded'"""
        ).fetchone()
        if row is None or row[0] is None:
            raise RefusedError("Neon holds no succeeded daily run")
        day: dt.date = row[0]
        versions = _frame(con, "SELECT * FROM live.study_versions")
        latest = _frame(con, "SELECT * FROM serving.trial_scores_latest WHERE as_of_date = %s",
                        (day,))  # fmt: skip
        unscored = _frame(
            con, "SELECT * FROM serving.trials_not_scored WHERE as_of_date = %s", (day,)
        )
        dates = con.execute(
            """SELECT as_of_date, count(*) FROM serving.trial_scores_latest
                    GROUP BY 1 ORDER BY 1"""
        ).fetchall()
    return {"day": day, "versions": versions, "latest": latest, "unscored": unscored,
            "served_dates": {str(d): int(n) for d, n in dates}}  # fmt: skip


def _frame(con: Any, query: str, params: Sequence[Any] = ()) -> pd.DataFrame:
    cur = con.execute(query, params)
    names = [c.name for c in cur.description]
    return pd.DataFrame(cur.fetchall(), columns=names)


def with_exported(versions: pd.DataFrame, export_dir: Path = EXPORT_DIR) -> pd.DataFrame:
    """Neon's live versions with those the retention exported to the laptop and deleted
    (ADR 0069), once each: after a retention, Neon alone no longer holds every live version
    since go-live, which the offline path of 5a needs."""
    files = sorted(export_dir.glob("live_study_versions_before_*.parquet"))
    if not files:
        return versions
    exported = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    both = pd.concat([versions, exported[versions.columns]], ignore_index=True)
    key = both["feature_hash"].map(lambda h: bytes(h) if h is not None else None)
    both = both.assign(_key=key).drop_duplicates(["nct_id", "effective_date", "_key"])
    return both.drop(columns="_key").reset_index(drop=True)


def sponsor_keys_by_id(keys: Sequence[str], secret: bytes) -> dict[str, str]:
    """The sponsor key of each keyed id (ADR 0044), over `keys`."""
    return {hmac.new(secret, k.encode("utf-8"), hashlib.sha256).hexdigest(): k for k in keys}


def live_rows(versions: pd.DataFrame, top: Mapping[str, int],
              by_id: Mapping[str, str]) -> list[dict[str, Any]]:  # fmt: skip
    """The live versions as compact rows for the export adapter: numbered after each trial's
    last combined version, in (post date, ingest) order, with sponsor ids mapped to keys."""
    rows = versions.sort_values(["nct_id", "effective_date", "ingested_at"]).to_dict("records")
    nxt = {n: v + 1 for n, v in top.items()}
    out = []
    for r in rows:
        nct = str(r["nct_id"])
        r["nct_version"] = nxt.get(nct, 0)
        nxt[nct] = r["nct_version"] + 1
        sponsor = r.get("sponsor_id")
        r["sponsor_id"] = by_id.get(sponsor, sponsor) if sponsor else None
        fh = r.get("feature_hash")
        r["feature_hash"] = bytes(fh).hex() if fh is not None else None
        r["intervention_types"] = list(r.get("intervention_types") or [])
        out.append(r)
    return out


def build_warehouse(combined: Path, combined_iv: str, versions: pd.DataFrame, secret: bytes,
                    out_dir: Path = OUT_DIR) -> tuple[Path, str, dict[str, int]]:  # fmt: skip
    """The 5a warehouse and its interventions glob (see the module text); counts."""
    if out_dir.exists():
        shutil.rmtree(out_dir / "interventions", ignore_errors=True)
        (out_dir / "warehouse.duckdb").unlink(missing_ok=True)
    (out_dir / "interventions").mkdir(parents=True, exist_ok=True)
    warehouse = out_dir / "warehouse.duckdb"
    shutil.copyfile(combined, warehouse)
    for f in Path(combined_iv).parent.glob(Path(combined_iv).name):
        shutil.copyfile(f, out_dir / "interventions" / f.name)
    with duckdb.connect(str(warehouse)) as con:
        found = con.execute(
            "SELECT sponsor_key, bool_or(sponsor_is_individual) FROM versions "
            "WHERE sponsor_key IS NOT NULL GROUP BY 1"
        ).fetchall()
        keys = [k for k, ind in found if has_identity(k, ind)]
        by_id = sponsor_keys_by_id(keys, secret)
        trials = sorted(set(versions["nct_id"].astype(str)))
        con.execute("CREATE TEMP TABLE wanted (nct_id VARCHAR)")
        con.executemany("INSERT INTO wanted VALUES (?)", [(t,) for t in trials])
        top = dict(
            con.execute(
                "SELECT nct_id, max(nct_version) FROM versions "
                "SEMI JOIN wanted USING (nct_id) GROUP BY 1"
            ).fetchall()
        )
        rows = live_rows(versions, top, by_id)
        frame = export.export_frame(rows)
        source = frame.astype(object)  # noqa: F841 (read by DuckDB)
        columns = ", ".join(f"CAST({c} AS {t}) AS {c}" for c, t in COLUMN_TYPES.items())
        con.execute(f"INSERT INTO versions BY NAME SELECT {columns} FROM source")
        stand_ins = pd.DataFrame({"text_hash": [export.title_hash(p) for p in export.PHASE_TITLES],  # noqa: F841
                              "text": list(export.PHASE_TITLES.values())})  # fmt: skip
        con.execute("""INSERT INTO texts BY NAME SELECT CAST(text_hash AS VARCHAR) AS text_hash,
                         CAST(text AS VARCHAR) AS text FROM stand_ins t
                       WHERE NOT EXISTS (SELECT 1 FROM main.texts m
                                         WHERE m.text_hash = t.text_hash)""")  # fmt: skip
        schema = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{combined_iv}')").fetchall()
        iv = pd.DataFrame([{"nct_id": r["nct_id"], "nct_version": r["nct_version"],
                            "intervention_type": t}
                           for r in rows for t in r["intervention_types"]],
                          columns=["nct_id", "nct_version", "intervention_type"])  # fmt: skip
        select = ", ".join(
            f"CAST({name} AS {kind}) AS {name}" if name in iv.columns
            else f"CAST(NULL AS {kind}) AS {name}" for name, kind, *_ in schema
        )  # fmt: skip
        target = (out_dir / "interventions" / "live.parquet").as_posix()
        con.execute(f"COPY (SELECT {select} FROM iv) TO '{target}' (FORMAT parquet)")
        mapped = sum(1 for r in rows if r["sponsor_id"] and r["sponsor_id"] in by_id.values())
    counts = {"live_versions": len(rows), "live_trials": len(trials),
              "live_trials_new_since_the_seed": sum(1 for t in trials if t not in top),
              "live_versions_with_a_mapped_sponsor": mapped,
              "live_versions_without_a_sponsor_id": sum(1 for r in rows if not r["sponsor_id"]),
              "live_intervention_rows": len(iv)}  # fmt: skip
    return warehouse, (out_dir / "interventions" / "*.parquet").as_posix(), counts


def _same(a: Any, b: Any, column: str) -> bool:
    if column == "driver_contributions":
        a, b = list(a), list(b)
        return len(a) == len(b) and all(abs(float(x) - float(y)) <= CONTRIBUTION_TOLERANCE
                                        for x, y in zip(a, b, strict=True))  # fmt: skip
    if column.startswith("driver_"):
        return [str(x) for x in a] == [str(x) for x in b]
    if a is None or b is None or (isinstance(a, float) and pd.isna(a)):
        return (a is None or pd.isna(a)) and (b is None or pd.isna(b))
    if column.startswith("risk_"):
        return abs(float(a) - float(b)) <= RISK_TOLERANCE
    return str(a) == str(b)


def compare(offline: pd.DataFrame, live: pd.DataFrame, columns: Sequence[str]) -> dict[str, Any]:
    """Every column of the trials on both sides, equal or different, by count."""
    both = live.merge(offline, on="nct_id", how="left", suffixes=("", "_off"), indicator=True)
    matched = both[both["_merge"] == "both"]
    out: dict[str, Any] = {"live_rows": len(live), "on_both": len(matched),
                           "live_only": int((both["_merge"] == "left_only").sum()),
                           "columns": {}}  # fmt: skip
    for column in columns:
        same = [_same(a, b, column) for a, b in zip(matched[column], matched[f"{column}_off"],
                                                    strict=True)]  # fmt: skip
        entry: dict[str, Any] = {"equal": sum(same), "different": len(same) - sum(same)}
        if column.startswith("risk_") and len(matched):
            entry["max_abs_difference"] = float(
                (matched[column].astype(float) - matched[f"{column}_off"].astype(float)).abs().max()
            )
        out["columns"][column] = entry
    if "content_hash" in live.columns:
        hashes = [bytes(a) == bytes(b) if a is not None and b is not None else a is b
                  for a, b in zip(matched["content_hash"], matched["content_hash_off"],
                                  strict=True)]  # fmt: skip
        out["content_hash_apart"] = {"equal": sum(hashes), "different": len(hashes) - sum(hashes)}
    return out


def run_5a(cfg: ProjectConfig, dsn: str, secret: bytes, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    from trialpulse.gap import rebootstrap
    from trialpulse.scoring import score as offline
    from trialpulse.scoring.bundle import CHAMPION_DIR, load_bundle
    from trialpulse.scoring.serving import latest_frame, unscored_frame

    started = clock.perf_counter()
    if not rebootstrap.COMBINED_WAREHOUSE.is_file():
        raise RefusedError("no combined warehouse: run the rebootstrap's combine step first")
    neon = read_neon(dsn)
    on_neon = len(neon["versions"])
    neon["versions"] = with_exported(neon["versions"])
    day = neon["day"]
    seconds = {"neon": round(clock.perf_counter() - started, 1)}
    warehouse, interventions, counts = build_warehouse(
        rebootstrap.COMBINED_WAREHOUSE, rebootstrap.combined_interventions(), neon["versions"],
        secret, out_dir)  # fmt: skip
    seconds["warehouse"] = round(clock.perf_counter() - started - seconds["neon"], 1)
    bundle = load_bundle(cfg, CHAMPION_DIR)
    scored = offline.score(rebootstrap.seeded_cfg(cfg, day), day, bundle,
                           offline.served_decisions(), warehouse=warehouse,
                           interventions=interventions)  # fmt: skip
    seconds["score"] = round(scored.seconds.get("total", 0.0), 1)
    off_latest, off_unscored = latest_frame(scored), unscored_frame(scored)
    latest, unscored = neon["latest"], neon["unscored"]
    for frame in (latest, unscored):
        frame["as_of_date"] = pd.to_datetime(frame["as_of_date"]).dt.date
    # a trial Neon scored on D that the offline path did not score, or the reverse
    crossed = {
        "scored_live_unscored_offline": int(latest["nct_id"].isin(off_unscored["nct_id"]).sum()),
        "unscored_live_scored_offline": int(unscored["nct_id"].isin(off_latest["nct_id"]).sum()),
    }
    result = {
        "day": day.isoformat(),
        "served_rows_by_date": neon["served_dates"],
        "live_versions": {
            "on_neon": on_neon,
            "from_retention_exports": len(neon["versions"]) - on_neon,
        },
        "warehouse": counts,
        "offline": {"scored": len(off_latest), "unscored": len(off_unscored)},
        "scores": compare(off_latest, latest, COMPARED),
        "unscored": compare(off_unscored, unscored, UNSCORED_COMPARED),
        "crossed": crossed,
        "seconds": {**seconds, "total": round(clock.perf_counter() - started, 1)},
    }
    result["differences"] = differences(result)
    result["equal"] = not result["differences"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "parity_5a.json").write_text(json.dumps(result, indent=2, default=str) + "\n",
                                            "utf-8")  # fmt: skip
    return result


def differences(result: Mapping[str, Any]) -> list[str]:
    out = []
    for part in ("scores", "unscored"):
        if result[part]["live_only"]:
            out.append(f"{part}: {result[part]['live_only']} served on Neon only")
        for column, entry in result[part]["columns"].items():
            if entry["different"]:
                out.append(f"{part}.{column}: {entry['different']} differ")
    out += [f"{k}: {v}" for k, v in result["crossed"].items() if v]
    return out


def main(argv: Sequence[str] | None = None) -> int:
    from trialpulse.ingest.history import refuse_on_a_runner
    from trialpulse.live.daily import _dsn

    argparse.ArgumentParser(description="Parity layer 5a (ADR 0038).").parse_args(argv)
    refuse_on_a_runner()
    key = Secrets().sponsor_id_key
    if key is None:
        raise RefusedError("SPONSOR_ID_KEY is not set (environment or .env)")
    cfg = load_project_config()
    CohortRules.from_config(cfg)  # the configuration is valid before anything is read
    result = run_5a(cfg, _dsn("neon"), key.get_secret_value().encode("utf-8"))
    print(json.dumps(result, indent=2, default=str))
    if not result["equal"]:
        print(f"parity 5a: {len(result['differences'])} kinds of difference (counted above)")
        return 1
    print("parity 5a: the offline path equals the live risks on every compared column")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
