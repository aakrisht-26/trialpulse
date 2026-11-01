"""Display rows of the API from API v2 records (ADR 0051): titles, names and areas."""

from typing import Any

import pytest

from trialpulse.live import display

SECRET = b"a test key for the sponsor ids"
psycopg = pytest.importorskip("psycopg")


def _study(nct: str, name: str | None, kind: str | None, ancestors: list[dict[str, str]],
           title: str = "A Study of X") -> dict[str, Any]:  # fmt: skip
    return {"protocolSection": {
        "identificationModule": {"nctId": nct, "briefTitle": title},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": name, "class": kind}}},
        "derivedSection": {"conditionBrowseModule": {"ancestors": ancestors}}}  # fmt: skip


NEO = [{"id": "D009369", "term": "Neoplasms"}, {"id": "D001523", "term": "Mental Disorders"},
       {"id": "D000001", "term": "Something Else"}]  # fmt: skip


def test_a_record_gives_its_title_areas_and_a_name_only_where_one_may_be_shown() -> None:
    rows = display.display_rows([
        _study("NCT00000001", "Acme Pharma Inc.", "INDUSTRY", NEO, "A  Study of &gt; X"),
        _study("NCT00000002", "Jane Example", "OTHER", []),  # may be a person
        _study("NCT00000003", "Dr. Jane Example", "OTHER", []),  # a person by title
        _study("NCT00000004", "Jane Example", "INDIV", []),
    ], SECRET)  # fmt: skip
    first, hidden, titled, individual = rows
    assert (first.brief_title, first.areas, first.sponsor_name) == (
        "A Study of > X", [2, 24], "Acme Pharma Inc.")  # fmt: skip
    assert first.sponsor_id is not None
    assert len(first.sponsor_id) == 64
    # The record's name is a candidate in memory; whether it is shown is decided on saving.
    assert (hidden.sponsor_name, hidden.areas) == ("Jane Example", [])
    assert hidden.sponsor_id is not None  # an organization might still own the id
    assert (titled.sponsor_name, titled.sponsor_id) == (None, None)
    assert (individual.sponsor_name, individual.sponsor_id) == (None, None)


def _dsn(scratch: str) -> str:
    return scratch.replace("postgresql+psycopg://", "postgresql://", 1)


def _names(cur: Any) -> list[tuple[str, str]]:
    return cur.execute(
        "SELECT display_name, search_key FROM serving.sponsors ORDER BY 1"
    ).fetchall()


def test_saving_keeps_names_only_where_they_may_be_shown(scratch: str) -> None:
    named = display.display_rows([_study("NCT00000001", "Acme Pharma Inc.", "INDUSTRY", NEO)],
                                 SECRET)  # fmt: skip
    with psycopg.connect(_dsn(scratch)) as con, con.cursor() as cur:
        assert display.save(cur, named)["sponsors_named"] == 1
        hidden = display.display_rows([_study("NCT00000003", "Jane Example", "OTHER", [])],
                                      SECRET)  # fmt: skip
        assert display.save(cur, hidden)["sponsors_hidden"] == 1
        assert _names(cur) == [("Acme Pharma Inc.", "acme pharma inc")]
        assert "jane" not in str(cur.execute("SELECT * FROM serving.trial_display").fetchall())
        assert "jane" not in str(cur.execute("SELECT * FROM serving.sponsors").fetchall())
        display.remove(cur, ["NCT00000001"])
        left = cur.execute("SELECT nct_id FROM serving.trial_display ORDER BY 1").fetchall()
        assert left == [("NCT00000003",)]
        assert _names(cur) == [("Acme Pharma Inc.", "acme pharma inc")]  # an organization word


def test_a_name_a_later_record_hides_is_removed(scratch: str) -> None:
    """Review finding 11: the same sponsor id, first shown, then given by a record that does
    not allow its name. The stored name goes (a deliberate fault that kept it got through
    the earlier test, which built the hiding record but never saved it)."""
    shown = display.display_rows(
        [_study(f"NCT0000001{i}", "Jane Example", "INDUSTRY", []) for i in range(5)], SECRET
    )
    with psycopg.connect(_dsn(scratch)) as con, con.cursor() as cur:
        assert display.save(cur, shown)["sponsors_named"] == 1  # INDUSTRY, leads 5 trials
        assert _names(cur) == [("Jane Example", "jane example")]
        later = display.display_rows([_study("NCT00000010", "Jane Example", "OTHER", [])],
                                     SECRET)  # fmt: skip
        assert later[0].sponsor_id == shown[0].sponsor_id
        assert display.save(cur, later)["sponsors_hidden"] == 1
        assert _names(cur) == []


def test_a_name_shown_for_leading_5_trials_goes_when_fewer_remain(scratch: str) -> None:
    rows = display.display_rows(
        [_study(f"NCT0000002{i}", "Jane Example", "NIH", []) for i in range(5)]
        + [_study("NCT00000030", "Example Hospital", "OTHER", [])],
        SECRET,
    )
    with psycopg.connect(_dsn(scratch)) as con, con.cursor() as cur:
        display.save(cur, rows[:4])  # four trials: not yet shown
        assert _names(cur) == []
        display.save(cur, rows)  # the fifth: shown
        assert _names(cur) == [("Example Hospital", "example hospital"),
                               ("Jane Example", "jane example")]  # fmt: skip
        display.remove(cur, ["NCT00000020", "NCT00000030"])  # one of the five ends
        # Four live trials remain: the name goes. An organization word does not depend on
        # how many trials a sponsor leads, so the hospital's name stays.
        assert _names(cur) == [("Example Hospital", "example hospital")]
