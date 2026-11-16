"""The documents' links (scripts/check_links.py, Step 18), offline: every relative link and
anchor in README.md, CLAUDE.md, CHANGELOG.md and docs/ resolves. External links are checked
by hand (`--external`), never here."""

import importlib.util
import sys
from pathlib import Path

from trialpulse.config import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "check_links.py"
spec = importlib.util.spec_from_file_location("check_links", SCRIPT)
assert spec is not None
assert spec.loader is not None
check_links = importlib.util.module_from_spec(spec)
sys.modules["check_links"] = check_links
spec.loader.exec_module(check_links)


def test_every_relative_link_and_anchor_of_the_documents_resolves() -> None:
    documents = check_links.documents()
    assert REPO_ROOT / "README.md" in documents
    assert len(documents) > 50
    broken, external = check_links.check(documents)
    assert broken == []
    assert any(a.startswith("https://clinicaltrials.gov") for a in external)


def test_github_anchors() -> None:
    assert check_links.slug("Part 4: Backups") == "part-4-backups"
    assert check_links.slug("Quickstart (Windows PowerShell)") == "quickstart-windows-powershell"
    citation = "license-attribution-and-citation"
    assert check_links.slug("License, attribution and citation") == citation
    assert check_links.slug("The `drift` step") == "the-drift-step"


def test_a_missing_file_or_heading_is_reported_and_code_is_not_a_link(tmp_path: Path) -> None:
    page = tmp_path / "a.md"
    page.write_text(
        "# Top\n## Top\n[gone](b.md) [here](#top) [second](#top-1) [none](#nope)\n"
        "`[code](c.md)`\n```\n[fenced](d.md)\n```\n[site](https://example.org) [mail](mailto:x)\n",
        encoding="utf-8",
    )
    broken, external = check_links.check([page], tmp_path)
    assert [(b.target, b.why) for b in broken] == [("b.md", "no such file"),
                                                   ("#nope", "no such heading")]  # fmt: skip
    assert external == {"https://example.org"}
