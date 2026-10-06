"""The document rules and the report command, on the synthetic registry.

The synthetic registry is not built to reproduce the real findings, so the tests that render
text replace the findings with two fixed ones. The findings and their checks have their own
tests in test_findings.py.
"""

import re
from pathlib import Path

import pytest

from trialpulse.cli import REFUSED_EXIT_CODE, RefusedError, run
from trialpulse.cohort.audit import MODELING_EDA_BEFORE
from trialpulse.config import REPO_ROOT, load_project_config
from trialpulse.eda import document, report
from trialpulse.eda.charts import FIGURES
from trialpulse.eda.findings import Finding, StaleFindingError
from trialpulse.eda.results import Results, compute

from .conftest import COVID_YEAR, World

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CITES = re.compile(r"\((Figures? \d|Table \d)")
STUB = [
    Finding("First finding", "Something measured (Figure 1, Table 1).", "Do this."),
    Finding("Second finding", "Something else (Table 2).", "Do that."),
]


@pytest.fixture
def stub_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(document, "findings", lambda res: STUB)


@pytest.fixture(scope="module")
def results(world: World) -> Results:
    return compute(world.sources, load_project_config(), MODELING_EDA_BEFORE)


@pytest.fixture
def text(results: Results, stub_findings: None) -> str:
    return document.render(results)


def _section(text: str, heading: str) -> str:
    start = text.index(f"\n## {heading}")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def _args(world: World, out: Path) -> list[str]:
    sources = world.sources
    return [
        "--warehouse", str(sources.warehouse),
        "--cohort-dir", str(sources.landmarks.parent),
        "--current-fields", str(sources.current_fields),
        "--out", str(out),
    ]  # fmt: skip


def test_the_document_opens_with_the_disclaimer_and_its_sources(text: str) -> None:
    lines = text.splitlines()
    assert lines[0] == "# Exploratory data analysis"
    assert lines[2] == "Research demo. Not medical advice. Not for patient decision-making."
    head = text[: text.index("## 1. ")]
    assert "ClinicalTrials.gov" in head
    assert "Data as of 2026-09-25" in head
    assert "CC-BY-NC-4.0" in head
    assert "dataset revision synthetic" in head  # the stamp of the warehouse it was built from
    assert "—" not in text
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_every_statement_cites_a_figure_or_a_table(text: str) -> None:
    """Step 5 acceptance: every claim in eda.md points to a figure or table. Claims are the
    bullets of the numbered sections and the findings."""
    body = text[text.index("\n## 1. ") : text.index("\n## Not in this report")]
    bullets = [line for line in body.splitlines() if line.startswith("- ")]
    assert len(bullets) >= 15
    for bullet in bullets:
        assert CITES.search(bullet), bullet
    numbered = re.findall(r"^\d+\. \*\*.*$", _section(text, "8. Findings"), flags=re.MULTILINE)
    assert len(numbered) == len(STUB)
    for finding in numbered:
        assert CITES.search(finding), finding
    assert text.count("*Implication for modeling:*") == len(STUB)


def test_figures_and_tables_are_numbered_in_order_and_cited_ones_exist(text: str) -> None:
    images = re.findall(r"!\[Figure (\d+): [^\]]+\]\(figures/([^)]+)\)", text)
    assert [int(n) for n, _ in images] == sorted(FIGURES)
    assert [name for _, name in images] == [FIGURES[n][0] for n in sorted(FIGURES)]
    tables = [int(n) for n in re.findall(r"^Table (\d+)\. ", text, flags=re.MULTILINE)]
    assert tables == list(range(1, len(tables) + 1))
    assert len(tables) == 11
    for cited in re.findall(r"Table (\d+)", text):
        assert int(cited) in tables
    for cited in re.findall(r"Figures? (\d+)", text):
        assert int(cited) in FIGURES
    assert set(re.findall(r"Figures (\d+) and (\d+)", text)) <= {("4", "5")}
    rules = [line for line in text.splitlines() if line.startswith("| --- |")]
    assert len(rules) == len(tables)  # one Markdown table under each table caption


def test_modeling_sections_say_so_and_use_landmarks_before_2018_only(
    text: str, results: Results
) -> None:
    tag = "*Modeling-relevant: landmarks before 2018-01-01 only.*"
    for heading in ("1. Early-stop risk", "3. Stopping competes", "4. Amendments"):
        assert tag in _section(text, heading), heading
    assert int(results.reg.year.max()) < MODELING_EDA_BEFORE.year
    first = _section(text, "1. Early-stop risk")
    assert str(COVID_YEAR) not in first  # the year registered after 2018 is in no modeling table
    assert "first posted from 2014-01-01 to 2017-12-31" in first


def test_phase_is_labeled_descriptive_only_not_used_for_modeling(text: str) -> None:
    section = _section(text, "2. Phase")
    assert section.startswith("\n## 2. Phase (descriptive only, not used for modeling)")
    assert "*Descriptive only, not used for modeling.*" in section
    assert "Table 4. Descriptive only, not used for modeling:" in section
    assert "ADR 0006" in section
    assert "current-record phase" in section
    for bullet in (line for line in section.splitlines() if line.startswith("- ")):
        assert bullet.startswith("- Descriptive only: "), bullet
    assert "No current record" in section


def test_later_years_are_labeled_descriptive_only(text: str) -> None:
    covid = _section(text, "7. The COVID period")
    assert covid.startswith("\n## 7. The COVID period (descriptive only)")
    assert "*Descriptive only, not used for modeling.*" in covid
    assert "Table 11. Descriptive only:" in covid
    for bullet in (line for line in covid.splitlines() if line.startswith("- ")):
        assert bullet.startswith("- Descriptive only: "), bullet
    for heading in ("5. Registration lag", "6. Estimated post dates"):
        section = _section(text, heading)
        rows = [line for line in section.splitlines() if line.startswith(f"| {COVID_YEAR} |")]
        assert rows, heading
        assert all("| descriptive only |" in row for row in rows)
        assert "| 2016 | modeling |" in section
        assert "| 2016 | descriptive only |" not in section


def test_the_reasons_section_is_left_out_and_said_to_be(text: str) -> None:
    assert "## Not in this report" in text
    assert "waits for the final Step 6 labels" in text
    assert "why_stopped" not in text


def test_a_stale_finding_stops_the_command_before_anything_is_written(
    world: World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def stale(res: Results) -> list[Finding]:
        raise StaleFindingError("the data no longer supports a finding (made up).")

    monkeypatch.setattr(document, "findings", stale)
    out = tmp_path / "docs" / "eda.md"
    assert run(report.main, _args(world, out)) == REFUSED_EXIT_CODE
    assert "refused: the data no longer supports a finding" in capsys.readouterr().err
    assert not out.exists()
    assert not (out.parent / "figures").exists()


def test_a_missing_input_is_refused_with_the_command_that_creates_it(
    world: World, tmp_path: Path
) -> None:
    args = _args(world, tmp_path / "eda.md")
    args[args.index("--current-fields") + 1] = str(tmp_path / "nowhere.parquet")
    with pytest.raises(RefusedError, match=r"nowhere\.parquet is missing.*feasibility\.spike"):
        report.main(args)
    assert not (tmp_path / "eda.md").exists()


@pytest.mark.slow
def test_the_command_writes_the_report_and_every_figure_the_same_way_twice(
    world: World, tmp_path: Path, stub_findings: None, text: str
) -> None:
    out = tmp_path / "docs" / "eda.md"
    assert report.main(_args(world, out)) == 0
    assert out.read_text(encoding="utf-8") == text
    figures = sorted((out.parent / "figures").iterdir())
    assert [p.name for p in figures] == [FIGURES[n][0] for n in sorted(FIGURES)]
    first = {p.name: p.read_bytes() for p in figures}
    for name, content in first.items():
        assert content.startswith(PNG_SIGNATURE), name
        assert len(content) > 10_000, name
        assert b"Software" not in content[:400], name  # no version stamp in the file
    assert report.main(_args(world, out)) == 0
    assert out.read_text(encoding="utf-8") == text
    assert {p.name: p.read_bytes() for p in figures} == first


@pytest.mark.slow
def test_the_committed_report_is_what_the_code_and_the_data_produce(tmp_path: Path) -> None:
    """With the real data on disk (never in CI), regenerating gives exactly the committed
    docs/eda.md and figures: nobody edited them by hand or forgot to regenerate."""
    cohort = report.COHORT_DIR
    inputs = [report.WAREHOUSE_PATH, report.CURRENT_FIELDS_PATH]
    inputs += [cohort / "landmarks.parquet", cohort / "outcomes.parquet"]
    if not all(path.is_file() for path in inputs):
        pytest.skip("the warehouse, the cohort or the current-record snapshot is not on disk")
    out = tmp_path / "eda.md"
    assert report.main(["--out", str(out)]) == 0
    committed = REPO_ROOT / "docs"
    assert out.read_text(encoding="utf-8") == (committed / "eda.md").read_text(encoding="utf-8")
    for name, _ in FIGURES.values():
        assert (tmp_path / "figures" / name).read_bytes() == (
            committed / "figures" / name
        ).read_bytes(), name
