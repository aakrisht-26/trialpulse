"""The registry, the whitelist and the future-perturbation test (CLAUDE.md Section 8, rules
2 to 5). The three mandatory leakage tests are here:

1. `test_features_at_a_landmark_ignore_every_later_version`: for 500 sampled landmarks,
   every version posted after the landmark is rewritten and the features must not change;
2. `test_features_read_only_whitelisted_fields` and its neighbors;
3. `test_the_sponsor_record_ignores_versions_posted_on_the_landmark_day` (with the
   hand-built `test_only_outcomes_posted_before_the_landmark_count` of test_sponsor.py).
"""

import datetime as dt

import duckdb
import numpy as np
import pytest

from trialpulse.cohort.outcomes import check_version_order
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import ProjectConfig
from trialpulse.contracts.versions import CANONICAL_COLUMNS
from trialpulse.features import registry as reg
from trialpulse.features.build import FEATURE_TABLE, compute_features
from trialpulse.features.leakage import (
    IDENTIFYING_FIELDS,
    REWRITTEN_FIELDS,
    REWRITTEN_INTERVENTIONS,
    REWRITTEN_VERSIONS,
    differing_rows,
    rewrite_after,
)
from trialpulse.features.states import expose_versions

from .conftest import VERSION_TYPES, World, restore_inputs

SPONSOR_COUNTS = ("sponsor_prior_registrations", "sponsor_prior_ended", "sponsor_prior_stopped")


def _recompute_after_rewrite(
    world: World, rules: CohortRules, cfg: ProjectConfig, day: dt.date, salt: int
) -> None:
    con = world.con
    rewrite_after(con, "all_versions", "base_interventions", day, rules, salt)
    expose_versions(con, REWRITTEN_VERSIONS, rules)
    con.execute(
        "CREATE OR REPLACE TEMP VIEW intervention_versions AS "
        f"SELECT * FROM {REWRITTEN_INTERVENTIONS}"
    )
    compute_features(con, rules, cfg)


def test_the_registry_is_consistent() -> None:
    reg.check_registry()
    names = [feature.name for feature in reg.FEATURES]
    assert len(names) == len(set(names))
    assert {feature.family for feature in reg.FEATURES} == set(reg.FAMILIES)
    assert not set(reg.ALLOWED_VERSION_FIELDS) & set(reg.FORBIDDEN_VERSION_FIELDS)
    # Only the sensitivity set may read a field without version history (ADR 0006).
    unversioned = [feature for feature in reg.FEATURES if not feature.versioned]
    assert [feature.name for feature in unversioned] == ["phase_current_record"]
    assert all(not feature.main and feature.family == reg.SENSITIVITY for feature in unversioned)
    assert "phase_current_record" not in reg.model_columns()
    assert set(reg.fitted_columns()) <= set(reg.model_columns())
    assert not set(reg.fitted_columns()) & set(reg.built_columns())


def test_the_whitelist_names_real_fields_and_leaves_out_the_label() -> None:
    """Rule 2 and rule 3. Both lists are about fields of the canonical version schema, and
    together they cover it, so a new field must be put on one side on purpose."""
    canonical = set(CANONICAL_COLUMNS)
    assert set(reg.ALLOWED_VERSION_FIELDS) | set(reg.FORBIDDEN_VERSION_FIELDS) == canonical
    for forbidden in ("why_stopped_hash", "effective_date_type", "lead_sponsor_name"):
        assert forbidden in reg.FORBIDDEN_VERSION_FIELDS
    allowed = {*reg.ALLOWED_VERSION_FIELDS, *reg.INTERVENTION_FIELDS}
    for feature in reg.FEATURES:
        if feature.versioned:
            assert set(feature.sources) <= allowed, feature.name
        else:
            assert set(feature.sources) <= set(reg.CURRENT_RECORD_FIELDS), feature.name
    assert set(VERSION_TYPES) == canonical  # the test registry has every field too


def test_every_whitelisted_field_is_the_source_of_a_feature() -> None:
    """A field is on the whitelist because a feature reads it, and that feature says so. The
    trial's identifier is the one exception: it joins versions, it is not read as a value."""
    read = {source for feature in reg.FEATURES if feature.versioned for source in feature.sources}
    assert set(reg.ALLOWED_VERSION_FIELDS) - read == {"nct_id"}


def test_the_view_shows_the_submitted_status_and_never_the_registrys_later_label(
    world: World,
) -> None:
    """The registry computes UNKNOWN long after the version it sits on was posted (ADR
    0014). The view the feature queries read shows the submitted status in its place."""
    con = world.con
    labeled = con.execute(
        "SELECT count(*) FROM all_versions "
        "WHERE overall_status = 'UNKNOWN' AND last_known_status IS NOT NULL"
    ).fetchone()
    assert labeled is not None
    assert labeled[0] > 5
    shown = con.execute(
        "SELECT count(*) FILTER (WHERE overall_status = 'UNKNOWN'), count(last_known_status), "
        "count(*) FROM versions"
    ).fetchone()
    assert shown is not None
    assert shown[:2] == (0, 0)
    assert shown[2] == len(world.registry.versions)
    same = con.execute(
        """SELECT count(*) FROM versions v JOIN all_versions a USING (nct_id, nct_version)
        WHERE v.overall_status = coalesce(a.last_known_status, a.overall_status)"""
    ).fetchone()
    assert same == (len(world.registry.versions),)


def test_features_read_only_whitelisted_fields(world: World) -> None:
    """The feature queries see a `versions` view that holds the whitelisted columns and no
    other, so a field outside the list cannot be read at all."""
    columns = [row[0] for row in world.con.execute("DESCRIBE versions").fetchall()]
    assert columns == list(reg.ALLOWED_VERSION_FIELDS)
    for forbidden in reg.FORBIDDEN_VERSION_FIELDS:
        with pytest.raises(duckdb.BinderException):
            world.con.execute(f"SELECT {forbidden} FROM versions LIMIT 1")
        assert world.con.execute(f"SELECT count({forbidden}) FROM all_versions").fetchone()


def test_every_computed_feature_is_registered_and_every_registered_one_is_computed(
    world: World,
) -> None:
    """Rule 4: a computed feature missing from the registry fails here, and so does a
    registered feature the build does not compute."""
    computed = [row[0] for row in world.con.execute("DESCRIBE baseline_features").fetchall()]
    assert set(computed) == {*reg.KEY_COLUMNS, *reg.built_columns()}
    assert len(computed) == len(set(computed))
    text = reg.features_markdown(dict.fromkeys(reg.built_columns(), 0.25), ["- a note"])
    for feature in reg.FEATURES:
        assert f"`{feature.name}`" in text
    assert "| 25.0% |" in text
    assert "—" not in text
    assert text.startswith("# Features\n\nResearch demo. Not medical advice.")


def test_features_at_a_landmark_ignore_every_later_version(
    world: World, rules: CohortRules, cfg: ProjectConfig
) -> None:
    """Rule 5, future perturbation. 500 landmark rows are sampled. For each of their dates L,
    every version posted after L is rewritten (other statuses, dates, sponsors, texts and
    interventions; a fifth dropped; one added per trial), everything is recomputed, and the
    features of every landmark row dated on or before L must be what they were."""
    con = world.con
    keys = con.execute(
        "SELECT trial_id, landmark_index, landmark_date FROM feature_keys ORDER BY 1, 2"
    ).fetchall()
    rng = np.random.default_rng(cfg.seeds.default)
    wanted = min(cfg.features.leakage_test_landmarks, len(keys))
    assert wanted == 500
    sampled = [keys[i] for i in rng.choice(len(keys), size=wanted, replace=False)]
    dates = sorted({day for _, _, day in sampled})
    assert len(dates) > 15
    checked = 0
    try:
        for number, day in enumerate(dates):
            _recompute_after_rewrite(world, rules, cfg, day, salt=number)
            rows, differing = differing_rows(con, "baseline_features", "baseline_text_keys", day)
            assert differing == 0, f"{differing} of {rows} rows on or before {day} changed"
            assert rows >= sum(1 for _, _, landmark in sampled if landmark <= day)
            checked += sum(1 for _, _, landmark in sampled if landmark == day)
    finally:
        restore_inputs(con)
    assert checked == wanted


def test_the_rewrite_reaches_every_field_a_feature_may_read(
    world: World, rules: CohortRules
) -> None:
    """A field the rewrite left alone could leak unnoticed. Every whitelisted field but the
    identifiers gets another value in the versions posted after the day, the post date
    included, and the versions stay in the order the cohort rules require."""
    changeable = set(reg.ALLOWED_VERSION_FIELDS) - set(IDENTIFYING_FIELDS)
    assert changeable <= set(REWRITTEN_FIELDS)
    con = world.con
    day = dt.date(2015, 7, 1)
    try:
        rewrite_after(con, "all_versions", "base_interventions", day, rules, salt=3)
        later = con.execute(
            "SELECT count(*) FROM all_versions WHERE effective_date > ?", [day]
        ).fetchone()
        assert later is not None
        assert later[0] > 300
        for field in sorted(changeable):
            changed = con.execute(
                f"""SELECT count(*) FROM all_versions a
                JOIN {REWRITTEN_VERSIONS} r USING (nct_id, nct_version)
                WHERE a.effective_date > ? AND a.{field} IS DISTINCT FROM r.{field}""",
                [day],
            ).fetchone()
            assert changed is not None
            assert changed[0] > 20, field
        moved = con.execute(
            f"""SELECT count(*) FILTER (WHERE r.effective_date <= ?),
              count(*) FILTER (WHERE r.effective_date < a.effective_date)
            FROM all_versions a JOIN {REWRITTEN_VERSIONS} r USING (nct_id, nct_version)
            WHERE a.effective_date > ?""",
            [day, day],
        ).fetchone()
        assert moved == (0, 0)  # later versions stay later than the day, and never move back
        expose_versions(con, REWRITTEN_VERSIONS, rules)
        check_version_order(con, rules)  # raises when post dates and version numbers disagree
    finally:
        restore_inputs(con)


def test_the_rewrite_changes_what_comes_after_and_nothing_before(
    world: World, rules: CohortRules, cfg: ProjectConfig
) -> None:
    """The check would notice a leak: with the registry rewritten after a day, most rows of
    later landmarks get other features, in every family. Up to the day one thing changes:
    a version the registry labels UNKNOWN shows its submitted status."""
    con = world.con
    day = dt.date(2015, 7, 1)
    before = con.execute(
        "SELECT count(*) FROM all_versions WHERE effective_date <= ?", [day]
    ).fetchone()
    status = "overall_status, last_known_status"
    try:
        _recompute_after_rewrite(world, rules, cfg, day, salt=99)
        kept = con.execute(
            f"""SELECT count(*) FROM (
              SELECT * EXCLUDE ({status}) FROM {REWRITTEN_VERSIONS} WHERE effective_date <= ?
              INTERSECT ALL
              SELECT * EXCLUDE ({status}) FROM all_versions WHERE effective_date <= ?)""",
            [day, day],
        ).fetchone()
        assert kept == before  # versions up to the day are untouched
        labels = con.execute(
            f"""SELECT count(*) FILTER (WHERE a.overall_status = 'UNKNOWN'),
              count(*) FILTER (WHERE r.overall_status = 'UNKNOWN'
                               OR r.last_known_status IS NOT NULL),
              count(*) FILTER (WHERE r.overall_status
                               IS DISTINCT FROM coalesce(a.last_known_status, a.overall_status))
            FROM all_versions a JOIN {REWRITTEN_VERSIONS} r USING (nct_id, nct_version)
            WHERE a.effective_date <= ?""",
            [day],
        ).fetchone()
        assert labels is not None
        assert labels[0] > 0  # the registry does label versions posted up to the day
        assert labels[1:] == (0, 0)  # and the rewrite shows their submitted status
        # Every later row is compared, and the check does report them.
        rows, differing = differing_rows(
            con, "baseline_features", "baseline_text_keys", dt.date(2030, 1, 1)
        )
        assert differing > 0.3 * rows > 100
        probes = ["masking", "status", "n_versions", "sponsor_class", "sponsor_prior_ended",
                  "open_interventional_trials", "eligibility_length", "n_interventions",
                  "title_phase", "enrollment_count"]  # fmt: skip
        later = dt.date(2017, 1, 1)
        for column in probes:
            row = con.execute(
                f"""SELECT count(*) FROM {FEATURE_TABLE} f
                JOIN baseline_features b USING (trial_id, landmark_index)
                WHERE f.landmark_date >= ? AND f.{column} IS DISTINCT FROM b.{column}""",
                [later],
            ).fetchone()
            assert row is not None
            assert row[0] > 20, column
    finally:
        restore_inputs(con)


def test_the_sponsor_record_ignores_versions_posted_on_the_landmark_day(
    world: World, rules: CohortRules, cfg: ProjectConfig
) -> None:
    """Section 8, leakage test 3: the track record uses only outcomes before L. Rewriting
    every version posted on L or later (after L minus one day) may change a row's state at
    L, which reads the versions of L itself, but not its sponsor's three counts."""
    con = world.con
    day = dt.date(2016, 1, 20)  # a first-post day of the registry, and a landmark of earlier ones
    rows = con.execute(
        "SELECT count(*) FROM baseline_features WHERE landmark_date = ? "
        "AND sponsor_prior_registrations IS NOT NULL",
        [day],
    ).fetchone()
    assert rows is not None
    assert rows[0] > 10
    posted_that_day = con.execute(
        "SELECT count(*) FROM all_versions WHERE effective_date = ?", [day]
    ).fetchone()
    assert posted_that_day is not None
    assert posted_that_day[0] > 5
    try:
        _recompute_after_rewrite(world, rules, cfg, day - dt.timedelta(days=1), salt=5)
        # Rows whose sponsor key at L is unchanged: the same sponsor must have the same record.
        comparison = con.execute(
            f"""SELECT count(*),
              count(*) FILTER (WHERE {
                " OR ".join(f"f.{c} IS DISTINCT FROM b.{c}" for c in SPONSOR_COUNTS)
            })
            FROM {FEATURE_TABLE} f JOIN baseline_features b USING (trial_id, landmark_index)
            JOIN baseline_at_landmark k USING (trial_id, landmark_index)
            JOIN feature_at_landmark n USING (trial_id, landmark_index)
            WHERE f.landmark_date = ? AND n.sponsor_key IS NOT DISTINCT FROM k.sponsor_key
              AND b.sponsor_prior_registrations IS NOT NULL""",
            [day],
        ).fetchone()
        assert comparison is not None
        assert comparison[0] > 10
        assert comparison[1] == 0
    finally:
        restore_inputs(con)
