"""The weekly drift report of the full run (ADR 0076).

    uv run python -m trialpulse.monitor.drift reference      (laptop: config/drift_reference.json)

**The reference** is an aggregate made on the laptop (ADR 0040): for each feature M4 reads as
the live path computes it (53 of its 54; the 54th, `sponsor_stop_rate`, is computed after from
three that are checked), over the served champion's training rows (the final cohort's
landmark rows), the shares of its decile bins (numbers) or of each category, and the share
missing; the same for the served risks at the data cutoff (Step 13's file). No row leaves the
laptop: only edges and shares are committed.

**The step** (`drift_step`, called by the daily job after a full run's publication has
committed): Evidently's data drift preset with the PSI method and its default threshold
(0.1) on the run's features and risks, against a fixed sample of 5,000 rows drawn without
randomness from the reference's bins (Evidently compares two tables; the reference exists
only as shares). One row per column goes to `monitoring.drift`, the HTML report to a file the
workflow keeps as an artifact. A failure is recorded and returned, never raised: it never
fails the run, never undoes publication and never blocks the next day.

Evidently lives in the `drift` dependency group (it needs plotly below 6, the dashboard 7);
it is imported only inside the step.
"""

import argparse
import datetime as dt
import json
import time as clock
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trialpulse.config import REPO_ROOT

REFERENCE_PATH = REPO_ROOT / "config" / "drift_reference.json"
SAMPLE = 5000
BINS = 10
METHOD = "psi"
SCORES = ("risk_12m", "risk_24m")
DERIVED = ("sponsor_stop_rate",)  # computed after the features, from three that are checked


def drift_columns() -> list[str]:
    from trialpulse.models import discrete_time

    names = discrete_time.feature_names(discrete_time.FAMILY_SETS["m4"])
    return [n for n in names if n not in DERIVED]


def _categorical(values: pd.Series) -> bool:
    return bool(values.dtype == object or values.dtype == bool or str(values.dtype) in
                ("category", "boolean", "string"))  # fmt: skip


def summarize(values: pd.Series) -> dict[str, Any]:
    """The shares of one column (see the module text)."""
    n = len(values)
    present = values.dropna()
    missing = 1 - len(present) / n if n else 0.0
    if _categorical(values):
        shares = present.astype(str).value_counts(normalize=True).sort_index()
        return {"kind": "categorical", "missing": missing,
                "shares": {str(k): float(v) for k, v in shares.items()}}  # fmt: skip
    numbers = present.astype(float).to_numpy()
    if not len(numbers):
        return {"kind": "numeric", "missing": missing, "edges": [0.0, 0.0], "shares": [0.0]}
    edges: np.ndarray = np.unique(np.quantile(numbers, np.linspace(0, 1, BINS + 1)))
    if len(edges) == 1:  # one value only: one bin holding it
        return {"kind": "numeric", "missing": missing,
                "edges": [float(edges[0]), float(edges[0])], "shares": [1.0]}  # fmt: skip
    counts = np.histogram(numbers, bins=edges)[0]
    total = counts.sum()
    return {"kind": "numeric", "missing": missing, "edges": [float(e) for e in edges],
            "shares": [float(c / total) if total else 0.0 for c in counts]}  # fmt: skip


def _counts(shares: Sequence[float], n: int) -> list[int]:
    """Whole counts summing to n, in proportion to shares (largest remainder)."""
    raw = np.asarray(shares, dtype=float) * n
    whole = np.floor(raw).astype(int)
    for i in np.argsort(-(raw - whole))[: n - int(whole.sum())]:
        whole[i] += 1
    return [int(c) for c in whole]


def sample(column: Mapping[str, Any], n: int = SAMPLE) -> pd.Series:
    """`n` values reproducing a column's shares, without randomness."""
    missing, present = _counts([column["missing"], 1 - column["missing"]], n)
    if column["kind"] == "categorical":
        names = list(column["shares"])
        values: list[Any] = []
        for name, count in zip(names, _counts(list(column["shares"].values()), present),
                               strict=True):  # fmt: skip
            values += [name] * count
        return pd.Series(values + [None] * missing, dtype=object)
    edges = column["edges"]
    parts = []
    for i, count in enumerate(_counts(column["shares"], present)):
        lo, hi = edges[i], edges[i + 1]
        last = i == len(column["shares"]) - 1
        parts.append(np.linspace(lo, hi, count, endpoint=last) if count else np.array([]))
    numbers = np.concatenate(parts) if parts else np.array([])
    return pd.Series(np.concatenate([numbers, np.full(missing, np.nan)]), dtype=float)


def reference_frame(reference: Mapping[str, Any]) -> pd.DataFrame:
    return pd.DataFrame({name: sample(col) for name, col in reference["columns"].items()})


def current_frame(features: pd.DataFrame, scores: pd.DataFrame,
                  reference: Mapping[str, Any]) -> pd.DataFrame:  # fmt: skip
    """The run's features and risks in the reference's columns and kinds."""
    out = {}
    for name, col in reference["columns"].items():
        source = scores if name in SCORES else features
        values = source[name] if name in source.columns else pd.Series([None] * len(source))
        if col["kind"] == "categorical":
            out[name] = values.map(lambda v: None if v is None or v != v else str(v))
        else:
            out[name] = pd.to_numeric(values, errors="coerce").astype(float)
    n = max(len(features), len(scores))
    return pd.DataFrame({k: pd.Series(v).reset_index(drop=True).reindex(range(n))
                         for k, v in out.items()})  # fmt: skip


def evidently_drift(
    current: pd.DataFrame, reference: pd.DataFrame, kinds: Mapping[str, str], html: Path | None
) -> list[dict[str, Any]]:
    """Evidently's PSI per column: [{column, statistic, threshold, drifted}]."""
    from evidently import DataDefinition, Dataset, Report  # the drift group (ADR 0076)
    from evidently.presets import DataDriftPreset

    definition = DataDefinition(
        numerical_columns=[c for c, k in kinds.items() if k == "numeric"],
        categorical_columns=[c for c, k in kinds.items() if k == "categorical"],
    )
    snapshot = Report([DataDriftPreset(method=METHOD)]).run(
        Dataset.from_pandas(current, data_definition=definition),
        Dataset.from_pandas(reference, data_definition=definition),
    )
    if html is not None:
        html.parent.mkdir(parents=True, exist_ok=True)
        snapshot.save_html(str(html))
    rows = []
    for metric in snapshot.dict()["metrics"]:
        config = metric.get("config", {})
        if not str(config.get("type", "")).endswith("ValueDrift"):
            continue
        value = metric["value"]
        statistic = None if value is None or not np.isfinite(float(value)) else float(value)
        threshold = float(config["threshold"])
        rows.append({"column": config["column"], "statistic": statistic,
                     "threshold": threshold,
                     "drifted": statistic is not None and statistic >= threshold})  # fmt: skip
    return rows


def drift_step(
    dsn: str,
    day: dt.date,
    run_id: int | None,
    features: pd.DataFrame,
    scores: pd.DataFrame,
    lease: Callable[[Any], None] | None = None,
    html: Path | None = None,
    reference_path: Path = REFERENCE_PATH,
) -> dict[str, Any]:
    """The drift step of a full run (see the module text): its summary, or its failure."""
    started = clock.perf_counter()
    try:
        import psycopg

        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        kinds = {name: col["kind"] for name, col in reference["columns"].items()}
        rows = evidently_drift(current_frame(features, scores, reference),
                               reference_frame(reference), kinds, html)  # fmt: skip
        n_current = len(features)
        with (
            psycopg.connect(dsn, autocommit=True, connect_timeout=30) as con,
            con.transaction(),
        ):
            # one short write transaction (ADR 0058), the lease renewed first
            if lease is not None:
                lease(con.cursor())
            con.execute("DELETE FROM monitoring.drift WHERE week_date = %s", (day,))
            con.cursor().executemany(
                """INSERT INTO monitoring.drift (week_date, column_name, kind, method,
                   statistic, threshold, drifted, n_current, run_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                [(day, r["column"], "score" if r["column"] in SCORES else "feature", METHOD,
                  r["statistic"], r["threshold"], r["drifted"], n_current, run_id)
                 for r in rows],
            )  # fmt: skip
        features_rows = [r for r in rows if r["column"] not in SCORES]
        drifted = sum(r["drifted"] for r in features_rows)
        return {"columns": len(rows), "features_drifted": drifted,
                "features_drifted_share": drifted / len(features_rows) if features_rows else None,
                "scores_drifted": sum(r["drifted"] for r in rows if r["column"] in SCORES),
                "seconds": round(clock.perf_counter() - started, 1)}  # fmt: skip
    except Exception as exc:  # never fails the run (ADR 0076)
        return {"failed": type(exc).__name__, "seconds": round(clock.perf_counter() - started, 1)}


def make_reference(cfg: Any) -> dict[str, Any]:
    """The reference of the served champion's training rows (laptop only, ADR 0040)."""
    import duckdb

    from trialpulse.eval.rows import LANDMARKS_PATH
    from trialpulse.features.build import FEATURES_DIR, FEATURES_NAME
    from trialpulse.scoring.score import SCORES_DIR

    columns = drift_columns()
    with duckdb.connect() as con:
        frame = con.execute(
            f"""SELECT {", ".join(f"f.{c}" for c in columns)}
            FROM read_parquet('{LANDMARKS_PATH.as_posix()}') l
            JOIN read_parquet('{(FEATURES_DIR / FEATURES_NAME).as_posix()}') f
              USING (trial_id, landmark_index)"""
        ).df()
    scores = pd.read_parquet(SCORES_DIR / f"scores_{cfg.dataset.cutoff}.parquet",
                             columns=list(SCORES))  # fmt: skip
    out = {name: summarize(frame[name]) for name in columns}
    out.update({name: summarize(scores[name]) for name in SCORES})
    return {"made_on": dt.date.today().isoformat(), "method": METHOD, "sample": SAMPLE,
            "features_rows": len(frame), "scores_rows": len(scores),
            "source": "the served champion's training rows (the final cohort's landmark "
                      "rows) and its risks at the data cutoff; shares only (ADR 0040)",
            "columns": out}  # fmt: skip


def main(argv: Sequence[str] | None = None) -> int:
    from trialpulse.cli import run as _run  # noqa: F401 (the command runs through cli.run)
    from trialpulse.config import load_project_config
    from trialpulse.ingest.history import refuse_on_a_runner

    parser = argparse.ArgumentParser(description="The drift reference (ADR 0076).")
    parser.add_argument("step", choices=("reference",))
    parser.parse_args(argv)
    refuse_on_a_runner()  # the reference is made from the dataset's rows (ADR 0040)
    reference = make_reference(load_project_config())
    REFERENCE_PATH.write_text(json.dumps(reference, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in reference.items() if k != "columns"}, indent=2))
    print(f"{len(reference['columns'])} columns written to {REFERENCE_PATH.name}")
    return 0


if __name__ == "__main__":
    from trialpulse.cli import run

    raise SystemExit(run(main))
