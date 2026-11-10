"""The monthly grading of live predictions (ADR 0077), run by grade.yml.

    uv run python -m trialpulse.monitor.grade --target neon
    uv run python -m trialpulse.monitor.grade --target neon --fail-on-purpose

It reads Neon only (the score files of `monitoring.score_files`, `live.outcome_events` and the
latest succeeded daily run's data date) in one read-only snapshot, grades every score file's
predictions (`monitor.grading`: matured or partial-horizon) and writes the aggregates to
`monitoring.graded_predictions` with source 'live', in one short transaction (ADR 0058). It
reads no dataset and imports no dataset module (ADR 0040). With `--fail-on-purpose` it raises
after reading, to prove the failure issue of grade.yml (ADR 0081). It prints counts only.
"""

import argparse
import datetime as dt
import io
import json
from collections.abc import Sequence
from typing import Any

import pandas as pd

from trialpulse.cli import RefusedError, run
from trialpulse.config import load_project_config
from trialpulse.monitor import grading


class ForcedFailureError(RuntimeError):
    """The failure grade.yml's dispatch asked for (ADR 0081)."""


def read_neon(dsn: str) -> dict[str, Any]:
    """The score files, the outcome events and the data date, in one read-only snapshot."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True, connect_timeout=30) as con, con.transaction():
        con.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        row = con.execute(
            """SELECT max(data_timestamp) FROM monitoring.pipeline_runs
                    WHERE job = 'daily' AND status = 'succeeded'"""
        ).fetchone()
        files = con.execute(
            "SELECT month, content FROM monitoring.score_files ORDER BY month"
        ).fetchall()
        events = con.execute(
            """SELECT nct_id, to_status, kind, effective_date FROM live.outcome_events
                    ORDER BY effective_date, event_id"""
        ).fetchall()
    if row is None or row[0] is None:
        raise RefusedError("Neon holds no succeeded daily run")
    return {
        "data_date": row[0],
        "files": [(month, bytes(content)) for month, content in files],
        "events": pd.DataFrame(events, columns=["nct_id", "to_status", "kind", "effective_date"]),
    }


def predictions(files: Sequence[tuple[dt.date, bytes]]) -> pd.DataFrame:
    """Every score file's rows (ADR 0039's columns)."""
    frames = [pd.read_parquet(io.BytesIO(content)) for _, content in files]
    if not frames:
        return pd.DataFrame(columns=["nct_id", "prediction_date", "risk_12m", "risk_24m",
                                     "sponsor_class", "landmark_index", "flagged",
                                     "model_version"])  # fmt: skip
    return pd.concat(frames, ignore_index=True)


def grade_live(dsn: str, fail_on_purpose: bool = False) -> dict[str, Any]:
    import psycopg

    cfg = load_project_config()
    neon = read_neon(dsn)
    if fail_on_purpose:
        raise ForcedFailureError("a failure on purpose (grade.yml fail_on_purpose, ADR 0081)")
    rows = predictions(neon["files"])
    if rows.empty:
        return {"data_date": neon["data_date"], "score_files": 0, "rows": 0, "written": 0}
    rows = grading.live_outcomes(rows, neon["events"], neon["data_date"])
    graded = grading.grade(rows, neon["data_date"], "live", cfg.evaluation.censoring_min_rows,
                           cfg.evaluation.lift_top_fraction)  # fmt: skip
    with (
        psycopg.connect(dsn, autocommit=True, connect_timeout=30) as con,
        con.transaction(),
        con.cursor() as cur,
    ):
        written = grading.upsert(cur, graded)
    return {"data_date": neon["data_date"], "score_files": len(neon["files"]),
            "predictions": len(rows), **grading.summary(graded), "written": written}  # fmt: skip


def main(argv: Sequence[str] | None = None) -> int:
    from trialpulse.live.daily import _dsn

    parser = argparse.ArgumentParser(description="Grade the live predictions (ADR 0077).")
    parser.add_argument("--target", choices=("local", "neon"), required=True)
    parser.add_argument(
        "--fail-on-purpose",
        action="store_true",
        help="raise after reading, to prove the failure issue (ADR 0081)",
    )
    args = parser.parse_args(argv)
    result = grade_live(_dsn(args.target), args.fail_on_purpose)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
