"""The live tables in Postgres (ADRs 0037, 0039, 0043, 0045): load and save live state.

Every function takes an open psycopg cursor; the caller owns the transaction, so a stage of
the daily job writes in one transaction and commits once. Feature hashes are hex strings in
memory and 32 bytes in the tables. Nothing here logs a row: failures carry their type only.

The bootstrap writes in three steps (ADRs 0045, 0058, 0060): `start_bootstrap` takes the
lease in a short transaction of its own, `prepare_bootstrap` makes every row with no
transaction open (COPY data made by the connection's own adapters and sent nowhere, and the
sponsor file's bytes), and `load_bootstrap` sends those prepared rows in one transaction,
computing nothing while it is open.
"""

import datetime as dt
import hashlib
import io
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from trialpulse.live import reasons as reason_counts
from trialpulse.live.contract import LIVE_FIELDS
from trialpulse.live.engine import Ingested
from trialpulse.live.sponsors import FORMAT, SponsorFile
from trialpulse.live.state import FIRST_FIELDS, Event, Wide
from trialpulse.scoring import serving

if TYPE_CHECKING:
    from trialpulse.scoring.predict import Scored

HEAD = "0007"
WIDE_COLUMNS: tuple[str, ...] = (
    "nct_id", *LIVE_FIELDS, "feature_hash", *FIRST_FIELDS, "n_versions", "ever_suspended",
    "recent_post_dates", "lapse_from", "reversal", "run_id",
)  # fmt: skip
VERSION_COLUMNS: tuple[str, ...] = ("nct_id", *LIVE_FIELDS, "feature_hash", "run_id")


class LeaseError(RuntimeError):
    """Another job holds the lease."""


class LeaseLostError(LeaseError):
    """This job's lease was taken over (it went stale): the job stops without writing."""


class SchemaError(RuntimeError):
    """The database is not at the migration head (migrations run by hand, ADR 0045)."""


def check_head(cur: Any) -> None:
    row = cur.execute("SELECT version_num FROM alembic_version").fetchone()
    if row is None or row[0] != HEAD:
        raise SchemaError(f"the database is at revision {row[0] if row else None}, not {HEAD}")


# Runs and the lease -------------------------------------------------------------------------


def start_run(cur: Any, job: str, kind: str, data_day: dt.date | None, stale_minutes: int) -> int:
    """A running row for this job, or LeaseError when another job is running. A running row
    whose last renewal is older than `stale_minutes` is stale and marked failed first
    (ADR 0060: renewal, not the start, keeps a lease)."""
    import psycopg

    cur.execute(
        """UPDATE monitoring.pipeline_runs SET status = 'failed', finished_at = now(),
          error = 'stale lease' WHERE status = 'running'
          AND renewed_at < now() - make_interval(mins => %s)""",
        (stale_minutes,),
    )
    try:
        row = cur.execute(
            """INSERT INTO monitoring.pipeline_runs (job, status, run_kind, data_timestamp)
            VALUES (%s, 'running', %s, %s) RETURNING run_id""",
            (job, kind, data_day),
        ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise LeaseError("another live job is running") from exc
    return int(row[0])


def renew(cur: Any, run_id: int) -> None:
    """Renew this job's lease, or raise LeaseLostError when it was taken over (ADR 0060).
    Called at every stage and first in every write transaction: the row lock it takes holds
    off a takeover until the transaction ends, so a write never lands after one."""
    row = cur.execute(
        """UPDATE monitoring.pipeline_runs SET renewed_at = now()
        WHERE run_id = %s AND status = 'running' RETURNING run_id""",
        (run_id,),
    ).fetchone()
    if row is None:
        raise LeaseLostError(f"run {run_id} lost its lease (taken over as stale)")


# The tables a full run rewrites whole, which plain VACUUM leaves at twice their size: every
# row of the latest scores, and the one row of the sponsor file (ADR 0068).
COMPACTED: tuple[str, ...] = ("serving.trial_scores_latest", "live.sponsor_file")
COMPACT_LOCK_TIMEOUT = "60s"  # VACUUM FULL waits this long for readers, then the run fails


def compact(cur: Any, run_id: int) -> None:
    """VACUUM FULL of `COMPACTED`, each after renewing the lease, so it runs only while the
    job holds it (ADR 0068). The connection must be in autocommit: VACUUM cannot run inside
    a transaction. It takes an exclusive lock on each table for its rewrite; a read waiting
    on it gets its answer after."""
    cur.execute(f"SET lock_timeout = '{COMPACT_LOCK_TIMEOUT}'")
    try:
        for table in COMPACTED:
            renew(cur, run_id)
            cur.execute(f"VACUUM (FULL, ANALYZE) {table}")
    finally:
        cur.execute("RESET lock_timeout")


def finish_run(
    cur: Any, run_id: int, status: str, counts: Mapping[str, Any], error: str | None = None
) -> None:
    """End a run that still holds its lease; LeaseLostError otherwise (ADR 0060)."""
    cur.execute(
        """UPDATE monitoring.pipeline_runs SET status = %s, finished_at = now(),
          watermark_from = %s, watermark_to = %s, records_fetched = %s, versions_added = %s,
          records_quarantined = %s, trials_rescored = %s, details = %s, error = %s
        WHERE run_id = %s AND status = 'running'""",
        (
            status, counts.get("watermark_from"), counts.get("watermark_to"),
            counts.get("records_fetched"), counts.get("versions_added"),
            counts.get("records_quarantined"), counts.get("trials_rescored"),
            json.dumps(counts.get("details", {}), default=str), error, run_id,
        ),
    )  # fmt: skip
    if cur.rowcount != 1:
        raise LeaseLostError(f"run {run_id} lost its lease (taken over as stale)")


def last_run(cur: Any, job: str, kind: str | None = None) -> dict[str, Any] | None:
    """The latest succeeded run of `job` (and kind), or None."""
    row = cur.execute(
        """SELECT run_id, data_timestamp, watermark_to, run_kind FROM monitoring.pipeline_runs
        WHERE job = %s AND status = 'succeeded' AND (%s::text IS NULL OR run_kind = %s)
        ORDER BY data_timestamp DESC NULLS LAST, run_id DESC LIMIT 1""",
        (job, kind, kind),
    ).fetchone()
    if row is None:
        return None
    return {"run_id": row[0], "data_timestamp": row[1], "watermark_to": row[2], "kind": row[3]}


def day_succeeded(cur: Any, day: dt.date) -> bool:
    run = last_run(cur, "daily")
    return run is not None and run["data_timestamp"] == day


# Wide rows ----------------------------------------------------------------------------------


def _wide_values(wide: Wide, run_id: int | None) -> tuple[Any, ...]:
    row = wide.row
    return (
        wide.nct_id,
        *(list(row[n]) if n == "intervention_types" else row.get(n) for n in LIVE_FIELDS),
        bytes.fromhex(str(row["feature_hash"])),
        *(wide.first.get(n) for n in FIRST_FIELDS),
        wide.n_versions, wide.ever_suspended, list(wide.recent_post_dates), wide.lapse_from,
        wide.reversal, run_id,
    )  # fmt: skip


def _wide_from(values: Sequence[Any]) -> Wide:
    data = dict(zip(WIDE_COLUMNS, values, strict=True))
    row = {"nct_id": data["nct_id"], **{n: data[n] for n in LIVE_FIELDS}}
    row["intervention_types"] = list(row["intervention_types"] or [])
    row["feature_hash"] = bytes(data["feature_hash"]).hex()
    return Wide(
        row=row,
        first={n: data[n] for n in FIRST_FIELDS},
        n_versions=int(data["n_versions"]),
        ever_suspended=bool(data["ever_suspended"]),
        recent_post_dates=tuple(data["recent_post_dates"] or ()),
        lapse_from=data["lapse_from"],
        reversal=bool(data["reversal"]),
    )


def load_wides(cur: Any, trials: Iterable[str] | None = None) -> dict[str, Wide]:
    """The wide rows of `trials`, or every wide row."""
    columns = ", ".join(WIDE_COLUMNS)
    if trials is None:
        rows = cur.execute(f"SELECT {columns} FROM live.trial_state").fetchall()
    else:
        rows = cur.execute(
            f"SELECT {columns} FROM live.trial_state WHERE nct_id = ANY(%s)", (sorted(trials),)
        ).fetchall()
    return {r[0]: _wide_from(r) for r in rows}


def save_wides(cur: Any, wides: Iterable[Wide], run_id: int | None) -> int:
    columns = ", ".join(WIDE_COLUMNS)
    marks = ", ".join(["%s"] * len(WIDE_COLUMNS))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in WIDE_COLUMNS if c != "nct_id")
    rows = [_wide_values(w, run_id) for w in wides]
    cur.executemany(
        f"""INSERT INTO live.trial_state ({columns}) VALUES ({marks})
        ON CONFLICT (nct_id) DO UPDATE SET {updates}, updated_at = now()""",
        rows,
    )
    return len(rows)


# Versions, events, markers, quarantine ------------------------------------------------------


def _version_values(row: Mapping[str, Any], run_id: int | None) -> tuple[Any, ...]:
    return (
        row["nct_id"],
        *(list(row[n]) if n == "intervention_types" else row.get(n) for n in LIVE_FIELDS),
        bytes.fromhex(str(row["feature_hash"])),
        run_id,
    )


def _version_from(values: Sequence[Any]) -> dict[str, Any]:
    data = dict(zip(VERSION_COLUMNS, values, strict=True))
    data["feature_hash"] = bytes(data["feature_hash"]).hex()
    data["intervention_types"] = list(data["intervention_types"] or [])
    return data


def append_versions(cur: Any, rows: Iterable[Mapping[str, Any]], run_id: int | None) -> int:
    columns = ", ".join(VERSION_COLUMNS)
    marks = ", ".join(["%s"] * len(VERSION_COLUMNS))
    values = [_version_values(r, run_id) for r in rows]
    cur.executemany(
        f"""INSERT INTO live.study_versions ({columns}) VALUES ({marks})
        ON CONFLICT (nct_id, effective_date, feature_hash) DO NOTHING""",
        values,
    )
    return len(values)


def latest_live(cur: Any, trials: Iterable[str]) -> dict[str, dict[str, Any]]:
    """The latest appended row of each trial (by post date, then by arrival)."""
    columns = ", ".join(VERSION_COLUMNS)
    rows = cur.execute(
        f"""SELECT DISTINCT ON (nct_id) {columns} FROM live.study_versions
        WHERE nct_id = ANY(%s) ORDER BY nct_id, effective_date DESC, ingested_at DESC""",
        (sorted(trials),),
    ).fetchall()
    return {r[0]: _version_from(r) for r in rows}


def versions_after(cur: Any, run_id: int | None) -> list[dict[str, Any]]:
    """The rows of runs after `run_id` (every row when None), in arrival order."""
    columns = ", ".join(VERSION_COLUMNS)
    rows = cur.execute(
        f"""SELECT {columns} FROM live.study_versions WHERE %s::bigint IS NULL OR run_id > %s
        ORDER BY effective_date, nct_id, ingested_at""",
        (run_id, run_id),
    ).fetchall()
    return [_version_from(r) for r in rows]


def latest_post_date(cur: Any) -> dt.date | None:
    row = cur.execute("SELECT max(effective_date) FROM live.study_versions").fetchone()
    value: dt.date | None = row[0] if row else None
    return value


def overlap_rows(cur: Any, since: dt.date) -> dict[str, dt.date]:
    """Each trial's latest appended post date, for trials whose latest is on or after `since`."""
    rows = cur.execute(
        """SELECT nct_id, max(effective_date) FROM live.study_versions GROUP BY nct_id
        HAVING max(effective_date) >= %s""",
        (since,),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def save_events(cur: Any, events: Iterable[Event], run_id: int | None) -> int:
    rows = [(e.nct_id, e.from_status, e.to_status, e.effective_date, e.kind, run_id)
            for e in events]  # fmt: skip
    cur.executemany(
        """INSERT INTO live.outcome_events
          (nct_id, from_status, to_status, effective_date, kind, run_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (nct_id, effective_date, to_status) DO NOTHING""",
        rows,
    )
    return len(rows)


def load_markers(cur: Any, trials: Iterable[str]) -> dict[str, list[tuple[dt.date, int]]]:
    rows = cur.execute(
        "SELECT nct_id, post_dates, kinds FROM serving.amendment_markers WHERE nct_id = ANY(%s)",
        (sorted(trials),),
    ).fetchall()
    return {r[0]: list(zip(r[1], r[2], strict=True)) for r in rows}


MARKER_COLUMNS: tuple[str, ...] = ("nct_id", "post_dates", "kinds")


def _marker_values(markers: Mapping[str, Sequence[tuple[dt.date, int]]]) -> list[tuple[Any, ...]]:
    return [(n, [d for d, _ in m], [k for _, k in m]) for n, m in markers.items()]


def save_markers(cur: Any, markers: Mapping[str, Sequence[tuple[dt.date, int]]]) -> int:
    rows = _marker_values(markers)
    cur.executemany(
        """INSERT INTO serving.amendment_markers (nct_id, post_dates, kinds) VALUES (%s, %s, %s)
        ON CONFLICT (nct_id) DO UPDATE SET post_dates = EXCLUDED.post_dates,
          kinds = EXCLUDED.kinds, updated_at = now()
        WHERE (serving.amendment_markers.post_dates, serving.amendment_markers.kinds)
          IS DISTINCT FROM (EXCLUDED.post_dates, EXCLUDED.kinds)""",
        rows,
    )
    return len(rows)


def quarantine(cur: Any, rows: Iterable[Mapping[str, Any]], run_id: int | None) -> int:
    values = [(r.get("nct_id"), r.get("effective_date"), r["payload_hash"], r["reasons"], run_id)
              for r in rows]  # fmt: skip
    cur.executemany(
        """INSERT INTO live.quarantine (nct_id, effective_date, payload_hash, reasons, run_id)
        VALUES (%s, %s, %s, %s, %s) ON CONFLICT (payload_hash) DO NOTHING""",
        values,
    )
    return len(values)


def save_ingest(cur: Any, done: Ingested, run_id: int | None) -> None:
    append_versions(cur, done.versions, run_id)
    save_wides(cur, done.upserted.values(), run_id)
    if done.dropped:
        cur.execute("DELETE FROM live.trial_state WHERE nct_id = ANY(%s)", (sorted(done.dropped),))
    save_events(cur, done.events, run_id)
    save_markers(cur, done.markers)


# The sponsor file ---------------------------------------------------------------------------


def file_values(sponsors: SponsorFile) -> tuple[Any, ...]:
    """The values of the sponsor file's row (format, content, sha256, through day, last run,
    trials, keys): the file is serialized here, once."""
    content = sponsors.to_bytes()
    return (FORMAT, content, hashlib.sha256(content).hexdigest(), sponsors.through_day,
            sponsors.last_run_id, len(sponsors.thin), len(sponsors.key_numbers))  # fmt: skip


def _write_file(cur: Any, values: Sequence[Any]) -> None:
    cur.execute(
        """INSERT INTO live.sponsor_file
          (file_id, format, content, sha256, through_day, last_run_id, trials, keys)
        VALUES (1, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (file_id) DO UPDATE SET format = EXCLUDED.format,
          content = EXCLUDED.content, sha256 = EXCLUDED.sha256,
          through_day = EXCLUDED.through_day, last_run_id = EXCLUDED.last_run_id,
          trials = EXCLUDED.trials, keys = EXCLUDED.keys, written_at = now()""",
        tuple(values),
    )


def save_file(cur: Any, sponsors: SponsorFile) -> str:
    values = file_values(sponsors)
    _write_file(cur, values)
    return str(values[2])


def load_file(cur: Any) -> SponsorFile:
    row = cur.execute("SELECT content, sha256 FROM live.sponsor_file WHERE file_id = 1").fetchone()
    if row is None:
        raise RuntimeError("no sponsor file: the bootstrap has not run")
    content = bytes(row[0])
    if hashlib.sha256(content).hexdigest() != row[1]:
        raise RuntimeError("the sponsor file does not match its digest")
    return SponsorFile.from_bytes(content)


# Population, open count, lapse exits (SQL, so a daily run reads little) ---------------------


def population_rows(
    cur: Any, day: dt.date, open_statuses: Sequence[str], study_type: str, first: dt.date
) -> list[tuple[Any, ...]]:
    """(trial, t0, reversal, feature hash, sponsor class) of the open population on `day`."""
    rows: list[tuple[Any, ...]] = cur.execute(
        """SELECT nct_id, study_first_post_date, reversal, encode(feature_hash, 'hex'),
          coalesce(lead_sponsor_class, 'MISSING')
        FROM live.trial_state
        WHERE effective_date <= %(d)s AND status = ANY(%(open)s) AND study_type = %(type)s
          AND (lapse_from IS NULL OR lapse_from > %(d)s)
          AND study_first_post_date BETWEEN %(first)s AND %(d)s
        ORDER BY nct_id""",
        {"d": day, "open": list(open_statuses), "type": study_type, "first": first},
    ).fetchall()
    return rows


def open_count(cur: Any, day: dt.date, open_statuses: Sequence[str], study_type: str) -> int:
    row = cur.execute(
        """SELECT count(*) FROM live.trial_state
        WHERE effective_date <= %(d)s AND status = ANY(%(open)s) AND study_type = %(type)s
          AND (lapse_from IS NULL OR lapse_from > %(d)s)""",
        {"d": day, "open": list(open_statuses), "type": study_type},
    ).fetchone()
    return int(row[0])


def lapsed_between(cur: Any, since: dt.date, day: dt.date) -> list[Wide]:
    columns = ", ".join(WIDE_COLUMNS)
    rows = cur.execute(
        f"SELECT {columns} FROM live.trial_state WHERE lapse_from > %s AND lapse_from <= %s",
        (since, day),
    ).fetchall()
    return [_wide_from(r) for r in rows]


def served_state(
    cur: Any,
) -> tuple[dict[str, tuple[str, int, str]], dict[str, tuple[str, int, str]]]:
    """What the scope compares with: the served and the not-scored rows."""
    latest = {
        r[0]: (bytes(r[1]).hex() if r[1] is not None else "", int(r[2]), r[3])
        for r in cur.execute(
            "SELECT nct_id, content_hash, true_landmark_index, model_version "
            "FROM serving.trial_scores_latest"
        ).fetchall()
    }
    not_scored = {
        r[0]: (bytes(r[1]).hex() if r[1] is not None else "", int(r[2]), r[3])
        for r in cur.execute(
            "SELECT nct_id, content_hash, true_landmark_index, reason "
            "FROM serving.trials_not_scored"
        ).fetchall()
    }
    return latest, not_scored


def database_mb(cur: Any) -> float:
    row = cur.execute("SELECT pg_database_size(current_database())").fetchone()
    return float(row[0]) / 1e6


def save_score_file(cur: Any, day: dt.date, model_version: str, content: bytes, rows: int,
                    run_id: int | None) -> None:  # fmt: skip
    """The month's score file (ADR 0039): a later full run of the month replaces it."""
    cur.execute(
        """INSERT INTO monitoring.score_files
          (month, as_of_date, model_version, rows, sha256, content, run_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (month) DO UPDATE SET as_of_date = EXCLUDED.as_of_date,
          model_version = EXCLUDED.model_version, rows = EXCLUDED.rows,
          sha256 = EXCLUDED.sha256, content = EXCLUDED.content, run_id = EXCLUDED.run_id,
          written_at = now()
        WHERE monitoring.score_files.sha256 IS DISTINCT FROM EXCLUDED.sha256""",
        (day.replace(day=1), day, model_version, rows, hashlib.sha256(content).hexdigest(),
         content, run_id),
    )  # fmt: skip


# Every table the daily job writes (held to its SQL by tests/live/test_store.py): a rebuild
# clears them all, so nothing of an earlier live state survives it.
REBUILT_TABLES: tuple[str, ...] = (
    "live.trial_state", "live.study_versions", "live.sponsor_file", "live.outcome_events",
    "live.quarantine", "serving.amendment_markers", "serving.trial_scores_latest",
    "serving.trial_score_history", "serving.trials_not_scored", "serving.trial_display",
    "serving.sponsors", "monitoring.score_files",
)  # fmt: skip


@dataclass(frozen=True)
class Payload:
    """The rows of one table in COPY's text format, made before a write opens its
    transaction (`copy_payload`)."""

    table: str
    columns: tuple[str, ...]
    data: bytes
    rows: int


@dataclass(frozen=True)
class BootstrapRows:
    """Everything the bootstrap writes, prepared before its one transaction opens
    (`prepare_bootstrap`): COPY data for the tables, the sponsor file's row and the reason
    counts. It holds bytes and plain values only, so the write can compute nothing."""

    run_id: int
    release: dt.date
    copies: tuple[Payload, ...]
    file: tuple[Any, ...]  # the values of the sponsor file's row (`file_values`)
    reasons: tuple[tuple[dt.date, str, str, int], ...] | None
    trials: int
    labeled_through: dt.date | None = None  # the reason counts' end, if not `release`

    def sizes(self) -> dict[str, dict[str, int]]:
        """Rows and bytes sent for each table (the sponsor file as its row's content)."""
        out = {p.table: {"rows": p.rows, "bytes": len(p.data)} for p in self.copies}
        out["live.sponsor_file"] = {"rows": 1, "bytes": len(self.file[1])}
        if self.reasons is not None:
            out["serving.reason_counts"] = {"rows": len(self.reasons), "bytes": 0}
        return out


def copy_payload(
    cur: Any, table: str, columns: Sequence[str], rows: Iterable[Sequence[Any]]
) -> Payload:
    """The rows of `table` in COPY's text format, made by the connection's own adapters and
    sent nowhere (psycopg's FileWriter, on a cursor of `cur`'s connection that has run
    nothing)."""
    from psycopg.copy import Copy, FileWriter

    buffer = io.BytesIO()
    count = 0
    with cur.connection.cursor() as fresh, Copy(fresh, writer=FileWriter(buffer)) as copy:
        for row in rows:
            copy.write_row(row)
            count += 1
    return Payload(table, tuple(columns), buffer.getvalue(), count)


def occupied_tables(cur: Any) -> list[str]:
    """The tables a rebuild clears that hold a row: a bootstrap without `rebuild` refuses
    over any of them."""
    return [t for t in REBUILT_TABLES
            if cur.execute(f"SELECT EXISTS (SELECT 1 FROM {t})").fetchone()[0]]  # fmt: skip


def start_bootstrap(cur: Any, release: dt.date, stale_minutes: int) -> int:
    """Take the lease for a bootstrap: its running row in `monitoring.pipeline_runs`, dated
    the release (LeaseError when another job runs). The caller commits it at once, in a
    transaction of its own, so the lease holds while the bootstrap computes."""
    return start_run(cur, "bootstrap", "bootstrap", release, stale_minutes)


def prepare_bootstrap(
    cur: Any,
    run_id: int,
    release: dt.date,
    wides: Mapping[str, Wide],
    sponsors: SponsorFile,
    markers: Mapping[str, Sequence[tuple[dt.date, int]]],
    scored: "Scored | None" = None,
    reasons: Sequence[tuple[dt.date, str, str, int]] | None = None,
    labeled_through: dt.date | None = None,
) -> BootstrapRows:
    """Every row of the bootstrap's write, made with no transaction open (ADR 0058): the wide
    rows, the amendment markers and, with `scored`, the release date's scores as first
    scores (`serving.first_rows`) as COPY data; the sponsor file, consumed through the run
    `run_id`, as its row's values; the reason counts as they are."""
    sponsors.last_run_id = run_id
    copies = [
        copy_payload(cur, "live.trial_state", WIDE_COLUMNS,
                     (_wide_values(w, run_id) for w in wides.values())),
        copy_payload(cur, "serving.amendment_markers", MARKER_COLUMNS, _marker_values(markers)),
    ]  # fmt: skip
    if scored is not None:
        latest, history, not_scored = serving.first_rows(scored, run_id)
        copies += [
            copy_payload(cur, "serving.trial_scores_latest", serving.LATEST_COLUMNS, latest),
            copy_payload(cur, "serving.trial_score_history", serving.HISTORY_COLUMNS, history),
            copy_payload(cur, "serving.trials_not_scored", serving.NOT_SCORED_COLUMNS,
                         not_scored),
        ]  # fmt: skip
    return BootstrapRows(
        run_id=run_id,
        release=release,
        copies=tuple(copies),
        file=file_values(sponsors),
        reasons=None if reasons is None else tuple(reasons),
        trials=len(wides),
        labeled_through=labeled_through,
    )


def load_bootstrap(cur: Any, rows: BootstrapRows, rebuild: bool = False) -> int:
    """Write the prepared rows of a bootstrap in the caller's one transaction, and end its
    run as succeeded with the release date as its watermark (ADR 0045); returns the run id.
    Nothing is computed here: every row arrives as COPY data or a plain value.

    The lease is renewed first (ADR 0060). Without `rebuild` the write refuses over any row
    in the tables a rebuild clears. A rebuild clears every table the daily job writes
    (`REBUILT_TABLES`), every other run's row, and the daily job's columns of
    `serving.data_status` (the reason counts' `labeled_through` stays, unless new counts
    come with the rows), so the next daily run is a first run on the new state. The
    bootstrap command itself runs on the laptop (ADR 0040)."""
    renew(cur, rows.run_id)  # the lease, first in the write
    if rebuild:
        cur.execute(f"TRUNCATE {', '.join(REBUILT_TABLES)}")
        cur.execute("DELETE FROM monitoring.pipeline_runs WHERE run_id <> %s", (rows.run_id,))
        cur.execute(
            """UPDATE serving.data_status SET data_as_of = NULL, model_version = NULL,
              early_stops_after_labels = NULL, updated_at = now()"""
        )
    elif occupied_tables(cur):
        raise RuntimeError("live state exists: the bootstrap refuses to load over it")
    for payload in rows.copies:
        with cur.copy(f"COPY {payload.table} ({', '.join(payload.columns)}) FROM STDIN") as copy:
            copy.write(payload.data)
    _write_file(cur, rows.file)
    if rows.reasons is not None:
        reason_counts.load(cur, rows.reasons, rows.labeled_through or rows.release)
    details = {"trials": rows.trials, "rows": {p.table: p.rows for p in rows.copies}}
    finish_run(cur, rows.run_id, "succeeded", {"watermark_to": rows.release, "details": details})
    return rows.run_id


def set_data_status(cur: Any, day: dt.date, model_version: str, early_stop: Sequence[str]) -> None:
    """The API's data-as-of date and model (ADR 0049), and the early stops recorded after the
    reason labels' date (ADR 0050); written only when a value changes."""
    cur.execute(
        """INSERT INTO serving.data_status (status_id, data_as_of, model_version)
        VALUES (1, %(d)s, %(m)s)
        ON CONFLICT (status_id) DO UPDATE SET data_as_of = EXCLUDED.data_as_of,
          model_version = EXCLUDED.model_version, updated_at = now()
        WHERE (serving.data_status.data_as_of, serving.data_status.model_version)
          IS DISTINCT FROM (EXCLUDED.data_as_of, EXCLUDED.model_version)""",
        {"d": day, "m": model_version},
    )
    cur.execute(
        """UPDATE serving.data_status s SET early_stops_after_labels = c.n, updated_at = now()
        FROM (SELECT CASE WHEN s2.labeled_through IS NULL THEN NULL ELSE (
                SELECT count(*) FROM live.outcome_events e
                WHERE e.kind = 'status' AND e.to_status = ANY(%(stops)s)
                  AND e.effective_date > s2.labeled_through AND e.effective_date <= %(d)s)
              END AS n FROM serving.data_status s2 WHERE s2.status_id = 1) c
        WHERE s.status_id = 1 AND s.early_stops_after_labels IS DISTINCT FROM c.n""",
        {"stops": list(early_stop), "d": day},
    )
