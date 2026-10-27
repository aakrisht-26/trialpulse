"""The served scores in Postgres (CLAUDE.md Steps 12 and 13, ADR 0034).

`publish` writes one scoring run to the serving schema, in one transaction:

- `serving.trial_scores_latest`: one row per scored trial, the latest score. A row whose
  values did not change is left as it is, so a second run on the same day changes nothing;
  a trial no longer scored (closed, or now without a score) leaves the table.
- `serving.trial_score_history`: a row when, against the trial's last history row of an
  earlier day, the score moved by 0.01 or more, the record changed, a landmark passed or the
  model changed (`history_rows`), recording why. A rerun on the same day replaces that day's
  rows, so the history always holds the score the latest table serves.
- `serving.trials_not_scored`: the open trials without a score on the run's date, with their
  reason code.

A run publishes one date, and scores without drivers are refused (CLAUDE.md Section 13). The
tables move forward in time: a run dated before the latest published date is refused
(`EarlierDateError`), so the history stays in date order.

The database is DATABASE_URL (Section 17), local Postgres in Docker for Step 13.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from trialpulse.config import ProjectConfig, Secrets
from trialpulse.scoring.score import Scored

MOVE = 0.01
FIRST, RECORD, LANDMARK, MODEL, CALENDAR = (
    "first_score",
    "record_change",
    "landmark_passed",
    "new_model",
    "calendar",
)
LATEST_COLUMNS: tuple[str, ...] = (
    "nct_id", "as_of_date", "landmark_index", "true_landmark_index", "flagged", "model_name",
    "model_version", "risk_12m", "risk_24m", "drivers", "content_hash",
)  # fmt: skip
HISTORY_COLUMNS: tuple[str, ...] = (
    "nct_id", "as_of_date", "model_name", "model_version", "risk_12m", "risk_24m", "reasons",
    "landmark_index", "true_landmark_index", "content_hash", "flagged",
)  # fmt: skip


class EarlierDateError(ValueError):
    """A scoring run dated before the latest date in the serving tables."""


def history_rows(new: pd.DataFrame, previous: pd.DataFrame) -> pd.DataFrame:
    """The rows of `new` the history keeps, each with its reasons (ADR 0034 decision 5),
    against `previous`, each trial's last history row of an earlier day: a first score (no
    such row); else a record change (another version scored), a landmark passed (a higher
    true landmark index), a new model; else, if the score moved by MOVE or more at either
    horizon, the calendar (the month-based features, the competition count and the sponsor
    record moved with time and with other trials). Rows with no reason are dropped."""
    merged = new.merge(
        previous[["nct_id", "risk_12m", "risk_24m", "model_version", "true_landmark_index",
                  "content_hash"]],
        on="nct_id", how="left", suffixes=("", "_prev"), indicator="side",
    )  # fmt: skip
    reasons: list[list[str]] = []
    for row in merged.itertuples(index=False):
        if row.side == "left_only":
            reasons.append([FIRST])
            continue
        found = []
        if row.content_hash != row.content_hash_prev:
            found.append(RECORD)
        if int(row.true_landmark_index) > int(row.true_landmark_index_prev):
            found.append(LANDMARK)
        if row.model_version != row.model_version_prev:
            found.append(MODEL)
        moved = max(abs(row.risk_12m - row.risk_12m_prev), abs(row.risk_24m - row.risk_24m_prev))
        if not found and moved >= MOVE:
            found.append(CALENDAR)
        reasons.append(found)
    merged["reasons"] = reasons
    kept = merged[merged["reasons"].map(len) > 0]
    return kept[[*new.columns, "reasons"]].reset_index(drop=True)


def _url() -> str:
    secret = Secrets().database_url
    if secret is None:
        raise ValueError("DATABASE_URL is not set (environment or .env)")
    url = secret.get_secret_value()
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def _staging(cursor: Any, name: str, table: str, columns: Sequence[str]) -> None:
    """A temporary table with `columns` of `table` (types only: no identity, no constraint),
    dropped at commit."""
    cursor.execute(
        f"CREATE TEMP TABLE {name} ON COMMIT DROP AS "
        f"SELECT {', '.join(columns)} FROM {table} WITH NO DATA"
    )


def _copy(cursor: Any, table: str, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    with cursor.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)


def _rows(frame: pd.DataFrame, columns: Sequence[str]) -> list[tuple[Any, ...]]:
    out = []
    for values in frame[list(columns)].itertuples(index=False, name=None):
        out.append(tuple(v.item() if isinstance(v, np.generic) else v for v in values))
    return out


def latest_frame(scored: Scored) -> pd.DataFrame:
    frame = scored.scores.rename(columns={"true_index": "true_landmark_index"}).copy()
    frame["as_of_date"] = pd.to_datetime(frame["as_of_date"]).dt.date
    if "drivers" not in frame or frame["drivers"].isna().any():
        raise ValueError("scores without drivers are not served (CLAUDE.md Section 13)")
    return frame


def publish(scored: Scored, cfg: ProjectConfig, url: str | None = None) -> dict[str, int]:
    """Write one scoring run to the serving schema (see the module text)."""
    import psycopg

    new = latest_frame(scored)
    unscored = scored.unscored.rename(columns={"true_index": "true_landmark_index"}).copy()
    unscored["as_of_date"] = pd.to_datetime(unscored["as_of_date"]).dt.date
    days = set(new["as_of_date"]) | set(unscored["as_of_date"])
    if len(days) != 1:
        raise ValueError(f"a run publishes one date, not {len(days)}")
    (day,) = days
    dsn = (url or _url()).replace("postgresql+psycopg://", "postgresql://")
    counts: dict[str, int] = {}
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        row = cur.execute("SELECT max(as_of_date) FROM serving.trial_scores_latest").fetchone()
        latest_day = row[0] if row else None
        if latest_day is not None and day < latest_day:
            raise EarlierDateError(
                f"the serving tables already hold scores of {latest_day}; a run dated {day} "
                "is not published"
            )
        previous = pd.DataFrame(
            cur.execute(
                """SELECT DISTINCT ON (nct_id) nct_id, risk_12m, risk_24m, model_version,
                  coalesce(true_landmark_index, landmark_index) AS true_landmark_index,
                  content_hash
                FROM serving.trial_score_history WHERE as_of_date < %s
                ORDER BY nct_id, as_of_date DESC, history_id DESC""",
                (day,),
            ).fetchall(),
            columns=["nct_id", "risk_12m", "risk_24m", "model_version", "true_landmark_index",
                     "content_hash"],
        )  # fmt: skip
        history = history_rows(new, previous)
        _staging(cur, "incoming", "serving.trial_scores_latest", LATEST_COLUMNS)
        _copy(cur, "incoming", LATEST_COLUMNS, _rows(new, LATEST_COLUMNS))
        listed = ", ".join(c for c in LATEST_COLUMNS if c != "nct_id")
        changed = " OR ".join(
            f"t.{c} IS DISTINCT FROM EXCLUDED.{c}" for c in LATEST_COLUMNS if c != "nct_id"
        )
        cur.execute(
            f"""INSERT INTO serving.trial_scores_latest AS t (nct_id, {listed})
            SELECT nct_id, {listed} FROM incoming
            ON CONFLICT (nct_id) DO UPDATE SET
              {", ".join(f"{c} = EXCLUDED.{c}" for c in LATEST_COLUMNS if c != "nct_id")},
              scored_at = now()
            WHERE {changed}"""
        )
        counts["latest_written"] = cur.rowcount
        cur.execute(
            "DELETE FROM serving.trial_scores_latest t WHERE NOT EXISTS "
            "(SELECT 1 FROM incoming i WHERE i.nct_id = t.nct_id)"
        )
        counts["latest_removed"] = cur.rowcount
        values = HISTORY_COLUMNS[2:]  # the columns after the key (nct_id, as_of_date)
        _staging(cur, "incoming_history", "serving.trial_score_history", HISTORY_COLUMNS)
        _copy(cur, "incoming_history", HISTORY_COLUMNS, _rows(history, HISTORY_COLUMNS))
        cur.execute(
            f"""INSERT INTO serving.trial_score_history AS h ({", ".join(HISTORY_COLUMNS)})
            SELECT {", ".join(HISTORY_COLUMNS)} FROM incoming_history
            ON CONFLICT (nct_id, as_of_date, model_version) DO UPDATE SET
              {", ".join(f"{c} = EXCLUDED.{c}" for c in values)}, scored_at = now()
            WHERE ({", ".join(f"h.{c}" for c in values)})
              IS DISTINCT FROM ({", ".join(f"EXCLUDED.{c}" for c in values)})"""
        )
        counts["history_written"] = cur.rowcount
        # A rerun on the same day: that day's rows of trials that no longer warrant one go.
        cur.execute(
            """DELETE FROM serving.trial_score_history h
            WHERE h.as_of_date = %s
              AND h.model_version IN (SELECT model_version FROM incoming)
              AND NOT EXISTS (SELECT 1 FROM incoming_history i
                              WHERE i.nct_id = h.nct_id AND i.model_version = h.model_version)""",
            (day,),
        )
        counts["history_removed"] = cur.rowcount
        not_scored_columns = ("nct_id", "as_of_date", "true_landmark_index", "reason",
                              "content_hash")  # fmt: skip
        _staging(cur, "incoming_not_scored", "serving.trials_not_scored", not_scored_columns)
        _copy(cur, "incoming_not_scored", not_scored_columns, _rows(unscored, not_scored_columns))
        cur.execute(
            f"""INSERT INTO serving.trials_not_scored AS t ({", ".join(not_scored_columns)})
            SELECT {", ".join(not_scored_columns)} FROM incoming_not_scored
            ON CONFLICT (nct_id) DO UPDATE SET
              as_of_date = EXCLUDED.as_of_date, true_landmark_index =
                EXCLUDED.true_landmark_index, reason = EXCLUDED.reason,
              content_hash = EXCLUDED.content_hash, recorded_at = now()
            WHERE t.as_of_date IS DISTINCT FROM EXCLUDED.as_of_date
              OR t.reason IS DISTINCT FROM EXCLUDED.reason
              OR t.true_landmark_index IS DISTINCT FROM EXCLUDED.true_landmark_index
              OR t.content_hash IS DISTINCT FROM EXCLUDED.content_hash"""
        )
        counts["not_scored_written"] = cur.rowcount
        cur.execute(
            "DELETE FROM serving.trials_not_scored t WHERE NOT EXISTS "
            "(SELECT 1 FROM incoming_not_scored i WHERE i.nct_id = t.nct_id)"
        )
        counts["not_scored_removed"] = cur.rowcount
        con.commit()
    return counts


def table_digest(url: str | None = None) -> Mapping[str, str]:
    """A digest of each serving table's content (without timestamps), to show that a second
    run changed nothing."""
    import hashlib

    import psycopg

    dsn = (url or _url()).replace("postgresql+psycopg://", "postgresql://")
    out = {}
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        for table, order in (
            ("serving.trial_scores_latest", "nct_id"),
            ("serving.trial_score_history", "nct_id, as_of_date, model_version"),
            ("serving.trials_not_scored", "nct_id"),
        ):
            rows = cur.execute(
                f"SELECT to_jsonb(t) - 'scored_at' - 'recorded_at' - 'history_id' "
                f"FROM {table} t ORDER BY {order}"
            ).fetchall()
            digest = hashlib.sha256()
            for (row,) in rows:
                digest.update(json.dumps(row, sort_keys=True).encode("utf-8"))
            out[table] = f"{len(rows)} rows, sha256:{digest.hexdigest()[:16]}"
    return out
