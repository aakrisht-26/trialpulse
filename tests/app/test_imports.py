"""The dashboard calls the API only: no database, no model, no data/ folder (Step 16), and
its Community Cloud packages are uv.lock's."""

import ast
import re
import sys
import tomllib
from pathlib import Path

from stubbed_api import APP, ROOT

ALLOWED = {"streamlit", "plotly", "httpx", "dashboard"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_the_dashboard_imports_only_its_runtime_and_the_standard_library() -> None:
    files = sorted(APP.rglob("*.py"))
    assert len(files) >= 10
    for path in files:
        outside = {n for n in _imports(path) if n not in sys.stdlib_module_names}
        assert outside <= ALLOWED, f"{path.relative_to(ROOT)} imports {outside - ALLOWED}"


def test_the_dashboard_reads_no_file_and_reaches_only_the_api() -> None:
    for path in sorted(APP.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for needle in ("open(", "read_text", "data/", "psycopg", "DATABASE_URL"):
            assert needle not in source, f"{path.relative_to(ROOT)} holds {needle!r}"
    client = (APP / "dashboard" / "client.py").read_text(encoding="utf-8")
    assert client.count("httpx.Client(") == 1  # the one client, built on the API's address
    ui = (APP / "dashboard" / "ui.py").read_text(encoding="utf-8")
    assert "Client(url)" in ui
    assert "API_URL" in ui


def test_the_community_cloud_requirements_are_the_lockfiles_versions() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = {p["name"]: p["version"] for p in lock["package"]}
    pins = dict(
        re.fullmatch(r"([a-z0-9-]+)==(\S+)", line).groups()  # type: ignore[union-attr]
        for line in (APP / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    assert set(pins) == ALLOWED - {"dashboard"}
    assert pins == {name: locked[name] for name in pins}
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    group = {re.split(r"[<>=\[]", d)[0] for d in project["dependency-groups"]["dashboard"]}
    assert group == set(pins)


def test_no_page_stops_its_script() -> None:
    """Once a script calls st.stop(), the live runtime drops what the frame draws after it:
    the attribution and the data-as-of date disappeared from the page of a trial without a
    score when it was tried in a browser. AppTest still shows them, so this is checked in
    the source."""
    for path in sorted(APP.rglob("*.py")):
        assert "st.stop(" not in path.read_text(encoding="utf-8"), path.relative_to(ROOT)
