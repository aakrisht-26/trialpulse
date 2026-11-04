"""Reason counts for the API (ADR 0050), computed on the laptop and loaded as aggregates.

    uv run python -m trialpulse.live.reasons --target neon

Counts of the cohort's labeled early stops by the month of the stop (the post date of the
first terminal version), the reason label of the distilled classifier (Step 6) and the lead
sponsor class of the version in effect that day. Only counts leave the laptop (ADR 0040):
no trial id and no label per trial. `serving.data_status.labeled_through` is set to the data
cutoff, and the daily job counts the early stops recorded after it.
"""

import argparse
import datetime as dt
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb

from trialpulse.cli import RefusedError, run
from trialpulse.config import load_project_config

COUNTS_SQL = """
WITH stops AS (
  SELECT o.trial_id, o.terminal_date
  FROM outcomes o
  WHERE o.event = 1 AND NOT o.reversal AND o.in_window AND o.ever_in_population
),
classed AS (
  SELECT s.trial_id, s.terminal_date,
    coalesce(arg_max(v.lead_sponsor_class, v.nct_version), 'MISSING') AS sponsor_class
  FROM stops s JOIN versions v
    ON v.nct_id = s.trial_id AND v.effective_date = s.terminal_date
  GROUP BY s.trial_id, s.terminal_date
)
SELECT CAST(date_trunc('month', c.terminal_date) AS DATE) AS month, r.label AS reason,
  c.sponsor_class, count(*) AS n
FROM classed c JOIN stop_reasons r ON r.nct_id = c.trial_id
GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
"""


def reason_counts(con: duckdb.DuckDBPyConnection) -> list[tuple[dt.date, str, str, int]]:
    """Counts from the relations `stop_reasons`, `outcomes` and `versions` in `con`."""
    rows = con.execute(COUNTS_SQL).fetchall()
    return [(r[0], str(r[1]), str(r[2]), int(r[3])) for r in rows]


def counts_from_files(warehouse: Path, outcomes: Path) -> list[tuple[dt.date, str, str, int]]:
    """The counts from the warehouse's `stop_reasons` and `versions` (opened read-only) and
    the cohort's outcomes file."""
    if not warehouse.is_file() or not outcomes.is_file():
        raise RefusedError("the warehouse or the cohort's outcomes are not on this machine")
    with duckdb.connect() as con:
        con.execute(f"ATTACH '{warehouse.as_posix()}' AS wh (READ_ONLY)")
        con.execute("CREATE VIEW stop_reasons AS SELECT * FROM wh.stop_reasons")
        con.execute("CREATE VIEW versions AS SELECT * FROM wh.versions")
        con.execute(f"CREATE VIEW outcomes AS SELECT * FROM read_parquet('{outcomes.as_posix()}')")
        return reason_counts(con)


def load(cur: Any, counts: Sequence[tuple[dt.date, str, str, int]], through: dt.date) -> None:
    """Replace the counts in `serving.reason_counts`, in the caller's transaction."""
    cur.execute("DELETE FROM serving.reason_counts")
    cur.executemany("INSERT INTO serving.reason_counts VALUES (%s, %s, %s, %s)", counts)
    cur.execute(
        """INSERT INTO serving.data_status (status_id, labeled_through) VALUES (1, %s)
        ON CONFLICT (status_id) DO UPDATE SET labeled_through = EXCLUDED.labeled_through,
          updated_at = now()""",
        (through,),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the reason counts into Postgres.")
    parser.add_argument("--target", choices=("local", "neon"), required=True)
    args = parser.parse_args(argv)
    import psycopg

    from trialpulse.cohort.build import OUTCOMES_PATH
    from trialpulse.live.daily import _dsn
    from trialpulse.warehouse.build import WAREHOUSE_PATH

    cfg = load_project_config()
    if cfg.dataset.cutoff is None:
        raise RefusedError("config/project.yaml has no data cutoff")
    counts = counts_from_files(WAREHOUSE_PATH, OUTCOMES_PATH)
    with psycopg.connect(_dsn(args.target), connect_timeout=10) as pg, pg.cursor() as cur:
        load(cur, counts, cfg.dataset.cutoff)
        pg.commit()
    print(f"{len(counts)} counts loaded ({sum(c[3] for c in counts)} early stops), labeled "
          f"through {cfg.dataset.cutoff}")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
