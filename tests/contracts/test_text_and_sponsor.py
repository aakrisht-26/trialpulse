"""Text normalization and sponsor identity (trialpulse.contracts)."""

import pytest

from trialpulse.contracts.sponsor import (
    has_identity,
    opens_with_personal_title,
    sponsor,
    sponsor_key,
)
from trialpulse.contracts.text import EMAIL_MARKER, is_html, normalize_text, scrub_emails


def test_html_and_markdown_forms_of_the_same_criteria_normalize_identically() -> None:
    dataset_html = (
        "<p>Inclusion Criteria:</p><ol><li>Age 18 years or older</li>"
        "<li>Life expectancy &gt;12 weeks and a platelet count &gt;= 100 x 10^9/L.\n"
        "Patients on trials are excluded.</li></ol>"
        "<p>Exclusion Criteria:</p><ul><li>Pregnancy &amp; nursing</li>"
        "<li><strong>Prior</strong> &#x27;treatment&#x27;</li></ul>"
    )
    api_markdown = (
        "Inclusion Criteria:\n\n1. Age 18 years or older\n"
        "2. Life expectancy \\>12 weeks and a platelet count \\>= 100 x 10\\^9/L. "
        "Patients on trials are excluded.\n\n"
        "Exclusion Criteria:\n\n* Pregnancy \\& nursing\n* Prior 'treatment'"
    )
    expected = (
        "Inclusion Criteria:\nAge 18 years or older\n"
        "Life expectancy >12 weeks and a platelet count >= 100 x 10^9/L. Patients on trials "
        "are excluded.\nExclusion Criteria:\nPregnancy & nursing\nPrior 'treatment'"
    )
    assert normalize_text(dataset_html) == expected
    assert normalize_text(api_markdown) == expected


def test_plain_text_soft_breaks_join_and_paragraphs_split() -> None:
    dataset = "Chronic pain is common.\nIt reduces quality of life.\r\n\r\nA second paragraph."
    api = "Chronic pain is common. It reduces quality of life.\n\nA second paragraph."
    assert normalize_text(dataset) == normalize_text(api)
    assert normalize_text(api) == (
        "Chronic pain is common. It reduces quality of life.\nA second paragraph."
    )


def test_markdown_lists_nested_items_and_lazy_continuations() -> None:
    text = "Criteria:\n\n* first item\ncontinued here\n  - nested item\n10) tenth item\n"
    assert normalize_text(text) == ("Criteria:\nfirst item continued here\nnested item\ntenth item")
    assert normalize_text("\\* not a list item") == "* not a list item"


def test_only_bullets_or_items_numbered_one_interrupt_a_paragraph() -> None:
    # CommonMark: "2)" after a paragraph line is paragraph text; API v2 shows it joined.
    dataset = "Aims: 1) measure uptake.\n2) The signal given by the graft."
    api = "Aims: 1) measure uptake. 2) The signal given by the graft."
    assert normalize_text(dataset) == normalize_text(api) == api
    # A bullet, or an ordered item numbered 1, does start a list after a paragraph.
    assert normalize_text("Aims:\n1. first\n2. second") == "Aims:\nfirst\nsecond"
    assert normalize_text("Aims:\n- first\n- second") == "Aims:\nfirst\nsecond"
    # Inside a list, any item number starts the next item.
    assert normalize_text("\n\n3. third\n4. fourth") == "third\nfourth"


def test_an_indented_paragraph_keeps_the_list_open() -> None:
    # API v2 indents a paragraph that belongs to a list item; the dataset's HTML has the next
    # number as a new list item.
    api = "1. Documented before transport\n\n   OR\n4. Tachycardia >110"
    dataset = "<ol><li>Documented before transport<p>OR</p></li><li>Tachycardia &gt;110</li></ol>"
    expected = "Documented before transport\nOR\nTachycardia >110"
    assert normalize_text(api) == normalize_text(dataset) == expected
    # A paragraph that is not indented ends the list, and "3." cannot interrupt it.
    assert normalize_text("1. first\n\nNote:\n3. more") == "first\nNote: 3. more"


def test_entities_are_unescaped_repeatedly_and_tags_detected() -> None:
    assert normalize_text("Sponsor&amp;#x27;s decision") == "Sponsor's decision"
    assert is_html("<p>x</p>")
    assert is_html("a<br/>b")
    assert not is_html("age <18 years")
    assert normalize_text("age <18 years or >65") == "age <18 years or >65"


def test_emails_are_scrubbed_everywhere() -> None:
    text, count = scrub_emails("write to a.b@site.org or c@d.co.uk")
    assert count == 2
    assert text == f"write to {EMAIL_MARKER} or {EMAIL_MARKER}"
    assert normalize_text("<p>Contact: pi&#64;hospital.org</p>") == f"Contact: {EMAIL_MARKER}"


@pytest.mark.parametrize("value", [None, "", "   ", "<p> </p>", "\n\n"])
def test_empty_text_is_missing(value: str | None) -> None:
    assert normalize_text(value) is None


def test_individual_sponsors_keep_no_name() -> None:
    for name, sponsor_class in (
        ("Jane Doe", "INDIV"),
        ("Jane Doe", "indiv"),
        ("John Smith, MD", "OTHER"),
        ("Maria Rossi PhD", "INDUSTRY"),
        (None, "INDIV"),
    ):
        stored = sponsor(name, sponsor_class)
        assert (stored.name, stored.key, stored.is_individual) == (None, None, True)


@pytest.mark.parametrize(
    "name",
    [
        "Dr. Jane Doe",
        "Dr Jane Doe",
        "DR. JANE DOE",
        "Dr.Jane Doe",
        "Prof. Jane Doe",
        "Professor Jane Doe",
        "Prof. Dr. Jane Doe",
        "Doctor Jane Doe",
        "Dra. Juana P\u00e9rez",
        "Drs. J. de Vries",
        "Mr. John Smith",
        "Mrs John Smith",
        "Ms. Jane Doe",
        "Miss Jane Doe",
        "Sir John Smith",
        "Dr. Jane O&#x27;Doe",  # entities are unescaped before the rule reads the name
    ],
)
def test_a_name_that_opens_with_a_personal_title_is_an_individual(name: str) -> None:
    """ADR 0022: such a sponsor is a person named by title, whatever its class. Nothing of
    the name is stored."""
    for sponsor_class in ("OTHER", "INDUSTRY", "NETWORK", None):
        stored = sponsor(name, sponsor_class)
        assert (stored.name, stored.key, stored.is_individual) == (None, None, True)
    assert opens_with_personal_title(sponsor_key(name))


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Dr. Example Memorial Hospital", "dr example memorial hospital"),
        ("Dr. Example's Laboratories Ltd.", "dr example s laboratories ltd"),
        ("Prof. Example Research Institute", "prof example research institute"),
        ("Sir Example Hospital &amp; Medical Centre", "sir example hospital medical centre"),
        ("Drexel University", "drexel university"),  # "dr" is not a word here
        ("Mrsa Diagnostics", "mrsa diagnostics"),
        ("Professional Therapies", "professional therapies"),
        ("Acme Pharma", "acme pharma"),
    ],
)
def test_an_organization_named_after_a_person_stays_an_organization(name: str, key: str) -> None:
    stored = sponsor(name, "OTHER")
    assert (stored.key, stored.is_individual) == (key, False)
    assert stored.name is not None
    assert not opens_with_personal_title(stored.key)
    assert has_identity(stored.key, stored.is_individual)


def test_no_stored_key_is_a_person_named_by_title() -> None:
    """Whatever name comes in, a key that `sponsor` stores never opens with a personal title
    without an organization word: the warehouse cannot hold one."""
    titles = ("Dr", "Dr.", "Dra.", "Drs", "Prof", "Prof.", "Professor", "Doctor", "Mr", "Mrs.",
              "Ms", "Miss", "Sir")  # fmt: skip
    rest = ("Jane Doe", "J. Doe, MD", "Doe", "Jane van der Doe PhD", "\u00c9tienne Exemple")
    for title in titles:
        for tail in rest:
            for name in (
                f"{title} {tail}",
                f"{title.upper()} {tail.upper()}",
                f"  {title}  {tail} ",
            ):
                stored = sponsor(name, "OTHER")
                assert stored.key is None, name
                assert stored.name is None
                assert stored.is_individual
    assert not opens_with_personal_title(None)
    assert not opens_with_personal_title("")


def test_organizations_keep_a_display_name_and_a_normalized_key() -> None:
    stored = sponsor("Acme Pharma &amp; Co., Inc.", "INDUSTRY")
    assert stored == type(stored)("Acme Pharma & Co., Inc.", "acme pharma co inc", False)
    # A degree title next to an organization word is still an organization.
    assert sponsor("Jane Doe MD Research Foundation", "OTHER").is_individual is False
    fullwidth = "\uff21\uff23\uff2d\uff25"  # ACME in fullwidth letters
    assert sponsor_key(f"{fullwidth}  Labs\u2014Europe") == "acme labs europe"
    assert sponsor(None, "OTHER") == type(stored)(None, None, False)
