"""Every page in AppTest against the recorded stubs (ADR 0056): the frame on every page, the
waking, 503 and error cases (ADR 0054), the trial page (ADR 0052), flags (ADR 0055), Model
health with Step 17's monitoring (ADRs 0053 and 0076 to 0080) and the descriptive pages
(ADR 0057)."""

import datetime as dt
import json
from typing import Any

import pytest
from dashboard import client, ui
from streamlit.testing.v1 import AppTest
from stubbed_api import APP, BASE, NAMED, STUBS, StubApi

PAGES = ("overview", "portfolio", "trial", "landscape", "reasons", "model_health")
DAY = "2026-09-25"
SEEDED_NAMES = ("Acme Pharma Inc.", "Northern University Hospital")


def app(page: str | None = None, *, nct: str | None = None, prefix: str | None = None,
        run: bool = True) -> AppTest:  # fmt: skip
    """A page run on its own (`page`) or the whole app through its navigation (None)."""
    path = APP / ("Home.py" if page is None else f"pages/{page}.py")
    at = AppTest.from_file(str(path), default_timeout=60)
    at.secrets["API_URL"] = BASE
    if nct is not None:
        at.query_params["nct"] = nct
    if prefix is not None:
        at.session_state["prefix"] = prefix
    if run:
        at.run()
    return at


def ready(page: str) -> dict[str, Any]:
    """The inputs that make a page show its main content."""
    return {"trial": {"nct": "NCT00000001"}, "portfolio": {"prefix": "acm"}}.get(page, {})


def texts(at: AppTest) -> str:
    """Every visible text of a run, tables included."""
    parts: list[str] = []
    for kind in ("title", "header", "subheader", "markdown", "caption", "info", "warning",
                 "error", "success"):  # fmt: skip
        parts += [str(e.value) for e in getattr(at, kind)]
    parts += [f"{m.label} {m.value}" for m in at.metric]
    parts += [b.label for b in at.button]
    parts += [d.value.to_csv() for d in at.dataframe]
    parts += [c.proto.spec for c in at.get("plotly_chart")]
    return "\n".join(parts)


def chart(at: AppTest, index: int = 0) -> dict[str, Any]:
    spec: dict[str, Any] = json.loads(at.get("plotly_chart")[index].proto.spec)
    return spec


def table(at: AppTest, index: int = 0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = at.dataframe[index].value.to_dict("records")
    return rows


# The frame -----------------------------------------------------------------------------------


@pytest.mark.parametrize("page", PAGES)
def test_every_page_shows_the_notice_attribution_data_date_and_risk_wording(
    stub_api: StubApi, page: str
) -> None:
    at = app(page, **ready(page))
    assert not at.exception
    shown = texts(at)
    assert at.warning[0].value == ui.NOTICE  # on top
    assert at.caption[-1].value == ui.NOTICE  # and at the foot
    assert f"Source: [ClinicalTrials.gov](https://clinicaltrials.gov). {ui.SOURCE_NOTE}" in shown
    assert f"Data as of {DAY} (the date ClinicalTrials.gov processed the data)" in shown
    assert "elevated early-stop risk" in shown
    assert "will fail" not in shown.lower()
    assert not at.error


@pytest.mark.parametrize(
    ("page", "setup"),
    [("trial", {"nct": "NCT00000003"}), ("trial", {"nct": "NCT09999999"}),
     ("trial", {"nct": "bad"}), ("trial", {}), ("portfolio", {"prefix": "ac"}),
     ("portfolio", {"prefix": "zzz"})],
)  # fmt: skip
def test_a_page_that_ends_early_keeps_its_frame(
    stub_api: StubApi, page: str, setup: dict[str, str]
) -> None:
    at = app(page, **setup)
    assert not at.exception
    assert at.warning[0].value == ui.NOTICE
    assert at.caption[-1].value == ui.NOTICE
    assert f"Source: [ClinicalTrials.gov](https://clinicaltrials.gov). {ui.SOURCE_NOTE}" in texts(
        at
    )


@pytest.mark.parametrize("page", PAGES)
def test_every_page_runs_through_the_apps_navigation(stub_api: StubApi, page: str) -> None:
    at = app()
    assert not at.exception
    assert at.title[0].value == "TrialPulse"  # the overview is the default page
    for key, value in ready(page).items():
        if key == "nct":
            at.query_params["nct"] = value
        else:
            at.session_state[key] = value
    at.switch_page(f"pages/{page}.py").run()
    assert not at.exception
    assert at.warning[0].value == ui.NOTICE
    assert f"Data as of {DAY}" in texts(at)


def test_the_dashboards_wording_is_the_apis() -> None:
    for entry in STUBS:
        body = entry["body"]
        assert body["notice"] == ui.NOTICE
        assert body["attribution"]["url"] == ui.SOURCE_URL
        assert body["attribution"]["note"] == f"Source: ClinicalTrials.gov. {ui.SOURCE_NOTE}"


def test_no_page_names_a_person_or_a_sponsor_the_api_does_not_name(stub_api: StubApi) -> None:
    at = app("trial", nct="NCT00000004")  # its sponsor has no display name
    shown = texts(at)
    assert "Name not shown (sponsor class INDUSTRY)" in shown
    assert not any(name in shown for name in SEEDED_NAMES)
    assert "sp-hidden" not in shown


# Waking, 503 and errors (ADR 0054) -------------------------------------------------------------


@pytest.mark.parametrize("page", PAGES)
def test_every_page_waits_out_the_service_starting(stub_api: StubApi, page: str) -> None:
    stub_api.fail("connect", "proxy", "timeout")  # the Space's container starts
    at = app(page, **ready(page))
    assert not at.exception
    assert not at.error
    assert stub_api.slept == [5, 10, 20]
    assert stub_api.waits == [
        f"The API service is starting (it sleeps when idle); retrying in {n} seconds."
        for n in (5, 10, 20)
    ]
    assert stub_api.calls[:4] == ["/health"] * 4  # the service first, before any data call
    assert f"Data as of {DAY}" in texts(at)


@pytest.mark.parametrize("page", PAGES)
def test_every_page_waits_out_the_database_waking(stub_api: StubApi, page: str) -> None:
    stub_api.fail("waking", "waking", on="/v1/")  # the API's own 503 with Retry-After
    at = app(page, **ready(page))
    assert not at.exception
    assert not at.error
    wait = int(NAMED["database waking"]["retry_after"])
    assert stub_api.slept == [wait, wait]
    assert stub_api.waits == [f"The database is waking up; retrying in {wait} seconds."] * 2
    assert f"Data as of {DAY}" in texts(at)


def test_a_rate_limited_answer_is_waited_out_for_its_retry_after(stub_api: StubApi) -> None:
    stub_api.fail("busy", on="/v1/")
    at = app("model_health")
    assert not at.exception
    assert stub_api.slept == [int(NAMED["rate limited"]["retry_after"])]
    assert stub_api.waits[0].startswith("The API is busy (too many requests)")


@pytest.mark.parametrize("page", PAGES)
def test_every_page_gives_up_in_plain_words_and_keeps_its_frame(
    stub_api: StubApi, page: str
) -> None:
    stub_api.always = "connect"
    at = app(page, **ready(page))
    assert not at.exception  # no traceback
    assert at.error[0].value.startswith("The API did not answer within about 3 minutes.")
    assert at.button(key="try_again").label == "Try again"
    assert sum(stub_api.slept) <= 180
    shown = texts(at)
    assert at.warning[0].value == ui.NOTICE
    assert f"Source: [ClinicalTrials.gov](https://clinicaltrials.gov). {ui.SOURCE_NOTE}" in shown
    assert "Data as of: not known yet (the API has not answered)." in shown
    stub_api.always = None  # the API is up again: "Try again" shows the page
    at.button(key="try_again").click().run()
    assert not at.error
    assert f"Data as of {DAY}" in texts(at)


@pytest.mark.parametrize("page", PAGES)
def test_a_page_that_is_not_the_apis_is_waited_out_never_shown_or_kept(
    stub_api: StubApi, page: str
) -> None:
    """Review finding 9: a 200 page of the platform reached the page (a KeyError) and was kept
    for 15 minutes. It is now waited out like the service starting, and never kept."""
    stub_api.fail("platform", "platform", on="/v1/")
    at = app(page, **ready(page))
    assert not at.exception
    assert not at.error
    assert stub_api.waits == [client.NOT_API.format(n=n) for n in (5, 10)]
    assert f"Data as of {DAY}" in texts(at)


def test_an_address_that_only_answers_pages_gives_up_and_keeps_nothing(stub_api: StubApi) -> None:
    stub_api.always = "platform"
    at = app("trial", nct="NCT00000001")
    assert not at.exception  # no KeyError from a page that is not the API's
    assert at.error[0].value == client.GAVE_UP_NOT_API
    assert at.warning[0].value == ui.NOTICE
    stub_api.always = None  # the API answers now: nothing of the platform's page was kept
    before = len(stub_api.data_calls())
    again = app("trial", nct="NCT00000001")
    assert not again.exception
    assert len(stub_api.data_calls()) > before
    assert "Early-stop risk within 12 months" in texts(again)


def test_the_database_that_never_wakes_gives_up_within_the_budget(stub_api: StubApi) -> None:
    stub_api.always = "waking"
    at = app("overview")
    assert at.error[0].value.startswith("The API did not answer")
    assert stub_api.calls[0] == "/health"
    assert sum(stub_api.slept) <= 180
    assert all(w.startswith("The service") is False for w in stub_api.waits)


@pytest.mark.parametrize("page", PAGES)
def test_an_error_answer_is_shown_in_plain_words(stub_api: StubApi, page: str) -> None:
    stub_api.fail("error", on="/v1/")
    at = app(page, **ready(page))
    assert not at.exception
    assert at.error[0].value == "The API answered with an error (status 500). Try again later."
    assert stub_api.slept == []  # a 500 is not a wake: no retry
    assert at.warning[0].value == ui.NOTICE


HEALTH_CALLS = ["/v1/model", "/v1/monitoring/performance", "/v1/monitoring/drift",
                "/v1/monitoring/promotions"]  # fmt: skip


def test_answers_are_kept_for_15_minutes_and_failures_never(stub_api: StubApi) -> None:
    app("model_health")
    assert stub_api.data_calls() == HEALTH_CALLS
    app("model_health")  # another viewer within 15 minutes: no call at all
    assert stub_api.data_calls() == HEALTH_CALLS
    stub_api.now += 15 * 60 + 1
    app("model_health")
    assert stub_api.data_calls() == HEALTH_CALLS * 2

    stub_api.calls.clear()
    stub_api.fail("error", on="/v1/trials/")
    first = app("trial", nct="NCT00000002")
    assert first.error
    second = app("trial", nct="NCT00000002")  # the failure was not kept
    assert not second.error
    assert stub_api.data_calls().count("/v1/trials/NCT00000002/risk") == 2


# Trial detail (ADR 0052) ---------------------------------------------------------------------


def test_a_flagged_trial_shows_its_score_badge_note_drivers_and_timeline(
    stub_api: StubApi,
) -> None:
    at = app("trial", nct="NCT00000001")
    risk = next(e for e in STUBS if e["path"] == "/v1/trials/NCT00000001/risk")["body"]
    score = risk["score"]
    assert [m.value for m in at.metric] == [f"{score['risk_12m']:.1%}", f"{score['risk_24m']:.1%}"]
    shown = texts(at)
    assert ui.FLAG_BADGE in shown
    assert score["flag_note"] in shown
    assert "(https://clinicaltrials.gov/study/NCT00000001)" in shown
    assert "not why a trial stops" in shown
    assert [t.label for t in at.tabs] == ["12 months", "24 months"]
    for i, horizon in enumerate(("12", "24")):
        rows = table(at, i)
        assert [r["Feature"] for r in rows] == [d["feature"] for d in score["drivers"][horizon]]
        assert all(r["Effect"].endswith("the odds of an early stop") for r in rows)
    spec = chart(at)
    names = [t["name"] for t in spec["data"]]
    assert names == ["Early-stop risk, 12 months", "Early-stop risk, 24 months",
                     "Outside the validated range", "Registry update"]  # fmt: skip
    assert spec["data"][3]["x"] == ["2025-01-10", "2026-03-02"]
    assert spec["data"][3]["text"] == ["Changed: status", "Changed: dates, enrollment"]
    shade = spec["layout"]["shapes"][0]
    assert shade["x1"] == min(spec["data"][0]["x"])  # the shaded span ends at the first point
    assert shade["x0"] < "2025-01-10"
    assert "The first point is the trial's score at the data cutoff" in shown


def test_an_unflagged_trial_has_no_badge(stub_api: StubApi) -> None:
    at = app("trial", nct="NCT00000002")
    shown = texts(at)
    assert ui.FLAG_BADGE not in shown
    assert "Outside the validated range" not in [t["name"] for t in chart(at)["data"]]


def test_a_trial_without_a_score_shows_its_reason_and_no_risk(stub_api: StubApi) -> None:
    at = app("trial", nct="NCT00000003")  # a reversal, with nothing on its timeline
    shown = texts(at)
    assert not at.metric
    assert not at.dataframe
    assert "No current score" in shown
    risk = next(e for e in STUBS if e["path"] == "/v1/trials/NCT00000003/risk")["body"]
    assert f"**Reason:** {risk['not_scored']['reason_text']} (code `reversal`)" in shown
    assert "no served points and no registry updates" in shown
    assert "%" not in "".join(str(m.value) for m in at.markdown)


def test_a_trial_without_a_score_keeps_its_earlier_points(stub_api: StubApi) -> None:
    at = app("trial", nct="NCT00000005")  # beyond 120 months, scored once before
    assert not at.metric
    assert "Earlier scores: this trial has no current score." in texts(at)
    traces = chart(at)["data"]
    assert [t["name"] for t in traces[:2]] == [
        "Early-stop risk, 12 months (earlier scores)",
        "Early-stop risk, 24 months (earlier scores)",
    ]
    points = next(e for e in STUBS if e["path"] == "/v1/trials/NCT00000005/timeline")["body"]
    assert points["points"]  # the recorded earlier score
    for trace, horizon in zip(traces[:2], ("12", "24"), strict=True):  # drawn, not just named
        assert trace["x"] == [p["as_of_date"] for p in points["points"]]
        assert trace["y"] == [p[f"risk_{horizon}m"] for p in points["points"]]


def test_an_unknown_or_malformed_registry_id(stub_api: StubApi) -> None:
    at = app("trial", nct="NCT09999999")
    assert "TrialPulse serves no trial with the id NCT09999999" in texts(at)
    assert "(https://clinicaltrials.gov/study/NCT09999999)" in texts(at)
    assert stub_api.data_calls() == ["/v1/trials/NCT09999999/risk"]

    stub_api.calls.clear()
    bad = app("trial", nct="12345")
    assert bad.error[0].value.startswith("A registry id is NCT followed by 8 digits")
    assert stub_api.calls == []
    empty = app("trial")
    assert empty.info[0].value.startswith("Enter a registry id")
    assert stub_api.calls == []


def test_a_typed_registry_id_is_read_in_capitals(stub_api: StubApi) -> None:
    at = app("trial", run=False)
    at.run()
    at.text_input(key="nct_input").input(" nct00000002 ").run()
    assert "/v1/trials/NCT00000002/risk" in stub_api.calls
    assert at.query_params["nct"] == ["NCT00000002"]


# Portfolio and flags (ADR 0055) --------------------------------------------------------------


def test_a_portfolio_ranks_by_risk_and_keeps_flagged_trials_in_place(stub_api: StubApi) -> None:
    at = app("portfolio", prefix="acm")
    assert at.selectbox(key="sponsor").value == "Acme Pharma Inc. (INDUSTRY)"
    rows = table(at)
    assert [r["Registry id"] for r in rows] == [
        "https://clinicaltrials.gov/study/NCT00000001",
        "https://clinicaltrials.gov/study/NCT00000002",
        "https://clinicaltrials.gov/study/NCT00000003",
    ]
    assert [r["Flag"] for r in rows] == ["Outside the validated range", "", ""]
    assert rows[0]["Early-stop risk, 12 months"] == "31.0%"
    assert [r["Rank"] for r in rows] == ["1", "2", ""]  # a trial without a score has no rank
    assert rows[2]["Not scored"] == "status reversal"
    assert rows[2]["Early-stop risk, 12 months"] == ""
    assert rows[0]["Detail"] == "trial?nct=NCT00000001"
    assert ui.FLAG_CAPTION in texts(at)
    hide = at.toggle(key="hide_flagged_portfolio")
    assert hide.value is False  # the switch starts off
    hide.set_value(True).run()
    assert [r["Registry id"][-11:] for r in table(at)] == ["NCT00000002", "NCT00000003"]


def test_a_short_or_unknown_sponsor_prefix(stub_api: StubApi) -> None:
    at = app("portfolio", prefix="ac")
    assert at.info[0].value == "Type at least 3 characters of the sponsor's name."
    assert stub_api.calls == []
    at = app("portfolio", prefix="zzz")
    assert "No sponsor with a public portfolio" in texts(at)
    assert not at.dataframe


# Landscape and Why trials stop (ADR 0057) ----------------------------------------------------


def test_landscape_shows_the_apis_statements_on_areas(stub_api: StubApi) -> None:
    at = app("landscape")
    note = next(e for e in STUBS if e["path"] == "/v1/areas")["body"]["note"]
    assert "no score reads them" in note
    assert "can exceed the number of trials" in note
    assert at.info[0].value == note
    at.selectbox(key="area").set_value("Neoplasms").run()
    assert at.subheader[0].value == "Neoplasms"
    landscape = next(e for e in STUBS if e["path"] == "/v1/areas/neoplasms/landscape")["body"]
    assert landscape["note"] in [c.value for c in at.caption]
    rows = table(at)
    assert [r["Registry id"][-11:] for r in rows] == [t["nct_id"] for t in landscape["top_trials"]]
    assert "Outside the validated range" in [r["Flag"] for r in rows]
    assert ui.FLAG_CAPTION in texts(at)
    bars = chart(at)["data"][0]
    listing = next(e for e in STUBS if e["path"] == "/v1/areas")["body"]["areas"]
    assert sorted(bars["x"]) == sorted(a["open_trials"] for a in listing)


def test_why_trials_stop_is_descriptive(stub_api: StubApi) -> None:
    at = app("reasons")
    assert stub_api.data_calls() == ["/v1/reasons/summary"]  # no score endpoint
    shown = texts(at)
    assert "do not predict the reason a trial stops" in shown
    assert "H3, falsified" in shown
    assert not at.metric
    assert "Early-stop risk" not in shown
    summary = next(e for e in STUBS if e["path"] == "/v1/reasons/summary")["body"]
    assert f"{summary['early_stops_after_labels']:,} early stops recorded after" in shown
    totals = {r["Reason"]: r["Early stops"] for r in table(at)}
    assert sum(totals.values()) == sum(summary["totals"].values())
    at.multiselect(key="classes").set_value(["INDUSTRY"]).run()
    industry = sum(c["n"] for c in summary["counts"] if c["sponsor_class"] == "INDUSTRY")
    assert sum(r["Early stops"] for r in table(at)) == industry
    years = chart(at)["data"][0]["x"]
    assert years == sorted(years)
    assert all(len(y) == 4 for y in years)


# Model health (ADR 0053) ---------------------------------------------------------------------


def test_model_health_with_step_17s_monitoring(
    stub_api: StubApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ui, "today", lambda: dt.date(2026, 9, 27))
    at = app("model_health")
    shown = texts(at)
    assert (at.metric[0].label, at.metric[0].value) == ("Data as of", DAY)
    assert at.success[0].value == "Within the 3-day freshness target."
    model = next(e for e in STUBS if e["path"] == "/v1/model")["body"]
    statement = model["statement"]
    auc = statement["registered_results"]["origins"]["2018"]["auc_12"]
    value = f"{auc['value']:.4f} ({auc['low']:.4f} to {auc['high']:.4f})"
    row = {"Origin": "2018", "Metric": "AUC", "Horizon": "12 months", "Value": value}
    assert row in table(at)
    for h in statement["falsified_hypotheses"]:
        assert f"**{h['id']}**" in shown
    for item in statement["known_limitations"]:
        assert item in shown
    assert f"{model['flagged_share']:.1%}" in shown
    assert [h.value for h in at.header] == ["Freshness", "Served model", "Today's scope",
                                            "Graded performance", "Drift",
                                            "Retraining and promotion"]  # fmt: skip
    assert "not a registered result" in shown  # the replay's label (ADR 0078)
    assert "graded on their follow-up so far" in shown  # live grades are partial-horizon
    assert len(at.get("plotly_chart")) == 2
    assert ("Features drifted", "50%") in [(m.label, m.value) for m in at.metric]
    assert any(r.get("Promoted") == "no"
               for i in range(len(at.dataframe)) for r in table(at, i))  # fmt: skip
    assert not [i for i in at.info if "Step 17" in i.value]  # no placeholder is left


def test_model_health_warns_when_the_data_is_older_than_3_days(
    stub_api: StubApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ui, "today", lambda: dt.date(2026, 9, 29))
    at = app("model_health")
    assert at.warning[1].value.startswith("The data is 4 days old, more than the 3 days")
    monkeypatch.setattr(ui, "today", lambda: dt.date(2026, 9, 28))
    assert not app("model_health").warning[1:]


def test_model_health_without_a_statement_or_a_data_date(stub_api: StubApi) -> None:
    stub_api.override("/v1/model", statement=None, data_as_of=None, model_version=None)
    for path in HEALTH_CALLS[1:]:
        stub_api.override(path, data_as_of=None)
    at = app("model_health")
    assert not at.exception
    shown = texts(at)
    assert "The model statement is not loaded yet." in shown
    assert "The data-as-of date is not known" in shown
    assert "Data as of: not known yet" in shown
