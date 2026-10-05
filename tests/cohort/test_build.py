"""The cohort build command end to end, on a tiny warehouse file made of mini histories."""

import csv
import json
from pathlib import Path

import duckdb
import numpy as np
import pytest

from trialpulse.cli import RefusedError
from trialpulse.cohort import build
from trialpulse.cohort.audit import aj_sanity_table
from trialpulse.cohort.landmarks import LANDMARK_COLUMNS
from trialpulse.dates import days_between
from trialpulse.eval.walkforward import REQUIRED_COLUMNS, load_landmark_rows
from trialpulse.models.aalen_johansen import aalen_johansen

from .conftest import load_versions
from .test_cohort import MINI


def _warehouse(path: Path, schema_version: str = "2") -> Path:
    with duckdb.connect(str(path)) as con:
        load_versions(con, MINI)
        con.execute("CREATE TABLE build_info (key VARCHAR, value VARCHAR)")
        con.execute("INSERT INTO build_info VALUES ('schema_version', ?)", [schema_version])
    return path


@pytest.fixture
def built(tmp_path: Path) -> Path:
    warehouse = _warehouse(tmp_path / "warehouse.duckdb")
    audit = tmp_path / "data_audit.md"
    audit.write_text("# Data audit, part 1: the warehouse\n\nPart one text.\n", encoding="utf-8")
    out = tmp_path / "cohort"
    args = ["--warehouse", str(warehouse), "--out-dir", str(out), "--audit", str(audit)]
    assert build.main(args) == 0
    return tmp_path


def test_the_landmarks_file_is_the_step_8_input_contract(built: Path) -> None:
    path = built / "cohort" / "landmarks.parquet"
    with duckdb.connect() as con:
        described = con.execute(f"DESCRIBE SELECT * FROM '{path.as_posix()}'").fetchall()
    assert [r[0] for r in described] == list(LANDMARK_COLUMNS)
    rows = load_landmark_rows(path)
    assert set(rows.features) == {"stratum"}  # nothing label-derived reaches the models
    assert len(rows) > 0
    assert np.all(rows.event_date > rows.landmark_date)
    assert set(rows.event.tolist()) <= {0, 1, 2}
    assert set(REQUIRED_COLUMNS) < set(LANDMARK_COLUMNS)


def test_outputs_audit_and_a_repeat_build(built: Path) -> None:
    out = built / "cohort"
    for name in ("person_period.parquet", "outcomes.parquet", "aj_sanity_by_sponsor_class.csv"):
        assert (out / name).is_file()
    audit = (built / "data_audit.md").read_text(encoding="utf-8")
    assert audit.startswith("# Data audit, part 1: the warehouse\n\nPart one text.\n")
    for heading in (
        "# Data audit, part 2: cohort, outcomes and landmarks",
        "## Cohort funnel",
        "## Landmark candidates",
        "## Outcomes by first-post year (cohort trials)",
        "## UNKNOWN: the registry's label and the derived rule",
        "## Person-period rows",
        "## Aalen-Johansen sanity table",
    ):
        assert heading in audit
    assert "—" not in audit
    first = json.loads((out / "build.json").read_text(encoding="utf-8"))
    assert first["identical_to_previous"] is None
    args = ["--warehouse", str(built / "warehouse.duckdb"), "--out-dir", str(out),
            "--audit", str(built / "data_audit.md")]  # fmt: skip
    assert build.main(args) == 0
    second = json.loads((out / "build.json").read_text(encoding="utf-8"))
    assert second["identical_to_previous"] is True
    assert second["tables"] == first["tables"]
    rebuilt = (built / "data_audit.md").read_text(encoding="utf-8")
    assert rebuilt.count("# Data audit, part 2") == 1
    assert rebuilt.startswith("# Data audit, part 1: the warehouse")


def test_the_aj_sanity_table_matches_the_estimator(built: Path) -> None:
    with duckdb.connect() as con:
        con.execute(
            f"""CREATE TEMP TABLE cohort_landmarks AS
            SELECT * FROM '{(built / "cohort" / "landmarks.parquet").as_posix()}'"""
        )
        rows = aj_sanity_table(con, (12, 24))
        l0 = con.execute(
            """SELECT landmark_date, event, event_date FROM cohort_landmarks
            WHERE landmark_index = 0 AND landmark_date < DATE '2018-01-01'"""
        ).fetchnumpy()
    pooled = rows[-1]
    assert pooled["stratum"] == "All"
    time = days_between(
        l0["landmark_date"].astype("datetime64[D]"), l0["event_date"].astype("datetime64[D]")
    )
    curve = aalen_johansen(time, np.asarray(l0["event"], dtype=np.int64))
    assert pooled["cif_12m"] == pytest.approx(float(curve.at(365.25)))
    assert pooled["trials"] == len(l0["event"])
    assert {r["stratum"] for r in rows} >= {"INDUSTRY", "MISSING"}
    with (built / "cohort" / "aj_sanity_by_sponsor_class.csv").open(encoding="utf-8") as handle:
        saved = list(csv.DictReader(handle))
    assert [r["stratum"] for r in saved] == [r["stratum"] for r in rows]


def test_an_old_warehouse_is_refused(tmp_path: Path) -> None:
    warehouse = _warehouse(tmp_path / "old.duckdb", schema_version="1")
    with pytest.raises(RefusedError, match="schema version 2"):
        build.main(["--warehouse", str(warehouse), "--out-dir", str(tmp_path / "out")])
    with pytest.raises(RefusedError, match="no warehouse"):
        build.main(["--warehouse", str(tmp_path / "missing.duckdb")])
