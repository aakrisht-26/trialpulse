"""README.md against the documents it summarizes (Step 18).

Every decimal, and every number written with a thousands separator, in the README must be a
number of docs/results.md or the model card, matched as a whole number, as tests/test_cards.py
checks the cards. Code blocks are left out. Fixed phrases hold the rules of the Step 11 review
(H1, H3 and H4 stated as falsified) and of CLAUDE.md Section 2 and ADR 0008."""

import re

import pytest

from trialpulse.config import REPO_ROOT

README = REPO_ROOT / "README.md"
SOURCES = (REPO_ROOT / "docs" / "results.md", REPO_ROOT / "docs" / "model_card.md")
NOTICE = "Research demo. Not medical advice. Not for patient decision-making."
NUMBER = re.compile(r"(?<![\w.,-])\d{1,3}(?:,\d{3})+(?!\d|,\d)|(?<![\w.])\d+\.\d+(?!\d|\.\d)")
HYPE = ("revolutionary", "cutting-edge", "cutting edge", "state-of-the-art", "state of the art",
        "seamless", "powerful", "blazing", "game-changing", "world-class", "best-in-class",
        "unparalleled", "leverag", "supercharg", "robust")  # fmt: skip


def _text() -> str:
    return README.read_text(encoding="utf-8")


def _without_code(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def _missing(readme: str) -> list[str]:
    found = set(NUMBER.findall("\n".join(p.read_text(encoding="utf-8") for p in SOURCES)))
    return sorted({n for n in NUMBER.findall(_without_code(readme)) if n not in found})


def test_every_number_in_the_readme_is_in_the_results_or_the_model_card() -> None:
    assert NUMBER.findall(_without_code(_text())), "the README states no result"
    assert _missing(_text()) == []


def test_a_changed_or_shortened_number_is_caught() -> None:
    text = _text()
    assert "0.0300 (0.0289 to 0.0313)" in text
    assert _missing(text.replace("0.0300 (0.0289", "0.0316 (0.0289")) == ["0.0316"]
    assert "(-0.0098 to +0.0075)" in text
    assert _missing(text.replace("(-0.0098 to", "(-0.0099 to")) == ["0.0099"]


def _rows(name: str) -> list[list[str]]:
    text = (REPO_ROOT / "docs" / name).read_text(encoding="utf-8")
    return [[c.strip(" *") for c in line.strip().strip("|").split("|")]
            for line in text.splitlines() if line.startswith("| ")]  # fmt: skip


def test_the_headline_table_is_the_champions_row_of_the_results() -> None:
    """The README's table, rounded for reading, against the M4 rows of the model table of
    docs/results.md, which prints AUC to three decimals and lift to two."""
    readme = {r[0]: r[1:] for r in _rows("../README.md") if len(r) == 3}
    m4 = {r[1]: r for r in _rows("results.md") if len(r) == 9 and r[0] == "M4"}
    for column, origin in ((0, "2018"), (1, "2019")):
        _, _, _, auc_12, auc_24, brier_12, lift_12, slope_12, slope_24 = m4[origin]
        assert readme["AUC, 12 months (primary)"][column] == auc_12
        assert readme["AUC, 24 months"][column] == auc_24
        assert readme["Brier score, 12 months"][column] == brier_12
        assert readme["Lift at 10%, 12 months"][column] == lift_12
        assert readme["Calibration slope, 12 and 24 months"][column] == (
            f"{slope_12} and {slope_24}"
        )


def test_the_stated_hypothesis_results_are_the_registered_ones() -> None:
    text = _text()
    observed = {r[0]: r[4] for r in _rows("results.md") if len(r) == 6}
    for name in ("h2_slope_12", "h5_difference", "h1_delta_auc_12", "h3_difference_12"):
        assert observed[name] in text, (name, observed[name])


def test_h1_states_both_horizons_with_their_registered_numbers() -> None:
    text = _text()
    h1 = text.split("**H1, falsified.**")[1].split("\n")[0]
    rows = {r[0]: r[4] for r in _rows("results.md") if len(r) == 6}
    assert rows["h1_delta_auc_12"] == "-0.0027 (-0.0166 to +0.0114)"
    assert rows["h1_delta_auc_24"] == "-0.0013 (-0.0098 to +0.0075)"
    assert f"The 12-month tie held: M4 minus M1 was {rows['h1_delta_auc_12']}" in h1
    assert f"The 24-month lead did not: {rows['h1_delta_auc_24']}" in h1


def test_the_falsified_hypotheses_are_stated_as_falsified() -> None:
    text = _text()
    for name in ("H1", "H3", "H4"):
        assert f"**{name}, falsified.**" in text
    assert "no claim that it predicts the reason a trial stops" in text


@pytest.mark.parametrize(
    "link", ["docs/results.md", "docs/preregistration.md", "docs/model_card.md",
             "docs/data_card.md", "docs/progress.md"],
)  # fmt: skip
def test_the_readme_links_the_documents_behind_it(link: str) -> None:
    assert f"]({link})" in _text()


def test_every_relative_link_exists() -> None:
    for target in re.findall(r"\]\(([^)#:]+)\)", _text()):
        assert (REPO_ROOT / target).exists(), target


def test_the_notice_attribution_and_citation() -> None:
    text = _text()
    assert f"> {NOTICE}" in text.splitlines()[4]
    assert "**Source: [ClinicalTrials.gov](https://clinicaltrials.gov).**" in text  # ADR 0008
    assert "Data as of 2026-09-25" in text
    assert "TrialPulse normalizes the registry records" in text
    assert "brbk/clinical_trials_history" in text
    assert "CC-BY-NC-4.0" in text
    assert "@misc{clinicaltrials_history_2026" in text


def test_the_wording_rules() -> None:
    text = _text()
    assert chr(0x2014) not in text  # no em dash
    assert chr(0x2013) not in text  # no en dash
    assert "will fail" not in text.lower()
    assert "elevated early-stop risk" in text
    lowered = text.lower()
    assert [w for w in HYPE if w in lowered] == []


def test_no_live_link_before_a_deployment_exists() -> None:
    """Status says plainly that nothing is deployed yet; no Spaces or Streamlit app URL."""
    text = _text()
    assert "hf.space" not in text
    assert "streamlit.app" not in text
    assert "there is no public link yet" in text
