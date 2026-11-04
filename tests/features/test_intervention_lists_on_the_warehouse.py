"""The 13 intervention features read different lists on the two paths: the offline feature
build reads the interventions config (every row, its raw type, ADR 0020), and the live path
reads the warehouse's canonical `intervention_types` (one type per distinct name and type,
ADR 0067), which the bootstrap streams into the compact rows. The features agree only while
the two lists agree, so this test holds them equal for every version of the warehouse, read
through the feature build's own `prepare_sources`. A later dataset revision on which they
part would fail here after its rebuild. Slow, and skipped without the warehouse and the
interventions config in data/."""

import duckdb
import pytest

from trialpulse.cli import RefusedError
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import REPO_ROOT, load_project_config
from trialpulse.features.build import prepare_sources
from trialpulse.warehouse.build import interventions_glob

WAREHOUSE = REPO_ROOT / "data" / "warehouse.duckdb"


def _interventions() -> str | None:
    try:
        return interventions_glob(load_project_config())
    except RefusedError:
        return None


INTERVENTIONS = _interventions()
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not WAREHOUSE.is_file() or INTERVENTIONS is None,
        reason="needs the warehouse and the interventions config in data/",
    ),
]


def test_the_canonical_lists_equal_the_lists_the_feature_build_reads() -> None:
    assert INTERVENTIONS is not None
    rules = CohortRules.from_config(load_project_config())
    with duckdb.connect() as con:
        con.execute("SET memory_limit = '2GB'")
        prepare_sources(con, WAREHOUSE, INTERVENTIONS, rules)
        row = con.execute(
            """SELECT count(*),
              count(*) FILTER (WHERE v.intervention_types IS DISTINCT FROM
                coalesce(i.intervention_types, []::VARCHAR[])),
              count(*) FILTER (WHERE len(v.intervention_types) > 0)
            FROM wh.versions v LEFT JOIN intervention_versions i USING (nct_id, nct_version)"""
        ).fetchone()
    assert row is not None
    versions, differing, listed = row
    assert differing == 0
    # Not a vacuous pass: most versions list interventions (3,843,841 of 4,444,542 on the
    # pinned revision v2026.09.26).
    assert versions > 4_000_000
    assert listed > 3_500_000
