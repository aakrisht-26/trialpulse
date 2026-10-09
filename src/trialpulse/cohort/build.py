"""Build the cohort (CLAUDE.md Step 4) from the warehouse.

    uv run python -m trialpulse.cohort.build

Outputs, in data/cohort/:

- landmarks.parquet: the Step 8 input contract (one row per trial and landmark, with the
  evaluation label and M0's stratum), read by `trialpulse.eval.walkforward`;
- person_period.parquet: the discrete-time training rows (up to four 6-month intervals per
  landmark);
- outcomes.parquet: one row per trial: t0, the event or censoring, reversal, the UNKNOWN
  lapse date (for the Step 11 sensitivity analysis);
- aj_sanity_by_sponsor_class.csv: the Aalen-Johansen sanity table, also printed (a
  sanity check only, not used for modeling: it follows outcomes to the data cutoff);
- training/origin_<date>/landmarks.parquet, person_period.parquet and outcomes.parquet,
  one set per walk-forward origin: the cohort as it would have been built on the origin,
  from versions posted before it only (ADR 0016). These are the training rows of the
  harness and of the discrete-time models, and the 2018-01-01 set is what the
  modeling-relevant EDA reads; landmarks.parquet and person_period.parquet above, with
  outcomes through the data cutoff, are for evaluation and description;
- build.json: row counts, checksums and whether they match the previous build.

It also writes part 2 of docs/data_audit.md. The build reads the warehouse read-only and
is deterministic: the same warehouse and config give the same files.
"""

import argparse
import csv
import datetime as dt
import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from trialpulse.cli import RefusedError, run
from trialpulse.cohort.audit import PART_2_HEADING, aj_sanity_table, render_part_2
from trialpulse.cohort.landmarks import LANDMARK_COLUMNS, build_landmarks
from trialpulse.cohort.outcomes import build_outcomes, build_states, check_version_order
from trialpulse.cohort.person_period import PERSON_PERIOD_COLUMNS, expand
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import load_project_config
from trialpulse.eval.rows import LANDMARKS_PATH, training_path
from trialpulse.parquet import write_table as _write_table
from trialpulse.reports import replace_section
from trialpulse.warehouse.audit import AUDIT_FAMILY, AUDIT_PATH
from trialpulse.warehouse.build import WAREHOUSE_PATH

COHORT_DIR = LANDMARKS_PATH.parent
PERSON_PERIOD_PATH = COHORT_DIR / "person_period.parquet"
OUTCOMES_PATH = COHORT_DIR / "outcomes.parquet"
AJ_PATH = COHORT_DIR / "aj_sanity_by_sponsor_class.csv"
BUILD_LOG_PATH = COHORT_DIR / "build.json"
TRAINING_DIR_NAME = "training"
LANDMARK_ORDER = "trial_id, landmark_index"
PERSON_PERIOD_ORDER = "trial_id, landmark_index, interval"
# 2 brought last_known_status (ADR 0014). 3 stopped storing sponsor names that open with a
# personal title (ADR 0022): an older warehouse still holds them and must be rebuilt.
MIN_WAREHOUSE_SCHEMA = 3
OUTCOME_COLUMNS: tuple[str, ...] = (
    "trial_id",
    "t0",
    "first_post_year",
    "in_window",
    "ever_in_population",
    "reversal",
    "terminal_status",
    "terminal_date",
    "event",
    "event_date",
    "censor_reason",
    "lapse_date",
    "registry_unknown",
)


def build_cohort(con: duckdb.DuckDBPyConnection, rules: CohortRules) -> None:
    """From a `versions` table or view to the cohort_* tables in `con`."""
    check_version_order(con, rules)
    build_states(con, rules)
    build_outcomes(con, rules)
    build_landmarks(con, rules)
    landmarks = con.execute(
        f"SELECT {', '.join(LANDMARK_COLUMNS)} FROM cohort_landmarks "
        "ORDER BY trial_id, landmark_index"
    ).fetchnumpy()
    rows = expand({k: np.asarray(v) for k, v in landmarks.items()}, rules)
    con.register("person_period_frame", pd.DataFrame(rows))
    con.execute(
        """CREATE OR REPLACE TEMP TABLE cohort_person_period AS
        SELECT trial_id, landmark_index, CAST(landmark_date AS DATE) AS landmark_date,
          interval, CAST(interval_start AS DATE) AS interval_start,
          CAST(interval_end AS DATE) AS interval_end, outcome,
          CAST(event_date AS DATE) AS event_date, stratum
        FROM person_period_frame ORDER BY trial_id, landmark_index, interval"""
    )
    con.unregister("person_period_frame")


def write_outputs(
    con: duckdb.DuckDBPyConnection, out_dir: Path, landmarks_path: Path
) -> list[tuple[str, int, str]]:
    """Write the Parquet files (sorted, so identical inputs give identical files) and return
    (name, rows, checksum) for each."""
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = [
        ("cohort_landmarks", landmarks_path, LANDMARK_COLUMNS, LANDMARK_ORDER),
        (
            "cohort_person_period",
            out_dir / PERSON_PERIOD_PATH.name,
            PERSON_PERIOD_COLUMNS,
            PERSON_PERIOD_ORDER,
        ),
        ("cohort_outcomes", out_dir / OUTCOMES_PATH.name, OUTCOME_COLUMNS, "trial_id"),
    ]
    return [
        (path.name, *_write_table(con, table, path, columns, order))
        for table, path, columns, order in targets
    ]


def _against_hindsight(
    con: duckdb.DuckDBPyConnection, final_landmarks: Path, origin: dt.date
) -> dict[str, int]:
    """How the landmark rows built as of an origin (cohort_landmarks in `con`) differ from
    the final cohort's landmarks before the origin, truncated at the origin: the labels
    that training used before ADR 0016, which know what was posted later."""
    day = f"DATE '{origin.isoformat()}'"
    row = con.execute(
        f"""WITH hindsight AS (
          SELECT trial_id, landmark_index, true AS in_hindsight,
            CASE WHEN event_date >= {day} THEN 0 ELSE event END AS h_event,
            least(event_date, {day}) AS h_date
          FROM read_parquet('{final_landmarks.as_posix()}') WHERE landmark_date < {day}
        ),
        as_of AS (
          SELECT trial_id, landmark_index, true AS in_as_of, event, event_date
          FROM cohort_landmarks
        )
        SELECT count(in_hindsight), count(*) FILTER (WHERE in_hindsight IS NULL),
          count(*) FILTER (WHERE in_as_of IS NULL),
          count(*) FILTER (WHERE in_hindsight AND in_as_of AND event <> h_event),
          count(*) FILTER (WHERE in_hindsight AND in_as_of AND event = h_event
            AND event_date <> h_date)
        FROM hindsight FULL JOIN as_of USING (trial_id, landmark_index)"""
    ).fetchone() or (0, 0, 0, 0, 0)
    keys = ("hindsight_rows", "only_as_of", "only_hindsight", "other_label", "other_end_date")
    return dict(zip(keys, (int(v) for v in row), strict=True))


def build_training_cohorts(
    con: duckdb.DuckDBPyConnection,
    rules: CohortRules,
    origins: Sequence[dt.date],
    training_dir: Path,
    final_landmarks: Path,
) -> list[dict[str, Any]]:
    """For each walk-forward origin, build the cohort as of the origin (ADR 0016) and write
    its landmark rows, person-period rows and outcomes. The cohort_* tables in `con` are
    replaced each time, so this needs a connection of its own. Returns one summary per
    origin."""
    summaries: list[dict[str, Any]] = []
    for origin in origins:
        build_cohort(con, rules.as_of(origin))
        landmarks_path = training_path(training_dir, origin)
        landmarks_path.parent.mkdir(parents=True, exist_ok=True)
        person_period_path = landmarks_path.parent / PERSON_PERIOD_PATH.name
        landmarks = _write_table(
            con, "cohort_landmarks", landmarks_path, LANDMARK_COLUMNS, LANDMARK_ORDER
        )
        person_period = _write_table(
            con,
            "cohort_person_period",
            person_period_path,
            PERSON_PERIOD_COLUMNS,
            PERSON_PERIOD_ORDER,
        )
        outcomes_path = landmarks_path.parent / OUTCOMES_PATH.name
        outcomes = _write_table(con, "cohort_outcomes", outcomes_path, OUTCOME_COLUMNS, "trial_id")
        trials = con.execute("SELECT count(DISTINCT trial_id) FROM cohort_landmarks").fetchone()
        folder = landmarks_path.parent.name
        summaries.append(
            {
                "origin": origin.isoformat(),
                "trials": int(trials[0]) if trials else 0,
                "files": {
                    f"{TRAINING_DIR_NAME}/{folder}/{landmarks_path.name}": landmarks,
                    f"{TRAINING_DIR_NAME}/{folder}/{person_period_path.name}": person_period,
                    f"{TRAINING_DIR_NAME}/{folder}/{outcomes_path.name}": outcomes,
                },
                "landmark_rows": landmarks[0],
                "person_period_rows": person_period[0],
                **_against_hindsight(con, final_landmarks, origin),
            }
        )
    return summaries


def write_aj_table(rows: Sequence[dict[str, Any]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: f"{v:.6f}" if isinstance(v, float) else v for k, v in row.items()})


def attach_warehouse(con: duckdb.DuckDBPyConnection, warehouse: Path) -> None:
    if not warehouse.is_file():
        raise RefusedError(
            f"no warehouse at {warehouse}; run: uv run python -m trialpulse.warehouse.build"
        )
    con.execute(f"ATTACH '{warehouse.as_posix()}' AS wh (READ_ONLY)")
    check_warehouse_schema(con)
    con.execute("CREATE TEMP VIEW versions AS SELECT * FROM wh.versions")


def check_warehouse_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Refuse a warehouse (attached as `wh`) built before the current rules for what is
    stored."""
    version = con.execute("SELECT value FROM wh.build_info WHERE key = 'schema_version'").fetchone()
    if version is None or int(version[0]) < MIN_WAREHOUSE_SCHEMA:
        raise RefusedError(
            f"the warehouse predates schema version {MIN_WAREHOUSE_SCHEMA} (no stored names for "
            "sponsors named by a personal title, ADR 0022); rebuild it: "
            "uv run python -m trialpulse.warehouse.build"
        )


def warehouse_stamp(con: duckdb.DuckDBPyConnection) -> str:
    """What the attached warehouse is: its dataset revision, schema version, and the row
    count and checksum of its versions table."""
    info = dict(con.execute("SELECT key, value FROM wh.build_info").fetchall())
    rows, checksum = con.execute(
        "SELECT count(*), coalesce(sum(hash(t)::HUGEINT), 0) FROM wh.versions t"
    ).fetchone() or (0, 0)
    return (
        f"dataset revision {info.get('dataset.revision', 'not recorded')}, schema version "
        f"{info.get('schema_version')}, {int(rows):,} versions with checksum {checksum}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the cohort from the warehouse.")
    parser.add_argument("--warehouse", type=Path, default=WAREHOUSE_PATH)
    parser.add_argument("--out-dir", type=Path, default=COHORT_DIR)
    parser.add_argument("--audit", type=Path, default=AUDIT_PATH)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    rules = CohortRules.from_config(cfg)
    started = time.monotonic()
    with duckdb.connect() as con:
        attach_warehouse(con, args.warehouse)
        build_cohort(con, rules)
        out_dir: Path = args.out_dir
        landmarks_path = out_dir / LANDMARKS_PATH.name
        outputs = write_outputs(con, out_dir, landmarks_path)
        aj_rows = aj_sanity_table(con, cfg.horizons_months)
        write_aj_table(aj_rows, out_dir / AJ_PATH.name)
        origins = [origin.date for origin in cfg.walk_forward.origins]
        with duckdb.connect() as as_of_con:
            attach_warehouse(as_of_con, args.warehouse)
            training = build_training_cohorts(
                as_of_con, rules, origins, out_dir / TRAINING_DIR_NAME, landmarks_path
            )
        replace_section(
            args.audit,
            PART_2_HEADING,
            render_part_2(
                con, rules, aj_rows, cfg.horizons_months, outputs, warehouse_stamp(con), training
            ),
            AUDIT_FAMILY,
        )
    log_path = out_dir / BUILD_LOG_PATH.name
    tables = {name: {"rows": rows, "checksum": checksum} for name, rows, checksum in outputs}
    for summary in training:
        for name, (rows, checksum) in summary["files"].items():
            tables[name] = {"rows": rows, "checksum": checksum}
    previous = (
        json.loads(log_path.read_text(encoding="utf-8")).get("tables")
        if log_path.is_file()
        else None
    )
    identical = None if previous is None else previous == tables
    record = {
        "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "duration_seconds": round(time.monotonic() - started, 1),
        "dataset_revision": cfg.dataset.revision,
        "data_cutoff": rules.cutoff.isoformat(),
        "identical_to_previous": identical,
        "tables": tables,
    }
    log_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print("Aalen-Johansen sanity table (sanity check only, not used for modeling): "
          "early-stop CIF from L0 by sponsor class, L0 before 2018-01-01")  # fmt: skip
    months = cfg.horizons_months
    print(f"{'sponsor class':<14}{'trials':>10}" + "".join(f"{f'CIF {m}m':>10}" for m in months))
    for row in aj_rows:
        cifs = "".join(f"{row[f'cif_{m}m']:>10.3f}" for m in months)
        print(f"{row['stratum']:<14}{row['trials']:>10,}{cifs}")
    for name, rows, checksum in outputs:
        print(f"{name}: {rows:,} rows, checksum {checksum}")
    print("Training cohorts as of each origin (ADR 0016):")
    for summary in training:
        print(
            f"  {summary['origin']}: {summary['landmark_rows']:,} landmark rows, "
            f"{summary['person_period_rows']:,} person-period rows"
        )
    verdict = {True: "yes", False: "NO", None: "no previous build"}[identical]
    print(f"Identical to the previous build: {verdict}")
    print(f"Built in {record['duration_seconds']} s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
