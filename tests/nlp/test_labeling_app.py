"""The Streamlit review app on a synthetic sample: dev texts only (test texts are never
shown, ADR 0010), one-click labeling, the assisted flag, and going back to revise."""

import csv
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from trialpulse.config import REPO_ROOT
from trialpulse.nlp.gold import GoldItem, read_labels, text_sha256, write_sample

APP = REPO_ROOT / "src" / "trialpulse" / "nlp" / "labeling_app.py"
SOURCE = "ctgov-api-v2 pulled 2026-09-23"
DEV_A = GoldItem("NCT1", text_sha256("a"), "TERMINATED", 2015, "Synthetic: too slow", "dev", SOURCE)
DEV_B = GoldItem("NCT2", text_sha256("b"), "WITHDRAWN", 2020, "Synthetic: pandemic", "dev", SOURCE)
TEST = GoldItem("NCT0", text_sha256("c"), "TERMINATED", 2012, "Synthetic: harm", "test", SOURCE)


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    sample, labels, suggestions = (tmp_path / n for n in ("s.csv", "l.csv", "sug.csv"))
    write_sample([TEST, DEV_A, DEV_B], sample)  # the test item sorts first by key on purpose
    # The file also carries a suggestion for the test item, which must never be shown.
    with suggestions.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["nct_id", "text_sha256", "suggestion"])
        for item, label in ((DEV_A, "accrual"), (TEST, "safety")):
            writer.writerow([item.nct_id, item.text_sha256, label])
    monkeypatch.setenv("TRIALPULSE_GOLD_SAMPLE", str(sample))
    monkeypatch.setenv("TRIALPULSE_GOLD_LABELS", str(labels))
    monkeypatch.setenv("TRIALPULSE_GOLD_SUGGESTIONS", str(suggestions))
    return sample, labels


def _app() -> AppTest:
    return AppTest.from_file(str(APP), default_timeout=30).run()


def _shown(at: AppTest) -> list[str]:
    return [s.value for s in at.subheader] + [t.value for t in at.text_area]


def test_only_dev_texts_are_served_and_test_texts_never_shown(
    paths: tuple[Path, Path],
) -> None:
    _, labels = paths
    at = _app()
    assert not at.exception
    assert "Not medical advice" in at.caption[0].value
    assert "0 of 2 labeled" in at.get("progress")[0].proto.text

    assert at.subheader[0].value == DEV_A.nct_id
    assert [i.value for i in at.info] == ["Suggestion: accrual"]
    at.button(key="label-business").click().run()  # one click saves and moves on

    assert at.subheader[0].value == DEV_B.nct_id
    assert len(at.info) == 0  # no suggestion in the file for this dev item
    at.button(key="label-covid19").click().run()

    assert "Every item is labeled." in at.success[0].value
    saved = read_labels(labels)
    assert {k: (v["label"], v["assisted"], v["method"]) for k, v in saved.items()} == {
        DEV_A.key: ("business", "true", "manual"),
        DEV_B.key: ("covid19", "false", "manual"),
    }
    assert TEST.key not in saved


def test_back_reaches_only_dev_texts(paths: tuple[Path, Path]) -> None:
    _, labels = paths
    at = _app()
    at.button(key="label-accrual").click().run()  # DEV_A
    assert at.subheader[0].value == DEV_B.nct_id

    at.button(key="back").click().run()
    assert at.subheader[0].value == DEV_A.nct_id
    assert "Saved label: accrual" in [c.value for c in at.caption]
    assert at.button(key="back").disabled  # nothing before the first dev text
    assert all(TEST.nct_id not in s and "harm" not in s for s in _shown(at))
    at.button(key="label-funding").click().run()  # revise, then on to the next unlabeled

    assert at.subheader[0].value == DEV_B.nct_id
    assert read_labels(labels)[DEV_A.key]["label"] == "funding"


def test_next_unlabeled_leaves_a_revisit(paths: tuple[Path, Path]) -> None:
    at = _app()
    at.button(key="label-accrual").click().run()
    at.button(key="back").click().run()
    assert at.subheader[0].value == DEV_A.nct_id
    at.button(key="next").click().run()
    assert at.subheader[0].value == DEV_B.nct_id


def test_app_explains_a_missing_sample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRIALPULSE_GOLD_SAMPLE", str(tmp_path / "missing.csv"))
    at = _app()
    assert "Build it first" in at.error[0].value
