"""The drift reference and step (`monitor.drift`, ADR 0076): shares only in the reference, a
deterministic sample that reproduces them, the current table in the reference's kinds, and,
where Evidently is installed (the drift group), a shifted column found drifted."""

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trialpulse.monitor import drift


def test_a_numeric_column_is_kept_as_decile_shares_only() -> None:
    values = pd.Series(np.r_[np.arange(1000, dtype=float), [np.nan] * 250])
    column = drift.summarize(values)
    assert column["kind"] == "numeric"
    assert column["missing"] == pytest.approx(0.2)
    assert len(column["edges"]) == 101  # 100 percentile bins of a continuous column
    assert sum(column["shares"]) == pytest.approx(1.0)
    assert set(column) == {"kind", "missing", "edges", "shares"}  # no row, no value list


def test_a_discrete_column_keeps_each_values_share_exactly() -> None:
    column = drift.summarize(pd.Series([0.0, 1.0, 1.0, 3.0]))
    assert column["values"] == [0.0, 1.0, 3.0]
    assert column["shares"] == [0.25, 0.5, 0.25]
    sample = drift.sample(column)
    assert set(sample.dropna()) == {0.0, 1.0, 3.0}  # no value the column never held
    assert (sample == 1.0).sum() == 2500


def test_a_categorical_or_constant_column_is_summarized() -> None:
    cats = drift.summarize(pd.Series(["A", "B", "B", None], dtype=object))
    assert cats == {"kind": "categorical", "missing": 0.25,
                    "shares": {"A": pytest.approx(1 / 3), "B": pytest.approx(2 / 3)}}  # fmt: skip
    flags = drift.summarize(pd.Series([True, False, True]))
    assert flags["kind"] == "categorical"
    constant = drift.summarize(pd.Series([3.0, 3.0, 3.0]))
    assert constant["shares"] == [1.0]


def test_the_sample_reproduces_the_shares_without_randomness() -> None:
    values = pd.Series(np.r_[np.linspace(0, 100, 900), [np.nan] * 100])
    column = drift.summarize(values)
    first, second = drift.sample(column), drift.sample(column)
    assert first.equals(second)
    assert len(first) == drift.SAMPLE
    assert first.isna().mean() == pytest.approx(0.1, abs=1e-3)
    again = drift.summarize(first)
    assert again["shares"] == pytest.approx(column["shares"], abs=0.01)
    cats = drift.sample({"kind": "categorical", "missing": 0.0, "shares": {"A": 0.3, "B": 0.7}})
    assert (cats == "A").sum() == 1500


def test_the_current_table_takes_the_reference_columns_and_kinds() -> None:
    reference = {"columns": {
        "enrollment_count": {"kind": "numeric", "missing": 0, "edges": [0, 1], "shares": [1]},
        "sponsor_class": {"kind": "categorical", "missing": 0, "shares": {"OTHER": 1.0}},
        "absent_feature": {"kind": "numeric", "missing": 0, "edges": [0, 1], "shares": [1]},
        "risk_12m": {"kind": "numeric", "missing": 0, "edges": [0, 1], "shares": [1]},
    }}  # fmt: skip
    features = pd.DataFrame({"enrollment_count": [10, None], "sponsor_class": ["OTHER", None]})
    scores = pd.DataFrame({"risk_12m": [0.1, 0.2]})
    frame = drift.current_frame(features, scores, reference)
    assert list(frame.columns) == list(reference["columns"])
    assert frame["sponsor_class"].tolist() == ["OTHER", None]
    assert frame["absent_feature"].isna().all()
    assert frame["risk_12m"].tolist() == [0.1, 0.2]


def test_the_committed_reference_holds_shares_of_every_drift_column() -> None:
    reference = json.loads(drift.REFERENCE_PATH.read_text(encoding="utf-8"))
    assert set(reference["columns"]) == {*drift.drift_columns(), *drift.SCORES}
    assert len(reference["columns"]) == 55
    for column in reference["columns"].values():
        assert set(column) <= {"kind", "missing", "edges", "values", "shares"}
        if "edges" in column:
            assert len(column["edges"]) == len(column["shares"]) + 1


def test_a_step_that_cannot_run_reports_its_failure(tmp_path: Path) -> None:
    day = dt.date(2026, 11, 13)
    result = drift.drift_step("postgresql://nobody@localhost:1/none", day, None, pd.DataFrame(),
                              pd.DataFrame(), reference_path=tmp_path / "none")  # fmt: skip
    assert result["failed"] == "FileNotFoundError"


def test_evidently_finds_a_shifted_column_drifted(tmp_path: Path) -> None:
    pytest.importorskip("evidently")  # the drift group (CI runs this file there too)
    rng = np.random.default_rng(1)
    base = pd.DataFrame({"steady": rng.normal(size=3000), "moved": rng.normal(size=3000),
                         "kind": rng.choice(["A", "B"], 3000)})  # fmt: skip
    reference = {"columns": {c: drift.summarize(base[c]) for c in base}}
    current = base.assign(moved=base["moved"] + 2.0)
    kinds = {c: col["kind"] for c, col in reference["columns"].items()}
    html = tmp_path / "drift.html"
    rows = {r["column"]: r for r in drift.evidently_drift(
        drift.current_frame(current, pd.DataFrame(), reference),
        drift.reference_frame(reference), kinds, html)}  # fmt: skip
    assert rows["moved"]["drifted"] is True
    assert rows["steady"]["drifted"] is False
    assert rows["kind"]["drifted"] is False
    assert rows["moved"]["threshold"] == 0.1
    assert html.is_file()


def test_nullable_live_columns_become_the_references_kinds() -> None:
    """The live features use pandas' nullable types; a missing value there is pd.NA."""
    reference = {"columns": {
        "sex": {"kind": "categorical", "missing": 0, "shares": {"ALL": 1.0}},
        "n_versions": {"kind": "numeric", "missing": 0, "values": [1.0], "shares": [1.0]},
        "healthy_volunteers": {"kind": "categorical", "missing": 0, "shares": {"True": 1.0}},
    }}  # fmt: skip
    features = pd.DataFrame({
        "sex": pd.Series(["ALL", pd.NA], dtype="string"),
        "n_versions": pd.Series([1, pd.NA], dtype="Int64"),
        "healthy_volunteers": pd.Series([True, pd.NA], dtype="boolean"),
    })  # fmt: skip
    frame = drift.current_frame(features, pd.DataFrame(), reference)
    assert frame["sex"].tolist() == ["ALL", None]
    assert frame["healthy_volunteers"].tolist() == ["True", None]
    assert frame["n_versions"].iloc[0] == 1.0
    assert np.isnan(frame["n_versions"].iloc[1])
