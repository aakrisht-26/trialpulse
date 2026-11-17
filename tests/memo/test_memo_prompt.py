"""The memo's prompt files hold rules only, no trial text (ADR 0084)."""

import json
import re
from pathlib import Path

import pytest

from trialpulse.config import REPO_ROOT, load_project_config
from trialpulse.memo import check as c

PROMPTS = sorted((REPO_ROOT / "src" / "trialpulse" / "memo" / "prompts").glob("*"))


def _stub_texts() -> set[str]:
    """Titles and names the recorded API answers hold (made up, but trial text in form)."""
    found: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k in ("brief_title", "sponsor_name", "display_name", "name") and v:
                    found.add(str(v))
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(json.loads((REPO_ROOT / "tests" / "app" / "stubs.json").read_text(encoding="utf-8")))
    return found


def test_the_configured_prompt_exists() -> None:
    assert PROMPTS
    assert (REPO_ROOT / "src" / "trialpulse" / "memo" / "prompts" /
            load_project_config().memo.prompt).is_file()  # fmt: skip


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.name)
def test_a_prompt_holds_no_trial_text(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not c.NCT.search(text), "a registry id"
    assert not c.ISO_DATE.search(text), "a date"
    assert not re.search(r"\b(?:19|20)\d{2}\b", text), "a year"
    for value in _stub_texts():
        assert value.lower() not in text.lower(), value
    assert not c.TITLES.search(text), "a personal title"
    assert chr(0x2014) not in text  # no em dash
