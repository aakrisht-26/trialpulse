"""Run control (ADR 0045) on hand-made rows: the NULL gate with its 1% share and quarantined
records, the overlap check, the full-run rule and the scope (review findings 3 and 5)."""

import datetime as dt

import pandas as pd

from trialpulse.live import runs

D = dt.date(2026, 10, 30)


def _row(nct: str, **fields: object) -> dict[str, object]:
    return {"nct_id": nct, "effective_date": D, "enrollment_count": 10, "masking": "NONE",
            **fields}  # fmt: skip


def test_the_gate_stops_above_its_share_and_not_at_it() -> None:
    stored = {(f"NCT{i:08d}", D): _row(f"NCT{i:08d}") for i in range(200)}
    records = [_row(f"NCT{i:08d}") for i in range(200)]
    assert runs.null_gate(records, stored, 0.01) == {}
    records[0] = _row("NCT00000000", enrollment_count=None)
    records[1] = _row("NCT00000001", enrollment_count=None)
    assert runs.null_gate(records, stored, 0.01) == {}  # 2 of 200 is 1%: not more
    records[2] = _row("NCT00000002", enrollment_count=None)
    assert runs.null_gate(records, stored, 0.01) == {"enrollment_count": 0.015}


def test_the_gate_ignores_records_that_match_no_stored_row() -> None:
    stored = {("NCT00000001", D): _row("NCT00000001")}
    records = [_row("NCT00000009", enrollment_count=None)]  # a new trial
    assert runs.null_gate(records, stored, 0.01) == {}


def test_quarantined_records_count_toward_the_gate() -> None:
    """A matched record that was quarantined lost every field its stored row had."""
    stored = {(f"NCT{i:08d}", D): _row(f"NCT{i:08d}") for i in range(100)}
    records = [_row(f"NCT{i:08d}") for i in range(98)]
    held = [("NCT00000098", D), ("NCT00000099", D), ("NCT00000500", D), (None, None)]
    lost = runs.null_gate(records, stored, 0.01, held)
    assert lost == {"effective_date": 0.02, "enrollment_count": 0.02, "masking": 0.02}
    full = [_row(f"NCT{i:08d}") for i in range(99)]
    assert runs.null_gate(full, stored, 0.01, [("NCT00000099", D)]) == {}  # 1 of 100: not more


def test_the_overlap_check_names_stored_rows_the_pull_did_not_return() -> None:
    since = D - dt.timedelta(days=3)
    stored = {"NCT1": D, "NCT2": D - dt.timedelta(days=1), "NCT3": since, "NCT4": since
              - dt.timedelta(days=1)}  # fmt: skip
    pulled = {"NCT1": D, "NCT2": D}  # NCT2 came back later: fine
    assert runs.missing_overlap(stored, pulled, since) == ["NCT3"]  # NCT4 is before the window
    assert runs.missing_overlap(stored, {"NCT1": D - dt.timedelta(days=2)}, since) == [
        "NCT1", "NCT2", "NCT3"]  # fmt: skip


def test_a_full_run_is_due_after_seven_days() -> None:
    assert runs.full_run_due(None, D, 7)
    assert not runs.full_run_due(D - dt.timedelta(days=6), D, 7)
    assert runs.full_run_due(D - dt.timedelta(days=7), D, 7)


def _people() -> pd.DataFrame:
    return pd.DataFrame(
        {"trial_id": ["NCT1", "NCT2", "NCT3"], "true_index": [1, 2, 9],
         "reason": [None, None, "band_not_validated"], "content_hash": ["a", "b", "c"]}
    )  # fmt: skip


def test_the_scope_rescores_a_changed_record_index_or_model() -> None:
    latest = {"NCT1": ("a", 1, "m/1"), "NCT2": ("b", 2, "m/1"), "NCT9": ("z", 0, "m/1")}
    not_scored = {"NCT3": ("c", 9, "band_not_validated")}
    same = runs.scope(_people(), latest, not_scored, "m/1", full=False)
    assert same.rescore == set()
    assert same.trials == {"NCT9"}  # left the population: its rows go
    assert runs.scope(_people(), latest, not_scored, "m/2", False).rescore == {"NCT1", "NCT2"}
    moved = {**latest, "NCT2": ("b", 1, "m/1")}  # a landmark passed
    assert runs.scope(_people(), moved, not_scored, "m/1", False).rescore == {"NCT2"}
    changed = {**latest, "NCT1": ("old", 1, "m/1")}
    assert runs.scope(_people(), changed, not_scored, "m/1", False).rescore == {"NCT1"}
    assert runs.scope(_people(), latest, not_scored, "m/1", True).rescore == {"NCT1", "NCT2"}
