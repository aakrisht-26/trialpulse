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
    assert "the first point is the trial's score at the data cutoff" in body["note"].lower()


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


def test_behind_cloudflare_the_key_is_cf_connecting_ip(make_client: Make) -> None:
    """ADR 0065, measured: Render's X-Forwarded-For ends in hops that change per request, and
    a client can prepend entries; Cloudflare's CF-Connecting-IP is the same for one client."""
    client, _, _ = make_client(rate_limit_per_minute=3, client_ip_header="cf-connecting-ip")

    def ask(cf: str | None, forwarded: str) -> int:
        headers = {"X-Forwarded-For": forwarded} | ({"CF-Connecting-IP": cf} if cf else {})
        return client.get("/v1/areas", headers=headers).status_code

    hops = ["203.0.113.5, 10.1.0.1, 10.2.0.1", "203.0.113.5, 10.1.0.2, 10.2.0.9",
            "198.51.100.1, 203.0.113.5, 10.1.0.3, 10.2.0.4"]  # fmt: skip
    assert [ask("203.0.113.5", h) for h in hops] == [200, 200, 200]  # (a) one key
    assert ask("203.0.113.5", "198.51.100.2, 203.0.113.5, 10.1.0.5, 10.2.0.6") == 429  # (b)
    assert ask("192.0.2.44", "192.0.2.44, 10.1.0.1, 10.2.0.1") == 200  # (c) another network
    assert ask(None, "198.51.100.3, 10.9.9.9") == 200  # no header: the X-Forwarded-For rule


@pytest.mark.parametrize("on", [True, False])
def test_the_forwarded_shape_is_logged_only_when_turned_on(
    make_client: Make, caplog: pytest.LogCaptureFixture, on: bool
) -> None:
    client, _, _ = make_client(forwarded_diagnostic=on)
    headers = {"X-Forwarded-For": "203.0.113.5, 198.51.100.7", "CF-Connecting-IP": "203.0.113.5"}
    with caplog.at_level("INFO", logger="trialpulse.api"):
        assert client.get("/v1/areas", headers=headers).status_code == 200
        assert client.get("/health", headers=headers).status_code == 200
    lines = [r.getMessage() for r in caplog.records if "forwarded shape" in r.getMessage()]
    assert len(lines) == (1 if on else 0)  # /v1/ paths only
    assert not any("203.0.113.5" in m or "198.51.100.7" in m for m in caplog.messages)


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


# Database errors that waiting will not fix (ADR 0061, review findings 7 and 8) ---------------

psycopg = pytest.importorskip("psycopg")


def _client_for(url: str, waits: list[float] | None = None) -> tuple[TestClient, Database]:
    cfg = CFG.model_copy(update={"retry_wait_seconds": 0})
    db = Database(engine_for(url, 5), 0, (waits if waits is not None else []).append)
    return TestClient(create_app(Api(db, cfg, now=FakeClock())),
                      raise_server_exceptions=False), db  # fmt: skip


def _refused(answer: Any, caplog: pytest.LogCaptureFixture, state: str) -> None:
    from trialpulse.api.main import REFUSED

    assert answer.status_code == 500
    assert answer.headers["content-type"].startswith("application/json")
    assert "retry-after" not in answer.headers
    body = answer.json()
    _envelope(body)
    assert body["detail"] == REFUSED
    assert f"SQLSTATE {state}" in caplog.text


def _role(seeded: str, grants: tuple[str, ...]) -> tuple[str, str]:
    import uuid

    role = f"api_err_{uuid.uuid4().hex[:8]}"
    database = seeded.rsplit("/", 1)[1]
    with psycopg.connect(seeded, autocommit=True) as con:
        con.execute(f"CREATE ROLE {role} LOGIN PASSWORD 'pw_{role}'")
        con.execute(f"GRANT CONNECT ON DATABASE {database} TO {role}")
        con.execute(f"GRANT USAGE ON SCHEMA serving TO {role}")
        for table in grants:
            con.execute(f"GRANT SELECT ON serving.{table} TO {role}")
    user = seeded.split("://", 1)[1].split("@", 1)[0]
    return role, seeded.replace(f"{user}@", f"{role}:pw_{role}@", 1)


def _drop(seeded: str, role: str) -> None:
    with psycopg.connect(seeded, autocommit=True) as con:
        con.execute(f"DROP OWNED BY {role}")
        con.execute(f"DROP ROLE {role}")


def test_a_wrong_password_answers_500_not_waking(
    seeded: str, caplog: pytest.LogCaptureFixture
) -> None:
    user = seeded.split("://", 1)[1].split("@", 1)[0]
    waits: list[float] = []
    client, _ = _client_for(seeded.replace(f"{user}@", f"{user.split(':')[0]}:wrong@", 1),
                            waits)  # fmt: skip
    with caplog.at_level("ERROR", logger="trialpulse.api"):
        _refused(client.get("/v1/model"), caplog, "28P01")
    assert waits == []  # not retried


def test_a_missing_grant_answers_500_and_is_not_retried(
    seeded: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Review fault R05: every database error was retried and answered as the database
    waking up. A missing grant on a score table is answered at once with a JSON 500."""
    role, url = _role(seeded, ("data_status",))
    try:
        waits: list[float] = []
        client, db = _client_for(url, waits)
        with caplog.at_level("ERROR", logger="trialpulse.api"):
            _refused(client.get("/v1/trials/NCT00000001/risk"), caplog, "42501")
        assert waits == []
        assert db.queries == 2  # the data date, then one attempt of the query: no retry
        db.engine.dispose()
    finally:
        _drop(seeded, role)


def test_an_unreadable_data_date_answers_500_not_waking(
    seeded: str, caplog: pytest.LogCaptureFixture
) -> None:
    role, url = _role(seeded, ("trial_scores_latest",))
    try:
        client, db = _client_for(url)
        with caplog.at_level("ERROR", logger="trialpulse.api"):
            _refused(client.get("/v1/model"), caplog, "42501")
        db.engine.dispose()
    finally:
        _drop(seeded, role)


def test_a_missing_table_answers_500(seeded: str, caplog: pytest.LogCaptureFixture) -> None:
    with psycopg.connect(seeded, autocommit=True) as con:
        con.execute("DROP TABLE serving.reason_counts")
    client, _ = _client_for(seeded)
    with caplog.at_level("ERROR", logger="trialpulse.api"):
        _refused(client.get("/v1/reasons/summary"), caplog, "42P01")


def test_an_unhandled_error_is_json_with_the_notice(
    make_client: Make, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from trialpulse.api import areas
    from trialpulse.api.main import INTERNAL

    def broken(codes: Any) -> Any:
        raise KeyError("a value that must not be logged")

    monkeypatch.setattr(areas, "slugs", broken)
    client, _, _ = make_client()
    client = TestClient(client.app, raise_server_exceptions=False)
    with caplog.at_level("ERROR", logger="trialpulse.api"):
        answer = client.get("/v1/trials/NCT00000001/risk")
    assert answer.status_code == 500
    assert answer.headers["content-type"].startswith("application/json")
    _envelope(answer.json())
    assert answer.json()["detail"] == INTERNAL
    assert "unhandled KeyError" in caplog.text
    assert "must not be logged" not in caplog.text


@pytest.mark.parametrize(
    ("text", "state", "waking"),
    [
        ('connection to server failed: FATAL:  password authentication failed for user "x"',
         "28P01", False),
        ('connection to server failed: FATAL:  database "x" does not exist', "3D000", False),
        ('connection to server failed: FATAL:  role "x" does not exist', "28000", False),
        ('connection to server failed: FATAL:  permission denied for database "x"', "42501",
         False),
        ("connection to server failed: FATAL:  sorry, too many clients already", "", True),
        ("connection to server failed: FATAL:  the database system is starting up", "", True),
        ("connection to server failed: Connection refused", "", True),
        ("failed to resolve host 'x': Name or service not known", "", True),
        ("connection timeout expired", "", True),
    ],
)  # fmt: skip
def test_connect_errors_are_told_apart_by_the_servers_answer(
    text: str, state: str, waking: bool
) -> None:
    from sqlalchemy.exc import OperationalError

    from trialpulse.api.db import sqlstate, unreachable

    exc = OperationalError("connect", {}, psycopg.OperationalError(text))
    assert sqlstate(exc) == state
    assert unreachable(exc) is waking


@pytest.mark.parametrize(
    ("error", "waking"),
    [
        (psycopg.errors.ConnectionTimeout, True),
        (psycopg.OperationalError, True),
        (psycopg.InterfaceError, True),
        (psycopg.ProgrammingError, False),
    ],
)
def test_driver_errors_without_a_sqlstate_are_classed_by_kind_not_by_name(
    error: type[psycopg.Error], waking: bool
) -> None:
    """psycopg raises ConnectionTimeout, a subclass of OperationalError, when the connect
    timeout fires before the server's refusal arrives (about 2 s on Windows). It must answer
    503 "waking up" like its parent class, whatever the timing of the machine running this."""
    from sqlalchemy.exc import OperationalError

    from trialpulse.api.db import sqlstate, unreachable

    exc = OperationalError("connect", {}, error("connection timeout expired"))
    assert sqlstate(exc) == ""
    assert unreachable(exc) is waking


def test_no_endpoint_names_a_trial_outside_the_served_set(make_client: Make, seeded: str) -> None:
    """ADR 0070: display rows are kept for served trials only, so no answer may need one of
    another trial. A trial with a display row, a history row and markers but neither a score
    nor a reason (it left the served set) is planted under a named sponsor and an area, and
    every GET route is called: its title appears nowhere, its id appears only in its own
    timeline (which never reads display rows), and its risk is not found. The dashboard
    shows only the API's answers (tests/app/test_imports.py)."""
    import datetime as dt

    import psycopg

    from trialpulse.api import areas as area_list

    gone, title = "NCT09999999", "A planted title of a trial that is not served"
    with psycopg.connect(seeded) as con:
        con.execute(
            """INSERT INTO serving.trial_display (nct_id, brief_title, sponsor_id,
              sponsor_class, areas) VALUES (%s, %s, 'sp-acme', 'INDUSTRY', '{2}')""",
            (gone, title),
        )
        con.execute(
            """INSERT INTO serving.trial_score_history (nct_id, as_of_date, model_version,
              risk_12m, risk_24m, reasons, landmark_index, true_landmark_index, content_hash,
              flagged) VALUES (%s, %s, 'm/1', 0.2, 0.3, '{first_score}', 1, 1, %s, false)""",
            (gone, dt.date(2026, 9, 25), b"\x07" * 32),
        )
        con.commit()
    client, _, _ = make_client()
    trial_ids = (gone, "NCT00000001", "NCT00000003")
    slugs = (*(a.slug for a in area_list.AREAS), area_list.NOT_CLASSIFIED_SLUG)
    values = {"nct_id": trial_ids, "name": ("Acme Pharma Inc.",), "area": slugs}
    routes = [
        r
        for r in client.app.routes  # type: ignore[attr-defined]
        if "GET" in getattr(r, "methods", set()) and r.path.startswith(("/v1", "/health"))
    ]
    assert {r.path for r in routes} >= {
        "/v1/trials/{nct_id}/risk",
        "/v1/areas",
        "/v1/sponsors/{name}/portfolio",
        "/v1/model",
    }
    called = 0
    for route in routes:
        params = [p for p in values if "{" + p + "}" in route.path]
        choices = [(route.path, {})]
        for p in params:
            choices = [
                (path.replace("{" + p + "}", v), {**k, p: v})
                for path, k in choices
                for v in values[p]
            ]
        for path, chosen in choices:
            query = "?prefix=acm" if route.path == "/v1/sponsors" else ""
            answer = client.get(path + query)
            called += 1
            text = answer.text
            assert title not in text, path
            if chosen.get("nct_id") == gone and path.endswith("/timeline"):
                continue  # its own history, asked for by id
            if chosen.get("nct_id") == gone:
                assert answer.status_code == 404, path
            assert gone not in text, path
    assert called > len(routes)


def test_the_monitoring_answers_label_replay_and_live(make_client: Make) -> None:
    """ADRs 0076 to 0080: grades by source, the latest drift week and the gate's decisions,
    through the serving views."""
    client, _, _ = make_client()
    grades = client.get("/v1/monitoring/performance").json()
    _envelope(grades)
    assert {g["source"] for g in grades["grades"]} == {"live", "replay"}
    assert "not a registered result" in grades["note"]
    live = [g for g in grades["grades"] if g["source"] == "live"]
    assert all(g["auc"] is None and not g["matured"] for g in live)
    drift = client.get("/v1/monitoring/drift").json()
    _envelope(drift)
    assert drift["features_drifted_share"] == 0.5
    assert {c["kind"] for c in drift["columns"]} == {"feature", "score"}
    gate = client.get("/v1/monitoring/promotions").json()
    _envelope(gate)
    assert gate["decisions"][0]["promoted"] is False
    assert gate["decisions"][0]["reason"].startswith("not promoted")
