"""The API against a seeded scratch database (Step 15, ADRs 0046 to 0051). Skipped without
Postgres, failed in CI (TRIALPULSE_REQUIRE_POSTGRES)."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from trialpulse.api import schemas
from trialpulse.api.db import Database, engine_for
from trialpulse.api.main import NO_SPONSOR, Api, create_app

from .conftest import CFG, FakeClock

Make = Callable[..., tuple[TestClient, Api, FakeClock]]
ENDPOINTS = (
    "/v1/trials/NCT00000001/risk", "/v1/trials/NCT00000003/risk",
    "/v1/trials/NCT00000001/timeline", "/v1/sponsors?prefix=acm",
    "/v1/sponsors/Acme%20Pharma%20Inc./portfolio", "/v1/areas", "/v1/areas/neoplasms/landscape",
    "/v1/reasons/summary", "/v1/model", "/health",
)  # fmt: skip
ERRORS = ("/v1/trials/NCT99999999/risk", "/v1/trials/not-an-id/risk",
          "/v1/sponsors/Nobody%20Here/portfolio", "/v1/areas/nowhere/landscape",
          "/v1/sponsors?prefix=ab")  # fmt: skip


def _envelope(body: dict[str, Any]) -> None:
    assert body["notice"] == schemas.NOTICE
    assert body["attribution"]["source"] == "ClinicalTrials.gov"
    assert "derived early-stop risk scores" in body["attribution"]["note"]
    assert "data_as_of" in body


def test_every_answer_and_every_error_carries_the_notice_and_attribution(make_client: Make) -> None:
    client, _, _ = make_client()
    for path in ENDPOINTS:
        answer = client.get(path)
        assert answer.status_code == 200, path
        _envelope(answer.json())
        if path != "/health":
            assert answer.json()["data_as_of"] == "2026-09-25", path
    for path in ERRORS:
        answer = client.get(path)
        assert answer.status_code in (404, 422), path
        _envelope(answer.json())
    page = client.get("/openapi.json").json()["info"]["description"]
    assert schemas.NOTICE in page
    assert "ClinicalTrials.gov" in page
    assert client.get("/docs").status_code == 200


def test_a_score_carries_its_flag_drivers_and_dates(make_client: Make) -> None:
    client, _, _ = make_client()
    body = client.get("/v1/trials/NCT00000001/risk").json()
    assert body["status"] == "scored"
    assert body["not_scored"] is None
    score = body["score"]
    assert (score["flagged"], score["flag"]) == (True, "outside the validated range")
    assert "overstate" in score["flag_note"]
    assert (score["risk_12m"], score["as_of_date"]) == (0.31, "2026-09-25")
    assert (score["landmark_index"], score["true_landmark_index"]) == (2, 9)
    assert [len(score["drivers"][h]) for h in ("12", "24")] == [5, 5]
    driver = score["drivers"]["12"][0]
    assert driver["direction"] == ("raises" if driver["contribution"] > 0 else "lowers")
    assert body["sponsor_name"] == "Acme Pharma Inc."
    assert body["areas"] == ["neoplasms", "cardiovascular"]
    plain = client.get("/v1/trials/NCT00000002/risk").json()["score"]
    assert (plain["flagged"], plain["flag"], plain["flag_note"]) == (False, None, None)


def test_an_unscored_trial_carries_its_reason_code(make_client: Make) -> None:
    client, _, _ = make_client()
    body = client.get("/v1/trials/NCT00000003/risk").json()
    assert (body["status"], body["score"]) == ("not_scored", None)
    assert body["not_scored"]["reason"] == "reversal"
    assert "Section 6" in body["not_scored"]["reason_text"]
    assert body["areas"] == ["mental-disorders"]


def test_a_sponsor_without_a_display_name_is_never_named_or_found(make_client: Make) -> None:
    """ADR 0047: its trials show the class and a null name; looking up a name it could have
    gives exactly the answer of a name that does not exist."""
    client, _, _ = make_client()
    body = client.get("/v1/trials/NCT00000004/risk").json()
    assert (body["sponsor_name"], body["sponsor_class"]) == (None, "INDUSTRY")
    unknown = client.get("/v1/sponsors/Nobody%20Here/portfolio")
    other = client.get("/v1/sponsors/Jane%20Example/portfolio")
    assert unknown.status_code == other.status_code == 404
    assert unknown.json() == other.json()
    assert unknown.json()["detail"] == NO_SPONSOR
    assert client.get("/v1/areas/not-classified/landscape").json()["open_trials"] >= 1


def test_a_portfolio_ranks_by_risk_then_lists_the_unscored(make_client: Make) -> None:
    client, _, _ = make_client()
    body = client.get("/v1/sponsors/ACME pharma, inc/portfolio").json()  # normalized match
    assert (body["sponsor_name"], body["trials_total"]) == ("Acme Pharma Inc.", 3)
    assert [t["nct_id"] for t in body["trials"]] == ["NCT00000001", "NCT00000002", "NCT00000003"]
    assert [t["status"] for t in body["trials"]] == ["scored", "scored", "not_scored"]
    assert body["trials"][0]["flag"] == "outside the validated range"
    assert body["trials"][2]["reason"] == "reversal"
    page = client.get("/v1/sponsors/Acme Pharma Inc./portfolio?limit=1&offset=1").json()
    assert [t["nct_id"] for t in page["trials"]] == ["NCT00000002"]
    names = client.get("/v1/sponsors?prefix=Acme").json()["sponsors"]
    assert names == [{"name": "Acme Pharma Inc.", "sponsor_class": "INDUSTRY"}]


def test_areas_count_a_trial_in_every_area_it_belongs_to(make_client: Make) -> None:
    client, _, _ = make_client()
    body = client.get("/v1/areas").json()
    by_slug = {a["slug"]: a["open_trials"] for a in body["areas"]}
    assert "area totals can exceed the number of trials" in body["note"]
    assert sum(by_slug.values()) > body["open_trials"]  # multi-area trials count twice
    assert "mental-disorders" in by_slug
    assert by_slug["mental-disorders"] >= 1
    neo = client.get("/v1/areas/neoplasms/landscape").json()
    assert neo["open_trials"] == by_slug["neoplasms"]
    assert neo["scored"] + sum(neo["not_scored"].values()) == neo["open_trials"]
    assert neo["top_trials"][0]["risk_12m"] >= neo["top_trials"][-1]["risk_12m"]
    assert "current record" in neo["note"]
    cardio = client.get("/v1/areas/cardiovascular/landscape").json()
    assert "NCT00000001" in {t["nct_id"] for t in cardio["top_trials"] + neo["top_trials"]}


def test_the_timeline_holds_points_and_amendment_markers(make_client: Make) -> None:
    client, _, _ = make_client()
    body = client.get("/v1/trials/NCT00000001/timeline").json()
    assert [p["as_of_date"] for p in body["points"]] == ["2026-09-25", "2026-10-02"]
    assert body["points"][1]["reasons"] == ["calendar"]
    assert body["markers"] == [
        {"post_date": "2025-01-10", "changed": ["status"]},
        {"post_date": "2026-03-02", "changed": ["dates", "enrollment"]},
    ]
    assert "go-live" in body["note"]


def test_the_reason_summary_holds_counts_and_the_stops_after_the_labels(make_client: Make) -> None:
    client, _, _ = make_client()
    body = client.get("/v1/reasons/summary").json()
    assert (body["labeled_through"], body["early_stops_after_labels"]) == ("2026-09-25", 42)
    assert sum(body["totals"].values()) == sum(c["n"] for c in body["counts"])
    assert set(body["counts"][0]) == {"month", "reason", "sponsor_class", "n"}
    window = client.get("/v1/reasons/summary?since=2020-03-01&until=2020-05-31").json()
    assert {c["month"] for c in window["counts"]} <= {"2020-03-01", "2020-04-01", "2020-05-01"}


def test_the_model_answer_holds_the_statement_and_todays_shares(make_client: Make) -> None:
    client, _, _ = make_client()
    body = client.get("/v1/model").json()
    statement = json.loads((CFG_ROOT / "docs" / "model_statement.json").read_text(encoding="utf-8"))
    assert body["statement"] == statement
    assert body["model_version"] == "trialpulse-m4/1"
    assert 0 < body["flagged_share"] < 1
    assert body["scored_trials"] == 43
    assert body["not_scored"]["reversal"] >= 1


def test_answers_are_cached_and_the_data_date_is_read_once_per_15_minutes(
    make_client: Make,
) -> None:
    """ADR 0049: a repeated request sends no query, and the data-as-of date is read from the
    database at most once every 15 minutes, whatever the traffic."""
    client, api, clock = make_client()
    client.get("/v1/trials/NCT00000001/risk")
    sent = api.db.queries
    for _ in range(5):
        client.get("/v1/trials/NCT00000001/risk")
        clock.now += 60
    assert api.db.queries == sent  # cached answers, and no read of the date within 15 minutes
    clock.now += 15 * 60
    client.get("/v1/trials/NCT00000001/risk")
    assert api.db.queries == sent + 1  # the date read once; the answer still cached


def test_health_never_touches_the_database(make_client: Make) -> None:
    client, api, _ = make_client()
    for _ in range(3):
        assert client.get("/health").status_code == 200
    assert api.db.queries == 0


def test_the_rate_limit_is_per_forwarded_client_not_per_proxy(make_client: Make) -> None:
    """Behind Spaces' proxy every request comes from the proxy; the client is the address the
    proxy appended. An address the client wrote itself in front of it is ignored."""
    client, _, _ = make_client(rate_limit_per_minute=3)

    def ask(forwarded: str) -> int:
        return client.get("/v1/areas", headers={"X-Forwarded-For": forwarded}).status_code

    assert [ask("203.0.113.5") for _ in range(3)] == [200, 200, 200]
    limited = client.get("/v1/areas", headers={"X-Forwarded-For": "203.0.113.5"})
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
    _envelope(limited.json())
    assert ask("198.51.100.7") == 200  # another client behind the same proxy
    assert ask("198.51.100.99, 203.0.113.5") == 429  # a spoofed first entry does not help
    assert client.get("/health", headers={"X-Forwarded-For": "203.0.113.5"}).status_code == 200


def test_prepared_statements_are_off(seeded: str) -> None:
    engine = engine_for(seeded, 10)
    with engine.connect() as con:
        raw = con.connection.dbapi_connection
        assert raw is not None
        assert raw.prepare_threshold is None
    engine.dispose()


def test_a_database_that_does_not_wake_answers_503_with_the_notice() -> None:
    cfg = CFG.model_copy(update={"retry_wait_seconds": 0, "connect_timeout_seconds": 2})
    waits: list[float] = []
    db = Database(engine_for("postgresql://nobody:x@127.0.0.1:5999/none", 2), 0, waits.append)
    client = TestClient(create_app(Api(db, cfg, now=FakeClock())))
    answer = client.get("/v1/trials/NCT00000001/risk")
    assert answer.status_code == 503
    assert answer.headers["Retry-After"] == str(CFG.unavailable_retry_after_seconds)
    _envelope(answer.json())
    assert waits == [0]  # one retry of the date's read, after the configured wait; no more
    assert client.get("/health").status_code == 200


def test_no_response_holds_a_name_the_serving_tables_do_not_show(make_client: Make) -> None:
    """No investigator, contact or individual sponsor: the only names in any answer are the
    display names of `serving.sponsors`."""
    client, _, _ = make_client()
    text = " ".join(client.get(p).text for p in (*ENDPOINTS, *ERRORS)).lower()
    for word in ("investigator", "contact", "email", "phone", "official", "jane"):
        assert word not in text, word


CFG_ROOT = __import__("trialpulse.config", fromlist=["REPO_ROOT"]).REPO_ROOT

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")
