"""The alerts of ADR 0081, read from Neon after every daily run.

    uv run python -m trialpulse.monitor.alerts --target neon

Each alert kind becomes one GitHub issue (opened or reused by daily.yml's alerts job):

- `watermark_lag`: the latest succeeded daily run's data date is more than
  `alerts.watermark_lag_days` (3) days before today (Section 14);
- `quarantine`: the latest daily run quarantined more than `alerts.quarantine_share` (1%) of
  the records it fetched;
- `drift`: the latest drift week has more than `alerts.drift_feature_share` (20%) of its
  features drifted (ADR 0076);
- `matured_auc`: the latest matured live 12-month AUC is below the lowest lower bound of the
  registered 12-month AUC intervals of the locked test (docs/model_statement.json; ADR 0048).

It reads Neon only, in one read-only snapshot, and never fails because of an alert: alerts go
to the step output `alerts` (a comma list) and the printed summary. It reads no dataset.
"""

import argparse
import datetime as dt
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from trialpulse.cli import run
from trialpulse.config import REPO_ROOT, AlertsConfig, load_project_config

STATEMENT_PATH = REPO_ROOT / "docs" / "model_statement.json"
KINDS = ("watermark_lag", "quarantine", "drift", "matured_auc")


def registered_auc_floor(path: Path = STATEMENT_PATH) -> float:
    """The lowest lower bound of the registered 12-month AUC intervals."""
    origins = json.loads(path.read_text(encoding="utf-8"))["registered_results"]["origins"]
    return min(float(m["auc_12"]["low"]) for m in origins.values())


def check(
    state: Mapping[str, Any], cfg: AlertsConfig, today: dt.date, floor: float
) -> dict[str, str]:
    """The alerts raised by a snapshot of the monitoring tables, each with a short reason."""
    found: dict[str, str] = {}
    day = state.get("data_date")
    if day is None or (today - day).days > cfg.watermark_lag_days:
        found["watermark_lag"] = (f"data as of {day}, more than {cfg.watermark_lag_days} days "
                                  f"before {today}")  # fmt: skip
    fetched, quarantined = state.get("records_fetched") or 0, state.get("records_quarantined") or 0
    if fetched and quarantined / fetched > cfg.quarantine_share:
        found["quarantine"] = f"{quarantined} of {fetched} records quarantined"
    share = state.get("drift_share")
    if share is not None and share > cfg.drift_feature_share:
        found["drift"] = f"{share:.0%} of the features drifted in the week of {state['drift_week']}"
    auc = state.get("matured_auc")
    if auc is not None and auc < floor:
        found["matured_auc"] = (f"matured 12-month AUC {auc:.4f} (predictions of "
                                f"{state['matured_date']}) below {floor:.4f}")  # fmt: skip
    return found


def read_state(dsn: str) -> dict[str, Any]:
    import psycopg

    with psycopg.connect(dsn, autocommit=True, connect_timeout=30) as con, con.transaction():
        con.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        day = con.execute(
            """SELECT max(data_timestamp) FROM monitoring.pipeline_runs
                    WHERE job = 'daily' AND status = 'succeeded'"""
        ).fetchone()
        last = con.execute(
            """SELECT records_fetched, records_quarantined FROM monitoring.pipeline_runs
                    WHERE job = 'daily' AND status IN ('succeeded', 'failed')
                    ORDER BY run_id DESC LIMIT 1"""
        ).fetchone()
        drift = con.execute(
            """SELECT week_date, avg(CASE WHEN drifted THEN 1.0 ELSE 0.0 END)
                    FROM monitoring.drift WHERE kind = 'feature'
                      AND week_date = (SELECT max(week_date) FROM monitoring.drift)
                    GROUP BY week_date"""
        ).fetchone()
        matured = con.execute(
            """SELECT prediction_date, auc FROM monitoring.graded_predictions
                    WHERE source = 'live' AND matured AND horizon_months = 12
                      AND landmark_index = -1 AND row_group = 'all' AND auc IS NOT NULL
                    ORDER BY prediction_date DESC LIMIT 1"""
        ).fetchone()
    return {
        "data_date": day[0] if day else None,
        "records_fetched": last[0] if last else None,
        "records_quarantined": last[1] if last else None,
        "drift_week": drift[0] if drift else None,
        "drift_share": float(drift[1]) if drift else None,
        "matured_date": matured[0] if matured else None,
        "matured_auc": float(matured[1]) if matured else None,
    }


def main(argv: Sequence[str] | None = None) -> int:
    from trialpulse.live.daily import _dsn

    parser = argparse.ArgumentParser(description="The alerts of ADR 0081.")
    parser.add_argument("--target", choices=("local", "neon"), required=True)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    found = check(read_state(_dsn(args.target)), cfg.alerts,
                  dt.datetime.now(dt.UTC).date(), registered_auc_floor())  # fmt: skip
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as fh:
            fh.write(f"alerts={','.join(found)}\n")
    print(json.dumps({"alerts": found}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
