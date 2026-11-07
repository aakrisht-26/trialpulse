"""The overlap check and the gap store (ADR 0041; items 4 and 5 of the gap read, ADR 0073).

    uv run python -m trialpulse.gap.compare

Laptop only (ADR 0040): it reads the pinned warehouse and the release at `gap_read.commit`,
and writes under data/gap/ only. It prints and writes counts, never a trial id, a name or a
text. In order:

1. **Adapt.** The release's `core` and `interventions` configs through `gap.adapter`, under
   the decisions of ADR 0073, written as Parquet to data/gap/release/adapted/.
2. **Canonical rows.** The warehouse build (`warehouse.build.build`) on the adapted files,
   into data/gap/release/warehouse.duckdb: the same conversion, texts and sponsor rules as
   the pinned warehouse. Its build_info names the release, not the pinned revision.
3. **The overlap check.** Every version posted on or before the pinned cutoff, in either
   warehouse, matched by (nct_id, nct_version):
   - a key in one warehouse only is counted by cause (in the other but posted after the
     cutoff, or not there at all);
   - on the keys in both, the content hash, and every compared column with its cause. A
     column a served feature reads (`census.FEATURE_COLUMNS`) is compared as the feature
     reads it: the status as the submitted status (ADR 0066), the titles by the phase they
     state; a difference is "the release changed the raw value" when the raw values of the
     two releases differ, and a bug when they are the same;
   - **agreement** is the share of all keys posted by the cutoff that are in both and agree
     on every column a served feature reads. Below `MIN_AGREEMENT` the command stops before
     the store.
4. **The gap store.** The release's versions posted after the cutoff, their texts and their
   adapted intervention rows, into data/gap/store.duckdb, apart from everything pinned.

The counts go to data/gap/overlap.json.
"""

import argparse
import datetime as dt
import json
import logging
import os
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb

from trialpulse.cli import StoppedEarlyError, run
from trialpulse.cohort.rules import CohortRules, submitted_status_sql
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.features.design import resolved_title_phase
from trialpulse.gap import adapter
from trialpulse.gap.release import gap_config, release_glob
from trialpulse.ingest.history import refuse_on_a_runner
from trialpulse.warehouse import census
from trialpulse.warehouse.build import (
    TEMP_DIR,
    WAREHOUSE_PATH,
    build,
    dataset_glob,
    interventions_glob,
)

GAP_DIR = REPO_ROOT / "data" / "gap"
ADAPTED_DIR = GAP_DIR / "release" / "adapted"
RELEASE_WAREHOUSE = GAP_DIR / "release" / "warehouse.duckdb"
STORE_PATH = GAP_DIR / "store.duckdb"
RESULT_PATH = GAP_DIR / "overlap.json"
MIN_AGREEMENT = 0.999  # on the columns a served feature reads (the gap read's item 4)
MEMORY_LIMIT = "8GB"
log = logging.getLogger(__name__)

# Canonical columns and the raw_versions column a difference is traced to.
RAW_OF: dict[str, str] = {
    "effective_date": "last_update_post_date",
    "effective_date_type": "last_update_post_date_type",
    "submitted_date": "last_update_submit_date",
    "minimum_age_years": "minimum_age",
    "maximum_age_years": "maximum_age",
}
TITLES = tuple(census.TITLE_COLUMNS)
SPONSOR = ("lead_sponsor_name", "sponsor_key", "sponsor_is_individual")
STATUS = census.STATUS_COLUMNS
RELEASE_CHANGED = "the release changed the raw value"
BUG = "bug: the same raw value gives another canonical value"
TITLE_SAME_PHASE = "title text, the same title phase"
TITLE_PHASE_MOVED = "the titles state another phase"
STATUS_LABEL_ONLY = "the registry's UNKNOWN label only (the submitted status is equal)"


def _lit(text: str) -> str:
    """A SQL string literal."""
    return "'" + text.replace("'", "''") + "'"


# 1 and 2: the adapted files and the release warehouse ---------------------------------------


def adapt_to_parquet(
    con: duckdb.DuckDBPyConnection, cfg: ProjectConfig, out_dir: Path = ADAPTED_DIR
) -> dict[str, Any]:
    """Write the adapted configs as Parquet (step 1) and count what the adapter changed."""
    refuse_on_a_runner()
    new_core, new_iv = release_glob(cfg, "core"), release_glob(cfg, "interventions")
    adapted = adapter.adapt(con, new_core, new_iv, dataset_glob(cfg), interventions_glob(cfg))
    for name, sql in (("core", adapted.core_sql), ("interventions", adapted.interventions_sql)):
        folder = out_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.parquet"):
            old.unlink()
        con.execute(f"COPY ({sql}) TO '{(folder / 'part-0.parquet').as_posix()}' "
                    "(FORMAT parquet, ROW_GROUP_SIZE 100000)")  # fmt: skip
    return adapter.left_out(con, new_core, new_iv)


def adapted_glob(name: str, out_dir: Path = ADAPTED_DIR) -> str:
    return (out_dir / name / "*.parquet").as_posix()


def release_cfg(cfg: ProjectConfig) -> ProjectConfig:
    """The project config with the dataset section naming the release, for build_info."""
    gap = gap_config(cfg)
    dataset = cfg.dataset.model_copy(update={"repo_id": gap.repo_id,
                                             "revision": f"{gap.tag}@{gap.commit}",
                                             "cutoff": None})  # fmt: skip
    return cfg.model_copy(update={"dataset": dataset})


def build_release(
    cfg: ProjectConfig, workers: int, out: Path = RELEASE_WAREHOUSE
) -> dict[str, Any]:
    """The release's canonical rows by the pinned warehouse's own build (step 2)."""
    refuse_on_a_runner()
    return build(adapted_glob("core"), release_cfg(cfg), out, workers, TEMP_DIR,
                 interventions=adapted_glob("interventions"))  # fmt: skip


# 3: the overlap check ------------------------------------------------------------------------


def _same(column: str, left: str = "p", right: str = "r") -> str:
    return f"{left}.{column} IS NOT DISTINCT FROM {right}.{column}"


def keys_sql(cutoff: dt.date) -> str:
    """Every key posted on or before the cutoff in either warehouse, with where it is."""
    day = f"DATE '{cutoff.isoformat()}'"
    return f"""CREATE TEMP TABLE overlap_keys AS
        SELECT coalesce(p.nct_id, r.nct_id) AS nct_id,
               coalesce(p.nct_version, r.nct_version) AS nct_version,
               p.nct_id IS NOT NULL AS in_pinned, r.nct_id IS NOT NULL AS in_release
        FROM (SELECT nct_id, nct_version FROM pinned.versions WHERE effective_date <= {day}) p
        FULL JOIN (SELECT nct_id, nct_version FROM rel.versions
                   WHERE effective_date <= {day}) r USING (nct_id, nct_version)"""


def missing_causes(con: duckdb.DuckDBPyConnection, cutoff: dt.date) -> dict[str, int]:
    """Keys in one warehouse only, by cause."""
    day = f"DATE '{cutoff.isoformat()}'"
    rows = con.execute(
        f"""SELECT CASE
              WHEN k.in_pinned AND r.nct_id IS NOT NULL
                THEN 'pinned only: in the release, posted after the cutoff'
              WHEN k.in_pinned THEN 'pinned only: not in the release'
              WHEN p.nct_id IS NOT NULL THEN 'release only: pinned, posted after the cutoff'
              ELSE 'release only: not in the pinned revision' END AS cause, count(*)
        FROM overlap_keys k
        LEFT JOIN rel.versions r ON k.in_pinned AND NOT k.in_release
             AND r.nct_id = k.nct_id AND r.nct_version = k.nct_version AND r.effective_date > {day}
        LEFT JOIN pinned.versions p ON k.in_release AND NOT k.in_pinned
             AND p.nct_id = k.nct_id AND p.nct_version = k.nct_version
        WHERE NOT (k.in_pinned AND k.in_release)
        GROUP BY 1 ORDER BY 1"""
    ).fetchall()
    return {str(c): int(n) for c, n in rows}


def compared_columns(con: duckdb.DuckDBPyConnection) -> list[str]:
    columns = {str(r[0]) for r in con.execute("DESCRIBE pinned.versions").fetchall()}
    return [c for c in census.COMPARED if c in columns]


def flags_sql(columns: Sequence[str], rules: CohortRules) -> str:
    """One row per key in both warehouses, with a flag per differing column."""
    flags = [f"NOT ({_same(c)}) AS d_{c}" for c in columns]
    flags.append(f"{submitted_status_sql(rules, 'p')} IS DISTINCT FROM "
                 f"{submitted_status_sql(rules, 'r')} AS submitted_differs")  # fmt: skip
    flags.append(f"NOT ({_same('content_hash')}) AS d_content_hash")
    return f"""CREATE TEMP TABLE overlap_flags AS
        SELECT p.nct_id, p.nct_version, {", ".join(flags)}
        FROM overlap_keys k
        JOIN pinned.versions p USING (nct_id, nct_version)
        JOIN rel.versions r USING (nct_id, nct_version)
        WHERE k.in_pinned AND k.in_release"""


def _raw_cause(con: duckdb.DuckDBPyConnection, column: str) -> dict[str, int]:
    """A differing column traced to its raw values; the status first by the submitted
    status, which is what features read (ADR 0066)."""
    raw = RAW_OF.get(column, column)
    label = (
        f"WHEN NOT f.submitted_differs THEN {_lit(STATUS_LABEL_ONLY)}" if column in STATUS else ""
    )
    rows = con.execute(
        f"""SELECT CASE {label}
                        WHEN rp.nct_id IS NULL OR rr.nct_id IS NULL THEN 'fail: no raw row'
                        WHEN {_same(raw, "rp", "rr")} THEN {_lit(BUG)}
                        ELSE {_lit(RELEASE_CHANGED)} END, count(*)
        FROM overlap_flags f
        LEFT JOIN pinned.raw_versions rp USING (nct_id, nct_version)
        LEFT JOIN rel.raw_versions rr USING (nct_id, nct_version)
        WHERE f.d_{column} GROUP BY 1"""
    ).fetchall()
    return {str(c): int(n) for c, n in rows}


def _intervention_cause(
    con: duckdb.DuckDBPyConnection, pinned_iv: str, release_iv: str
) -> dict[str, int]:
    """A differing intervention list traced to the raw (name, type) pairs of the configs."""
    pair = "coalesce(intervention_name, '') || chr(31) || coalesce(intervention_type, '')"

    def config(glob: str) -> str:
        return f"""SELECT nct_id, nct_version, list_sort(list(DISTINCT {pair})) AS pairs
              FROM read_parquet('{glob}', union_by_name=true) JOIN d USING (nct_id, nct_version)
              GROUP BY 1, 2"""

    rows = con.execute(
        f"""WITH d AS (SELECT nct_id, nct_version FROM overlap_flags WHERE d_intervention_types),
        p AS ({config(pinned_iv)}), r AS ({config(release_iv)})
        SELECT CASE WHEN p.pairs IS NOT DISTINCT FROM r.pairs THEN {_lit(BUG)}
                    ELSE 'the release changed the interventions' END, count(*)
        FROM d LEFT JOIN p USING (nct_id, nct_version) LEFT JOIN r USING (nct_id, nct_version)
        GROUP BY 1"""
    ).fetchall()
    return {str(c): int(n) for c, n in rows}


def _sponsor_cause(con: duckdb.DuckDBPyConnection, column: str) -> dict[str, int]:
    rows = con.execute(
        f"""SELECT CASE
              WHEN NOT ({_same("lead_sponsor_name")}) THEN 'the release changed the sponsor name'
              WHEN NOT (rp.lead_sponsor_class IS NOT DISTINCT FROM rr.lead_sponsor_class)
                THEN 'the release changed the sponsor class'
              ELSE {_lit(BUG)} END, count(*)
        FROM overlap_flags f
        JOIN pinned.versions p USING (nct_id, nct_version)
        JOIN rel.versions r USING (nct_id, nct_version)
        LEFT JOIN pinned.raw_versions rp USING (nct_id, nct_version)
        LEFT JOIN rel.raw_versions rr USING (nct_id, nct_version)
        WHERE f.d_{column} GROUP BY 1"""
    ).fetchall()
    return {str(c): int(n) for c, n in rows}


def _title_rows(con: duckdb.DuckDBPyConnection) -> list[tuple[Any, ...]]:
    """Per key whose titles differ: the four title texts."""
    either = " OR ".join(f"f.d_{c}" for c in TITLES)
    return con.execute(
        f"""SELECT f.nct_id, f.nct_version, f.d_official_title_hash, f.d_brief_title_hash,
                   po.text, pb.text, ro.text, rb.text
        FROM overlap_flags f
        JOIN pinned.versions p USING (nct_id, nct_version)
        JOIN rel.versions r USING (nct_id, nct_version)
        LEFT JOIN pinned.texts po ON po.text_hash = p.official_title_hash
        LEFT JOIN pinned.texts pb ON pb.text_hash = p.brief_title_hash
        LEFT JOIN rel.texts ro ON ro.text_hash = r.official_title_hash
        LEFT JOIN rel.texts rb ON rb.text_hash = r.brief_title_hash
        WHERE {either}"""
    ).fetchall()


def overlap(
    con: duckdb.DuckDBPyConnection,
    cutoff: dt.date,
    rules: CohortRules,
    pinned_iv: str,
    release_iv: str,
) -> dict[str, Any]:
    """The overlap check (step 3) on a connection with the pinned warehouse attached as
    `pinned` and the release warehouse as `rel`. Counts only."""
    con.execute(keys_sql(cutoff))
    totals = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE in_pinned AND in_release), "
        "count(*) FILTER (WHERE in_pinned), count(*) FILTER (WHERE in_release) FROM overlap_keys"
    ).fetchone()
    assert totals is not None
    columns = compared_columns(con)
    con.execute(flags_sql(columns, rules))
    sums = ", ".join(f"count(*) FILTER (WHERE d_{c})" for c in (*columns, "content_hash"))
    counts = dict(zip((*columns, "content_hash"),
                      con.execute(f"SELECT {sums} FROM overlap_flags").fetchone() or (),
                      strict=True))  # fmt: skip
    causes: dict[str, Counter[str]] = {}
    for column in columns:
        if not counts[column]:
            continue
        if column in SPONSOR:
            causes[column] = Counter(_sponsor_cause(con, column))
        elif column == "intervention_types":
            causes[column] = Counter(_intervention_cause(con, pinned_iv, release_iv))
        elif column in TITLES or column in census.COUNTED_TEXTS:
            continue  # below, from the texts
        else:
            causes[column] = Counter(_raw_cause(con, column))
    phase_moved: set[tuple[str, int]] = set()
    for nct, version, d_official, d_brief, po, pb, ro, rb in _title_rows(con):
        moved = resolved_title_phase(po, pb) != resolved_title_phase(ro, rb)
        if moved:
            phase_moved.add((nct, version))
        for column, flagged, left, right in (("official_title_hash", d_official, po, ro),
                                             ("brief_title_hash", d_brief, pb, rb)):  # fmt: skip
            if flagged:
                cause = TITLE_PHASE_MOVED if moved else (
                    f"{TITLE_SAME_PHASE}: {census.text_class(right, left)}")  # fmt: skip
                causes.setdefault(column, Counter())[cause] += 1
    for column in census.COUNTED_TEXTS:
        if counts.get(column):
            causes[column] = Counter({"text differs (no served feature reads it)": counts[column]})
    # Agreement: the feature columns as features read them.
    feature = [c for c in columns if c in census.FEATURE_COLUMNS
               and c not in TITLES and c not in STATUS]  # fmt: skip
    disagree = " OR ".join([*(f"d_{c}" for c in feature), "submitted_differs"])
    con.execute(f"CREATE TEMP TABLE feature_disagree AS SELECT nct_id, nct_version "
                f"FROM overlap_flags WHERE {disagree}")  # fmt: skip
    if phase_moved:
        con.executemany("INSERT INTO feature_disagree VALUES (?, ?)", sorted(phase_moved))
    disagreeing = con.execute(
        "SELECT count(*) FROM (SELECT DISTINCT nct_id, nct_version FROM feature_disagree)"
    ).fetchone()
    assert disagreeing is not None
    union, both = int(totals[0]), int(totals[1])
    agreeing = both - int(disagreeing[0])
    bugs = sum(n for found in causes.values() for cause, n in found.items()
               if cause.startswith(("bug", "fail")))  # fmt: skip
    return {
        "keys_posted_by_the_cutoff": {
            "either": union,
            "both": both,
            "pinned": int(totals[2]),
            "release": int(totals[3]),
        },
        "keys_in_one_only": missing_causes(con, cutoff),
        "content_hash_equal": both - int(counts["content_hash"]),
        "differences_by_column": {c: int(n) for c, n in counts.items() if n},
        "causes": {c: dict(sorted(found.items())) for c, found in sorted(causes.items())},
        "feature_columns_agree": agreeing,
        "agreement_on_feature_columns": agreeing / union if union else 0.0,
        "bugs_or_failures": bugs,
        "trials_with_a_feature_difference": int(
            (con.execute("SELECT count(DISTINCT nct_id) FROM feature_disagree").fetchone() or (0,))[
                0
            ]
        ),
    }


# 4: the gap store ----------------------------------------------------------------------------


def write_store(
    con: duckdb.DuckDBPyConnection, cutoff: dt.date, release_iv: str, out: Path = STORE_PATH
) -> dict[str, int]:
    """The release's versions posted after the cutoff, with their texts and adapted
    interventions, in a DuckDB file of their own (step 4). Nothing pinned is touched."""
    refuse_on_a_runner()
    building = out.with_name(out.name + ".building")
    building.unlink(missing_ok=True)
    day = f"DATE '{cutoff.isoformat()}'"
    con.execute(f"ATTACH '{building.as_posix()}' AS store")
    con.execute(f"CREATE TABLE store.versions AS SELECT * FROM rel.versions "
                f"WHERE effective_date > {day} ORDER BY nct_id, nct_version")  # fmt: skip
    hashes = " UNION ".join(f"SELECT {c} AS h FROM store.versions"
                            for c in ("brief_title_hash", "official_title_hash",
                                      "brief_summary_hash", "eligibility_criteria_hash",
                                      "why_stopped_hash"))  # fmt: skip
    con.execute(f"CREATE TABLE store.texts AS SELECT t.* FROM rel.texts t "
                f"WHERE t.text_hash IN ({hashes}) ORDER BY t.text_hash")  # fmt: skip
    con.execute(f"""CREATE TABLE store.interventions AS
        SELECT i.* FROM read_parquet('{release_iv}', union_by_name=true) i
        JOIN store.versions v USING (nct_id, nct_version)
        ORDER BY i.nct_id, i.nct_version, i.intervention_name, i.intervention_type""")
    counts = {
        name: int((con.execute(f"SELECT count(*) FROM store.{name}").fetchone() or (0,))[0])
        for name in ("versions", "texts", "interventions")
    }
    trials = con.execute("SELECT count(DISTINCT nct_id), min(effective_date), "
                         "max(effective_date) FROM store.versions").fetchone()  # fmt: skip
    assert trials is not None
    counts["trials"] = int(trials[0])
    info = {"cutoff": cutoff.isoformat(), "first_post_date": str(trials[1]),
            "last_post_date": str(trials[2]), **{k: str(v) for k, v in counts.items()}}  # fmt: skip
    con.execute("CREATE TABLE store.info (key VARCHAR, value VARCHAR)")
    con.executemany("INSERT INTO store.info VALUES (?, ?)", sorted(info.items()))
    con.execute("DETACH store")
    building.replace(out)
    return counts


# The command ---------------------------------------------------------------------------------


def _connect(cfg: ProjectConfig) -> duckdb.DuckDBPyConnection:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{MEMORY_LIMIT}'")
    con.execute(f"SET temp_directory = '{TEMP_DIR.as_posix()}'")
    return con


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--workers", type=int, default=min(8, max(1, (os.cpu_count() or 2) - 2)))
    parser.add_argument("--skip-build", action="store_true",
                        help="reuse the adapted files and the release warehouse")  # fmt: skip
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    refuse_on_a_runner()
    cfg = load_project_config()
    cutoff = cfg.dataset.cutoff
    if cutoff is None:
        raise StoppedEarlyError("config/project.yaml has no dataset cutoff")
    gap = gap_config(cfg)
    started = time.monotonic()
    result: dict[str, Any] = {"release": {"repo_id": gap.repo_id, "tag": gap.tag,
                                          "commit": gap.commit},
                              "pinned_revision": cfg.dataset.revision,
                              "cutoff": cutoff.isoformat(), "seconds": {}}  # fmt: skip
    if not args.skip_build:
        with _connect(cfg) as con:
            result["adapter"] = adapt_to_parquet(con, cfg)
        result["seconds"]["adapt"] = round(time.monotonic() - started, 1)
        log.info("adapted: %s", result["adapter"])
        stats = build_release(cfg, args.workers)
        kept = ("raw.rows", "versions.rows", "versions.quarantined", "trials.rows")
        result["release_build"] = {k: stats[k] for k in kept}
        result["seconds"]["build"] = round(time.monotonic() - started, 1)
    with _connect(cfg) as con:
        con.execute(f"ATTACH '{WAREHOUSE_PATH.as_posix()}' AS pinned (READ_ONLY)")
        con.execute(f"ATTACH '{RELEASE_WAREHOUSE.as_posix()}' AS rel (READ_ONLY)")
        found = overlap(con, cutoff, CohortRules.from_config(cfg), interventions_glob(cfg),
                        adapted_glob("interventions"))  # fmt: skip
        result["overlap"] = found
        result["seconds"]["overlap"] = round(time.monotonic() - started, 1)
        passes = found["agreement_on_feature_columns"] >= MIN_AGREEMENT
        result["verdict"] = {"agreement_at_least": MIN_AGREEMENT, "passes": passes}
        if passes:
            result["store"] = write_store(con, cutoff, adapted_glob("interventions"))
    result["seconds"]["total"] = round(time.monotonic() - started, 1)
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))
    if not passes:
        raise StoppedEarlyError(
            f"agreement on the feature columns is {found['agreement_on_feature_columns']:.4%}, "
            f"below {MIN_AGREEMENT:.1%}: the gap store is not written (the gap read's item 4)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
