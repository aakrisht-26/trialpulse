"""The document rules and the report command, on the synthetic registry.

The synthetic registry is not built to reproduce the real findings, so the tests that render
text replace the findings with two fixed ones. The findings and their checks have their own
tests in test_findings.py, and one test here runs the real findings on computed results to
show that the two fit together.
"""

import ast
import os
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from trialpulse.cli import REFUSED_EXIT_CODE, RefusedError, run
from trialpulse.cohort.audit import MODELING_EDA_BEFORE
from trialpulse.config import REPO_ROOT, load_project_config
from trialpulse.eda import analysis as an
from trialpulse.eda import document, report
from trialpulse.eda.document import pct
from trialpulse.eda.findings import BUILDERS, Finding, StaleFindingError
from trialpulse.eda.refs import FIGURES, TABLES, cite
from trialpulse.eda.results import STATE_MONTHS, STATE_TABLE_MONTHS, Results, compute

from .conftest import BEFORE, COVID_YEAR, World, cif, followed

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
LONG_AGO = 1_577_836_800_000_000_000  # 2020-01-01, in nanoseconds
CITES = re.compile(r"\((Figures? \d|Table \d)")
MODELING_TAG = "*Modeling-relevant: nothing dated 2018-01-01 or later is read.*"
STUB = [
    Finding("First finding", "Something measured (Figure 1, Table 1).", "Do this."),
    Finding("Second finding", "Something else (Table 2).", "Do that."),
]


@pytest.fixture
def stub_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(document, "findings", lambda res: STUB)


@pytest.fixture
def no_figures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the drawing, for tests of the text that need the command but not the figures."""
    monkeypatch.setattr(report, "draw", lambda res, out_dir: [])


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


def _bullets(section: str) -> list[str]:
    return [line for line in section.splitlines() if line.startswith("- ")]


def _table(text: str, key: str) -> list[list[str]]:
    """The cells of a table, by its name in refs: the header row, then the body rows."""
    lines = text.splitlines()
    caption = f"Table {TABLES.index(key) + 1}. "
    start = next(i for i, line in enumerate(lines) if line.startswith(caption))
    rows: list[list[str]] = []
    for line in lines[start + 1 :]:
        if line.startswith("|"):
            rows.append([cell.strip() for cell in line.strip("|").split("|")])
        elif rows:
            break
    return [rows[0], *rows[2:]]


def _row(table: list[list[str]], first_cell: str) -> dict[str, str]:
    (row,) = [r for r in table[1:] if r[0] == first_cell]
    return dict(zip(table[0], row, strict=True))


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
    assert '"Elevated early-stop risk" is an operational statement about a trial record' in head
    assert "never a statement about whether a treatment works" in head
    assert "under the registry's UNKNOWN rule" in head  # both kinds of censoring are named
    assert "an outcome on or after that date counts as not observed yet" in head
    assert "—" not in text
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_every_statement_cites_a_figure_or_a_table(text: str) -> None:
    """Step 5 acceptance: every claim in eda.md points to a figure or table. Claims are the
    bullets of the numbered sections and the findings."""
    body = text[text.index("\n## 1. ") : text.index("\n## Not in this report")]
    bullets = _bullets(body)
    assert len(bullets) >= 20
    for bullet in bullets:
        assert CITES.search(bullet), bullet
    numbered = re.findall(r"^\d+\. \*\*.*$", _section(text, "8. Findings"), flags=re.MULTILINE)
    assert len(numbered) == len(STUB)
    assert text.count("*Implication for modeling:*") == len(STUB)


def test_the_numbers_of_a_statement_are_in_a_table_it_cites(text: str) -> None:
    """Each percentage and each rate with two decimals in a bullet appears in a table the
    bullet cites, so a bullet cannot cite the wrong table for its numbers."""
    tables = {
        n: " ".join(" ".join(row) for row in _table(text, key)) for n, key in enumerate(TABLES, 1)
    }
    body = text[text.index("\n## 1. ") : text.index("\n## 8. ")]
    checked = 0
    for bullet in _bullets(body):
        cited = [int(n) for n in re.findall(r"Table (\d+)", bullet)]
        if not cited:
            continue
        for number in re.findall(r"\d+\.\d%|\b\d+\.\d\d\b", bullet):
            assert any(number in tables[n] for n in cited), (number, bullet)
            checked += 1
    assert checked >= 40


def test_figures_and_tables_are_numbered_in_order_and_cited_ones_exist(text: str) -> None:
    images = re.findall(r"!\[Figure (\d+): ([^\]]+)\]\(figures/([^)]+)\)", text)
    assert [int(n) for n, _, _ in images] == list(range(1, len(FIGURES) + 1))
    assert [(caption, name) for _, caption, name in images] == [
        (caption, name) for name, caption in FIGURES.values()
    ]
    tables = [int(n) for n in re.findall(r"^Table (\d+)\. ", text, flags=re.MULTILINE)]
    assert tables == list(range(1, len(TABLES) + 1))
    for cited in re.findall(r"Table (\d+)", text):
        assert int(cited) in tables
    for cited in re.findall(r"Figures? (\d+)", text):
        assert 1 <= int(cited) <= len(FIGURES)
    rules = [line for line in text.splitlines() if line.startswith("| --- |")]
    assert len(rules) == len(tables)  # one Markdown table under each table caption


def test_citations_are_built_from_names() -> None:
    assert cite("sponsor", "sponsor") == "(Figure 1, Table 1)"
    assert cite(("states", "naive"), "states") == "(Figures 4 and 5, Table 6)"
    assert cite(tables=("signals_outcome", "signals_cif")) == "(Tables 7 and 8)"
    assert cite("covid") == "(Figure 9)"
    with pytest.raises(ValueError, match="at least one"):
        cite()
    with pytest.raises(ValueError, match="not in tuple"):
        cite(tables="no_such_table")


def test_modeling_sections_say_so_and_read_nothing_from_2018_on(
    text: str, results: Results
) -> None:
    for heading in ("1. Early-stop risk", "3. Stopping competes", "4. Amendments"):
        assert MODELING_TAG in _section(text, heading), heading
    assert int(results.reg.year.max()) < MODELING_EDA_BEFORE.year
    first = _section(text, "1. Early-stop risk")
    assert (
        "first posted from 2014-01-01 to 2017-12-31, with outcomes observed before 2018-01-01"
        in first
    )
    years = [row[0] for row in _table(text, "year")[1:]]
    assert years == ["2014", "2015", "2016", "2017", "All"]  # no later registration year
    # A year is shown only where all of its trials could be followed for the whole horizon.
    by_year = {row[0]: row for row in _table(text, "year")[1:]}
    assert [by_year[y][3] == "n/a" for y in ("2015", "2016", "2017")] == [False, False, True]
    assert [by_year[y][4] == "n/a" for y in ("2015", "2016", "2017")] == [False, True, True]
    assert "Modeling-relevant: nothing dated 2018-01-01 or later is read." in _section(
        text, "5. Registration lag"
    )


def test_phase_is_labeled_descriptive_only_not_used_for_modeling(text: str) -> None:
    section = _section(text, "2. Phase")
    assert section.startswith("\n## 2. Phase (descriptive only, not used for modeling)")
    assert "*Descriptive only, not used for modeling.*" in section
    assert f"Table {TABLES.index('phase') + 1}. Descriptive only, not used for modeling:" in section
    assert "ADR 0006" in section
    assert "current-record phase" in section
    for bullet in _bullets(section):
        assert bullet.startswith("- Descriptive only: "), bullet
    assert "No current record" in section


def test_later_years_are_labeled_descriptive_only(text: str) -> None:
    covid = _section(text, "7. The COVID period")
    assert covid.startswith("\n## 7. The COVID period (descriptive only)")
    assert "*Descriptive only, not used for modeling.*" in covid
    assert f"Table {TABLES.index('covid') + 1}. Descriptive only:" in covid
    for bullet in _bullets(covid):
        assert bullet.startswith("- Descriptive only: "), bullet
    lag = _section(text, "5. Registration lag")
    assert "*Years before 2018 are modeling-relevant; rows from 2018 on are descriptive only" in lag
    assert "Rows from 2018 on are still marked descriptive only" in _section(text, "6. Estimated")
    for key in ("timing_year", "post_dates"):
        use = {row[0]: row[1] for row in _table(text, key)[1:]}
        assert use["2016"] == use["2017"] == "modeling", key
        assert use["2018"] == use[str(COVID_YEAR)] == "descriptive only", key
    # The bullets of the two sections quote modeling years only.
    for bullet in _bullets(lag) + _bullets(_section(text, "6. Estimated")):
        assert not re.search(r"\b(2018|2019|202\d)\b", bullet), bullet


def test_the_reasons_section_is_left_out_and_said_to_be(text: str) -> None:
    assert "## Not in this report" in text
    assert "waits for the final Step 6 labels" in text
    assert "why_stopped" not in text


def test_table_cells_hold_the_known_answers(text: str, world: World) -> None:
    """Parsed from the rendered Markdown and compared with the twins, column by column."""
    rows = world.landmark_rows(0)
    time, event, kind, _ = followed(rows)
    sponsor = np.array([trial.sponsor for trial, _ in rows])
    table = _table(text, "sponsor")
    assert table[0][:4] == ["Sponsor class", "Trials", "Early stops observed", "CIF at 12 months"]
    for name in ("INDUSTRY", "OTHER"):
        mask = sponsor == name
        row = _row(table, name)
        assert row["Trials"] == f"{mask.sum():,}"
        assert row["Early stops observed"] == f"{(event[mask] == 1).sum():,}"
        assert row["CIF at 12 months"] == pct(cif(time[mask], event[mask], 12))
        assert row["CIF at 24 months"] == pct(cif(time[mask], event[mask], 24))
        assert row["Trials"] != row["Early stops observed"]

    differ = False
    for months in STATE_TABLE_MONTHS:
        states = _row(_table(text, "states"), str(months))
        terminated = pct(cif(time, kind, months, an.KIND_TERMINATED))
        withdrawn = pct(cif(time, kind, months, an.KIND_WITHDRAWN))
        assert states["Terminated"] == terminated, months
        assert states["Withdrawn"] == withdrawn, months
        assert states["Completed"] == pct(cif(time, kind, months, an.KIND_COMPLETED)), months
        differ = differ or len({terminated, withdrawn, states["Completed"]}) == 3
    assert differ  # at some month the three columns differ, so a swap would show

    index_2 = world.landmark_rows(2)
    time_2, event_2, _, _ = followed(index_2)
    landmark = _row(_table(text, "landmark"), "12 (landmark index 2)")
    assert landmark["Trials"] == f"{len(index_2):,}"
    assert landmark["CIF within 12 months of the landmark"] == pct(cif(time_2, event_2, 12))

    lapse = _row(_table(text, "lapse"), "OTHER")
    assert lapse["Censored under the UNKNOWN rule before 2018-01-01"] == "1"
    assert _row(_table(text, "lapse"), "INDUSTRY")["Incidence at 60 months"] == "0.0%"


def test_each_sentence_gives_its_numbers_to_the_right_group(
    text: str, results: Results, world: World
) -> None:
    rows = world.landmark_rows(0)
    time, event, kind, _ = followed(rows)
    sponsor = np.array([trial.sponsor for trial, _ in rows])

    def at(name: str, months: int) -> str:
        return pct(cif(time[sponsor == name], event[sponsor == name], months))

    assert at("INDUSTRY", 12) != at("OTHER", 12)
    assert (
        f"- INDUSTRY trials are at {at('INDUSTRY', 12)} after 12 months and "
        f"{at('INDUSTRY', 60)} after 60; OTHER trials are at {at('OTHER', 12)} and "
        f"{at('OTHER', 60)} (Figure 1, Table 1)."
    ) in text

    def state(cause: int, months: int) -> str:
        return pct(cif(time, kind, months, cause))

    end = STATE_MONTHS
    assert (
        f"- By 12 months {state(an.KIND_WITHDRAWN, 12)} of trials are withdrawn and "
        f"{state(an.KIND_TERMINATED, 12)} terminated; by {end} months, "
        f"{state(an.KIND_WITHDRAWN, end)} and {state(an.KIND_TERMINATED, end)} (Table 6)."
    ) in text

    stopped, completed = results.amendments_outcome[0], results.amendments_outcome[1]
    slip, waiting = "primary_completion_later", "not_yet_recruiting"
    assert stopped[slip] != completed[slip]
    assert stopped[waiting] != completed[waiting]
    assert (
        f"- {pct(stopped[slip])} of the trials that later stopped early had moved their primary "
        f"completion date later by the landmark, and {pct(completed[slip])} of the trials that "
        "later completed (Table 7)."
    ) in text
    assert (
        f"- {pct(stopped[waiting])} of the trials that later stopped early were still not yet "
        f"recruiting, against {pct(completed[waiting])} of those that later completed"
    ) in text

    phases = [r for r in results.phase if r["group"] in an.PHASE_ORDER]
    low, high = min(phases, key=lambda r: r["cif_24m"]), max(phases, key=lambda r: r["cif_24m"])
    assert low["cif_24m"] < high["cif_24m"]
    assert (
        f'runs from {pct(low["cif_24m"])} for "{low["group"]}" to {pct(high["cif_24m"])} for '
        f'"{high["group"]}" (Figure 3, Table 5).'
    ) in text

    signal = {r["key"]: r for r in results.amendments_cif}
    short = signal["enrollment_short"]
    assert short["cif_with"] != short["cif_without"]
    assert (
        f"- The 24-month CIF is {pct(short['cif_with'])} where the number enrolled is 10% or "
        f"more below the first target and {pct(short['cif_without'])} for the other trials "
        "with an ACTUAL enrollment count (Figure 6, Table 8)."
    ) in text
    cells = _row(_table(text, "signals_cif"), "Enrolled 10% or more below the first target")
    assert cells["CIF at 24 months with"] == pct(short["cif_with"])
    assert cells["CIF at 24 months without"] == pct(short["cif_without"])
    assert cells["Compared among"] == an.COUNT_ACTUAL


def test_the_real_findings_fit_the_computed_results(results: Results) -> None:
    """The synthetic registry need not support the real findings, but each one must run on
    results that `compute` produced: it writes a finding or refuses, and never trips over a
    missing key or an empty group."""
    outcomes = set()
    for build in BUILDERS:
        try:
            finding = build(results)
        except StaleFindingError:
            outcomes.add("refused")
        else:
            outcomes.add("written")
            assert CITES.search(finding.evidence), finding.title
    assert outcomes <= {"refused", "written"}
    assert "refused" in outcomes  # this registry was not made to agree with the real one


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


def test_the_report_reads_nothing_after_2018_by_default() -> None:
    assert MODELING_EDA_BEFORE == BEFORE  # the date the twins and the document tests assume
    # The date is a constant in the code. It must stay the first locked origin of the
    # configuration (Section 6), which is where the locked definitions live.
    locked = [o.date for o in load_project_config().walk_forward.origins if o.role != "dev"]
    assert min(locked) == MODELING_EDA_BEFORE


def _as_git_checks_it_out_on_windows(path: Path) -> bytes:
    """Rewrite a text file with CRLF line endings, as git does on checkout with
    core.autocrlf, give it an old modification time, and return its bytes."""
    content = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    path.write_bytes(content)
    os.utime(path, ns=(LONG_AGO, LONG_AGO))
    return content


def test_a_report_checked_out_with_crlf_is_reproduced_byte_for_byte(
    world: World,
    tmp_path: Path,
    stub_findings: None,
    no_figures: None,
    text: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """What happened on Aakrisht's machine on 2026-10-07: git had checked docs/eda.md out
    with CRLF, the generator rewrote it with LF, and `git status` showed it as modified
    although no character of its content had changed. A regeneration must leave such a file
    exactly as it is."""
    out = tmp_path / "docs" / "eda.md"
    assert report.main(_args(world, out)) == 0
    assert out.read_bytes() == text.encode("utf-8")  # a new file is written with LF
    assert "Wrote 1 of 10 files" in capsys.readouterr().out

    checked_out = _as_git_checks_it_out_on_windows(out)
    assert checked_out.count(b"\r\n") == text.count("\n")
    assert report.main(_args(world, out)) == 0
    assert out.read_bytes() == checked_out  # still CRLF: not one byte differs
    assert out.stat().st_mtime_ns == LONG_AGO  # and the file was not even rewritten
    assert "Up to date" in capsys.readouterr().out


def test_the_report_does_not_depend_on_the_working_directory(
    world: World,
    tmp_path: Path,
    stub_findings: None,
    no_figures: None,
    text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elsewhere = tmp_path / "somewhere" / "else"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    out = tmp_path / "docs" / "eda.md"
    assert report.main(_args(world, out)) == 0
    assert out.read_bytes() == text.encode("utf-8")
    assert str(tmp_path) not in text  # no path of this machine in the document


def test_the_report_never_reads_the_clock_or_the_working_directory() -> None:
    """Its content cannot depend on the run date: no module of the EDA asks for the time or
    the current directory. The one exception is the duration the command prints."""
    forbidden = {"today", "now", "utcnow", "time", "time_ns", "getcwd", "cwd", "localtime",
                 "gmtime", "getmtime"}  # fmt: skip
    package = Path(report.__file__).parent
    for module in sorted(package.glob("*.py")):
        calls = [
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            for node in ast.walk(ast.parse(module.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call)
        ]
        assert not forbidden & set(calls), module.name
    assert "time.monotonic()" in (package / "report.py").read_text(encoding="utf-8")


def test_git_checks_generated_files_out_as_the_generator_writes_them() -> None:
    """.gitattributes pins LF for the report and marks the figures binary, so a checkout
    has the generator's own bytes on every machine, whatever core.autocrlf says."""
    figure = f"docs/figures/{next(iter(FIGURES.values()))[0]}"
    try:
        checked = subprocess.run(
            ["git", "check-attr", "text", "eol", "binary", "--", "docs/eda.md", figure],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available here")
    assert "docs/eda.md: text: set" in checked
    assert "docs/eda.md: eol: lf" in checked
    assert f"{figure}: binary: set" in checked


@pytest.mark.slow
def test_the_command_writes_the_report_and_every_figure_the_same_way_twice(
    world: World, tmp_path: Path, stub_findings: None, text: str
) -> None:
    out = tmp_path / "docs" / "eda.md"
    assert report.main(_args(world, out)) == 0
    assert out.read_bytes() == text.encode("utf-8")
    figures = sorted((out.parent / "figures").iterdir())
    assert [p.name for p in figures] == sorted(name for name, _ in FIGURES.values())
    first = {p.name: p.read_bytes() for p in figures}
    for name, content in first.items():
        assert content.startswith(PNG_SIGNATURE), name
        assert len(content) > 10_000, name
        assert b"Software" not in content[:400], name  # no version stamp in the file

    # Second run, on files as git checks them out on Windows: nothing is rewritten.
    checked_out = _as_git_checks_it_out_on_windows(out)
    for figure in figures:
        os.utime(figure, ns=(LONG_AGO, LONG_AGO))
    assert report.main(_args(world, out)) == 0
    assert out.read_bytes() == checked_out
    assert {p.name: p.read_bytes() for p in figures} == first
    assert {p.stat().st_mtime_ns for p in (out, *figures)} == {LONG_AGO}


@pytest.mark.slow
@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_the_committed_report_is_reproduced_byte_for_byte(
    tmp_path: Path, line_ending: bytes
) -> None:
    """With the real data on disk (never in CI): regenerating over a copy of the committed
    docs/eda.md and figures changes no byte of them, whether git checked the report out with
    LF or with CRLF. So nobody edited them by hand or forgot to regenerate, and `git status`
    stays clean after the command. This is also where the real findings meet the real
    results."""
    cohort = report.COHORT_DIR
    inputs = [report.WAREHOUSE_PATH, report.CURRENT_FIELDS_PATH]
    inputs += [cohort / "landmarks.parquet", cohort / "outcomes.parquet"]
    if not all(path.is_file() for path in inputs):
        pytest.skip("the warehouse, the cohort or the current-record snapshot is not on disk")
    committed = REPO_ROOT / "docs"
    out = tmp_path / "eda.md"
    as_committed = (committed / "eda.md").read_bytes().replace(b"\r\n", b"\n")
    out.write_bytes(as_committed.replace(b"\n", line_ending))
    shutil.copytree(committed / "figures", tmp_path / "figures")
    files = [out, *[tmp_path / "figures" / name for name, _ in FIGURES.values()]]
    before = {path: path.read_bytes() for path in files}
    for path in files:
        os.utime(path, ns=(LONG_AGO, LONG_AGO))

    assert report.main(["--out", str(out)]) == 0
    for path in files:
        assert path.read_bytes() == before[path], path.name
        assert path.stat().st_mtime_ns == LONG_AGO, path.name
