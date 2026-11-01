"""The compact live row, its feature hash, the keyed sponsor ids and the display-name rule
(ADRs 0037, 0042 and 0044)."""

import datetime as dt
import hashlib
import hmac
from typing import Any

import pytest

from trialpulse.cohort.rules import CohortRules
from trialpulse.config import load_project_config
from trialpulse.live import contract

RULES = CohortRules.from_config(load_project_config(), cutoff=dt.date(2026, 9, 25))
SECRET = b"test secret, not the real key"


def _canonical(**fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "nct_id": "NCT00000001",
        "effective_date": dt.date(2026, 10, 1),
        "overall_status": "RECRUITING",
        "last_known_status": None,
        "study_type": "INTERVENTIONAL",
        "study_first_post_date": dt.date(2024, 1, 5),
        "enrollment_count": 120,
        "enrollment_type": "ESTIMATED",
        "lead_sponsor_class": "INDUSTRY",
        "sponsor_key": "acme pharma",
        "sponsor_is_individual": False,
        "lead_sponsor_name": "Acme Pharma",
        "brief_summary_hash": "a" * 64,
        "eligibility_criteria_hash": "b" * 64,
    }
    row.update(fields)
    return row


def _compact(
    canonical: dict[str, Any],
    official: str | None = "A Phase 2 Study",
    brief: str | None = None,
    types: list[str] | None = None,
) -> dict[str, Any]:
    return contract.compact_row(canonical, official, brief, types or ["DRUG"], RULES, SECRET)


def test_a_field_no_feature_reads_never_changes_the_hash() -> None:
    """ADR 0042 condition 4: summary, eligibility, display name, post-date type and the
    registry's UNKNOWN label (over the same submitted status) leave the record the same."""
    base = _compact(_canonical())
    for change in (
        {"brief_summary_hash": "c" * 64},
        {"eligibility_criteria_hash": "d" * 64},
        {"lead_sponsor_name": "ACME PHARMA, INC (renamed display)"},
        {"effective_date_type": "ESTIMATED", "submitted_date": dt.date(2026, 9, 29)},
        {"why_stopped_hash": "e" * 64},
        {"overall_status": "UNKNOWN", "last_known_status": "RECRUITING"},
    ):
        assert _compact(_canonical(**change))["feature_hash"] == base["feature_hash"], change
    # Titles only through the phase they state.
    assert _compact(_canonical(), "A PHASE 2 STUDY OF X")["feature_hash"] == base["feature_hash"]


def test_a_field_a_feature_reads_changes_the_hash() -> None:
    base = _compact(_canonical())["feature_hash"]
    for change in (
        {"overall_status": "ACTIVE_NOT_RECRUITING"},
        {"enrollment_count": 121},
        {"sponsor_key": "acme pharma global"},
        {"lead_sponsor_class": "OTHER"},
        {"start_date": dt.date(2026, 1, 1)},
        {"effective_date": dt.date(2026, 10, 2)},
    ):
        assert _compact(_canonical(**change))["feature_hash"] != base, change
    assert _compact(_canonical(), "A Phase 3 Study")["feature_hash"] != base
    assert _compact(_canonical(), types=["DRUG", "DRUG"])["feature_hash"] != base


def test_the_compact_row_has_the_submitted_status_and_the_resolved_phase() -> None:
    row = _compact(_canonical(overall_status="UNKNOWN", last_known_status="RECRUITING"),
                   official="A Study", brief="Phase 1 study")  # fmt: skip
    assert row["status"] == "RECRUITING"
    assert row["title_phase"] == "PHASE1"
    assert list(row) == ["nct_id", *contract.LIVE_FIELDS, "feature_hash"]
    bare = _compact(_canonical(overall_status="UNKNOWN"))
    assert bare["status"] == "UNKNOWN"  # a bare UNKNOWN keeps the label (ADR 0014)


def test_the_sponsor_id_is_keyed_and_only_for_a_sponsor_with_an_identity() -> None:
    expected = hmac.new(SECRET, b"acme pharma", hashlib.sha256).hexdigest()
    assert contract.sponsor_id("acme pharma", False, SECRET) == expected
    assert (
        contract.sponsor_id("acme pharma", False, SECRET)
        != hashlib.sha256(b"acme pharma").hexdigest()
    )
    assert contract.sponsor_id("acme pharma", False, b"another key") != expected
    for key, individual in (("acme pharma", True), (None, False), ("redacted", False),
                            ("dr someone example", False)):  # fmt: skip
        assert contract.sponsor_id(key, individual, SECRET) is None
    with pytest.raises(contract.MissingSecretError):
        contract.sponsor_id("acme pharma", False, b"")


@pytest.mark.parametrize(
    ("name", "sponsor_class", "shown"),
    [
        ("Jane Example", "OTHER", False),
        ("Jane Q Example", "UNKNOWN", False),
        ("Mary-Jane O'Example", "AMBIG", False),
        ("Jane Example", "INDUSTRY", True),  # industry sponsors stay searchable
        ("Jane Example", "NIH", True),
        ("Jane Example", "NETWORK", True),
        ("Jane Example", "OTHER_GOV", True),
        ("Example University", "OTHER", True),  # an organization word
        ("Example Medical Center", "OTHER", True),
        ("Kestrel", "OTHER", True),  # one word
        ("Four Words Name Here", "OTHER", True),
        ("Example 2 Trust", "OTHER", True),
        ("Acme Co.", "OTHER", True),  # not plain words
        ("Jane Example", None, False),
        (None, "OTHER", False),
    ],
)
def test_who_gets_a_display_name(name: str | None, sponsor_class: str | None, shown: bool) -> None:
    assert (contract.display_name(name, sponsor_class) is not None) is shown


def test_interventions_count_distinct_pairs_on_exact_strings() -> None:
    pairs = [("Drug A", "DRUG"), ("Drug A", "DRUG"), ("drug a", "DRUG"), ("Device", "DEVICE"),
             ("Nameless", None)]  # fmt: skip
    assert contract.distinct_intervention_types(pairs) == ["DEVICE", "DRUG", "DRUG"]
    assert contract.unknown_intervention_types(["DRUG", "NEW_KIND"]) == ["NEW_KIND"]


def test_a_marker_says_what_changed() -> None:
    before = _compact(_canonical())
    assert contract.marker_code(None, before) == 1
    assert contract.marker_code(before, before) == 0
    after = _compact(_canonical(overall_status="SUSPENDED", enrollment_count=50))
    assert contract.marker_code(before, after) == 1 | 4
    later = _compact(_canonical(sponsor_key="other pharma"), types=["DEVICE"])
    assert contract.marker_code(before, later) == 16 | 32


def test_every_live_column_reads_only_whitelisted_fields() -> None:
    """Review finding 8 (ADR 0038, layer 1): the feature whitelist covers the live state.
    Every compact field and every column of the wide row comes from fields the offline
    features may read, and none from a forbidden one (why stopped, names, post-date types)."""
    from trialpulse.features.registry import (
        ALLOWED_VERSION_FIELDS,
        FORBIDDEN_VERSION_FIELDS,
        INTERVENTION_FIELDS,
    )
    from trialpulse.live.state import FIRST_FIELDS
    from trialpulse.live.store import VERSION_COLUMNS, WIDE_COLUMNS

    allowed = set(ALLOWED_VERSION_FIELDS) | set(INTERVENTION_FIELDS)
    assert set(contract.SOURCES) == set(contract.LIVE_FIELDS)
    for name, sources in contract.SOURCES.items():
        assert sources, name
        assert set(sources) <= allowed, name
        assert not set(sources) & set(FORBIDDEN_VERSION_FIELDS), name
    assert set(FIRST_FIELDS.values()) <= allowed
    bookkeeping = {"nct_id", "feature_hash", "run_id"}
    history = {*FIRST_FIELDS, "n_versions", "ever_suspended", "recent_post_dates", "lapse_from",
               "reversal"}  # fmt: skip
    assert set(WIDE_COLUMNS) == set(contract.LIVE_FIELDS) | bookkeeping | history
    assert set(VERSION_COLUMNS) == set(contract.LIVE_FIELDS) | bookkeeping


def test_a_compact_row_holds_only_its_columns() -> None:
    """Fields outside the compact row (a name, a why-stopped text) never enter it."""
    row = _compact(_canonical(why_stopped_hash="f" * 64, submitted_date=dt.date(2026, 9, 1)))
    assert list(row) == ["nct_id", *contract.LIVE_FIELDS, "feature_hash"]
    assert "Acme Pharma" not in str(row.values())
