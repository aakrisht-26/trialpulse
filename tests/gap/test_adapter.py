"""The gap release's adapter (`gap.adapter`, ADR 0041) on synthetic Parquet files written
here: one test per rule of the layout, and the refusal of anything not listed. No row of
either real release is used."""

import datetime as dt
from pathlib import Path
from typing import Any

import duckdb
import pytest

from trialpulse.cli import RefusedError
from trialpulse.contracts.text import is_html, normalize_text
from trialpulse.contracts.versions import distinct_intervention_types
from trialpulse.gap import adapter

D1, D2, RESET = dt.date(2026, 9, 1), dt.date(2026, 10, 5), dt.date(2026, 10, 9)


def _write(con: duckdb.DuckDBPyConnection, folder: Path, select: str) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY ({select}) TO '{(folder / 'part-0.parquet').as_posix()}' (FORMAT parquet)")
    return (folder / "*.parquet").as_posix()


PINNED_CORE = """SELECT * FROM (VALUES
  ('NCT1', 0::BIGINT, DATE '2026-09-01', 'Phase 2 study', NULL::INTEGER, NULL::INTEGER))
  t(nct_id, nct_version, last_update_post_date, brief_title, version_holder,
    unposted_responsible_party)"""
PINNED_IV = """SELECT * FROM (VALUES ('NCT1', 0::BIGINT, 'Aspirin', 'DRUG', 'first'))
  t(nct_id, nct_version, intervention_name, intervention_type, description)"""


def _new_core(extra: str = "") -> str:
    """The new layout: the pinned columns (two retyped), then the new ones; a public
    version, a later one that failed review, and a version of a merged registration."""
    return f"""SELECT * FROM (VALUES
      ('NCT1', 0::BIGINT, DATE '2026-09-01', 'Phase 2 study', NULL::DATE, 'Sponsor',
       NULL::DATE, false, NULL::VARCHAR, NULL::VARCHAR, NULL::BOOLEAN),
      ('NCT1', 1::BIGINT, NULL::DATE, 'Phase 2 study', NULL::DATE, NULL,
       DATE '2026-10-09', false, NULL, 'MONTH', NULL),
      ('NCT2', 0::BIGINT, DATE '2026-10-05', 'A study', NULL::DATE, NULL,
       NULL::DATE, true, 'NCT1', NULL, NULL)
      {extra})
      t(nct_id, nct_version, last_update_post_date, brief_title, version_holder,
        unposted_responsible_party, reset_notice_post_date, is_obsolete, obsolete_by,
        status_verified_date_precision, document_no_sap)"""


NEW_IV = """SELECT * FROM (VALUES
  ('NCT1', 0::BIGINT, 0::BIGINT, 'Aspirin', 'DRUG', 'first', false, NULL::VARCHAR),
  ('NCT1', 0::BIGINT, 1::BIGINT, 'Aspirin', 'DRUG', 'second', false, NULL),
  ('NCT1', 0::BIGINT, 2::BIGINT, NULL, 'OTHER', 'no name', false, NULL),
  ('NCT1', 0::BIGINT, 3::BIGINT, 'Saline', 'OTHER', 'saline', false, NULL))
  t(nct_id, nct_version, ord, intervention_name, intervention_type, description,
    is_obsolete, obsolete_by)"""


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, Any]:
    con = duckdb.connect()
    return {
        "con": con,
        "pinned_core": _write(con, tmp_path / "pinned" / "core", PINNED_CORE),
        "pinned_iv": _write(con, tmp_path / "pinned" / "interventions", PINNED_IV),
        "new_core": _write(con, tmp_path / "new" / "core", _new_core()),
        "new_iv": _write(con, tmp_path / "new" / "interventions", NEW_IV),
    }


def _adapt(layout: dict[str, Any]) -> adapter.Adapted:
    return adapter.adapt(layout["con"], layout["new_core"], layout["new_iv"],
                         layout["pinned_core"], layout["pinned_iv"])  # fmt: skip


def test_the_core_rows_come_out_in_the_pinned_columns_and_types(layout: dict[str, Any]) -> None:
    con = layout["con"]
    out = con.execute(f"DESCRIBE {_adapt(layout).core_sql}").fetchall()
    assert [(r[0], r[1]) for r in out] == [
        ("nct_id", "VARCHAR"), ("nct_version", "BIGINT"), ("last_update_post_date", "DATE"),
        ("brief_title", "VARCHAR"), ("version_holder", "INTEGER"),
        ("unposted_responsible_party", "INTEGER"),
    ]  # fmt: skip


def test_a_version_that_failed_review_is_kept_at_its_reset_notice_and_counted(
    layout: dict[str, Any],
) -> None:
    """ADR 0073, decision 2: the pinned revision's rule, a failed review dated at its reset
    notice, on the new release's rows too."""
    con = layout["con"]
    rows = con.execute(f"SELECT nct_id, nct_version, last_update_post_date "
                       f"FROM ({_adapt(layout).core_sql}) ORDER BY 1, 2").fetchall()  # fmt: skip
    assert rows == [("NCT1", 0, D1), ("NCT1", 1, RESET), ("NCT2", 0, D2)]
    counts = adapter.left_out(con, layout["new_core"], layout["new_iv"])
    assert (counts["failed_review_versions"], counts["failed_review_trials"]) == (1, 1)


def test_a_failed_review_without_a_reset_notice_or_a_public_reset_is_refused(
    layout: dict[str, Any], tmp_path: Path
) -> None:
    con = layout["con"]
    bad = (", ('NCT3', 0::BIGINT, NULL::DATE, 'x', NULL::DATE, NULL, NULL::DATE, false, "
           "NULL, NULL, NULL)")  # fmt: skip
    layout["new_core"] = _write(con, tmp_path / "bad1" / "core", _new_core(bad))
    with pytest.raises(RefusedError, match="1 versions have neither"):
        _adapt(layout)
    public = (", ('NCT3', 0::BIGINT, DATE '2026-10-01', 'x', NULL::DATE, NULL, DATE "
              "'2026-10-02', false, NULL, NULL, NULL)")  # fmt: skip
    layout["new_core"] = _write(con, tmp_path / "bad2" / "core", _new_core(public))
    with pytest.raises(RefusedError, match="1 public versions have a reset notice"):
        _adapt(layout)


def test_retyped_columns_become_null_of_the_pinned_type(layout: dict[str, Any]) -> None:
    con = layout["con"]
    sql = _adapt(layout).core_sql
    values = con.execute(f"SELECT version_holder, unposted_responsible_party FROM ({sql}) "
                         "WHERE nct_id = 'NCT1' AND nct_version = 0").fetchall()  # fmt: skip
    assert values == [(None, None)]  # the new release's value is not carried


def test_merged_registrations_are_kept_and_their_flag_dropped(layout: dict[str, Any]) -> None:
    con = layout["con"]
    sql = _adapt(layout).core_sql
    assert con.execute(f"SELECT count(*) FROM ({sql}) WHERE nct_id = 'NCT2'").fetchone() == (1,)
    assert adapter.left_out(con, layout["new_core"], layout["new_iv"])["obsolete_rows_kept"] == 1


def test_interventions_give_one_row_per_distinct_named_pair(layout: dict[str, Any]) -> None:
    """Keyed by ord now: a repeated (name, type) is one row, with its first entry's
    description; an entry without a name is left out, as the pinned config held none. The
    canonical types rule of ADR 0067 then gives what it gave on the pinned config."""
    con = layout["con"]
    sql = _adapt(layout).interventions_sql
    rows = con.execute(f"SELECT * FROM ({sql}) ORDER BY intervention_name").fetchall()
    assert rows == [("NCT1", 0, "Aspirin", "DRUG", "first"), ("NCT1", 0, "Saline", "OTHER",
                                                                "saline")]  # fmt: skip
    pairs = [(r[2], r[3]) for r in rows]
    assert distinct_intervention_types(pairs) == ["DRUG", "OTHER"]
    counts = adapter.left_out(con, layout["new_core"], layout["new_iv"])
    assert counts["interventions_without_a_name"] == 1
    assert counts["interventions_repeating_a_pair"] == 1


def test_rich_text_in_markdown_takes_the_canonical_form_of_its_html_original() -> None:
    """The release's rich text is Markdown; `normalize_text` reads it as it reads API v2's
    and gives the canonical form of ADR 0012 the pinned HTML gave."""
    html = "<p>Inclusion Criteria:</p><ul><li>Age &gt;= 18</li><li>A &amp; B</li></ul>"
    markdown = "Inclusion Criteria:\n\n* Age \\>= 18\n* A & B"
    assert is_html(html)
    assert not is_html(markdown)
    assert normalize_text(markdown) == normalize_text(html)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            "SELECT nct_id, nct_version, last_update_post_date, brief_title, version_holder, "
            "unposted_responsible_party, reset_notice_post_date, is_obsolete, obsolete_by, "
            "status_verified_date_precision, document_no_sap, 1 AS brand_new "
            "FROM ({base})",
            "new and not handled: brand_new",
        ),
        (
            "SELECT nct_id, nct_version, last_update_post_date, version_holder, "
            "unposted_responsible_party, reset_notice_post_date, is_obsolete, obsolete_by, "
            "status_verified_date_precision, document_no_sap FROM ({base})",
            "missing: brief_title",
        ),
        (
            "SELECT nct_id, nct_version::VARCHAR AS nct_version, last_update_post_date, "
            "brief_title, version_holder, unposted_responsible_party, reset_notice_post_date, "
            "is_obsolete, obsolete_by, status_verified_date_precision, document_no_sap "
            "FROM ({base})",
            "type changed: nct_version BIGINT to VARCHAR",
        ),
    ],
)
def test_a_difference_the_adapter_does_not_list_is_refused(
    layout: dict[str, Any], tmp_path: Path, change: str, message: str
) -> None:
    con = layout["con"]
    layout["new_core"] = _write(con, tmp_path / "changed" / "core",
                                change.format(base=_new_core()))  # fmt: skip
    with pytest.raises(RefusedError, match=message):
        _adapt(layout)


def test_the_adapter_refuses_on_a_runner(
    layout: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(RefusedError, match="ADR 0040"):
        _adapt(layout)
    with pytest.raises(RefusedError, match="ADR 0040"):
        adapter.left_out(layout["con"], layout["new_core"], layout["new_iv"])
