"""The table of plain-English labels and display values (ADR 0084, memo_v2): complete for the
served features, deterministic, and safe under the fabrication check."""

from typing import Any

import pytest

from trialpulse.features import registry
from trialpulse.memo import check as c
from trialpulse.memo import labels as lab
from trialpulse.memo import payload as p
from trialpulse.memo import template as t

# Stored values of each kind, as `explain.drivers.shown` writes them.
SAMPLES: dict[str, tuple[str, ...]] = {
    lab.COUNT: ("0", "7", "1730", "88,123", "12.5", "not given"),
    lab.MONTHS: ("0", "1", "36", "-2", "not given"),
    lab.SLIP: ("-33", "0", "1", "24", "not given"),
    lab.LAG: ("-24", "0", "5", "not given"),
    lab.YEARS: ("1", "18", "65.5", "not given"),
    lab.YEAR: ("2019", "not given"),
    lab.PERCENT: ("0.0672", "0.241", "1.5", "0", "not given"),
    lab.STEP: ("0", "3", "6", "not given"),
    lab.FLAG: ("yes", "no", "not given"),
}


def values(name: str) -> tuple[str, ...]:
    entry = lab.FEATURES[name]
    if entry.kind == lab.CATEGORY:
        return (*entry.levels, "SOMETHING_NEW", "not given")
    return SAMPLES[entry.kind]


def test_every_served_feature_has_an_entry() -> None:
    served = lab.served_features()
    assert len(served) == 54  # ADR 0026: every main feature but the text families
    assert set(served) == set(lab.FEATURES)
    assert all(registry.BY_NAME[n].family != registry.TEXT for n in served)


def test_every_shown_field_has_an_entry() -> None:
    assert set(lab.FIELDS) == set(p.CHANGE_FIELDS)


def test_a_category_covers_the_registrys_levels() -> None:
    from trialpulse.contracts.versions import ENUMS
    from trialpulse.features.design import PHASE_LABELS

    for name, column in (("allocation", "allocation"), ("intervention_model",
                         "intervention_model"), ("primary_purpose", "primary_purpose"),
                         ("masking", "masking"), ("sex", "sex"),
                         ("sponsor_class", "lead_sponsor_class"),
                         ("organization_class", "organization_class")):  # fmt: skip
        assert set(ENUMS[column]) <= set(lab.FEATURES[name].levels), name
    assert set(PHASE_LABELS) <= set(lab.PHASE_WORDS)


@pytest.mark.parametrize(
    ("name", "stored", "shown"),
    [
        ("enrolled_to_first_target_ratio", "0.0672", "6.7% of the first target"),
        ("enrollment_target_ratio", "1.5", "150.0% of the first target"),
        ("sponsor_stop_rate", "0.241", "24.1%"),
        ("completion_slip_months", "-33", "33 months earlier than in the first version"),
        ("start_slip_months", "1", "1 month later than in the first version"),
        ("primary_completion_slip_months", "0", "unchanged from the first version"),
        ("registration_lag_months", "-24", "registered 24 months before the start"),
        ("registration_lag_months", "3", "registered 3 months after the start"),
        ("months_since_t0", "36", "36 months"),
        ("minimum_age_years", "18", "18 years"),
        ("registration_year", "2019", "2019"),
        ("sponsor_prior_registrations", "1730", "1,730"),
        ("open_interventional_trials", "88,123", "88,123"),
        ("landmark_index", "3", "step 3"),
        ("currently_suspended", "yes", "suspended now"),
        ("healthy_volunteers", "no", "not accepted"),
        ("status", "ACTIVE_NOT_RECRUITING", "active and not recruiting"),
        ("masking", "DOUBLE", "masked for 2 roles"),
        ("title_phase", "PHASE1_PHASE2", "phase 1/2"),
        ("sponsor_class", "OTHER", "other, mostly universities and hospitals"),
        ("allocation", "SOMETHING_NEW", "something new"),
        ("enrollment_count", "not given", "not given"),
    ],
)
def test_a_value_is_shown_as_a_reader_would_say_it(name: str, stored: str, shown: str) -> None:
    assert lab.feature_display(name, stored) == shown


def test_field_values_are_shown_in_words() -> None:
    assert lab.field_display("status", "SUSPENDED") == "suspended"
    assert lab.field_display("start_date", "2026-11-19") == "2026-11-19"
    assert lab.field_display("intervention_types", "BEHAVIORAL, DRUG") == "behavioral, drug"
    assert lab.field_display("enrollment_type", "ACTUAL") == "actual"
    assert lab.field_display("lead_sponsor_class", "none") == "not given"


@pytest.mark.parametrize("name", sorted(lab.FEATURES))
def test_the_display_is_deterministic(name: str) -> None:
    first = [lab.feature_display(name, v) for v in values(name)]
    assert first == [lab.feature_display(name, v) for v in values(name)]
    assert all(isinstance(s, str) and s for s in first)


@pytest.mark.parametrize("name", sorted(lab.FEATURES))
def test_a_label_holds_no_digit_and_no_refused_word(name: str) -> None:
    label = lab.feature_label(name)
    assert not any(ch.isdigit() for ch in label), label
    for pattern in (c.NUMBER_WORDS, c.COMPARISONS, c.BANNED, c.TITLES, c.LINKS, c.ELEVATED,
                    c.RISE, c.FALL, *c.OTHER_DATES):  # fmt: skip
        assert not pattern.search(label), (label, pattern.pattern)


def _phrases(name: str) -> list[str]:
    entry = lab.FEATURES[name]
    return [entry.label, *entry.levels.values(), *([entry.yes, entry.no] if entry.yes else [])]


def test_no_label_is_part_of_another_features_label_or_display() -> None:
    """So the check finds each driver's sentence by its label, and no other."""
    for name, entry in lab.FEATURES.items():
        for other in lab.FEATURES:
            if other == name:
                continue
            for phrase in _phrases(other):
                assert not c._has(entry.label, phrase), (entry.label, other, phrase)


def one_driver(name: str, value: str, direction: str) -> dict[str, Any]:
    driver = {"feature": name, "label": lab.feature_label(name), "value": value,
              "display": lab.feature_display(name, value), "direction": direction}  # fmt: skip
    return {
        "payload_version": 2, "nct_id": "NCT01234567", "score_date": "2026-11-16",
        "horizons_months": [12, 24], "risk_percent": {"12": 14, "24": 26},
        "chosen_as": [p.TOP], "top_n": 100, "drivers_per_horizon": 5,
        "drivers": {"12": [driver], "24": []}, "record_changes": [], "record_changes_total": 0,
        "field_changes": [], "values_held_from": "2026-11-08",
    }  # fmt: skip


@pytest.mark.parametrize("name", sorted(lab.FEATURES))
def test_a_faithful_sentence_about_every_value_passes_the_check(name: str) -> None:
    """Every label and display value of the table, in the sentence the prompt asks for."""
    for value in values(name):
        for direction in ("raises", "lowers"):
            payload = one_driver(name, value, direction)
            d = payload["drivers"]["12"][0]
            sentence = f"The {d['label']} is {d['display']}, which {direction} the risk."
            result = c.check(t.memo(payload, [sentence]), payload, ["Acme Pharma Inc."])
            assert result.passed, (sentence, result.details)
