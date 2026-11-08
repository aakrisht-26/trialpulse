"""The overlap check and the gap store (`gap.compare`) on two small synthetic warehouses
written here: every cause of a difference, the agreement and the store. No real row is used."""

import datetime as dt
from pathlib import Path
from typing import Any

import duckdb
import pytest

from trialpulse.cohort.rules import CohortRules
from trialpulse.config import load_project_config
from trialpulse.gap import compare
from trialpulse.warehouse import census

CUTOFF = dt.date(2026, 9, 25)
RULES = CohortRules.from_config(load_project_config())
COLUMNS = (
    "nct_id VARCHAR, nct_version BIGINT, effective_date DATE, overall_status VARCHAR, "
    "last_known_status VARCHAR, start_date DATE, enrollment_count BIGINT, "
    "lead_sponsor_name VARCHAR, sponsor_key VARCHAR, sponsor_is_individual BOOLEAN, "
    "intervention_types VARCHAR[], official_title_hash VARCHAR, brief_title_hash VARCHAR, "
    "brief_summary_hash VARCHAR, eligibility_criteria_hash VARCHAR, why_stopped_hash VARCHAR, "
    "content_hash VARCHAR"
)
RAW = (
    "nct_id VARCHAR, nct_version BIGINT, last_update_post_date DATE, overall_status VARCHAR, "
    "last_known_status VARCHAR, start_date DATE, enrollment_count BIGINT, "
    "lead_sponsor_class VARCHAR"
)
D = dt.date(2020, 1, 1)
LATE = dt.date(2026, 10, 2)
TEXTS = {
    "t-plain": "A Phase 2 Study of Aspirin",
    "t-case": "A PHASE 2 STUDY OF ASPIRIN",
    "t-phase3": "A Phase 3 Study of Aspirin",
}


def _version(nct: str, version: int, **change: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "nct_id": nct, "nct_version": version, "effective_date": D,
        "overall_status": "RECRUITING", "last_known_status": None, "start_date": D,
        "enrollment_count": 100, "lead_sponsor_name": "Acme", "sponsor_key": "acme",
        "sponsor_is_individual": False, "intervention_types": ["DRUG"],
        "official_title_hash": "t-plain", "brief_title_hash": None, "brief_summary_hash": None,
        "eligibility_criteria_hash": None, "why_stopped_hash": None, "content_hash": "h",
    }  # fmt: skip
    row.update(change)
    return row


def _raw(row: dict[str, Any], **change: Any) -> dict[str, Any]:
    raw = {
        "nct_id": row["nct_id"], "nct_version": row["nct_version"],
        "last_update_post_date": row["effective_date"], "overall_status": row["overall_status"],
        "last_known_status": row["last_known_status"], "start_date": row["start_date"],
        "enrollment_count": row["enrollment_count"], "lead_sponsor_class": "INDUSTRY",
    }  # fmt: skip
    raw.update(change)
    return raw


def _warehouse(path: Path, versions: list[dict[str, Any]], raws: list[dict[str, Any]]) -> None:
    with duckdb.connect(str(path)) as con:
        con.execute(f"CREATE TABLE versions ({COLUMNS})")
        con.execute(f"CREATE TABLE raw_versions ({RAW})")
        con.execute("CREATE TABLE texts (text_hash VARCHAR, text VARCHAR)")
        for table, rows in (("versions", versions), ("raw_versions", raws)):
            if rows:
                names = list(rows[0])
                marks = ", ".join("?" * len(names))
                con.executemany(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({marks})",
                                [[r[n] for n in names] for r in rows])  # fmt: skip
        con.executemany("INSERT INTO texts VALUES (?, ?)", sorted(TEXTS.items()))


def _interventions(path: Path, rows: list[tuple[str, int, str | None, str]]) -> str:
    path.mkdir(parents=True)
    with duckdb.connect() as con:
        con.execute(
            "CREATE TABLE iv (nct_id VARCHAR, nct_version BIGINT, intervention_name VARCHAR, "
            "intervention_type VARCHAR, description VARCHAR)"
        )
        con.executemany("INSERT INTO iv VALUES (?, ?, ?, ?, NULL)", rows)
        con.execute(f"COPY iv TO '{(path / 'part-0.parquet').as_posix()}' (FORMAT parquet)")
    return (path / "*.parquet").as_posix()


@pytest.fixture
def pair(tmp_path: Path) -> dict[str, Any]:
    same = _version("NCT1", 0)
    pinned = [
        same,
        _version("NCT1", 1, start_date=dt.date(2020, 2, 1)),  # the release changed it
        _version(
            "NCT2", 0, overall_status="UNKNOWN", last_known_status="RECRUITING", content_hash="u"
        ),  # the release lost the UNKNOWN label only
        _version("NCT3", 0),  # posted after the cutoff in the release
        _version("NCT4", 0),  # the title in another case: the same phase
        _version("NCT5", 0),  # the title states another phase
        _version("NCT6", 0, enrollment_count=100),  # a bug: same raw, another value
        _version("NCT9", 0),  # the release changed the interventions
    ]
    release = [
        same,
        _version("NCT1", 1, start_date=dt.date(2020, 3, 1), content_hash="x"),
        _version("NCT2", 0),
        _version("NCT3", 0, effective_date=LATE),
        _version("NCT4", 0, official_title_hash="t-case", content_hash="x"),
        _version("NCT5", 0, official_title_hash="t-phase3", content_hash="x"),
        _version("NCT6", 0, enrollment_count=120, content_hash="x"),
        _version("NCT7", 0),  # only in the release, posted by the cutoff
        _version("NCT8", 0, effective_date=LATE, official_title_hash="t-case"),  # a gap row
        _version("NCT9", 0, intervention_types=["DEVICE"], content_hash="x"),
    ]
    raws_p = [_raw(r) for r in pinned]
    raws_r = [_raw(r) for r in release]
    raws_r[6] = _raw(release[6], enrollment_count=100)  # NCT6: the raw value did not change
    _warehouse(tmp_path / "pinned.duckdb", pinned, raws_p)
    _warehouse(tmp_path / "release.duckdb", release, raws_r)
    pinned_iv = _interventions(tmp_path / "piv", [("NCT9", 0, "Aspirin", "DRUG")])
    release_iv = _interventions(tmp_path / "riv", [("NCT9", 0, "Stent", "DEVICE"),
                                                   ("NCT8", 0, "Aspirin", "DRUG")])  # fmt: skip
    con = duckdb.connect()
    con.execute(f"ATTACH '{(tmp_path / 'pinned.duckdb').as_posix()}' AS pinned (READ_ONLY)")
    con.execute(f"ATTACH '{(tmp_path / 'release.duckdb').as_posix()}' AS rel (READ_ONLY)")
    return {"con": con, "pinned_iv": pinned_iv, "release_iv": release_iv, "tmp": tmp_path}


def _overlap(pair: dict[str, Any]) -> dict[str, Any]:
    return compare.overlap(pair["con"], CUTOFF, RULES, pair["pinned_iv"], pair["release_iv"])


def test_keys_in_one_warehouse_only_are_counted_by_cause(pair: dict[str, Any]) -> None:
    found = _overlap(pair)
    assert found["keys_posted_by_the_cutoff"] == {"either": 9, "both": 7, "pinned": 8,
                                                  "release": 8}  # fmt: skip
    assert found["keys_in_one_only"] == {
        "pinned only: in the release, posted after the cutoff": 1,
        "release only: not in the pinned revision": 1,
    }


def test_every_difference_has_its_cause(pair: dict[str, Any]) -> None:
    causes = _overlap(pair)["causes"]
    assert causes["start_date"] == {compare.RELEASE_CHANGED: 1}
    assert causes["overall_status"] == {compare.STATUS_LABEL_ONLY: 1}
    assert causes["last_known_status"] == {compare.STATUS_LABEL_ONLY: 1}
    assert causes["enrollment_count"] == {compare.BUG: 1}
    assert causes["intervention_types"] == {"the release changed the interventions": 1}
    assert causes["official_title_hash"] == {
        f"{compare.TITLE_SAME_PHASE}: case only": 1,
        compare.TITLE_PHASE_MOVED: 1,
    }


def test_agreement_reads_the_feature_columns_as_the_features_read_them(
    pair: dict[str, Any],
) -> None:
    """NCT1 v0, NCT2 (the UNKNOWN label only) and NCT4 (the same title phase) agree; NCT1 v1,
    NCT5, NCT6 and NCT9 do not; the two keys in one warehouse only count against."""
    found = _overlap(pair)
    assert found["feature_columns_agree"] == 3
    assert found["agreement_on_feature_columns"] == pytest.approx(3 / 9)
    assert found["content_hash_equal"] == 1
    assert found["bugs_or_failures"] == 1
    assert found["trials_with_a_feature_difference"] == 4


def test_the_store_holds_the_rows_posted_after_the_cutoff_only(pair: dict[str, Any]) -> None:
    _overlap(pair)
    out = pair["tmp"] / "store.duckdb"
    counts = compare.write_store(pair["con"], CUTOFF, pair["release_iv"], out)
    assert counts == {"versions": 2, "texts": 2, "interventions": 1, "trials": 2}
    with duckdb.connect(str(out), read_only=True) as con:
        keys = con.execute("SELECT nct_id, effective_date FROM versions ORDER BY 1").fetchall()
        info = dict(con.execute("SELECT key, value FROM info").fetchall())
    assert keys == [("NCT3", LATE), ("NCT8", LATE)]
    assert info["cutoff"] == CUTOFF.isoformat()


def test_the_store_refuses_on_a_runner(
    pair: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trialpulse.cli import RefusedError

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(RefusedError, match="ADR 0040"):
        compare.write_store(pair["con"], CUTOFF, pair["release_iv"], pair["tmp"] / "s.duckdb")


# ADR 0075: sponsor differences by cause -------------------------------------------------------


def _core(path: Path, rows: list[tuple[str, int, str | None, str]]) -> str:
    path.mkdir(parents=True)
    with duckdb.connect() as con:
        con.execute("CREATE TABLE c (nct_id VARCHAR, nct_version BIGINT, "
                    "lead_sponsor_name VARCHAR, lead_sponsor_class VARCHAR)")  # fmt: skip
        con.executemany("INSERT INTO c VALUES (?, ?, ?, ?)", rows)
        con.execute(f"COPY c TO '{(path / 'part-0.parquet').as_posix()}' (FORMAT parquet)")
    return (path / "*.parquet").as_posix()


@pytest.mark.parametrize(
    ("names", "classes", "cause"),
    [
        (("Acme", "Acme Holdings"), ("INDUSTRY", "INDUSTRY"), f"{compare.RENAME}: another name"),
        (("ACME, Inc", "Acme Inc"), ("INDUSTRY", "INDUSTRY"),
         f"{compare.RENAME}: case or punctuation"),
        (("Acme &amp; Co", "Acme & Co"), ("INDUSTRY", "INDUSTRY"), compare.BUG),
        (("Acme", "Acme"), ("INDIV", "INDUSTRY"), compare.CLASS_CHANGED),
        (("Acme", None), ("INDUSTRY", "INDUSTRY"), compare.NOT_RENAME_MISSING),
        (("Café", "Café"), ("OTHER", "OTHER"), compare.NOT_RENAME_UNICODE),
    ],
)  # fmt: skip
def test_a_sponsor_difference_is_a_rename_only_when_the_raw_name_changed(
    names: tuple[str | None, str | None], classes: tuple[str, str], cause: str
) -> None:
    assert compare.classify_sponsor(*names, *classes) == cause


def test_a_class_change_under_the_same_name_is_the_census_cause() -> None:
    # ADR 0075 as amended on 2026-11-08: the overlap check and the census (ADR 0042) name
    # the same registry change by the same cause, and neither counts it as unexplained
    assert compare.CLASS_CHANGED == "registry: the class changed under the same name"
    assert compare.CLASS_CHANGED.startswith(census.REGISTRY)
    assert compare.CLASS_CHANGED not in census.FAILING


def test_a_version_without_a_raw_name_is_unexplained() -> None:
    assert compare.classify_sponsor(None, None, None, None, found=False) == compare.NO_RAW_SPONSOR


@pytest.fixture
def sponsors(tmp_path: Path) -> dict[str, Any]:
    pinned = [
        _version("NCT20", 0),  # renamed in the release, with a gap row
        _version("NCT21", 0, sponsor_key="beta", lead_sponsor_name="Beta"),  # a bug
        _version("NCT22", 0),  # the same
        _version("NCT23", 0),  # renamed, no gap row
        # an individual in the pinned revision (no name, no key), a company in the release
        _version("NCT24", 0, sponsor_key=None, sponsor_is_individual=True,
                 lead_sponsor_name=None),
    ]  # fmt: skip
    release = [
        _version("NCT20", 0, sponsor_key="acmeholdings", lead_sponsor_name="Acme Holdings"),
        _version(
            "NCT20",
            1,
            effective_date=LATE,
            sponsor_key="acmeholdings",
            lead_sponsor_name="Acme Holdings",
        ),
        _version("NCT21", 0, sponsor_key="beta2", lead_sponsor_name="Beta"),
        _version("NCT22", 0),
        _version("NCT23", 0, sponsor_key="acmeholdings", lead_sponsor_name="Acme Holdings"),
        _version("NCT24", 0, sponsor_key="delta", lead_sponsor_name="Delta"),
        _version("NCT24", 1, effective_date=LATE, sponsor_key="delta", lead_sponsor_name="Delta"),
    ]
    pinned_raws = [_raw(r, lead_sponsor_class="INDIV" if r["nct_id"] == "NCT24" else "INDUSTRY")
                   for r in pinned]  # fmt: skip
    _warehouse(tmp_path / "pinned.duckdb", pinned, pinned_raws)
    _warehouse(tmp_path / "release.duckdb", release, [_raw(r) for r in release])
    holdings, ind = "Acme Holdings", "INDUSTRY"
    pinned_core = _core(tmp_path / "pcore", [("NCT20", 0, "Acme", ind), ("NCT21", 0, "Beta", ind),
                                             ("NCT22", 0, "Acme", ind),
                                             ("NCT23", 0, "Acme", ind),
                                             ("NCT24", 0, "Delta", "INDIV")])  # fmt: skip
    release_core = _core(tmp_path / "rcore", [("NCT20", 0, holdings, ind),
                                              ("NCT20", 1, holdings, ind),
                                              ("NCT21", 0, "Beta", ind), ("NCT22", 0, "Acme", ind),
                                              ("NCT23", 0, holdings, ind),
                                              ("NCT24", 0, "Delta", ind),
                                              ("NCT24", 1, "Delta", ind)])  # fmt: skip
    iv = _interventions(tmp_path / "iv", [("NCT22", 0, "Aspirin", "DRUG")])
    con = duckdb.connect()
    con.execute(f"ATTACH '{(tmp_path / 'pinned.duckdb').as_posix()}' AS pinned (READ_ONLY)")
    con.execute(f"ATTACH '{(tmp_path / 'release.duckdb').as_posix()}' AS rel (READ_ONLY)")
    found = compare.overlap(con, CUTOFF, RULES, iv, iv)
    result = compare.sponsor_renames(con, CUTOFF, pinned_core, release_core)
    return {"found": found, "sponsors": result}


def test_sponsor_differences_are_counted_by_cause(sponsors: dict[str, Any]) -> None:
    result = sponsors["sponsors"]
    assert result["versions_judged"] == 4
    assert result["causes"] == {
        f"{compare.RENAME}: another name": 2,
        compare.CLASS_CHANGED: 1,
        compare.BUG: 1,
    }
    assert result["unexplained"] == 1  # the bug only; the class change is explained
    assert result["renamed_trials"] == 2
    assert result["class_changed_trials"] == 1
    assert result["registry_changed_trials"] == 3
    assert result["release_trials"] == 5
    assert result["registry_changed_share"] == pytest.approx(3 / 5)
    assert result["renamed_trials_with_gap_rows"] == 1
    assert result["renamed_trials_whose_first_gap_version_shows_a_new_sponsor"] == 1
    assert result["class_changed_trials_with_gap_rows"] == 1
    assert result["class_changed_trials_whose_first_gap_version_shows_a_new_sponsor"] == 1


def test_the_other_feature_columns_leave_out_the_sponsor(sponsors: dict[str, Any]) -> None:
    found = sponsors["found"]
    assert found["agreement_on_feature_columns"] == pytest.approx(1 / 5)
    assert found["agreement_on_other_feature_columns"] == pytest.approx(1.0)


def test_the_verdict_needs_all_three_conditions(sponsors: dict[str, Any]) -> None:
    found, result = sponsors["found"], dict(sponsors["sponsors"])
    failed = compare.verdict(found, result)
    assert failed["conditions"]["every_sponsor_difference_a_registry_change"] is False
    assert failed["conditions"]["registry_changed_trials_at_most"] is False  # 3 of 5 trials
    assert failed["passes"] is False
    assert found["bugs_or_failures"] == 1  # the same raw name under another key
    clean = {**found, "bugs_or_failures": 0}
    explained = {**result, "unexplained": 0, "registry_changed_share": 0.0}
    passing = compare.verdict(clean, {**explained, "registry_changed_share": 0.02})
    assert passing["passes"] is True
    assert compare.verdict(found, explained)["passes"] is False
    worse = {**clean, "agreement_on_other_feature_columns": 0.9989}
    assert compare.verdict(worse, explained)["passes"] is False
