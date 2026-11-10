"""Parity layer 5a's own logic (`live.parity_5a`): the sponsor identity, the numbering of the
live rows and the comparison. Synthetic rows only; the run itself reads Neon and the laptop's
warehouse."""

import datetime as dt
from pathlib import Path

import pandas as pd

from trialpulse.live import contract, parity_5a

SECRET = b"a test key, never a real one"


def test_a_keyed_id_maps_back_to_its_sponsor_key() -> None:
    by_id = parity_5a.sponsor_keys_by_id(["acme", "beta labs"], SECRET)
    assert by_id[contract.sponsor_id("acme", False, SECRET)] == "acme"
    assert by_id[contract.sponsor_id("beta labs", False, SECRET)] == "beta labs"


def test_live_rows_are_numbered_after_the_combined_versions_and_mapped() -> None:
    known = contract.sponsor_id("acme", False, SECRET)
    versions = pd.DataFrame([
        {"nct_id": "NCT2", "effective_date": dt.date(2026, 11, 5), "ingested_at": 2,
         "sponsor_id": "abc123", "feature_hash": b"\x01", "intervention_types": None},
        {"nct_id": "NCT1", "effective_date": dt.date(2026, 11, 9), "ingested_at": 3,
         "sponsor_id": known, "feature_hash": b"\x02", "intervention_types": ["DRUG"]},
        {"nct_id": "NCT1", "effective_date": dt.date(2026, 11, 4), "ingested_at": 1,
         "sponsor_id": None, "feature_hash": b"\x03", "intervention_types": ["DRUG", "DEVICE"]},
    ])  # fmt: skip
    rows = parity_5a.live_rows(versions, {"NCT1": 6}, {known: "acme"})
    by = {(r["nct_id"], r["nct_version"]): r for r in rows}
    assert by[("NCT1", 7)]["effective_date"] == dt.date(2026, 11, 4)  # post date order
    assert by[("NCT1", 8)]["sponsor_id"] == "acme"  # the id mapped back to its key
    assert by[("NCT1", 7)]["sponsor_id"] is None  # no identity stays none
    assert by[("NCT2", 0)]["sponsor_id"] == "abc123"  # a trial new since the seed; id kept
    assert by[("NCT1", 7)]["feature_hash"] == "03"
    assert by[("NCT2", 0)]["intervention_types"] == []


def _latest(**change: object) -> dict[str, object]:
    row: dict[str, object] = {
        "nct_id": "NCT1", "as_of_date": dt.date(2026, 11, 9), "landmark_index": 2,
        "true_landmark_index": 2, "flagged": False, "model_version": "m/1",
        "risk_12m": 0.1234, "risk_24m": 0.2345, "driver_months": [12, 24],
        "driver_features": [3, 4], "driver_values": ["1", "x"],
        "driver_contributions": [0.123, -0.045], "content_hash": b"\x01",
    }  # fmt: skip
    row.update(change)
    return row


def test_equal_rows_compare_equal_within_the_stored_precision() -> None:
    live = pd.DataFrame([_latest(driver_contributions=[0.12300000339746475, -0.04500000178813934])])
    offline = pd.DataFrame([_latest(), _latest(nct_id="NCT9")])
    result = parity_5a.compare(offline, live, parity_5a.COMPARED)
    assert result["on_both"] == 1
    assert result["live_only"] == 0
    assert all(c["different"] == 0 for c in result["columns"].values())
    assert result["content_hash_apart"] == {"equal": 1, "different": 0}


def test_every_difference_is_counted_by_column() -> None:
    live = pd.DataFrame([_latest(), _latest(nct_id="NCT2")])
    offline = pd.DataFrame([_latest(risk_12m=0.1235, driver_values=["2", "x"])])
    result = parity_5a.compare(offline, live, parity_5a.COMPARED)
    assert result["live_only"] == 1
    assert result["columns"]["risk_12m"]["different"] == 1
    assert result["columns"]["risk_12m"]["max_abs_difference"] > 0
    assert result["columns"]["driver_values"]["different"] == 1
    assert result["columns"]["risk_24m"]["different"] == 0
    full = {"scores": result, "unscored": parity_5a.compare(
        pd.DataFrame(columns=["nct_id", *parity_5a.UNSCORED_COMPARED]),
        pd.DataFrame(columns=["nct_id", *parity_5a.UNSCORED_COMPARED]),
        parity_5a.UNSCORED_COMPARED), "crossed": {"scored_live_unscored_offline": 1}}  # fmt: skip
    found = parity_5a.differences(full)
    assert "scores: 1 served on Neon only" in found
    assert "scores.risk_12m: 1 differ" in found
    assert "scored_live_unscored_offline: 1" in found


def test_a_contribution_beyond_the_stored_precision_differs() -> None:
    assert parity_5a._same([0.123], [0.1234], "driver_contributions")
    assert not parity_5a._same([0.123], [0.124], "driver_contributions")
    assert not parity_5a._same([0.123], [0.123, 0.1], "driver_contributions")


def test_the_retention_exports_join_neons_rows_once_each(tmp_path: Path) -> None:
    def row(nct: str, day: int, h: bytes) -> dict[str, object]:
        return {"nct_id": nct, "effective_date": dt.date(2026, 11, day), "feature_hash": h}

    neon = pd.DataFrame([row("NCT1", 9, b"\x01"), row("NCT2", 9, b"\x02")])
    assert parity_5a.with_exported(neon, tmp_path).equals(neon)  # no export: Neon's rows
    exported = pd.DataFrame([row("NCT1", 3, b"\x09"), row("NCT2", 9, b"\x02")])
    exported["run_id"] = 3  # a column 5a does not read
    exported.to_parquet(tmp_path / "live_study_versions_before_2026-11-05_run7.parquet")
    both = parity_5a.with_exported(neon, tmp_path)
    assert len(both) == 3  # the deleted version added, the one on both sides once
    assert list(both.columns) == list(neon.columns)


def test_risks_compare_within_the_reproduction_tolerance_and_no_further() -> None:
    # ADR 0038, amended 2026-11-10: the risks within 1e-12, every other column exact
    assert parity_5a.RISK_TOLERANCE == 1e-12
    assert parity_5a._same(0.25, 0.25 + 1.4e-17, "risk_12m")  # the measured platform bits
    assert parity_5a._same(0.25, 0.25 + 9e-13, "risk_24m")
    assert not parity_5a._same(0.25, 0.25 + 2e-12, "risk_12m")
    assert not parity_5a._same(0.25, 0.2501, "risk_24m")
    assert not parity_5a._same(2, 3, "landmark_index")  # exact columns stay exact
    live = pd.DataFrame([_latest(risk_12m=0.1234 + 5e-12)])
    result = parity_5a.compare(pd.DataFrame([_latest()]), live, parity_5a.COMPARED)
    assert result["columns"]["risk_12m"]["different"] == 1
