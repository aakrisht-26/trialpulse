"""The alerts of ADR 0081, read from Neon after every daily run.

    uv run python -m trialpulse.monitor.alerts --target neon

Each alert kind becomes one GitHub issue (opened or reused by daily.yml's alerts job):

- `watermark_lag`: the latest succeeded daily run's data date is more than
  `alerts.watermark_lag_days` (3) days before today (Section 14);
- `quarantine`: the latest daily run quarantined more than `alerts.quarantine_share` (1%) of
  the records it fetched;
- `drift`: the latest drift week has more than `alerts.drift_feature_share` (20%) of its
  features drifted (ADR 0076), or one of `alerts.drift_top_features` drifted, the 5 most
  important features (ADR 0081, amended 2026-11-17). `sponsor_stop_rate` has no drift row of
  its own (it is computed after the features from three that are checked, ADR 0076), so it
  counts as drifted when one of those three did;
- `matured_auc`: the latest matured live 12-month AUC is below the lowest lower bound of the
  registered 12-month AUC intervals of the locked test (docs/model_statement.json; ADR 0048).

It reads Neon only, in one read-only snapshot, and never fails because of an alert: alerts go
to the step output `alerts` (a comma list) and the printed summary. It reads no dataset.

The step output `alert_states` is one JSON object with every kind, raised or not, and a key of
the details that matter (`states`): the alerts job comments on an issue only when a kind's
state or key changes (`monitor.alert_issues`). A key leaves out what moves every day (today's
date, counts, shares, the drift week), so a lasting alert is one comment, not two a day.
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
# A top feature without a drift row of its own, watched through the columns it is computed from
# (monitor.drift.DERIVED; the rate of features.transforms.SponsorPriors).
DERIVED_INPUTS = {
    "sponsor_stop_rate": ("sponsor_class", "sponsor_prior_ended", "sponsor_prior_stopped"),
}


def top_features_drifted(top: Sequence[str], drifted: Sequence[str]) -> list[str]:
    """The top features that drifted, in the order of `top`: a feature with a drift row by
    that row, a derived one by any of its inputs."""
    seen = set(drifted)
    return [f for f in top if f in seen or seen.intersection(DERIVED_INPUTS.get(f, ()))]


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
    top = top_features_drifted(cfg.drift_top_features, state.get("drifted_features") or ())
    if (share is not None and share > cfg.drift_feature_share) or top:
        named = f"; top features drifted: {', '.join(top)}" if top else ""
        found["drift"] = (f"{share or 0:.0%} of the features drifted in the week of "
                          f"{state['drift_week']}{named}")  # fmt: skip
    auc = state.get("matured_auc")
    if auc is not None and auc < floor:
        found["matured_auc"] = (f"matured 12-month AUC {auc:.4f} (predictions of "
                                f"{state['matured_date']}) below {floor:.4f}")  # fmt: skip
    return found


def states(
    state: Mapping[str, Any], cfg: AlertsConfig, today: dt.date, floor: float
) -> dict[str, dict[str, Any]]:
    """Every alert kind, raised or not, with its message and the key of its details: the
    stale data date, the drift rules that fired and their top features, the graded prediction
    date; a quarantine has no key (its counts belong to one run)."""
    found = check(state, cfg, today, floor)
    keys: dict[str, str] = {
        "watermark_lag": f"data as of {state.get('data_date')}",
        "quarantine": "",
        "matured_auc": f"predictions of {state.get('matured_date')}",
    }
    share = state.get("drift_share")
    top = top_features_drifted(cfg.drift_top_features, state.get("drifted_features") or ())
    rules = (["share over the threshold"] if share is not None and share > cfg.drift_feature_share
             else []) + ([f"top features {', '.join(top)}"] if top else [])  # fmt: skip
    keys["drift"] = "; ".join(rules)
    return {kind: {"raised": kind in found, "key": keys[kind] if kind in found else "",
                   "detail": found.get(kind, "")} for kind in KINDS}  # fmt: skip


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
        drifted = con.execute(
            """SELECT column_name FROM monitoring.drift WHERE kind = 'feature' AND drifted
                      AND week_date = (SELECT max(week_date) FROM monitoring.drift)
                    ORDER BY column_name"""
        ).fetchall()
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
        "drifted_features": [r[0] for r in drifted],
        "matured_date": matured[0] if matured else None,
        "matured_auc": float(matured[1]) if matured else None,
    }


def main(argv: Sequence[str] | None = None) -> int:
    from trialpulse.live.daily import _dsn

    parser = argparse.ArgumentParser(description="The alerts of ADR 0081.")
    parser.add_argument("--target", choices=("local", "neon"), required=True)
    args = parser.parse_args(argv)
    cfg = load_project_config()
    every = states(read_state(_dsn(args.target)), cfg.alerts,
                   dt.datetime.now(dt.UTC).date(), registered_auc_floor())  # fmt: skip
    found = {kind: s["detail"] for kind, s in every.items() if s["raised"]}
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as fh:
            fh.write(f"alerts={','.join(found)}\n")
            fh.write(f"alert_states={json.dumps(every, separators=(',', ':'))}\n")
    print(json.dumps({"alerts": found}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
