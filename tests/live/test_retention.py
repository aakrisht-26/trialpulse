"""The retention command of ADR 0045 on a real Postgres: rows older than 12 months are
exported, the file is verified, and only then are they deleted; each trial's newest row and
any row the sponsor pass has not consumed stay. Skipped without Postgres (failed in CI)."""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from trialpulse.cli import RefusedError
from trialpulse.live import retention, store
from trialpulse.live.sponsors import SponsorFile

psycopg = pytest.importorskip("psycopg")

AS_OF = dt.date(2027, 12, 15)  # the cutoff is 2026-12-15
OLD, NEW = dt.date(2026, 12, 1), dt.date(2027, 6, 1)


def _dsn(scratch: str) -> str:
    return scratch.replace("postgresql+psycopg://", "postgresql://", 1)


def _rows(dsn: str) -> None:
    """Two runs, the sponsor file consumed through the first, and rows old and new."""
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        consumed = store.start_run(cur, "daily", "daily", OLD, 120)
        store.finish_run(cur, consumed, "succeeded", {})
        later = store.start_run(cur, "daily", "daily", NEW, 120)
        store.finish_run(cur, later, "succeeded", {})
        sponsors = SponsorFile.empty(2, 365, NEW)
        sponsors.last_run_id = consumed
        store.save_file(cur, sponsors)
        versions = [  # (trial, post date, run, hash label)
            ("NCT00000001", OLD - dt.timedelta(days=30), consumed, "a0"),  # old: exported
            ("NCT00000001", OLD, consumed, "a1"),  # old: exported
            ("NCT00000001", NEW, later, "a2"),  # its newest: kept
            ("NCT00000002", OLD, consumed, "b0"),  # old but its newest: kept
            ("NCT00000003", OLD, later, "c0"),  # old but not consumed: kept
            ("NCT00000003", NEW, later, "c1"),
        ]
        for i, (trial, posted, run, label) in enumerate(versions):
            cur.execute(
                """INSERT INTO live.study_versions (nct_id, effective_date, status, title_phase,
                  intervention_types, feature_hash, run_id, ingested_at)
                VALUES (%s, %s, 'RECRUITING', 'NONE', '{DRUG}', %s, %s,
                  timestamptz '2027-01-01 00:00:00+00' + make_interval(secs => %s))""",
                (trial, posted, label.encode() * 16, run, i),
            )
        history = [
            ("NCT00000001", OLD - dt.timedelta(days=60), ["first_score"]),  # exported
            ("NCT00000001", OLD, ["calendar"]),  # exported
            ("NCT00000001", NEW, ["record_change"]),  # newest: kept
            ("NCT00000002", OLD, ["first_score"]),  # old but its newest: kept
        ]
        for trial, day, reasons in history:
            cur.execute(
                """INSERT INTO serving.trial_score_history (nct_id, as_of_date, model_version,
                  risk_12m, risk_24m, reasons, flagged, run_id, content_hash)
                VALUES (%s, %s, 'trialpulse-m4/1', 0.123456789, 0.25, %s, false, %s, %s)""",
                (trial, day, reasons, consumed, b"\x01" * 32),
            )
        con.commit()


def _left(dsn: str) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    with psycopg.connect(dsn) as con:
        versions = con.execute(
            "SELECT nct_id, effective_date FROM live.study_versions ORDER BY 1, 2"
        ).fetchall()
        history = con.execute(
            "SELECT nct_id, as_of_date FROM serving.trial_score_history ORDER BY 1, 2"
        ).fetchall()
    return versions, history


def test_old_rows_are_exported_verified_and_then_deleted(scratch: str, tmp_path: Path) -> None:
    dsn = _dsn(scratch)
    _rows(dsn)
    results = retention.retain(dsn, AS_OF, 12, tmp_path)
    by_table = {r.table: r for r in results}
    assert (by_table["serving.trial_score_history"].rows,
            by_table["serving.trial_score_history"].deleted) == (2, 2)  # fmt: skip
    assert (by_table["live.study_versions"].rows, by_table["live.study_versions"].deleted) == (2, 2)
    versions, history = _left(dsn)
    assert versions == [("NCT00000001", NEW), ("NCT00000002", OLD), ("NCT00000003", OLD),
                        ("NCT00000003", NEW)]  # fmt: skip
    assert history == [("NCT00000001", NEW), ("NCT00000002", OLD)]
    # The files hold exactly the deleted rows, every value intact, and a manifest.
    exported = pq.read_table(by_table["serving.trial_score_history"].path).to_pylist()
    assert [(r["nct_id"], r["as_of_date"]) for r in exported] == [
        ("NCT00000001", OLD - dt.timedelta(days=60)), ("NCT00000001", OLD)]  # fmt: skip
    assert exported[0]["risk_12m"] == 0.123456789
    assert exported[0]["content_hash"] == b"\x01" * 32
    assert exported[1]["reasons"] == ["calendar"]
    versions_file = pq.read_table(by_table["live.study_versions"].path).to_pylist()
    assert [r["feature_hash"] for r in versions_file] == [b"a0" * 16, b"a1" * 16]
    manifest = json.loads(by_table["live.study_versions"].path.with_suffix(".json").read_text())
    assert (manifest["rows"], manifest["before"], manifest["deleted"]) == (2, "2026-12-15", True)
    # Nothing is left to export the second time.
    again = retention.retain(dsn, AS_OF, 12, tmp_path)
    assert [(r.rows, r.deleted) for r in again] == [(0, 0), (0, 0)]
    with psycopg.connect(dsn) as con:
        runs = con.execute(
            "SELECT status FROM monitoring.pipeline_runs WHERE job = 'retention'"
        ).fetchall()
    assert runs == [("succeeded",), ("succeeded",)]


def test_versions_go_after_6_months_and_history_after_12(scratch: str, tmp_path: Path) -> None:
    """ADR 0069: with the version months set, a version 6 to 12 months old goes and a history
    row of the same age stays."""
    dsn = _dsn(scratch)
    _rows(dsn)
    with psycopg.connect(dsn) as con:
        consumed = con.execute("SELECT last_run_id FROM live.sponsor_file").fetchone()[0]
        con.execute(
            """INSERT INTO live.study_versions (nct_id, effective_date, status, title_phase,
              intervention_types, feature_hash, run_id, ingested_at)
            VALUES ('NCT00000001', %s, 'RECRUITING', 'NONE', '{DRUG}', %s, %s,
              timestamptz '2027-01-01 00:00:00+00')""",
            (AS_OF - dt.timedelta(days=270), b"m9" * 16, consumed),
        )
        con.execute(
            """INSERT INTO serving.trial_score_history (nct_id, as_of_date, model_version,
              risk_12m, risk_24m, reasons, flagged, run_id, content_hash)
            VALUES ('NCT00000001', %s, 'trialpulse-m4/1', 0.1, 0.2, '{calendar}', false, %s, %s)""",
            (AS_OF - dt.timedelta(days=270), consumed, b"\x02" * 32),
        )
        con.commit()
    results = retention.retain(dsn, AS_OF, 12, tmp_path, version_months=6)
    versions, history = _left(dsn)
    nine_months = AS_OF - dt.timedelta(days=270)
    assert ("NCT00000001", nine_months) not in versions
    assert ("NCT00000001", nine_months) in history
    by_table = {r.table: r for r in results}
    assert by_table["live.study_versions"].deleted == 3  # the two old rows and the 9-month one
    assert by_table["serving.trial_score_history"].deleted == 2
    manifests = [json.loads(p.read_text()) for p in tmp_path.glob("*.json")]
    assert {m["table"]: m["before"] for m in manifests} == {
        "live.study_versions": "2027-06-15", "serving.trial_score_history": "2026-12-15"
    }  # fmt: skip


def test_export_only_keeps_the_rows(scratch: str, tmp_path: Path) -> None:
    dsn = _dsn(scratch)
    _rows(dsn)
    before = _left(dsn)
    results = retention.retain(dsn, AS_OF, 12, tmp_path, export_only=True)
    assert [(r.rows, r.deleted) for r in results] == [(2, 0), (2, 0)]
    assert _left(dsn) == before
    assert all(r.path is not None and r.path.is_file() for r in results)


def test_a_file_that_does_not_match_its_rows_deletes_nothing(
    scratch: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the file read back differs from the rows (here, it lost one), nothing is deleted
    and the run is recorded as failed."""
    dsn = _dsn(scratch)
    _rows(dsn)
    before = _left(dsn)
    real = retention._write

    def lossy(path: Path, rows: Any) -> None:
        real(path, rows[:-1])

    monkeypatch.setattr(retention, "_write", lossy)
    with pytest.raises(retention.ExportMismatchError, match="nothing was deleted"):
        retention.retain(dsn, AS_OF, 12, tmp_path)
    assert _left(dsn) == before
    with psycopg.connect(dsn) as con:
        status = con.execute(
            "SELECT status FROM monitoring.pipeline_runs WHERE job = 'retention'"
        ).fetchone()
    assert status == ("failed",)


def test_a_changed_value_in_the_file_is_caught(
    scratch: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dsn = _dsn(scratch)
    _rows(dsn)
    before = _left(dsn)
    real = retention._write

    def altered(path: Path, rows: Any) -> None:
        real(path, [{**r, **({"risk_12m": 0.5} if "risk_12m" in r else {})} for r in rows])

    monkeypatch.setattr(retention, "_write", altered)
    with pytest.raises(retention.ExportMismatchError):
        retention.retain(dsn, AS_OF, 12, tmp_path)
    assert _left(dsn) == before


def test_retention_never_overlaps_a_running_job(scratch: str, tmp_path: Path) -> None:
    dsn = _dsn(scratch)
    _rows(dsn)
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        store.start_run(cur, "daily", "daily", AS_OF, 120)
        con.commit()
    with pytest.raises(store.LeaseError):
        retention.retain(dsn, AS_OF, 12, tmp_path)


def test_retention_refuses_on_a_runner(
    scratch: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(RefusedError, match="never runs on a runner"):
        retention.retain(_dsn(scratch), AS_OF, 12, tmp_path)


def test_a_slow_export_does_not_idle_in_a_transaction(
    scratch: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0058: the export and its check run with no transaction open, so a file that takes
    longer to write than Neon's idle-in-transaction limit (here lowered to 1 second) does
    not end the session; the rows are still exported, verified and deleted."""
    import time

    from psycopg import conninfo

    dsn = _dsn(scratch)
    _rows(dsn)
    real = retention._write

    def slow(path: Path, rows: Any) -> None:
        time.sleep(1.5)
        real(path, rows)

    monkeypatch.setattr(retention, "_write", slow)
    limited = conninfo.make_conninfo(dsn, options="-c idle_in_transaction_session_timeout=1000")
    results = retention.retain(limited, AS_OF, 12, tmp_path)
    assert all(r.deleted == r.rows for r in results)
    assert sum(r.deleted for r in results) > 0


def test_retention_that_lost_its_lease_deletes_nothing(
    scratch: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0060: while the file is written, retention's lease goes stale and a daily run takes
    over. The delete renews first, finds the lease gone, and deletes nothing; the run rows
    stay as the daily run left them."""
    dsn = _dsn(scratch)
    _rows(dsn)
    before = _left(dsn)
    real = retention._write

    def taken_over(path: Path, rows: Any) -> None:
        real(path, rows)
        with psycopg.connect(dsn) as con, con.cursor() as cur:
            cur.execute("UPDATE monitoring.pipeline_runs SET renewed_at = now() - "
                        "interval '3 hours' WHERE status = 'running'")  # fmt: skip
            store.start_run(cur, "daily", "daily", AS_OF, 120)
            con.commit()

    monkeypatch.setattr(retention, "_write", taken_over)
    with pytest.raises(store.LeaseLostError):
        retention.retain(dsn, AS_OF, 12, tmp_path)
    assert _left(dsn) == before
    with psycopg.connect(dsn) as con:
        runs = con.execute("SELECT job, status, error FROM monitoring.pipeline_runs "
                           "WHERE job IN ('retention', 'daily') AND status <> 'succeeded' "
                           "ORDER BY run_id").fetchall()  # fmt: skip
    assert runs == [("retention", "failed", "stale lease"), ("daily", "running", None)]


# ADR 0069: what the daily job reads of live.study_versions survives the retention -----------

DAILY_READERS = {"latest_live", "versions_after", "latest_post_date", "overlap_rows"}


def _readers_of_versions() -> set[str]:
    """Every function of the package whose code reads live.study_versions (an INSERT is not
    a read), but the retention itself and the laptop's replay."""
    import ast

    from trialpulse.config import REPO_ROOT

    found = set()
    for path in (REPO_ROOT / "src" / "trialpulse").rglob("*.py"):
        if path.name in ("retention.py", "replay_parity.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                text = ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
                if "FROM live.study_versions" in text:
                    found.add(node.name)
    return found


def test_the_daily_job_reads_versions_only_through_four_store_functions() -> None:
    assert _readers_of_versions() == DAILY_READERS


def _daily_reads(dsn: str) -> dict[str, Any]:
    """What the daily job's four reads return, with the arguments it passes them."""
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        trials = [r[0] for r in cur.execute("SELECT DISTINCT nct_id FROM live.study_versions")]
        consumed = store.load_file(cur).last_run_id
        latest = store.latest_post_date(cur)
        assert latest is not None
        return {
            "latest_live": store.latest_live(cur, trials),
            "versions_after": store.versions_after(cur, consumed),
            "latest_post_date": latest,
            "overlap_rows": store.overlap_rows(cur, latest - dt.timedelta(days=3)),
        }


def test_retention_at_6_months_deletes_no_version_the_daily_job_reads(
    scratch: str, tmp_path: Path
) -> None:
    """Every read the daily job makes of live.study_versions gives the same answer before and
    after the versions older than 6 months are deleted. A trial's newest row and the rows the
    sponsor pass has not consumed are what it reads, and the retention keeps both."""
    dsn = _dsn(scratch)
    _rows(dsn)
    before = _daily_reads(dsn)
    results = retention.retain(dsn, AS_OF, 12, tmp_path, version_months=6)
    assert {r.table: r.deleted for r in results}["live.study_versions"] == 2
    assert _daily_reads(dsn) == before
