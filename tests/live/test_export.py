"""The export adapter (ADR 0037): the offline feature code on exported compact rows gives the
features it gives on the original canonical rows, at every landmark of the scripted registry.
Without the adapter, the joins on titles, interventions and sponsor keys would blank 14 of
the 54 served features."""

import duckdb
import pandas as pd

from trialpulse.cohort.rules import add_months
from trialpulse.features.build import FEATURE_TABLE, compute_features
from trialpulse.features.states import expose_versions
from trialpulse.live import export

from .conftest import (
    _FEATURES,
    CFG,
    RULES,
    compact_history_of,
    registry,
)

FEATURES = _FEATURES


def _features(load: object) -> pd.DataFrame:
    data = registry(150, 11)
    t0s = {v["nct_id"]: v["study_first_post_date"] for v in data.versions}
    keys = [(n, k, add_months(t0, 6 * k)) for n, t0 in sorted(t0s.items()) for k in range(7)]
    with duckdb.connect() as con:
        if load == "canonical":
            FEATURES.load_versions(con, data.versions, "all_versions")
            FEATURES.load_texts(con, data.texts)
            FEATURES.load_interventions(con, data.interventions)
            expose_versions(con, "all_versions", RULES)
        else:
            rows = compact_history_of(data.versions, data.texts, data.interventions)
            export.register_export(con, rows, RULES)
        FEATURES.load_keys(con, keys)
        compute_features(con, RULES, CFG, text=False)
        frame: pd.DataFrame = con.execute(
            f"SELECT * FROM {FEATURE_TABLE} ORDER BY trial_id, landmark_index"
        ).df()
    return frame


def test_the_offline_code_reads_exported_rows_as_it_reads_the_registry() -> None:
    original, exported = _features("canonical"), _features("export")
    assert len(original) > 700
    pd.testing.assert_frame_equal(original, exported, check_exact=True)


def test_every_phase_has_a_title_that_states_it() -> None:
    from trialpulse.features import design

    for phase in design.PHASE_LABELS:
        assert design.title_phase(export.PHASE_TITLES[phase]) == phase


def test_live_rows_continue_each_trials_version_numbers() -> None:
    rows = [
        {"nct_id": "NCT1", "nct_version": 0, "effective_date": 1},
        {"nct_id": "NCT1", "nct_version": 4, "effective_date": 2},
        {"nct_id": "NCT1", "nct_version": None, "effective_date": 5},
        {"nct_id": "NCT1", "nct_version": None, "effective_date": 3},
        {"nct_id": "NCT2", "nct_version": None, "effective_date": 3},
    ]
    numbered = {(r["nct_id"], r["effective_date"]): r["nct_version"] for r in export.numbered(rows)}
    assert numbered == {("NCT1", 1): 0, ("NCT1", 2): 4, ("NCT1", 3): 5, ("NCT1", 5): 6,
                        ("NCT2", 3): 0}  # fmt: skip
