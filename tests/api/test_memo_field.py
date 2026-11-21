"""The risk endpoint's `memo` field (ADR 0084): null while memos are not public, shown only
beside the score it was checked against, never for a trial without a score."""

import json
from collections.abc import Callable
from typing import Any

import psycopg

from trialpulse.api.main import MEMO_LABEL

from . import seed as seeding

PARAGRAPHS = ["NCT00000002 has elevated early-stop risk.", "Drivers.", "Record changes."]


def _store(dsn: str, nct: str, valid_as_of: Any) -> None:
    with psycopg.connect(dsn, autocommit=True) as con:
        con.execute(
            """INSERT INTO serving.trial_memos (nct_id, payload_hash, payload_version, payload,
               prompt_version, model, chosen_as, paragraphs, valid_as_of)
               VALUES (%s, %s, 4, %s, 'memo_v4', 'template', %s, %s, %s)""",
            (nct, b"\x01" * 32, json.dumps({"nct_id": nct}), ["top_100"], PARAGRAPHS,
             valid_as_of),
        )  # fmt: skip


def test_a_current_memo_stays_hidden_while_memos_are_not_public(
    seeded: str, make_client: Callable[..., Any]
) -> None:
    _store(seeded, "NCT00000002", seeding.DAY)
    client, _, _ = make_client(memo_public=False)  # the switch off (on since 2026-11-21)
    body = client.get("/v1/trials/NCT00000002/risk").json()
    assert body["status"] == "scored"
    assert body["memo"] is None


def test_a_public_memo_is_shown_beside_the_score_it_was_checked_against(
    seeded: str, make_client: Callable[..., Any]
) -> None:
    _store(seeded, "NCT00000002", seeding.DAY)
    client, _, _ = make_client(memo_public=True)
    memo = client.get("/v1/trials/NCT00000002/risk").json()["memo"]
    assert memo["label"] == MEMO_LABEL
    assert memo["paragraphs"] == PARAGRAPHS
    assert memo["as_of_date"] == seeding.DAY.isoformat()
    assert (memo["model"], memo["prompt_version"]) == ("template", "memo_v4")
    assert "checked against the stored values" in memo["note"]
    assert "fixed template" in memo["note"]
    assert "not why a trial stops" in memo["note"]
    assert "no language model" in memo["note"]  # ADR 0085
    assert "AI" not in memo["label"]


def test_a_memo_of_another_score_date_or_an_unscored_trial_is_never_shown(
    seeded: str, make_client: Callable[..., Any]
) -> None:
    import datetime as dt

    _store(seeded, "NCT00000002", seeding.DAY - dt.timedelta(days=7))  # stale
    _store(seeded, "NCT00000003", seeding.DAY)  # not scored (a reversal)
    client, _, _ = make_client(memo_public=True)
    assert client.get("/v1/trials/NCT00000002/risk").json()["memo"] is None
    unscored = client.get("/v1/trials/NCT00000003/risk").json()
    assert unscored["status"] == "not_scored"
    assert unscored["memo"] is None
