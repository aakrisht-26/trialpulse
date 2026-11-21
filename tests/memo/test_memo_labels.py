"""The table of plain-English labels and display values (ADR 0084; memo_v4, ADR 0085):
complete for the served features, deterministic, readable, and safe under the fabrication
check; and the template's driver paragraph built from it, exactly."""

from typing import Any

import pytest

from trialpulse.features import registry
from trialpulse.live.contract import MARKER_GROUPS
from trialpulse.memo import check as c
from trialpulse.memo import labels as lab
from trialpulse.memo import payload as p
from trialpulse.memo import template as t

# Stored values of each kind, as `explain.drivers.shown` writes them.
SAMPLES: dict[str, tuple[str, ...]] = {
    lab.COUNT: ("0", "7", "1730", "88,123", "12.5", "not given"),
    lab.ENROLLMENT: ("0", "10", "1730", "not given"),
    lab.MONTHS: ("0", "1", "36", "-2", "not given"),
    lab.MOVED: ("-33", "0", "1", "24", "not given"),
    lab.START: ("-44", "0", "3", "1", "not given"),
    lab.YEARS: ("1", "18", "65.5", "not given"),
    lab.YEAR: ("2019", "not given"),
    lab.PERCENT: ("0.0672", "0.241", "1.5", "0", "not given"),
    lab.RATE: ("0.241", "0", "1", "not given"),
    lab.STAGE: ("0", "1", "4", "6", "not given"),
    lab.RECENT: ("0", "3", "124", "not given"),
    lab.FLAG: ("yes", "no", "not given"),
}
ENROLLMENT_TYPES = ("ESTIMATED", "ACTUAL", None)


def values(name: str) -> tuple[str, ...]:
    entry = lab.FEATURES[name]
    if entry.kind == lab.CATEGORY:
        return (*entry.levels, "SOMETHING_NEW", "not given")
    return SAMPLES[entry.kind]


def displays(name: str) -> list[str]:
    """Every display of a feature over its sample values (and enrollment types)."""
    kinds = ENROLLMENT_TYPES if lab.FEATURES[name].kind == lab.ENROLLMENT else (None,)
    return [lab.feature_display(name, v, k) for v in values(name) for k in kinds]


def test_every_served_feature_has_an_entry() -> None:
    served = lab.served_features()
    assert len(served) == 54  # ADR 0026: every main feature but the text families
    assert set(served) == set(lab.FEATURES)
    assert all(registry.BY_NAME[n].family != registry.TEXT for n in served)


def test_every_field_of_a_markers_groups_has_an_entry() -> None:
    """memo_v3: a change since go-live shows every field its marker's groups cover."""
    assert set(lab.FIELDS) == {f for fields in MARKER_GROUPS.values() for f in fields}
    assert set(p.CHANGE_FIELDS) == set(lab.FIELDS)


def test_a_category_covers_the_registrys_levels() -> None:
    from trialpulse.contracts.versions import ENUMS
    from trialpulse.features.design import PHASE_LABELS

    for name, column in (("allocation", "allocation"), ("intervention_model",
                         "intervention_model"), ("primary_purpose", "primary_purpose"),
                         ("masking", "masking"), ("sex", "sex"),
                         ("sponsor_class", "lead_sponsor_class"),
                         ("organization_class", "organization_class")):  # fmt: skip
        assert set(ENUMS[column]) <= set(lab.FEATURES[name].levels), name
    for name in ("study_type", "primary_completion_date_type", "enrollment_type"):
        assert set(ENUMS[name]) <= set(lab.FIELDS[name].levels), name
    assert set(PHASE_LABELS) <= set(lab.PHASE_WORDS)


@pytest.mark.parametrize(
    ("name", "stored", "shown"),
    [
        ("enrolled_to_first_target_ratio", "0.0672", "6.7%"),
        ("enrollment_target_ratio", "1.5", "150.0%"),
        ("sponsor_stop_rate", "0.241", "24.1%"),  # bare without its inputs (memo_v5)
        ("completion_slip_months", "-33", "33 months earlier than in the first version"),
        ("start_slip_months", "1", "1 month later than in the first version"),
        ("primary_completion_slip_months", "0", "in the same month as in the first version"),
        ("registration_lag_months", "-44",
         "44 months after the first posting, any move of the start included"),
        ("registration_lag_months", "3", "3 months before the first posting (registered late), "
         "any move of the start included"),
        ("registration_lag_months", "0", "in the month of the first posting"),
        ("months_since_t0", "36", "36 months"),
        ("minimum_age_years", "18", "18 years"),
        ("maximum_age_years", "99", "99 years"),  # a value the sponsor entered, shown as such
        ("maximum_age_years", "not given", "no upper limit"),  # the registry's N/A (No limit)
        ("minimum_age_years", "not given", "no lower limit"),
        ("registration_year", "2019", "2019"),
        ("sponsor_prior_registrations", "1730", "1,730"),
        ("open_interventional_trials", "88,123", "88,123"),
        ("landmark_index", "4",
         "stage 4 (stages run 0 to 6, a stage every 6 months after the first posting)"),
        ("landmark_index", "0",
         "stage 0 (stages run 0 to 6, a stage every 6 months after the first posting)"),
        ("currently_suspended", "no", "not suspended now"),
        ("n_versions_recent", "124", "124 in the last 6 months"),
        ("currently_suspended", "yes", "suspended now"),
        ("ever_suspended", "yes", "recorded"),
        ("ever_suspended", "no", "not recorded"),
        ("healthy_volunteers", "no", "not allowed"),
        ("status", "ACTIVE_NOT_RECRUITING", "active and not recruiting"),
        ("masking", "DOUBLE", "2"),
        ("masking", "NONE", "none (open label)"),
        ("title_phase", "PHASE1_PHASE2", "phase 1/2"),
        ("title_phase", "NONE", "none"),
        ("sponsor_class", "OTHER", "other, mostly universities and hospitals"),
        ("allocation", "SOMETHING_NEW", "something new"),
        ("enrollment_count", "not given", "not stated in the record"),
        ("healthy_volunteers", "not given", "not stated in the record"),
        ("start_anticipated", "no", "this month or earlier"),
        ("enrollment_closed", "no", "not closed, so the count is a target"),
        ("primary_completion_reached", "no", "not shown as reached in the registry"),
        ("registered_after_start", "no", "not after the recorded start month"),
    ],
)  # fmt: skip
def test_a_value_is_shown_as_a_reader_would_say_it(name: str, stored: str, shown: str) -> None:
    assert lab.feature_display(name, stored) == shown


def test_the_enrollment_count_shows_its_type() -> None:
    """memo_v3: a count is a target while ESTIMATED and the number enrolled once ACTUAL."""
    assert lab.feature_display("enrollment_count", "10", "ESTIMATED") == "10, the estimated target"
    assert lab.feature_display("enrollment_count", "2", "ACTUAL") == "2, the actual number enrolled"
    assert lab.feature_display("enrollment_count", "1704", None) == "1,704"


def test_field_values_are_shown_in_words() -> None:
    assert lab.field_display("status", "SUSPENDED") == "suspended"
    assert lab.field_display("start_date", "2026-11-19") == "2026-11-19"
    assert lab.field_display("status_verified_date", "2026-10-01") == "2026-10"
    assert lab.field_display("primary_completion_date_type", "ESTIMATED") == "anticipated"
    assert lab.field_display("completion_date_precision", "month") == "month"
    assert lab.field_display("intervention_types", "BEHAVIORAL, DRUG") == "behavioral, drug"
    assert lab.field_display("enrollment_type", "ACTUAL") == "actual"
    assert lab.field_display("lead_sponsor_class", "none") == "not stated in the record"
    assert lab.field_display("status", "TERMINATED") == "terminated"  # a field may end so
    assert lab.field_display("completion_date", "2027-01-01", "month") == "2027-01"
    assert lab.field_display("primary_completion_date", "2027-01-01", "year") == "2027"
    assert lab.field_display("primary_completion_date", "2027-03-15", "day") == "2027-03-15"


@pytest.mark.parametrize("name", sorted(lab.FEATURES))
def test_the_display_is_deterministic(name: str) -> None:
    first = displays(name)
    assert first == displays(name)
    assert all(isinstance(s, str) and s for s in first)


@pytest.mark.parametrize("name", sorted(lab.FEATURES))
def test_a_label_holds_no_digit_no_apostrophe_and_no_refused_word(name: str) -> None:
    label = lab.feature_label(name)
    assert not any(ch.isdigit() for ch in label), label
    assert "'" not in label, label
    assert chr(0x2019) not in label, label
    assert not label.lower().startswith("the "), label  # the sentence adds its own "The"
    for pattern in (c.NUMBER_WORDS, c.COMPARISONS, c.BANNED, c.TITLES, c.LINKS, c.ELEVATED,
                    c.RISE, c.FALL, *c.OTHER_DATES):  # fmt: skip
        assert not pattern.search(label), (label, pattern.pattern)


@pytest.mark.parametrize("name", sorted(lab.FEATURES))
def test_a_display_holds_no_refused_word(name: str) -> None:
    for shown in displays(name):
        assert "'" not in shown, shown
        for pattern in (c.NUMBER_WORDS, c.COMPARISONS, c.BANNED, c.TITLES, c.LINKS,
                        c.ELEVATED, c.RISE, c.FALL, *c.OTHER_DATES):  # fmt: skip
            assert not pattern.search(shown), (shown, pattern.pattern)


def test_no_label_holds_a_comma() -> None:
    """The memo_v3 audit: "smoothed toward its class rate is 24.1%" read the class rate as
    24.1%, since a comma inside a label opens a clause the sentence never closes (memo_v4)."""
    for name, entry in [*lab.FEATURES.items(), *lab.FIELDS.items()]:
        assert "," not in entry.label, name


def test_the_sponsor_counts_say_what_each_counts() -> None:
    """features/sponsor.py: every count leaves the trial itself out (SponsorPass.answer); a
    registration is an interventional trial first posted under the sponsor, while ended and
    stopped trials include trials first posted under another sponsor, so a rate of its own
    beside no registration is not a contradiction (memo_v4). For a sponsor without a track
    record the rate is the class rate alone (features/transforms.py), which the display says,
    so its wording holds for every sponsor."""
    assert lab.feature_label("sponsor_prior_registrations") == (
        "number of other interventional trials first posted under the sponsor"
    )
    for name in ("sponsor_prior_ended", "sponsor_prior_stopped"):
        label = lab.feature_label(name)
        assert label.startswith("number of other trials listed under the sponsor"), name
        assert "first posted under another sponsor included" in label
    assert lab.feature_label("sponsor_stop_rate") == ("early-stop rate used for the sponsor")


QUALIFIER = "listed under the sponsor, trials first posted under another sponsor included"
RATE_FORMS = [  # exactly one of the two forms, from the stored inputs (memo_v5, words memo_v6)
    (lab.RateInputs(True, 1, "OTHER", 0.16522), "0.241",  # memo 2: 1 ended, 0 first posted
     f"24.1%, from 1 other ended trial {QUALIFIER}, smoothed toward 16.5%, the class rate for "
     "other sponsors (mostly universities and hospitals)"),
    (lab.RateInputs(True, 2075, "INDUSTRY", 0.15990), "0.221",  # memo 5: 2,075 against 1,732
     f"22.1%, from 2,075 other ended trials {QUALIFIER}, smoothed toward 16.0%, the class rate "
     "for industry sponsors"),
    (lab.RateInputs(True, 0, "NIH", 0.15010), "0.150",
     f"15.0%, from 0 other ended trials {QUALIFIER}, smoothed toward 15.0%, the class rate for "
     "the US national research institutes"),
    (lab.RateInputs(False, None, "INDUSTRY", 0.15990), "0.160",
     "16.0%, the class rate for industry sponsors, as the sponsor has no track record"),
    (lab.RateInputs(False, None, None, 0.16241), "0.162",
     "16.2%, the overall rate, as the sponsor has no track record"),
]  # fmt: skip

# Every class in the same sentence (memo_v6): "the class rate for <its sponsors>".
CLASS_SPONSORS = {
    "INDUSTRY": "industry sponsors", "NIH": "the US national research institutes",
    "FED": "US federal agencies", "OTHER_GOV": "other government sponsors",
    "NETWORK": "research networks", "INDIV": "individual sponsors",
    "OTHER": "other sponsors (mostly universities and hospitals)",
    "UNKNOWN": "sponsors of unknown class", "AMBIG": "sponsors of ambiguous class",
}  # fmt: skip


def test_every_class_is_named_in_the_rate_sentence() -> None:
    """The memo_v5 audit: "the other (mostly universities and hospitals) class rate" read as
    "the other class"; every class now reads "the class rate for <its sponsors>"."""
    assert set(CLASS_SPONSORS) == set(lab.CLASS_WORDS)
    for code, sponsors in CLASS_SPONSORS.items():
        for record in (True, False):
            inputs = lab.RateInputs(record, 3 if record else None, code, 0.16)
            shown = lab.feature_display("sponsor_stop_rate", "0.2", rate=inputs)
            assert f"16.0%, the class rate for {sponsors}" in shown, shown
            payload = one_driver("sponsor_stop_rate", "0.2", "raises", rate=inputs)
            result = c.check(t.memo(payload), payload, ["Acme Pharma Inc."])
            assert result.passed, (shown, result.details)


def test_the_ended_trials_carry_the_qualifier_of_their_own_label() -> None:
    """The memo_v5 audit (memos 2 and 5): "from 1 other ended trial of the sponsor" beside "0
    other interventional trials first posted under the sponsor" read as more ended trials than
    were posted; the rate now says what its ended count counts, as the count's label does."""
    assert "trials first posted under another sponsor included" in lab.feature_label(
        "sponsor_prior_ended"
    )
    one = lab.feature_display("sponsor_stop_rate", "0.241",
                              rate=lab.RateInputs(True, 1, "OTHER", 0.16522))  # fmt: skip
    assert f"from 1 other ended trial {QUALIFIER}," in one
    many = lab.feature_display("sponsor_stop_rate", "0.221",
                               rate=lab.RateInputs(True, 2075, "INDUSTRY", 0.15990))  # fmt: skip
    assert f"from 2,075 other ended trials {QUALIFIER}," in many


@pytest.mark.parametrize(("inputs", "value", "shown"), RATE_FORMS)
def test_the_rate_says_exactly_how_it_was_made(inputs: Any, value: str, shown: str) -> None:
    """The memo_v4 audit (memo 2): the display named both ways a rate can be made, so beside
    no registration 24.1% read as the class rate (16.5%)."""
    assert lab.feature_display("sponsor_stop_rate", value, rate=inputs) == shown


@pytest.mark.parametrize(("inputs", "value", "shown"), RATE_FORMS)
def test_both_rate_forms_pass_the_check(inputs: Any, value: str, shown: str) -> None:
    payload = one_driver("sponsor_stop_rate", value, "raises", rate=inputs)
    assert payload["drivers"]["12"][0]["display"] == shown
    paragraphs = t.memo(payload)
    assert (
        paragraphs[1]
        == f"The early-stop rate used for the sponsor is {shown}, which raises the risk."
    )
    result = c.check(paragraphs, payload, ["Acme Pharma Inc."])
    assert result.passed, result.details


def test_the_stage_is_never_shown_in_months() -> None:
    """The memo_v3 audit: "the 24-month checkpoint" beside the 24-month risk horizon."""
    for value in SAMPLES[lab.STAGE]:
        shown = lab.feature_display("landmark_index", value)
        assert "-month" not in shown
        assert "checkpoint" not in shown
    assert "checkpoint" not in lab.feature_label("landmark_index")


def test_a_count_label_is_a_number_so_it_takes_is() -> None:
    """The v2 review's grammar: plural count labels took "is" in some memos and "are" in
    others; every count is now "the number of ..." (memo_v3)."""
    for name, entry in lab.FEATURES.items():
        if entry.kind in (lab.COUNT, lab.RECENT):
            assert entry.label.startswith(("number of", "total number of")), name


def _phrases(other: str) -> list[str]:
    entry = lab.FEATURES[other]
    return [entry.label, *entry.levels.values(), *([entry.yes, entry.no] if entry.yes else [])]


def test_no_label_is_part_of_another_features_label_or_words() -> None:
    """So the check finds each driver's sentence by its label, and no other."""
    for name, entry in lab.FEATURES.items():
        for other in lab.FEATURES:
            if other == name:
                continue
            for phrase in _phrases(other):
                assert not c._has(entry.label, phrase), (entry.label, other, phrase)


def test_no_sentence_about_one_feature_names_another() -> None:
    """Every display of every kind, numbers included, in the sentence the prompt asks for:
    none holds another feature's label, so two drivers never trip the other-label rule."""
    sentences = {name: [f"The {lab.feature_label(name)} is {shown}, which raises the risk."
                        for shown in displays(name)] for name in lab.FEATURES}  # fmt: skip
    for name, texts in sentences.items():
        for other, entry in lab.FEATURES.items():
            if other != name:
                assert not any(c._has(entry.label, s) for s in texts), (name, other)


def one_driver(name: str, value: str, direction: str, kind: str | None = None,
               rate: Any = None) -> dict[str, Any]:  # fmt: skip
    driver = {"feature": name, "label": lab.feature_label(name), "value": value,
              "display": lab.feature_display(name, value, kind, rate),
              "direction": direction}  # fmt: skip
    block = {} if rate is None else {"sponsor_rate": {
        "has_record": rate.has_record,
        "other_ended": rate.other_ended if rate.other_ended is not None else "none",
        "class": rate.sponsor_class or "none",
        "class_rate_percent": f"{rate.class_rate * 100:.1f}"}}  # fmt: skip
    return {**block,
        "payload_version": p.PAYLOAD_VERSION, "nct_id": "NCT01234567", "score_date": "2026-11-16",
        "horizons_months": [12, 24], "risk_percent": {"12": 14, "24": 26},
        "chosen_as": [p.TOP], "top_n": 100, "drivers_per_horizon": 5,
        "status_verified_month": "none", "drivers": {"12": [driver], "24": []},
        "first_posted_on": "none", "stored_markers": True, "record_changes": [],
        "record_changes_total": 0, "since_go_live": [], "values_held_from": "2026-11-08",
    }  # fmt: skip


@pytest.mark.parametrize("name", sorted(lab.FEATURES))
def test_the_template_writes_every_value_exactly_and_passes_the_check(name: str) -> None:
    """memo_v4: for every feature, every value kind and both effects, the second paragraph is
    exactly "The <label> is <value>, which <effect>." and the whole memo passes the check."""
    kinds = ENROLLMENT_TYPES if lab.FEATURES[name].kind == lab.ENROLLMENT else (None,)
    for value in values(name):
        for kind in kinds:
            for direction in ("raises", "lowers"):
                payload = one_driver(name, value, direction, kind)
                shown = lab.feature_display(name, value, kind)
                sentence = f"The {lab.feature_label(name)} is {shown}, which {direction} the risk."
                paragraphs = t.memo(payload)
                assert paragraphs[1] == sentence
                result = c.check(paragraphs, payload, ["Acme Pharma Inc."])
                assert result.passed, (sentence, result.details)


def test_a_served_status_is_open() -> None:
    """ADR 0014: the status of a served trial is one of the open statuses, so the feature's
    words never hold a terminal one ("is terminated" would read as an outcome claim)."""
    assert set(lab.FEATURES["status"].levels) == {"NOT_YET_RECRUITING", "RECRUITING",
                                                  "ENROLLING_BY_INVITATION",
                                                  "ACTIVE_NOT_RECRUITING", "SUSPENDED"}  # fmt: skip


def test_flags_that_can_hold_together_do_not_contradict() -> None:
    """The memo_v3 review: an overdue start beside "already reached"."""
    overdue = lab.feature_display("start_overdue", "yes")
    anticipated = lab.feature_display("start_anticipated", "no")
    assert "reached" not in anticipated
    assert "started" not in anticipated
    assert overdue.startswith("overdue")
    assert anticipated == "this month or earlier"
