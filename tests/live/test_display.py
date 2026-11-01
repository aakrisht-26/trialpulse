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
    assert (first.brief_title, first.areas, first.display_name) == (
        "A Study of > X", [2, 24], "Acme Pharma Inc.")  # fmt: skip
    assert first.sponsor_id is not None
    assert len(first.sponsor_id) == 64
    assert (hidden.display_name, hidden.areas) == (None, [])
    assert hidden.sponsor_id is not None  # an organization might still own the id
    assert (titled.display_name, titled.sponsor_id) == (None, None)
    assert (individual.display_name, individual.sponsor_id) == (None, None)


def test_saving_keeps_names_only_where_they_may_be_shown(scratch: str) -> None:
    dsn = scratch.replace("postgresql+psycopg://", "postgresql://", 1)
    named = display.display_rows([_study("NCT00000001", "Acme Pharma Inc.", "INDUSTRY", NEO)],
                                 SECRET)  # fmt: skip
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        assert display.save(cur, named)["sponsors_named"] == 1
        # The same sponsor id later shown with a class that hides the name: the name goes.
        same_key = display.display_rows(
            [_study("NCT00000002", "Acme Pharma Inc", "OTHER", [])], SECRET)  # fmt: skip
        assert same_key[0].sponsor_id == named[0].sponsor_id
        hidden = display.display_rows([_study("NCT00000003", "Jane Example", "OTHER", [])],
                                      SECRET)  # fmt: skip
        display.save(cur, hidden)
        names = cur.execute("SELECT display_name, search_key FROM serving.sponsors").fetchall()
        assert names == [("Acme Pharma Inc.", "acme pharma inc")]
        assert "jane" not in str(cur.execute("SELECT * FROM serving.trial_display").fetchall())
        display.remove(cur, ["NCT00000001"])
        left = cur.execute("SELECT nct_id FROM serving.trial_display ORDER BY 1").fetchall()
        assert left == [("NCT00000003",)]
