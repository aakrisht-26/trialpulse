"""Smoke test of the Streamlit labeling app on a synthetic sample."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from trialpulse.config import REPO_ROOT
from trialpulse.nlp.gold import GoldItem, read_labels, text_sha256, write_sample

APP = REPO_ROOT / "src" / "trialpulse" / "nlp" / "labeling_app.py"
SOURCE = "ctgov-api-v2 pulled 2026-09-23"
FIRST = GoldItem("NCT1", text_sha256("a"), "TERMINATED", 2015, "Synthetic: too slow", "dev", SOURCE)
SECOND = GoldItem(
    "NCT2", text_sha256("b"), "WITHDRAWN", 2020, "Synthetic: pandemic", "test", SOURCE
)


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    sample, labels = tmp_path / "sample.csv", tmp_path / "labels.csv"
    write_sample([FIRST, SECOND], sample)
    monkeypatch.setenv("TRIALPULSE_GOLD_SAMPLE", str(sample))
    monkeypatch.setenv("TRIALPULSE_GOLD_LABELS", str(labels))
    return sample, labels


def test_app_saves_a_label(paths: tuple[Path, Path]) -> None:
    _, labels = paths
    at = AppTest.from_file(str(APP), default_timeout=30).run()

    assert not at.exception
    assert "Not medical advice" in at.caption[0].value
    assert "0 of 2 labeled" in at.get("progress")[0].proto.text
    assert at.text_area[0].value == "Synthetic: too slow"

    at.radio[0].set_value("accrual").run()
    at.button[0].click().run()

    saved = read_labels(labels)
    assert {k: v["label"] for k, v in saved.items()} == {FIRST.key: "accrual"}
    assert saved[FIRST.key]["source"] == SOURCE
    assert at.text_area[0].value == "Synthetic: pandemic"


def test_app_explains_a_missing_sample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRIALPULSE_GOLD_SAMPLE", str(tmp_path / "missing.csv"))
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert "Build it first" in at.error[0].value
