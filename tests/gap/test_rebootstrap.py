"""The rebootstrap's combine step and its seed rule (`gap.rebootstrap`, ADR 0074) on small
synthetic warehouses written here. No real row is used."""

import datetime as dt
from pathlib import Path
from typing import Any

import duckdb
import pytest

from trialpulse.cli import RefusedError
from trialpulse.config import load_project_config
from trialpulse.gap import rebootstrap

CFG = load_project_config()
SEED = dt.date(2026, 11, 2)
VERSIONS = "nct_id VARCHAR, nct_version BIGINT, effective_date DATE, brief_title_hash VARCHAR"


def _db(path: Path, versions: list[tuple[Any, ...]], texts: list[tuple[str, str]],
        extra: str = "") -> None:  # fmt: skip
    with duckdb.connect(str(path)) as con:
        con.execute(f"CREATE TABLE versions ({VERSIONS})")
        con.executemany("INSERT INTO versions VALUES (?, ?, ?, ?)", versions)
        con.execute("CREATE TABLE texts (text_hash VARCHAR, text VARCHAR)")
        con.executemany("INSERT INTO texts VALUES (?, ?)", texts)
        if extra:
            con.execute(extra)


@pytest.fixture
def con(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    d = dt.date
    _db(tmp_path / "pinned.duckdb",
        [("NCT1", 0, d(2020, 1, 1), "a"), ("NCT1", 1, d(2026, 9, 20), "b"),
         ("NCT2", 0, d(2021, 1, 1), "a")],
        [("a", "Title A"), ("b", "Title B")],
        "CREATE TABLE stop_reasons AS SELECT 'NCT9' AS nct_id, 'accrual' AS reason;"
        "CREATE TABLE build_info AS SELECT 'schema_version' AS key, '4' AS value")  # fmt: skip
    _db(tmp_path / "store.duckdb",
        [("NCT1", 2, d(2026, 10, 1), "b"), ("NCT3", 0, d(2026, 10, 5), "c"),
         ("NCT3", 1, d(2026, 11, 3), "c")],  # the last one is after the seed
        [("b", "Title B"), ("c", "Title C")],
        "CREATE TABLE interventions (nct_id VARCHAR, nct_version BIGINT, "
        "intervention_name VARCHAR, intervention_type VARCHAR, description VARCHAR);"
        "INSERT INTO interventions VALUES ('NCT3', 0, 'Aspirin', 'DRUG', NULL), "
        "('NCT3', 1, 'Aspirin', 'DRUG', NULL)")  # fmt: skip
    (tmp_path / "piv").mkdir()
    with duckdb.connect() as c:
        c.execute(
            f"COPY (SELECT 'NCT1' AS nct_id, 0::BIGINT AS nct_version, 'Saline' AS "
            f"intervention_name, 'OTHER' AS intervention_type, NULL::VARCHAR AS description) "
            f"TO '{(tmp_path / 'piv' / 'part-0.parquet').as_posix()}' (FORMAT parquet)"
        )
    out = duckdb.connect()
    out.execute(f"ATTACH '{(tmp_path / 'pinned.duckdb').as_posix()}' AS pinned (READ_ONLY)")
    out.execute(f"ATTACH '{(tmp_path / 'store.duckdb').as_posix()}' AS gap (READ_ONLY)")
    return out


def test_the_combined_rows_are_pinned_plus_gap_rows_through_the_seed(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    piv = (tmp_path / "piv" / "*.parquet").as_posix()
    counts = rebootstrap.combine(con, SEED, piv, tmp_path / "combined", gap_label="v@c")
    assert counts == {"versions": 5, "texts": 3, "gap_versions": 2, "gap_trials": 2,
                      "intervention_rows": 2}  # fmt: skip
    with duckdb.connect(str(tmp_path / "combined" / "warehouse.duckdb"), read_only=True) as c:
        keys = c.execute("SELECT nct_id, nct_version FROM versions ORDER BY 1, 2").fetchall()
        info = dict(c.execute("SELECT key, value FROM build_info").fetchall())
        reasons = c.execute("SELECT count(*) FROM stop_reasons").fetchone()
    assert keys == [("NCT1", 0), ("NCT1", 1), ("NCT1", 2), ("NCT2", 0), ("NCT3", 0)]
    assert info == {"schema_version": "4", "combined.gap": "v@c", "combined.seed": "2026-11-02"}
    assert reasons == (1,)
    glob = rebootstrap.combined_interventions(tmp_path / "combined")
    iv = duckdb.sql(f"SELECT nct_id, nct_version FROM read_parquet('{glob}') ORDER BY 1").fetchall()
    assert iv == [("NCT1", 0), ("NCT3", 0)]  # the version after the seed is left out


def test_a_gap_version_not_after_the_pinned_history_is_refused(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    con.execute(f"ATTACH '{(tmp_path / 'bad.duckdb').as_posix()}' AS bad")
    con.execute(f"CREATE TABLE bad.versions ({VERSIONS})")
    con.execute("INSERT INTO bad.versions VALUES ('NCT1', 1, DATE '2026-10-01', 'b')")
    con.execute("DETACH gap")
    con.execute("DETACH bad")
    con.execute(f"ATTACH '{(tmp_path / 'bad.duckdb').as_posix()}' AS gap (READ_ONLY)")
    piv = (tmp_path / "piv" / "*.parquet").as_posix()
    with pytest.raises(RefusedError, match="1 gap versions are not after"):
        rebootstrap.combine(con, SEED, piv, tmp_path / "combined")


def test_the_seed_is_the_release_date_minus_one_to_seven_days() -> None:
    released = rebootstrap.release_date(CFG)
    assert released == dt.date(2026, 11, 4)
    rebootstrap.check_seed(CFG, released - dt.timedelta(days=1))
    rebootstrap.check_seed(CFG, released - dt.timedelta(days=7))
    for wrong in (released, released - dt.timedelta(days=8)):
        with pytest.raises(RefusedError, match="outside the tail"):
            rebootstrap.check_seed(CFG, wrong)


def test_the_seeded_configuration_moves_the_cutoff_only() -> None:
    seeded = rebootstrap.seeded_cfg(CFG, SEED)
    assert seeded.dataset.cutoff == SEED
    assert seeded.dataset.revision == CFG.dataset.revision
    assert CFG.dataset.cutoff == dt.date(2026, 9, 25)


def test_the_steps_refuse_on_a_runner(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(RefusedError, match="ADR 0040"):
        rebootstrap.combine(con, SEED, "x", tmp_path / "combined")
    with pytest.raises(RefusedError, match="ADR 0040"):
        rebootstrap.main(["combine", "--seed", SEED.isoformat()])
