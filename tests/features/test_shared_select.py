"""The final SELECT shared by the offline build and the live path (ADR 0037).

The refactor that moved the SELECT into `feature_select_sql` must not change the offline
build: the feature table of the synthetic registry is held to the digest taken before the
change (with and without the text features). A DuckDB upgrade that changes how values print
would change the digest too; re-pin only after checking that the upgrade alone did it."""

import hashlib

import duckdb
import pytest

from trialpulse.cohort.rules import add_months
from trialpulse.features import design
from trialpulse.features.build import FEATURE_TABLE, compute_features
from trialpulse.features.states import expose_versions

from .conftest import CFG, RULES, load_interventions, load_keys, load_texts, load_versions, registry

DIGEST_BEFORE_THE_REFACTOR = {
    True: "d4ee20f9a03d9d0a121c12e12fb07c75441bc3b4e72bd3e65931689930c12c05",
    False: "2ba96f9519639cc9c5e938b5ca31a968451690d33696b65286f7b0dac9bba110",
}


@pytest.mark.parametrize("text", [True, False])
def test_the_offline_feature_table_is_unchanged_by_the_shared_select(text: bool) -> None:
    data = registry()
    t0s = {v["nct_id"]: v["study_first_post_date"] for v in data.versions}
    keys = [(n, k, add_months(t0, 6 * k)) for n, t0 in sorted(t0s.items()) for k in range(7)]
    with duckdb.connect() as con:
        load_versions(con, data.versions, "all_versions")
        load_texts(con, data.texts)
        load_interventions(con, data.interventions)
        load_keys(con, keys)
        expose_versions(con, "all_versions", RULES)
        compute_features(con, RULES, CFG, text=text)
        columns = con.execute(f"DESCRIBE {FEATURE_TABLE}").fetchall()
        rows = con.execute(
            f"SELECT * FROM {FEATURE_TABLE} ORDER BY trial_id, landmark_index"
        ).fetchall()
    digest = hashlib.sha256(repr(columns).encode() + repr(rows).encode()).hexdigest()
    assert digest == DIGEST_BEFORE_THE_REFACTOR[text]


TITLES = [
    None,
    "",
    "A Study of Compound 1",
    "Phase 2 Trial of Compound 2",
    "A Phase I/II Study",
    "Phase 1 and Phase 3 together",
    "Compound 5 (Phase 4)",
]


def test_the_title_phase_rule_has_one_meaning_in_python_and_sql() -> None:
    """The live path resolves the phase at ingest in Python; the offline build in SQL."""
    pairs = [(o, b) for o in TITLES for b in TITLES]
    with duckdb.connect() as con:
        for official, brief in pairs:
            op = design.title_phase(official) if official else None
            bp = design.title_phase(brief) if brief else None
            row = con.execute(
                f"SELECT {design.resolved_title_phase_sql('?::VARCHAR', '?::VARCHAR')}",
                [op, op, op, bp],
            ).fetchone()
            assert row is not None
            assert row[0] == design.resolved_title_phase(official, brief), (official, brief)
