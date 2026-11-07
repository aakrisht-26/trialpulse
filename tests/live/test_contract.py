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


# The shapes the review of Steps 14 to 16 found shown under ADR 0044's rule, and the masked
# shapes of its measurement on real records ("Aaaa A. Aaaa" and others), with what ADR 0059
# shows. Synthetic names only.
PERSON_SHAPES = [
    ("Jane Example", "OTHER"),
    ("Jane A. Example", "OTHER"),  # a middle initial
    ("Example, Jane", "OTHER"),  # surname first
    ("Jane Example, MSc", "OTHER"),  # degrees outside ADR 0012's list
    ("Jane Example, PharmD", "OTHER"),
    ("Jane Example, MBBS", "OTHER"),
    ("Jane Example RN", "OTHER"),
    ("Maria del Carmen Example", "OTHER"),  # four words
    ("J. Example", "OTHER"),
    ("Jane Q Example-Sample Jr", "OTHER"),
    ("Jane Mary A. Example", "OTHER"),  # "Aaaa Aaaa A. Aaaa"
    ("Ann B. Example Jr.", "OTHER"),  # "Aaa A. Aaaa Aa."
    ("Jane A. Example, Sc.D.", "FED"),  # "Aaaa A. Aaaa, Aa.A." under a public class
    ("Jane Ex Ample A Sample Ex Ample Ex Ample Ex Ample Example Ex Ample", "OTHER"),
    ("Jane Example", "UNKNOWN"),
    ("Mary-Jane O'Example", "AMBIG"),
    ("Jane Example", "INDUSTRY"),  # a person under an industry class, leading one trial
    ("Jane Example", "NIH"),
    ("Jane Example", "NETWORK"),
    ("Jane Example", "OTHER_GOV"),
    ("Jane Example", None),
    ("Carey Example", "OTHER"),  # opens "care", which is a whole word only
    ("Charity Example", "OTHER"),
    ("Lorenzo Medici", "OTHER"),  # opens "medic", which is not a stem
    ("Jose Co", "OTHER"),  # "Co" without its dot
    ("Kim Sa", "OTHER"),
    ("Ab Example", "OTHER"),
]


@pytest.mark.parametrize(("name", "sponsor_class"), PERSON_SHAPES)
def test_a_name_that_may_be_a_person_is_hidden(name: str, sponsor_class: str | None) -> None:
    assert contract.display_name(name, sponsor_class, 1) is None


@pytest.mark.parametrize(
    "name",
    [
        "Example University", "Example Medical Center", "Example NHS Foundation Trust",
        "Acme Co.", "Acme S.A.", "Acme A/S", "Acme GmbH", "Acme Pharma Inc.", "Acme AG",
        "Universidad de Example", "Hospital Universitario Example", "Fundação Example",
        "Hôpital Example", "Assistance Publique - Hôpitaux de Paris", "Groupe Example",
        "Ospedale Example", "Azienda Ospedaliera Example", "IRCCS Example",
        "Example Universitätsklinikum", "Example Krankenhaus", "Stiftung Example",
        "Example Ziekenhuis", "Stichting Example", "Example Sjukhus", "Region Example",
        "Example Yliopisto", "Szpital Example", "Example Egyetem", "Example Hastanesi",
        "Example Eğitim ve Araştırma", "Example Study Group", "Example Trials Unit",  # noqa: RUF001
    ],
)  # fmt: skip
def test_a_name_with_an_organization_word_is_shown(name: str) -> None:
    assert contract.display_name(name, "OTHER", 1) == name


@pytest.mark.parametrize(
    ("name", "sponsor_class", "led", "shown"),
    [
        ("Kestrel", "OTHER", 1, True),  # a single word
        ("Kestrel", None, 0, True),
        ("Example-Sample", "OTHER", 1, False),  # two words once punctuation is a space
        ("Hoffmann Example", "INDUSTRY", 5, True),  # a large sponsor of a listed class
        ("Hoffmann Example", "INDUSTRY", 4, False),
        ("Jane Example", "NIH", 5, True),
        ("Jane Example", "FED", 5, True),
        ("Jane Example", "OTHER_GOV", 5, True),
        ("Jane Example", "NETWORK", 5, True),
        ("Jane Example", "OTHER", 50, False),  # OTHER is not a listed class
        ("Jane Example", "INDIV", 50, False),
        (None, "INDUSTRY", 50, False),
        ("", "INDUSTRY", 50, False),
    ],
)
def test_who_gets_a_display_name(
    name: str | None, sponsor_class: str | None, led: int, shown: bool
) -> None:
    assert (contract.display_name(name, sponsor_class, led) is not None) is shown


def test_interventions_count_distinct_pairs_on_exact_strings() -> None:
    pairs = [("Drug A", "DRUG"), ("Drug A", "DRUG"), ("drug a", "DRUG"), ("Device", "DEVICE"),
             ("Nameless", None), (None, "OTHER"), ("", "DEVICE")]  # fmt: skip
    assert contract.distinct_intervention_types(pairs) == ["DEVICE", "DRUG", "DRUG"]


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
