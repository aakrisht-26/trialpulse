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
    build_info = tables["build_info"]  # judged by key: only the allowed keys changed
    assert not build_info.allowed
    assert build_info.allowed_keys == ("interventions.*", "schema_version")
    assert build_info.named_keys == {
        "differing": ["schema_version"],
        "only in old": [],
        "only in new": ["interventions.rows"],
    }
    assert build_info.problems() == []
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
    assert "keys differing: schema_version (allowed)" in printed
    assert "keys only in new: interventions.rows (allowed)" in printed
    assert "value: 1 of 2 matched rows differ (judged by key, below)" in printed
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


def test_a_statistics_table_is_judged_by_its_keys(tmp_path: Path) -> None:
    """Only the allowed keys of build_info may change (ADR 0066): any other key that differs,
    appears or disappears is a difference outside the allowlist."""
    table = "CREATE TABLE build_info (key VARCHAR, value VARCHAR)"
    old = _file(
        tmp_path / "old.duckdb",
        [table, "INSERT INTO build_info VALUES ('schema_version', '3'), ('raw.rows', '4'), "
                "('dataset.revision', 'v1'), ('raw.files', '19')"],
    )  # fmt: skip
    new = _file(
        tmp_path / "new.duckdb",
        [table, "INSERT INTO build_info VALUES ('schema_version', '4'), ('raw.rows', '5'), "
                "('dataset.revision', 'v1'), ('interventions.rows', '7'), ('texts.rows', '2')"],
    )  # fmt: skip
    result = _by_name(compare.compare_files(old, new, temp_dir=tmp_path / "tmp"))["build_info"]
    assert result.named_keys == {
        "differing": ["raw.rows", "schema_version"],
        "only in old": ["raw.files"],
        "only in new": ["interventions.rows", "texts.rows"],
    }
    assert result.problems() == [
        "build_info: keys outside the allowlist: raw.files, raw.rows, texts.rows"
    ]
    # Allowed keys only: nothing outside, though rows sit on one side and a value differs.
    allowed = (*compare.DEFAULT_ALLOWED, "build_info[raw.*]", "build_info[texts.rows]")
    result = _by_name(compare.compare_files(old, new, allowed, temp_dir=tmp_path / "tmp"))
    assert result["build_info"].problems() == []
    # The whole table on the allowlist allows every key, as before.
    whole = _by_name(compare.compare_files(old, new, ["build_info"], temp_dir=tmp_path / "tmp"))
    assert whole["build_info"].problems() == []


def test_a_key_entry_for_another_table_or_a_malformed_entry_is_refused(tmp_path: Path) -> None:
    old = _file(tmp_path / "old.duckdb", OLD[:2])
    for entry in ("versions[nct_id]", "build_info[", "build_info.value]"):
        with pytest.raises(RefusedError):
            compare.compare_files(old, old, [entry], temp_dir=tmp_path / "tmp")


def test_a_value_that_becomes_missing_or_present_is_counted(tmp_path: Path) -> None:
    """NULL against a value differs (IS DISTINCT FROM), in a table matched by key."""
    table = "CREATE TABLE trials (nct_id VARCHAR, latest_status VARCHAR, n INTEGER)"
    old = _file(
        tmp_path / "old.duckdb",
        [table, "INSERT INTO trials VALUES ('NCT00000001', NULL, 1), ('NCT00000002', 'X', NULL), "
                "('NCT00000003', NULL, NULL)"],
    )  # fmt: skip
    new = _file(
        tmp_path / "new.duckdb",
        [table, "INSERT INTO trials VALUES ('NCT00000001', 'Y', 1), ('NCT00000002', 'X', 2), "
                "('NCT00000003', NULL, NULL)"],
    )  # fmt: skip
    trials = _by_name(compare.compare_files(old, new, temp_dir=tmp_path / "tmp"))["trials"]
    assert trials.key == ("nct_id",)
    columns = {c.name: c.differing for c in trials.columns}
    assert columns == {"latest_status": 1, "n": 1}
    assert trials.problems() == ["trials.latest_status", "trials.n"]


def test_a_table_compared_as_whole_rows_counts_changed_and_repeated_rows(
    tmp_path: Path,
) -> None:
    """Without a usable key the rows are compared as multisets (EXCEPT ALL) over every column
    outside the allowlist: a changed row and a repeated row are both counted."""
    table = (
        "CREATE TABLE versions_quarantine (nct_id VARCHAR, nct_version BIGINT, reasons VARCHAR, "
        "content_hash VARCHAR)"
    )
    old = _file(
        tmp_path / "old.duckdb",
        [table, "INSERT INTO versions_quarantine VALUES ('NCT00000009', 1, 'a', 'h1'), "
                "('NCT00000009', 1, 'a', 'h1'), ('NCT00000009', 2, 'b', 'h2')"],
    )  # fmt: skip
    new = _file(
        tmp_path / "new.duckdb",
        [table, "INSERT INTO versions_quarantine VALUES ('NCT00000009', 1, 'a', 'k1'), "
                "('NCT00000009', 2, 'changed', 'k2'), ('NCT00000009', 3, 'c', 'k3')"],
    )  # fmt: skip
    result = _by_name(compare.compare_files(old, new, temp_dir=tmp_path / "tmp"))
    quarantine = result["versions_quarantine"]
    assert quarantine.status == "compared as rows"
    # Old less new: the repeated row and the changed row; new less old: the changed row and
    # the added one. The allowed content_hash is left out, so the first row still matches.
    assert (quarantine.only_old, quarantine.only_new, quarantine.matched) == (2, 2, 1)
    assert quarantine.problems() == ["versions_quarantine: rows on one side only"]
    # Identical rows apart from the allowed column: nothing outside the allowlist.
    same = _file(
        tmp_path / "same.duckdb",
        [table, "INSERT INTO versions_quarantine VALUES ('NCT00000009', 1, 'a', 'x'), "
                "('NCT00000009', 1, 'a', 'y'), ('NCT00000009', 2, 'b', 'z')"],
    )  # fmt: skip
    again = _by_name(compare.compare_files(old, same, temp_dir=tmp_path / "tmp"))
    assert again["versions_quarantine"].problems() == []


def test_the_command_refuses_a_missing_or_the_same_file(files: tuple[Path, Path]) -> None:
    old, _ = files
    with pytest.raises(RefusedError, match="no warehouse"):
        compare.main(["--old", str(old.with_name("missing.duckdb")), "--new", str(old)])
    with pytest.raises(RefusedError, match="same file"):
        compare.main(["--old", str(old), "--new", str(old)])
