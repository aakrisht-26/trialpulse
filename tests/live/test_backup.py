"""Backups and restores of the live system (`live.backup`, ADR 0045) on local scratch databases:
the files equal the rows, a restore equals the backup, a damaged file is caught, eight are kept,
and nothing restores anywhere but localhost. Synthetic rows only."""

import datetime as dt
import gzip
import json
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import conninfo

from trialpulse.cli import RefusedError
from trialpulse.live import backup

TEXT = "a tab\there, a newline\nhere, a backslash \\ and a quote '"


def _plain(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _seed(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as con:
        run = con.execute(
            """INSERT INTO monitoring.pipeline_runs (job, started_at, status, details)
               VALUES ('daily', '2026-11-08 12:34:56.789+00', 'succeeded', %s::jsonb)
               RETURNING run_id""",
            (json.dumps({"full": True, "note": TEXT, "seconds": {"publish": 361.6738}}),),
        ).fetchone()
        assert run is not None
        con.execute(
            """INSERT INTO monitoring.score_files
               (month, as_of_date, model_version, rows, sha256, content, run_id)
               VALUES ('2026-11-01', '2026-11-06', 'm/1', 3, 'abc', %s, %s)""",
            (bytes(range(256)), run[0]),
        )
        con.execute(
            """INSERT INTO live.outcome_events (nct_id, to_status, effective_date, run_id, kind)
               VALUES ('NCT00000001', 'TERMINATED', '2026-11-05', %s, 'status'),
                      ('NCT00000002', 'COMPLETED', '2026-11-04', %s, 'status')""",
            (run[0], run[0]),
        )
        con.execute(
            """INSERT INTO serving.reason_counts VALUES ('2020-04-01', 'covid19', 'OTHER', 12),
               ('2020-04-01', 'accrual', 'INDUSTRY', 7)"""
        )
        con.execute(
            """INSERT INTO serving.trial_display (nct_id, brief_title, areas)
               VALUES ('NCT00000001', %s, '{1,2}')""",
            (TEXT,),
        )


@pytest.fixture
def seeded(scratch: str) -> str:
    dsn = _plain(scratch)
    _seed(dsn)
    return dsn


def _restore_name(request: pytest.FixtureRequest) -> str:
    return f"trialpulse_restore_t{abs(hash(request.node.name)) % 10**8}"


def test_a_backup_holds_every_table_and_its_files_read_back_equal(
    seeded: str, tmp_path: Path
) -> None:
    result = backup.backup(seeded, tmp_path, keep=8, today=dt.date(2026, 11, 10))
    folder = tmp_path / "2026-11-10"
    manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
    with psycopg.connect(seeded) as con:
        names = {f"{s}.{n}" for s, n in con.execute(
            """SELECT table_schema, table_name FROM information_schema.tables
                WHERE table_schema IN ('live', 'serving', 'monitoring')
                  AND table_type = 'BASE TABLE'""").fetchall()}  # fmt: skip
    assert set(manifest["tables"]) == names  # every table, empty ones included
    assert manifest["tables"]["live.outcome_events"]["rows"] == 2
    assert manifest["tables"]["serving.reason_counts"]["rows"] == 2
    assert result["rows"] == sum(t["rows"] for t in manifest["tables"].values())
    assert manifest["alembic_revision"]
    for item in manifest["tables"].values():
        assert backup.read_back(folder / item["file"]) == (item["rows"], item["sha256"])
    assert not (tmp_path / "2026-11-10.partial").exists()


def test_a_restore_equals_the_backup_value_for_value(
    seeded: str, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    backup.backup(seeded, tmp_path, keep=8, today=dt.date(2026, 11, 10))
    name = _restore_name(request)
    try:
        result = backup.restore(tmp_path / "2026-11-10", seeded, name)
        assert result["equal"] is True, result["different"]
        restored = conninfo.make_conninfo(seeded, dbname=name)
        with psycopg.connect(restored) as con:
            title = con.execute("SELECT brief_title FROM serving.trial_display").fetchone()
            content = con.execute("SELECT content FROM monitoring.score_files").fetchone()
            # the identity sequence moved past the loaded ids
            new = con.execute("""INSERT INTO monitoring.pipeline_runs (job, status)
                VALUES ('daily', 'running') RETURNING run_id""").fetchone()  # fmt: skip
            old = con.execute("SELECT min(run_id) FROM monitoring.pipeline_runs").fetchone()
        assert title == (TEXT,)
        assert content is not None
        assert bytes(content[0]) == bytes(range(256))
        assert new is not None
        assert old is not None
        assert new[0] > old[0]
    finally:
        _drop(seeded, name)


def _drop(dsn: str, name: str) -> None:
    admin = conninfo.make_conninfo(dsn, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as con:
        con.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def test_a_damaged_file_fails_the_restore_check(
    seeded: str, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    backup.backup(seeded, tmp_path, keep=8, today=dt.date(2026, 11, 10))
    folder = tmp_path / "2026-11-10"
    path = folder / "serving.reason_counts.copy.gz"
    text = gzip.decompress(path.read_bytes()).replace(b"\t12\n", b"\t13\n")
    path.write_bytes(gzip.compress(text))
    assert (
        backup.read_back(path)[1]
        != json.loads((folder / "manifest.json").read_text("utf-8"))["tables"][
            "serving.reason_counts"
        ]["sha256"]
    )
    name = _restore_name(request)
    try:
        result = backup.restore(folder, seeded, name)
    finally:
        _drop(seeded, name)
    assert result["equal"] is False
    assert result["different"] == ["serving.reason_counts"]


def test_a_file_that_differs_from_the_stream_is_not_kept(
    seeded: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = backup.read_back
    monkeypatch.setattr(backup, "read_back", lambda path: (real(path)[0], "0" * 64))
    with pytest.raises(backup.BackupError, match="read back differs"):
        backup.backup(seeded, tmp_path, keep=8, today=dt.date(2026, 11, 10))
    assert not (tmp_path / "2026-11-10").exists()  # no manifest, no backup


def test_a_backup_is_never_overwritten(seeded: str, tmp_path: Path) -> None:
    backup.backup(seeded, tmp_path, keep=8, today=dt.date(2026, 11, 10))
    with pytest.raises(RefusedError, match="never overwritten"):
        backup.backup(seeded, tmp_path, keep=8, today=dt.date(2026, 11, 10))


def test_the_newest_eight_finished_backups_are_kept(tmp_path: Path) -> None:
    for day in range(1, 11):
        folder = tmp_path / f"2026-10-{day:02d}"
        folder.mkdir()
        (folder / "manifest.json").write_text("{}", "utf-8")
    (tmp_path / "2026-09-01").mkdir()  # no manifest: not a backup, never counted
    (tmp_path / "2026-11-01.partial").mkdir()
    removed = backup.prune(tmp_path, 8)
    assert removed == ["2026-10-01", "2026-10-02"]
    assert backup.complete_backups(tmp_path)[0].name == "2026-10-03"
    assert (tmp_path / "2026-09-01").exists()
    assert (tmp_path / "2026-11-01.partial").exists()


@pytest.mark.parametrize("dsn", [
    "postgresql://u:p@ep-example.us-east-1.aws.neon.tech/db",
    "host=db.example.org dbname=x user=u",
])  # fmt: skip
def test_a_restore_goes_only_into_the_laptops_postgres(dsn: str, tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("{}", "utf-8")
    with pytest.raises(RefusedError, match="localhost"):
        backup.restore(tmp_path, dsn, "trialpulse_restore")


def test_a_restore_database_must_carry_the_restore_prefix(tmp_path: Path) -> None:
    with pytest.raises(RefusedError, match="starts with"):
        backup.restore(tmp_path, "postgresql://u:p@localhost/x", "trialpulse")


def test_the_load_order_follows_the_foreign_keys() -> None:
    edges = [("live.outcome_events", "monitoring.pipeline_runs"),
             ("monitoring.score_files", "monitoring.pipeline_runs")]  # fmt: skip
    order = backup._load_order(
        ["live.outcome_events", "monitoring.score_files", "monitoring.pipeline_runs"], edges
    )
    assert order.index("monitoring.pipeline_runs") == 0


def test_the_command_prints_no_connection_string(
    seeded: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setattr(backup, "_dsn", lambda target: seeded)
    assert backup.main(["backup", "--target", "local", "--out-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    password = conninfo.conninfo_to_dict(seeded).get("password")
    assert seeded not in out
    assert not password or str(password) not in out
    assert json.loads(out)["tables"] >= 10


def _finished(out: Path, name: str, manifest: bool = True) -> None:
    folder = out / name
    folder.mkdir(parents=True)
    if manifest:
        (folder / "manifest.json").write_text("{}", encoding="utf-8")


def test_a_backup_under_seven_days_old_makes_the_scheduled_run_skip(tmp_path: Path) -> None:
    today = dt.date(2026, 11, 17)
    assert backup.recent_backup(tmp_path, today) is None  # no backup at all
    _finished(tmp_path, "2026-11-10")  # 7 days old: due again
    _finished(tmp_path, "2026-11-15", manifest=False)  # never finished: not a backup
    assert backup.recent_backup(tmp_path, today) is None
    _finished(tmp_path, "2026-11-11")  # 6 days old
    found = backup.recent_backup(tmp_path, today)
    assert found is not None
    assert found.name == "2026-11-11"


def test_the_recent_step_exits_0_to_skip_and_3_to_back_up(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert backup.main(["recent", "--out-dir", str(tmp_path)]) == backup.NOT_RECENT
    _finished(tmp_path, dt.datetime.now(dt.UTC).date().isoformat())
    assert backup.main(["recent", "--out-dir", str(tmp_path)]) == 0
    assert "recent backup:" in capsys.readouterr().out


class FakeGh:
    def __init__(self, open_issues: list[dict[str, Any]]) -> None:
        self.open_issues = open_issues
        self.calls: list[list[str]] = []

    def __call__(self, args: Any) -> str:
        self.calls.append(list(args))
        return json.dumps(self.open_issues) if args[:2] == ["issue", "list"] else ""


DAY = dt.date(2026, 11, 17)


def test_a_failed_backup_opens_the_issue_with_the_date_and_exit_code_only() -> None:
    gh = FakeGh([{"number": 4, "title": "Daily live run failed"}])
    assert backup.report(1, DAY, gh) == "opened the issue"
    create = gh.calls[-1]
    assert create[:4] == ["issue", "create", "--title", backup.ISSUE_TITLE]
    body = create[create.index("--body") + 1]
    assert "2026-11-17" in body
    assert "exit code 1" in body
    assert "data/logs on the laptop" in body  # the log itself stays there
    assert "host" not in body.lower()


def test_a_second_failure_comments_on_the_open_issue() -> None:
    gh = FakeGh([{"number": 12, "title": backup.ISSUE_TITLE}])
    assert backup.report(1, DAY, gh) == "commented on issue 12"
    assert gh.calls[-1][:3] == ["issue", "comment", "12"]


def test_a_successful_backup_closes_the_open_issue_and_otherwise_does_nothing() -> None:
    gh = FakeGh([{"number": 12, "title": backup.ISSUE_TITLE}])
    assert backup.report(0, DAY, gh) == "closed issue 12"
    assert gh.calls[-1][:4] == ["issue", "close", "12", "--comment"]
    quiet = FakeGh([])
    assert backup.report(0, DAY, quiet) == "nothing to report"
    assert [c[:2] for c in quiet.calls] == [["issue", "list"]]
