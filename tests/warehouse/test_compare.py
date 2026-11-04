"""The old-against-new guard of a warehouse rebuild (ADRs 0066 and 0067), on two small files."""

from pathlib import Path

import duckdb
import pytest

from trialpulse.cli import RefusedError
from trialpulse.warehouse import compare

OLD = [
    "CREATE TABLE versions (nct_id VARCHAR, nct_version BIGINT, overall_status VARCHAR, "
    "note VARCHAR, content_hash VARCHAR)",
    "INSERT INTO versions VALUES ('NCT00000001', 0, 'RECRUITING', 'SECRET-A', 'h1'), "
    "('NCT00000001', 1, 'COMPLETED', 'SECRET-B', 'h2'), "
    "('NCT00000002', 0, 'RECRUITING', NULL, 'h3'), "
    "('NCT00000003', 0, 'WITHDRAWN', 'SECRET-C', 'h4')",
    "CREATE TABLE trials (nct_id VARCHAR, latest_status VARCHAR, n INTEGER)",
    "INSERT INTO trials VALUES ('NCT00000001', 'COMPLETED', 2), ('NCT00000002', 'RECRUITING', 1)",
    "CREATE TABLE build_info (key VARCHAR, value VARCHAR)",
    "INSERT INTO build_info VALUES ('schema_version', '3'), ('raw.rows', '4')",
    "CREATE TABLE texts (text_hash VARCHAR, text VARCHAR)",
    "INSERT INTO texts VALUES ('t1', 'SECRET text one'), ('t2', 'SECRET text two')",
    "CREATE TABLE versions_quarantine (nct_id VARCHAR, nct_version BIGINT, reasons VARCHAR)",
    "INSERT INTO versions_quarantine VALUES ('NCT00000009', 1, 'row: unique_history_version'), "
    "('NCT00000009', 1, 'row: unique_history_version')",
    "CREATE TABLE gone (x INTEGER)",
    "INSERT INTO gone VALUES (1)",
]
NEW = [
    "CREATE TABLE versions (nct_id VARCHAR, nct_version BIGINT, overall_status VARCHAR, "
    "note VARCHAR, content_hash VARCHAR, intervention_types VARCHAR[])",
    "INSERT INTO versions VALUES ('NCT00000001', 0, 'RECRUITING', 'SECRET-A', 'g1', ['DRUG']), "
    "('NCT00000001', 1, 'COMPLETED', 'SECRET-B', 'g2', []), "
    "('NCT00000002', 0, 'TERMINATED', NULL, 'g3', []), "
    "('NCT00000004', 0, 'RECRUITING', 'SECRET-D', 'g5', [])",
    "CREATE TABLE trials (nct_id VARCHAR, latest_status VARCHAR, n BIGINT)",
    "INSERT INTO trials VALUES ('NCT00000001', 'COMPLETED', 2), ('NCT00000002', 'RECRUITING', 1)",
    "CREATE TABLE build_info (key VARCHAR, value VARCHAR)",
    "INSERT INTO build_info VALUES ('schema_version', '4'), ('raw.rows', '4'), "
    "('interventions.rows', '7')",
    "CREATE TABLE texts (text_hash VARCHAR, text VARCHAR)",
    "INSERT INTO texts VALUES ('t2', 'SECRET text two'), ('t1', 'SECRET text one')",
    "CREATE TABLE versions_quarantine (nct_id VARCHAR, nct_version BIGINT, reasons VARCHAR)",
    "INSERT INTO versions_quarantine VALUES ('NCT00000009', 1, 'row: unique_history_version'), "
    "('NCT00000009', 1, 'row: unique_history_version')",
    "CREATE TABLE added (x INTEGER)",
]


def _file(path: Path, statements: list[str]) -> Path:
    with duckdb.connect(str(path)) as con:
        for sql in statements:
            con.execute(sql)
    return path


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path]:
    return _file(tmp_path / "old.duckdb", OLD), _file(tmp_path / "new.duckdb", NEW)


def _by_name(results: list[compare.TableResult]) -> dict[str, compare.TableResult]:
    return {r.name: r for r in results}


def test_every_table_and_column_is_counted(files: tuple[Path, Path], tmp_path: Path) -> None:
    old, new = files
    tables = _by_name(compare.compare_files(old, new, temp_dir=tmp_path / "tmp"))
    assert set(tables) == {"added", "build_info", "gone", "texts", "trials", "versions",
                           "versions_quarantine"}  # fmt: skip
    versions = tables["versions"]
    assert (versions.matched, versions.only_old, versions.only_new) == (3, 1, 1)
    columns = {c.name: c for c in versions.columns}
    assert (columns["content_hash"].differing, columns["content_hash"].allowed) == (3, True)
    assert (columns["overall_status"].differing, columns["overall_status"].allowed) == (1, False)
    assert columns["note"].differing == 0  # NULL equals NULL
    assert (columns["intervention_types"].status, columns["intervention_types"].allowed) == (
        "only in new",
        True,
    )
    assert tables["texts"].problems() == []
    assert tables["trials"].problems() == ["trials.n: type changed"]
    assert tables["build_info"].allowed
    assert tables["build_info"].named_keys == {
        "differing": ["schema_version"],
        "only in old": [],
        "only in new": ["interventions.rows"],
    }
    quarantine = tables["versions_quarantine"]  # a key that repeats: compared as whole rows
    assert (quarantine.status, quarantine.only_old, quarantine.only_new) == (
        "compared as rows",
        0,
        0,
    )
    assert tables["gone"].problems() == ["gone: table only in old"]
    assert tables["added"].problems() == ["added: table only in new"]
    assert versions.problems() == ["versions: rows on one side only", "versions.overall_status"]


def test_the_command_prints_counts_and_never_a_value(
    files: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old, new = files
    out = tmp_path / "result.json"
    spill = ["--temp-dir", str(tmp_path / "tmp")]
    assert compare.main(["--old", str(old), "--new", str(new), "--out", str(out), *spill]) == 1
    printed = capsys.readouterr().out
    assert "content_hash: 3 of 3 matched rows differ (allowed)" in printed
    assert "overall_status: 1 of 3 matched rows differ" in printed
    assert "intervention_types: column only in new (allowed)" in printed
    assert "keys differing: schema_version" in printed
    verdict = printed.splitlines()[-1]
    assert verdict == (
        "Differences outside the allowlist: added: table only in new; gone: table only in old; "
        "trials.n: type changed; versions: rows on one side only; versions.overall_status"
    )
    for value in ("SECRET", "NCT0000", "TERMINATED", "WITHDRAWN", "g1", "h1", "unique_history"):
        assert value not in printed, value
        assert value not in out.read_text(encoding="utf-8"), value


def test_nothing_outside_the_allowlist_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = _file(tmp_path / "old.duckdb", OLD[:2])
    new = _file(tmp_path / "new.duckdb", [OLD[0], OLD[1].replace("'h1'", "'k1'")])
    spill = ["--temp-dir", str(tmp_path / "tmp")]
    assert compare.main(["--old", str(old), "--new", str(new), *spill]) == 0
    assert "Differences outside the allowlist: none" in capsys.readouterr().out
    # The allowlist given on the command line replaces the default one.
    assert compare.main(["--old", str(old), "--new", str(new), "--allow", "trials", *spill]) == 1


def test_the_command_refuses_a_missing_or_the_same_file(files: tuple[Path, Path]) -> None:
    old, _ = files
    with pytest.raises(RefusedError, match="no warehouse"):
        compare.main(["--old", str(old.with_name("missing.duckdb")), "--new", str(old)])
    with pytest.raises(RefusedError, match="same file"):
        compare.main(["--old", str(old), "--new", str(old)])
