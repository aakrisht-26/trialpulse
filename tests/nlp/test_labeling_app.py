"""The Streamlit labeling app on a synthetic sample: serving order, blind test items,
one-click labeling, the assisted flag, and going back to revise."""

import csv
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from trialpulse.config import REPO_ROOT
from trialpulse.nlp.gold import GoldItem, read_labels, text_sha256, write_sample

APP = REPO_ROOT / "src" / "trialpulse" / "nlp" / "labeling_app.py"
SOURCE = "ctgov-api-v2 pulled 2026-09-23"
DEV = GoldItem("NCT1", text_sha256("a"), "TERMINATED", 2015, "Synthetic: too slow", "dev", SOURCE)
TEST_A = GoldItem(
    "NCT2", text_sha256("b"), "WITHDRAWN", 2020, "Synthetic: pandemic", "test", SOURCE
)
TEST_B = GoldItem("NCT3", text_sha256("c"), "TERMINATED", 2012, "Synthetic: harm", "test", SOURCE)


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    sample, labels, suggestions = (tmp_path / n for n in ("s.csv", "l.csv", "sug.csv"))
    write_sample([DEV, TEST_A, TEST_B], sample)  # dev listed first on purpose
    # The file also carries suggestions for the test items, which must never be shown.
    with suggestions.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["nct_id", "text_sha256", "suggestion"])
        for item, label in ((DEV, "accrual"), (TEST_A, "covid19"), (TEST_B, "safety")):
            writer.writerow([item.nct_id, item.text_sha256, label])
    monkeypatch.setenv("TRIALPULSE_GOLD_SAMPLE", str(sample))
    monkeypatch.setenv("TRIALPULSE_GOLD_LABELS", str(labels))
    monkeypatch.setenv("TRIALPULSE_GOLD_SUGGESTIONS", str(suggestions))
    return sample, labels


def _app() -> AppTest:
    return AppTest.from_file(str(APP), default_timeout=30).run()


def test_test_items_come_first_blind_then_dev_with_its_suggestion(
    paths: tuple[Path, Path],
) -> None:
    _, labels = paths
    at = _app()
    assert not at.exception
    assert "Not medical advice" in at.caption[0].value

    for expected in (TEST_A, TEST_B):  # all test items first, in sample order
        assert at.subheader[0].value == expected.nct_id
        assert len(at.info) == 0  # no suggestion is ever shown for a test item
        at.button(key="label-other").click().run()  # one click saves and moves on

    assert at.subheader[0].value == DEV.nct_id
    assert [i.value for i in at.info] == ["Suggestion: accrual"]
    at.button(key="label-business").click().run()  # confirm or change the suggestion

    saved = read_labels(labels)
    assert {k: (v["label"], v["assisted"]) for k, v in saved.items()} == {
        TEST_A.key: ("other", "false"),
        TEST_B.key: ("other", "false"),
        DEV.key: ("business", "true"),
    }
    assert "Every item is labeled." in at.success[0].value


def test_back_revises_an_earlier_text_and_returns(paths: tuple[Path, Path]) -> None:
    _, labels = paths
    at = _app()
    at.button(key="label-accrual").click().run()  # TEST_A
    assert at.subheader[0].value == TEST_B.nct_id

    at.button(key="back").click().run()
    assert at.subheader[0].value == TEST_A.nct_id
    assert "Saved label: accrual" in [c.value for c in at.caption]
    assert len(at.info) == 0  # still blind when revisiting a test item
    at.button(key="label-funding").click().run()  # revise, then back to the next unlabeled

    assert at.subheader[0].value == TEST_B.nct_id
    assert read_labels(labels)[TEST_A.key]["label"] == "funding"


def test_next_unlabeled_leaves_a_revisit(paths: tuple[Path, Path]) -> None:
    at = _app()
    at.button(key="label-accrual").click().run()
    at.button(key="back").click().run()
    assert at.subheader[0].value == TEST_A.nct_id
    at.button(key="next").click().run()
    assert at.subheader[0].value == TEST_B.nct_id


def test_app_explains_a_missing_sample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRIALPULSE_GOLD_SAMPLE", str(tmp_path / "missing.csv"))
    at = _app()
    assert "Build it first" in at.error[0].value
