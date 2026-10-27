"""The model card and the data card (Step 12) against the documents they summarize.

Every decimal, and every number written with a thousands separator, on a card must be a
number of the documents it summarizes (whole numbers matched, not text: 0.78 does not pass
for 0.782). Small whole numbers, which cell a number sits in and the order of a trend are not
checked by that test; the rules of the Step 11 review (H1, H3 and H4 stated as falsified, the
calibration trend and its monitoring) are checked by fixed phrases."""

import re
from pathlib import Path

import pytest

from trialpulse.config import REPO_ROOT

DOCS = REPO_ROOT / "docs"
NOTICE = "Research demo. Not medical advice. Not for patient decision-making."
# Decimals and numbers with thousands separators; dates, hashes and small counts are left out.
NUMBER = re.compile(r"(?<![\w.,-])\d{1,3}(?:,\d{3})+(?!\d|,\d)|(?<![\w.])\d+\.\d+(?!\d|\.\d)")
SOURCES = {
    "model_card.md": ("results.md", "explanations.md", "preregistration.md", "data_audit.md",
                      "scoring_checks.md"),
    "data_card.md": ("data_audit.md", "feasibility_report.md", "eda.md", "reason_labeling.md",
                     "results.md", "preregistration.md"),
}  # fmt: skip


def _text(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def _without_code(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


@pytest.mark.parametrize("card", sorted(SOURCES))
def test_every_number_on_a_card_appears_in_the_documents_it_summarizes(card: str) -> None:
    assert _missing(_text(card), SOURCES[card]) == []


def _missing(card: str, sources: tuple[str, ...]) -> list[str]:
    found = set(NUMBER.findall("\n".join(_text(name) for name in sources)))
    return sorted({n for n in NUMBER.findall(_without_code(card)) if n not in found})


def test_a_shortened_or_sentence_final_number_is_caught() -> None:
    card = _text("model_card.md")
    assert "+0.0060 to +0.0114" in card
    assert _missing(card.replace("+0.0060 to", "+0.006 to"), SOURCES["model_card.md"]) == ["0.006"]
    assert NUMBER.findall("lower by 0.644. Next") == ["0.644"]
    assert NUMBER.findall("rows 90,985, and 1,046.") == ["90,985", "1,046"]


@pytest.mark.parametrize("card", sorted(SOURCES))
def test_a_card_carries_the_notice_and_the_attribution(card: str) -> None:
    text = _text(card)
    assert text.splitlines()[2] == NOTICE
    source = "**Source: [ClinicalTrials.gov](https://clinicaltrials.gov).**"
    assert f"{source} Data as of 2026-09-25" in text
    assert "TrialPulse normalizes the registry records" in text  # ADR 0008
    assert chr(0x2014) not in text  # no em dash
    assert chr(0x2013) not in text  # no en dash
    for match in re.finditer("will fail", text):
        assert text[max(0, match.start() - 7) : match.start()] == 'never "', "says will fail"


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", " ".join(text.split()))


def test_the_model_card_states_the_falsified_hypotheses_as_falsified() -> None:
    text = _text("model_card.md")
    for name in ("H1", "H3", "H4"):
        assert f"**{name}, falsified.**" in text
    for sentence in _sentences(text):  # H3: no claim that operational stops are more predictable
        if "more predictable" in sentence:
            assert "no claim" in sentence or "falsified" in sentence, sentence
    assert "below the pre-registered range" in text  # H4, at 24 months


def test_the_model_card_states_the_calibration_trend_and_its_monitoring() -> None:
    text = _text("model_card.md")
    trend = text.split("**A trend whose cause is not established.**")[1].split("\n\n")[0]
    for value in ("0.855", "0.803", "0.664", "0.782", "0.732", "0.644"):
        assert value in trend
    assert "The cause is not established" in trend
    assert "Step 17 must track the calibration slope" in trend
    assert "post-test product change" in text  # ADR 0032


def test_the_cards_link_to_files_that_exist() -> None:
    for card in SOURCES:
        for target in re.findall(r"\]\(([^)#:]+)\)", _text(card)):
            assert (DOCS / target).resolve().exists() or Path(target).is_absolute(), target
