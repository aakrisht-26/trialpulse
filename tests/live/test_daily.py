"""The daily job end to end on a real Postgres (ADR 0038, layer 1): seeded state, the recorded
API pages, the real ingest, sponsor pass, features and publication, with a stand-in scorer
(the champion's bundle is not in CI). Skipped when no Postgres answers at DATABASE_URL."""

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
import respx
from tenacity import wait_none

from trialpulse.ingest.ctgov_api import STUDIES_URL, VERSION_URL, ApiClient
from trialpulse.live import daily, store
from trialpulse.live.seed import seed
from trialpulse.live.sponsors import SponsorFile

from .conftest import CFG, DECISIONS, KEEP, RULES, SECRET, compact_history

psycopg = pytest.importorskip("psycopg")

FIXTURES = Path(__file__).parent / "fixtures"
RELEASE = dt.date(2026, 10, 20)
DAY = dt.date(2026, 10, 30)  # the recorded dataTimestamp


@pytest.fixture
def dsn(scratch: str) -> str:
    """The libpq form of the scratch database's URL (tests/conftest.py)."""
    return scratch.replace("postgresql+psycopg://", "postgresql://", 1)


def _bootstrap(dsn: str) -> None:
    aliases = (CFG.features.sponsor_alias_min_trials, CFG.features.sponsor_alias_wait_days)
    seeded = seed(compact_history(120), RELEASE, RULES, KEEP, *aliases)
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        store.load_bootstrap(cur, seeded.wides, seeded.sponsors, seeded.markers, RELEASE)
        con.commit()


def _scorer(scored: pd.DataFrame, features: pd.DataFrame, day: dt.date) -> pd.DataFrame:
    """A stand-in for the champion: risks from the content hash, one driver per horizon."""
    merged = scored.merge(features[["trial_id", "true_index", "n_versions"]],
                          on=["trial_id", "true_index"], how="left")  # fmt: skip
    risk = merged["content_hash"].map(lambda h: int(str(h)[:4], 16) % 997 / 2000)
    drivers = [json.dumps({"12": [{"feature": "n_versions", "value": str(n), "contribution": 0.1,
                                   "direction": "raises"}], "24": []})
               for n in merged["n_versions"]]  # fmt: skip
    return pd.DataFrame(
        {
            "nct_id": merged["trial_id"].astype(str), "as_of_date": pd.Timestamp(day),
            "true_index": merged["true_index"], "landmark_index": merged["k_star"],
            "flagged": merged["flagged"].astype(bool), "risk_12m": risk, "risk_24m": risk * 1.5,
            "content_hash": merged["content_hash"], "model_name": "stand-in",
            "model_version": "stand-in/1", "drivers": drivers,
        }
    )  # fmt: skip


MODEL = daily.Model("stand-in/1", _scorer, DECISIONS)


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _api() -> ApiClient:
    respx.get(VERSION_URL).mock(return_value=httpx.Response(200, json=_load("version.json")))
    respx.get(STUDIES_URL).mock(
        side_effect=lambda request: httpx.Response(
            200,
            json=_load("delta_page_2.json" if "pageToken" in request.url.params
                       else "delta_page_1.json"),
        )
    )  # fmt: skip
    return ApiClient(httpx.Client(), per_minute=60_000, wait=wait_none(), sleep=lambda _: None)


TABLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "live.trial_state": ("nct_id", ("updated_at", "run_id")),
    "live.study_versions": ("nct_id, effective_date", ("ingested_at", "run_id")),
    "live.outcome_events": ("nct_id, effective_date, to_status", ("event_id", "detected_at",
                                                                  "run_id")),
    "live.quarantine": ("payload_hash", ("quarantine_id", "quarantined_at", "run_id")),
    "serving.trial_scores_latest": ("nct_id", ("scored_at",)),
    "serving.trial_score_history": ("nct_id, as_of_date", ("history_id", "scored_at", "run_id")),
    "serving.trials_not_scored": ("nct_id", ("recorded_at",)),
    "serving.amendment_markers": ("nct_id", ("updated_at",)),
    "monitoring.score_files": ("month", ("written_at", "run_id")),
    "serving.trial_display": ("nct_id", ("updated_at",)),
    "serving.sponsors": ("sponsor_id", ("updated_at",)),
    "serving.data_status": ("status_id", ("updated_at",)),
}  # fmt: skip


DISPLAY = ("serving.trial_display", "serving.sponsors")


def _digest(dsn: str, skip: tuple[str, ...] = ()) -> dict[str, str]:
    """Every live and serving table's content, without timestamps and run ids, and the
    sponsor file without the id of the run that wrote it."""
    out = {}
    with psycopg.connect(dsn) as con:
        for table, (order, dropped) in TABLES.items():
            if table in skip:
                continue
            minus = "".join(f" - '{c}'" for c in dropped)
            rows = con.execute(f"SELECT to_jsonb(t){minus} FROM {table} t ORDER BY {order}")
            digest = hashlib.sha256()
            n = 0
            for (row,) in rows.fetchall():
                digest.update(json.dumps(row, sort_keys=True).encode())
                n += 1
            out[table] = f"{n}:{digest.hexdigest()[:16]}"
        row = con.execute("SELECT content FROM live.sponsor_file").fetchone()
        sponsors = SponsorFile.from_bytes(bytes(row[0]))
        sponsors.last_run_id = None
        out["live.sponsor_file"] = hashlib.sha256(sponsors.to_bytes()).hexdigest()[:16]
    return out


@respx.mock
def test_a_daily_run_publishes_and_a_second_run_changes_nothing(dsn: str) -> None:
    _bootstrap(dsn)
    report = daily.run_daily(CFG, dsn, SECRET, _api(), MODEL)
    assert (report.status, report.day, report.exit_code) == ("succeeded", DAY, 0)
    assert report.counts["records_fetched"] == 8
    assert report.counts["details"]["actions"] == {"new_trial": 8}
    with psycopg.connect(dsn) as con:
        latest = con.execute("SELECT count(*) FROM serving.trial_scores_latest").fetchone()[0]
        kinds = con.execute(
            "SELECT run_kind, status, data_timestamp FROM monitoring.pipeline_runs "
            "WHERE job = 'daily'"
        ).fetchall()
        through = con.execute("SELECT through_day FROM live.sponsor_file").fetchone()[0]
        files = con.execute("SELECT count(*) FROM monitoring.score_files").fetchone()[0]
    assert latest > 0
    assert kinds == [("full", "succeeded", DAY)]
    assert through == DAY - dt.timedelta(days=1)
    assert files == 1
    with psycopg.connect(dsn) as con:  # the API's rows (ADRs 0049 and 0051)
        live = {r[0] for r in con.execute("SELECT nct_id FROM live.trial_state")}
        shown = con.execute("SELECT nct_id, brief_title FROM serving.trial_display").fetchall()
        names = [r[0] for r in con.execute("SELECT display_name FROM serving.sponsors")]
        status = con.execute("SELECT data_as_of, model_version FROM serving.data_status").fetchone()
    pages = ("delta_page_1.json", "delta_page_2.json")
    pulled = {s["protocolSection"]["identificationModule"]["nctId"]
              for p in pages for s in _load(p)["studies"]}  # fmt: skip
    assert {n for n, _ in shown} == live & pulled
    assert all(title for _, title in shown)
    assert "Example Sponsor Organization" not in names  # three plain words of class OTHER
    assert names
    assert status == (DAY, "stand-in/1")
    before = _digest(dsn)
    # The second cron: the day already succeeded, so nothing runs.
    assert daily.run_daily(CFG, dsn, SECRET, _api(), MODEL).status == "noop"
    # Forced (the idempotence check of Step 14): it runs, and changes nothing.
    again = daily.run_daily(CFG, dsn, SECRET, _api(), MODEL, force=True)
    assert again.status == "succeeded"
    assert again.counts["details"]["actions"] == {"overlap": 8}
    assert again.counts["trials_rescored"] == 0
    assert _digest(dsn) == before
    _assert_served_is_the_population(dsn, DAY)


@respx.mock
@pytest.mark.parametrize("stage", [daily.INGEST, daily.SPONSORS])
def test_a_run_that_fails_after_a_stage_heals_on_the_rerun(dsn: str, stage: str) -> None:
    """The crash matrix: fail after each committed stage, rerun, and the tables equal those
    of an uninterrupted run on another database."""
    _bootstrap(dsn)
    with pytest.raises(daily.JobFailedError, match=f"{stage}: InjectedFailureError: {stage}"):
        daily.run_daily(CFG, dsn, SECRET, _api(), MODEL, fail_after=stage)
    with psycopg.connect(dsn) as con:
        status, error = con.execute(
            "SELECT status, error FROM monitoring.pipeline_runs WHERE job = 'daily'"
        ).fetchone()
    assert status == "failed"
    assert error == f"{stage}: InjectedFailureError"
    healed = daily.run_daily(CFG, dsn, SECRET, _api(), MODEL)
    assert healed.status == "succeeded"
    crashed = _digest(dsn)
    with psycopg.connect(dsn) as con, con.cursor() as cur:  # an uninterrupted run, afresh
        for table in (*TABLES, "live.sponsor_file", "monitoring.pipeline_runs"):
            cur.execute(f"DELETE FROM {table}")
        con.commit()
    _bootstrap(dsn)
    assert daily.run_daily(CFG, dsn, SECRET, _api(), MODEL).status == "succeeded"
    assert _digest(dsn) == crashed


@respx.mock
def test_quarantined_records_end_the_run_in_failure_after_publishing(dsn: str) -> None:
    _bootstrap(dsn)
    page = _load("delta_page_1.json")
    page["studies"][0]["protocolSection"]["statusModule"]["overallStatus"] = "NOT_A_STATUS"
    api = _api()
    respx.get(STUDIES_URL).mock(
        side_effect=lambda request: httpx.Response(
            200, json=_load("delta_page_2.json") if "pageToken" in request.url.params else page
        )
    )
    report = daily.run_daily(CFG, dsn, SECRET, api, MODEL)
    assert (report.status, report.exit_code) == ("failed", daily.QUARANTINED_EXIT)
    with psycopg.connect(dsn) as con:
        reasons = con.execute("SELECT reasons FROM live.quarantine").fetchall()
        published = con.execute("SELECT count(*) FROM serving.trial_scores_latest").fetchone()[0]
        error = con.execute("SELECT error FROM monitoring.pipeline_runs WHERE job = 'daily'")
        (message,) = error.fetchone()
    assert len(reasons) == 1
    assert "overall_status" in reasons[0][0]
    assert "NOT_A_STATUS" not in reasons[0][0]
    assert published > 0
    assert message == "QuarantinedRecords: 1"


@respx.mock
def test_the_job_refuses_without_a_bootstrap_and_does_nothing_on_a_weekend(dsn: str) -> None:
    from trialpulse.cli import RefusedError

    with pytest.raises(RefusedError, match="bootstrap"):
        daily.run_daily(CFG, dsn, SECRET, _api(), MODEL)
    respx.get(VERSION_URL).mock(
        return_value=httpx.Response(200, json={"dataTimestamp": "2026-10-31T09:00:04"})
    )
    api = ApiClient(httpx.Client(), per_minute=60_000, wait=wait_none(), sleep=lambda _: None)
    assert daily.run_daily(CFG, dsn, SECRET, api, MODEL).status == "weekend"


@respx.mock
def test_one_running_job_at_a_time(dsn: str) -> None:
    _bootstrap(dsn)
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        store.start_run(cur, "daily", "daily", DAY, 120)
        con.commit()
    with pytest.raises(daily.JobFailedError, match="start: LeaseError: another live job"):
        daily.run_daily(CFG, dsn, SECRET, _api(), MODEL)
    with psycopg.connect(dsn) as con, con.cursor() as cur:  # a stale lease is released
        cur.execute("UPDATE monitoring.pipeline_runs SET started_at = now() - interval '3 hours'")
        con.commit()
    assert daily.run_daily(CFG, dsn, SECRET, _api(), MODEL).status == "succeeded"


@respx.mock
@pytest.mark.parametrize(
    ("size", "alerts", "code"),
    [(120.0, [], 0), (650.0, [600], 0), (720.0, [600, 700], daily.RETENTION_EXIT)],
)
def test_retention_alerts_at_600_and_700_mb(
    dsn: str, monkeypatch: pytest.MonkeyPatch, size: float, alerts: list[int], code: int
) -> None:
    """ADR 0045: the run records the alerts; at the last one it ends non-zero so the
    failure issue opens. It never deletes anything itself."""
    _bootstrap(dsn)
    monkeypatch.setattr(store, "database_mb", lambda cur: size)
    report = daily.run_daily(CFG, dsn, SECRET, _api(), MODEL)
    assert report.status == "succeeded"
    assert report.counts["details"]["retention_alerts"] == alerts
    assert report.exit_code == code


@respx.mock
def test_a_change_no_feature_reads_makes_no_version_history_row_or_record_change(
    dsn: str,
) -> None:
    """ADR 0042, condition 4: the same records again with another summary, other
    eligibility text, a title in other case and the sponsor's name in other case leave
    every live and serving table as it was."""
    _bootstrap(dsn)
    assert daily.run_daily(CFG, dsn, SECRET, _api(), MODEL).status == "succeeded"
    before = _digest(dsn, skip=DISPLAY)

    def edited(name: str) -> Any:
        page = _load(name)
        for study in page["studies"]:
            section = study["protocolSection"]
            section.setdefault("descriptionModule", {})["briefSummary"] = "Rewritten summary."
            section.setdefault("eligibilityModule", {})["eligibilityCriteria"] = "* New text"
            ident = section["identificationModule"]
            ident["briefTitle"] = str(ident.get("briefTitle", "")).upper()
            lead = section["sponsorCollaboratorsModule"]["leadSponsor"]
            lead["name"] = str(lead["name"]).upper()
        return page

    respx.get(STUDIES_URL).mock(
        side_effect=lambda request: httpx.Response(
            200, json=edited("delta_page_2.json" if "pageToken" in request.url.params
                             else "delta_page_1.json"),
        )
    )  # fmt: skip
    api = ApiClient(httpx.Client(), per_minute=60_000, wait=wait_none(), sleep=lambda _: None)
    again = daily.run_daily(CFG, dsn, SECRET, api, MODEL, force=True)
    assert again.counts["details"]["actions"] == {"overlap": 8}
    assert _digest(dsn, skip=DISPLAY) == before  # no version, history row or record change
    with psycopg.connect(dsn) as con:  # the display text follows the record (ADR 0051)
        titles = [r[0] for r in con.execute("SELECT brief_title FROM serving.trial_display")]
    assert titles
    assert all(t == t.upper() for t in titles)


def _too_risky(scored: pd.DataFrame, features: pd.DataFrame, day: dt.date) -> pd.DataFrame:
    out = _scorer(scored, features, day)
    out["risk_12m"] = 2.0  # breaks the serving table's check constraint
    return out


@respx.mock
def test_a_database_error_prints_no_value_of_the_row(
    dsn: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Review finding 1 (ADR 0040): a CheckViolation in publication, whose message repeats the
    failing row, is reported as one line with the stage, the type and the SQLSTATE; no value
    of any row reaches stdout, stderr, the run's ledger or the workflow's output."""
    from trialpulse.cli import run

    _bootstrap(dsn)
    _api()
    outputs = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    monkeypatch.setenv("SPONSOR_ID_KEY", SECRET.decode())
    risky = daily.Model("stand-in/1", _too_risky, DECISIONS)
    monkeypatch.setattr(daily, "champion_model", lambda cfg, folder: risky)
    code = run(daily.main, ["--target", "local"])
    printed = capsys.readouterr()
    text = printed.out + printed.err
    assert code == 1
    (line,) = [x for x in printed.err.splitlines() if x.strip()]
    assert line.startswith("failed: publish: CheckViolation 23514 (at ")
    assert outputs.read_text().strip() == "error_class=CheckViolation"
    with psycopg.connect(dsn) as con:
        (error,) = con.execute(
            "SELECT error FROM monitoring.pipeline_runs WHERE job = 'daily'"
        ).fetchone()
        trials = [r[0] for r in con.execute("SELECT nct_id FROM live.trial_state").fetchall()]
        hashes = [bytes(r[0]).hex()[:12] for r in con.execute(
            "SELECT feature_hash FROM live.trial_state").fetchall()]  # fmt: skip
    assert error == "publish: CheckViolation 23514"
    for leaked in ("Failing row", "DETAIL", "stand-in/1", "2026-10-30", *trials, *hashes):
        assert leaked not in text, leaked
        assert leaked not in error, leaked


# Review findings 2, 3, 5, 8 and 9 -------------------------------------------------------------


def _edited_api(edit: Any, version_day: str = "2026-10-30") -> ApiClient:
    """The recorded pages with `edit(studies)` applied to each page's records; returns only
    the records `edit` keeps, with the count to match."""

    def page(request: httpx.Request) -> httpx.Response:
        second = "pageToken" in request.url.params
        data = _load("delta_page_2.json" if second else "delta_page_1.json")
        data["studies"] = edit(data["studies"], second)
        if not second:
            first, rest = len(data["studies"]), len(edit(_load("delta_page_2.json")["studies"],
                                                         True))  # fmt: skip
            data["totalCount"] = first + rest
            if not rest:
                data.pop("nextPageToken")
        return httpx.Response(200, json=data)

    respx.get(VERSION_URL).mock(
        return_value=httpx.Response(200, json={"dataTimestamp": f"{version_day}T09:00:04"})
    )
    respx.get(STUDIES_URL).mock(side_effect=page)
    return ApiClient(httpx.Client(), per_minute=60_000, wait=wait_none(), sleep=lambda _: None)


def _set(studies: list[dict[str, Any]], path: str, value: Any, which: Any = None) -> list[
    dict[str, Any]
]:  # fmt: skip
    for i, study in enumerate(studies):
        if which is not None and i not in which:
            continue
        node = study["protocolSection"]
        keys = path.split(".")
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        if value is None:
            node.pop(keys[-1], None)
        else:
            node[keys[-1]] = value
    return studies


def _assert_served_is_the_population(dsn: str, day: dt.date) -> None:
    """ADR 0038 layer 1: after any run, the served rows and the not-scored rows are the
    population of the day, each trial once."""
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        rows = store.population_rows(cur, day, RULES.open_statuses, RULES.study_type,
                                     RULES.min_first_post_date)  # fmt: skip
        latest = {r[0] for r in cur.execute("SELECT nct_id FROM serving.trial_scores_latest")}
        not_scored = {r[0] for r in cur.execute("SELECT nct_id FROM serving.trials_not_scored")}
    assert not latest & not_scored
    assert latest | not_scored == {r[0] for r in rows}
    assert latest


@respx.mock
def test_a_record_that_cannot_be_converted_is_quarantined_and_the_run_goes_on(dsn: str) -> None:
    """Review finding 2: the "about 40" enrollment count quarantines its record by the
    field's name; the other 7 records are ingested and published."""
    _bootstrap(dsn)
    api = _edited_api(lambda s, second: s if second else _set(
        s, "designModule.enrollmentInfo.count", "about 40", {0}))  # fmt: skip
    report = daily.run_daily(CFG, dsn, SECRET, api, MODEL)
    assert (report.status, report.exit_code) == ("failed", daily.QUARANTINED_EXIT)
    assert report.counts["details"]["actions"] == {"new_trial": 7}
    with psycopg.connect(dsn) as con:
        held = con.execute("SELECT nct_id, reasons FROM live.quarantine").fetchall()
        error = con.execute("SELECT error FROM monitoring.pipeline_runs WHERE job = 'daily'")
        (message,) = error.fetchone()
    assert [r for _, r in held] == ["enrollment_count: wrong JSON type"]
    assert held[0][0] == _load("delta_page_1.json")["studies"][0]["protocolSection"][
        "identificationModule"]["nctId"]  # fmt: skip
    assert "about 40" not in message
    _assert_served_is_the_population(dsn, DAY)


@respx.mock
@pytest.mark.parametrize(
    ("path", "value"),
    [("designModule.enrollmentInfo", None), ("designModule.enrollmentInfo.count", "about 40")],
    ids=["field_turned_null", "records_quarantined"],
)
def test_the_gate_stops_a_run_before_any_write(dsn: str, path: str, value: Any) -> None:
    """Review finding 3: on the next run every refetched record lost its enrollment count, or
    was quarantined for it; either way more than 1% of the matched records lost the field,
    and the run stops before writing anything."""
    _bootstrap(dsn)
    assert daily.run_daily(CFG, dsn, SECRET, _api(), MODEL).status == "succeeded"
    before = _digest(dsn)
    api = _edited_api(lambda s, second: _set(s, path, value))
    with pytest.raises(daily.JobFailedError, match=r"ingest: GateError: .*enrollment_count"):
        daily.run_daily(CFG, dsn, SECRET, api, MODEL, force=True)
    assert _digest(dsn) == before


@respx.mock
def test_a_pull_that_misses_stored_overlap_rows_fails(dsn: str) -> None:
    """Review finding 3: the next pull returns 2 of the 8 records stored in its window, so 6
    stored rows did not come back (more than 5): the run fails before any write."""
    _bootstrap(dsn)
    assert daily.run_daily(CFG, dsn, SECRET, _api(), MODEL).status == "succeeded"
    before = _digest(dsn)
    api = _edited_api(lambda s, second: [] if second else s[:2])
    with pytest.raises(daily.JobFailedError, match="6 stored overlap rows were not returned"):
        daily.run_daily(CFG, dsn, SECRET, api, MODEL, force=True)
    assert _digest(dsn) == before
    # Five missing is within the allowance.
    api = _edited_api(lambda s, second: s[:1] if second else s[:2])
    assert daily.run_daily(CFG, dsn, SECRET, api, MODEL, force=True).status == "succeeded"


@respx.mock
def test_a_new_model_rescores_every_scored_trial_on_a_daily_run(dsn: str) -> None:
    """Review finding 5: a run that is not a full run still rescores every served trial
    when the model changed, and the history says why."""
    _bootstrap(dsn)
    assert daily.run_daily(CFG, dsn, SECRET, _api(), MODEL).status == "succeeded"
    with psycopg.connect(dsn) as con:
        served = con.execute("SELECT count(*) FROM serving.trial_scores_latest").fetchone()[0]

    def newer_scorer(scored: pd.DataFrame, features: pd.DataFrame, day: dt.date) -> pd.DataFrame:
        return _scorer(scored, features, day).assign(model_version="stand-in/2")

    newer = daily.Model("stand-in/2", newer_scorer, DECISIONS)
    monday = _edited_api(lambda s, second: s, "2026-11-02")  # history compares earlier days
    report = daily.run_daily(CFG, dsn, SECRET, monday, newer)
    assert report.counts["details"]["full"] is False
    assert report.counts["trials_rescored"] == served
    with psycopg.connect(dsn) as con:
        latest = "SELECT DISTINCT model_version FROM serving.trial_scores_latest"
        versions = con.execute(latest).fetchall()
        reasons = con.execute(
            "SELECT DISTINCT reasons FROM serving.trial_score_history "
            "WHERE model_version = 'stand-in/2'"
        ).fetchall()
    assert versions == [("stand-in/2",)]
    assert reasons == [(["new_model"],)]
    _assert_served_is_the_population(dsn, dt.date(2026, 11, 2))


@respx.mock
def test_a_record_posted_after_the_run_date_waits_for_a_later_run(dsn: str) -> None:
    """Review finding 9: a record posted after d is not applied (it would be refused), and
    the next run, whose date has reached it, applies it."""
    _bootstrap(dsn)
    late = _load("delta_page_1.json")["studies"][0]["protocolSection"]["identificationModule"][
        "nctId"]  # fmt: skip

    def after(studies: list[dict[str, Any]], second: bool) -> list[dict[str, Any]]:
        if second:
            return studies
        return _set(studies, "statusModule.lastUpdatePostDateStruct.date", "2026-10-31", {0})

    report = daily.run_daily(CFG, dsn, SECRET, _edited_api(after), MODEL)
    assert report.status == "succeeded"
    assert report.counts["details"]["deferred"] == 1
    with psycopg.connect(dsn) as con:
        held = con.execute("SELECT count(*) FROM live.trial_state WHERE nct_id = %s", (late,))
        assert held.fetchone()[0] == 0
    monday = daily.run_daily(CFG, dsn, SECRET, _edited_api(after, "2026-11-02"), MODEL)
    assert monday.status == "succeeded"
    with psycopg.connect(dsn) as con:
        row = con.execute(
            "SELECT effective_date FROM live.trial_state WHERE nct_id = %s", (late,)
        ).fetchone()
    assert row == (dt.date(2026, 10, 31),)
    _assert_served_is_the_population(dsn, dt.date(2026, 11, 2))


@respx.mock
def test_a_database_not_at_the_migration_head_is_refused(dsn: str) -> None:
    """Review finding 9: migrations run by hand, so the job refuses another revision."""
    _bootstrap(dsn)
    with psycopg.connect(dsn) as con:
        con.execute("UPDATE alembic_version SET version_num = '0003'")
        con.commit()
    with pytest.raises(daily.JobFailedError, match=r"start: SchemaError: .*0003, not 0005"):
        daily.run_daily(CFG, dsn, SECRET, _api(), MODEL)


@respx.mock
def test_a_trial_that_ends_loses_its_display_row(dsn: str) -> None:
    """ADR 0051: display rows are those of the live trials; a trial that completes leaves the
    live state, and its display row goes with it (found by a deliberate fault: nothing
    tested the removal)."""
    _bootstrap(dsn)
    assert daily.run_daily(CFG, dsn, SECRET, _api(), MODEL).status == "succeeded"
    first = _load("delta_page_1.json")["studies"][0]["protocolSection"]
    nct = first["identificationModule"]["nctId"]
    with psycopg.connect(dsn) as con:
        shown = con.execute("SELECT count(*) FROM serving.trial_display WHERE nct_id = %s",
                            (nct,)).fetchone()  # fmt: skip
    assert shown == (1,)

    def completed(studies: list[dict[str, Any]], second: bool) -> list[dict[str, Any]]:
        if second:
            return studies
        studies = _set(studies, "statusModule.overallStatus", "COMPLETED", {0})
        return _set(studies, "statusModule.lastUpdatePostDateStruct.date", "2026-11-02", {0})

    monday = daily.run_daily(CFG, dsn, SECRET, _edited_api(completed, "2026-11-02"), MODEL)
    assert monday.status == "succeeded"
    with psycopg.connect(dsn) as con:
        live = con.execute("SELECT count(*) FROM live.trial_state WHERE nct_id = %s", (nct,))
        assert live.fetchone() == (0,)
        shown = con.execute("SELECT count(*) FROM serving.trial_display WHERE nct_id = %s",
                            (nct,)).fetchone()  # fmt: skip
    assert shown == (0,)


# Neon's idle-in-transaction limit (ADR 0058) ------------------------------------------------

IDLE_LIMIT_MS = 1000  # Neon: 300000 (5 minutes); scaled down with every wait below
SLOW = 1.5  # seconds: longer than the limit, as a full run's drivers are longer than 5 minutes


def _limited(dsn: str) -> str:
    """The job's connection string with the session limit lowered on every connection it
    opens (the main one, publication's and VACUUM's)."""
    from psycopg import conninfo

    return conninfo.make_conninfo(
        dsn, options=f"-c idle_in_transaction_session_timeout={IDLE_LIMIT_MS}"
    )


def _slow_api() -> ApiClient:
    """The recorded pages, each answered after a wait (the pull of a long catch-up run)."""
    import time

    respx.get(VERSION_URL).mock(return_value=httpx.Response(200, json=_load("version.json")))

    def page(request: httpx.Request) -> httpx.Response:
        time.sleep(SLOW)
        name = "delta_page_2.json" if "pageToken" in request.url.params else "delta_page_1.json"
        return httpx.Response(200, json=_load(name))

    respx.get(STUDIES_URL).mock(side_effect=page)
    return ApiClient(httpx.Client(), per_minute=60_000, wait=wait_none(), sleep=lambda _: None)


def _slow_scorer(scored: pd.DataFrame, features: pd.DataFrame, day: dt.date) -> pd.DataFrame:
    import time

    time.sleep(SLOW)  # the drivers of a full run
    return _scorer(scored, features, day)


@respx.mock
def test_the_limit_ends_a_session_idle_in_a_transaction(dsn: str) -> None:
    """The control for the test below: the lowered limit does end an idle transaction."""
    import time

    with psycopg.connect(_limited(dsn)) as con:
        assert con.execute("SHOW idle_in_transaction_session_timeout").fetchone() == ("1s",)
        time.sleep(SLOW)
        with pytest.raises(psycopg.Error, match="idle-in-transaction timeout"):
            con.execute("SELECT 1").fetchone()


@respx.mock
def test_runs_that_wait_longer_than_the_idle_limit_succeed(
    dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review's two-run reproduction (finding 1): with the limit lowered and a slow pull,
    sponsor pass and scorer, the job held a transaction open while it waited, the session was
    ended, every run failed at publication and stayed a full run. Now no transaction is open
    while the job waits, and both runs succeed."""
    import time

    _bootstrap(dsn)
    advance = daily.engine.advance

    def slow_advance(*args: Any, **kwargs: Any) -> Any:
        time.sleep(SLOW)
        return advance(*args, **kwargs)

    monkeypatch.setattr(daily.engine, "advance", slow_advance)
    model = daily.Model("stand-in/1", _slow_scorer, DECISIONS)
    limited = _limited(dsn)
    first = daily.run_daily(CFG, limited, SECRET, _slow_api(), model)
    assert (first.status, first.exit_code) == ("succeeded", 0)
    with psycopg.connect(dsn) as con:
        runs = con.execute("SELECT run_kind, status FROM monitoring.pipeline_runs "
                           "WHERE job = 'daily' ORDER BY run_id").fetchall()  # fmt: skip
        assert runs == [("full", "succeeded")]
        assert con.execute("SELECT data_as_of FROM serving.data_status").fetchone() == (DAY,)
        assert con.execute("SELECT count(*) FROM monitoring.score_files").fetchone() == (1,)
    second = daily.run_daily(CFG, limited, SECRET, _slow_api(), model, force=True)
    assert second.status == "succeeded"
    with psycopg.connect(dsn) as con:
        runs = con.execute("SELECT run_kind, status FROM monitoring.pipeline_runs "
                           "WHERE job = 'daily' ORDER BY run_id").fetchall()  # fmt: skip
    assert runs == [("full", "succeeded"), ("daily", "succeeded")]


@respx.mock
def test_a_failure_is_recorded_when_the_jobs_connection_is_gone(dsn: str) -> None:
    """The run's own connection is ended while it scores (as Neon ends a session, or a network
    drops): the failure is recorded on a fresh connection, so the run is not left running
    and its lease is free at once."""

    def ending(scored: pd.DataFrame, features: pd.DataFrame, day: dt.date) -> pd.DataFrame:
        with psycopg.connect(dsn, autocommit=True) as other:
            other.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
        return _scorer(scored, features, day)

    _bootstrap(dsn)
    with pytest.raises(daily.JobFailedError, match=r"^publish: "):
        daily.run_daily(CFG, dsn, SECRET, _api(), daily.Model("stand-in/1", ending, DECISIONS))
    with psycopg.connect(dsn) as con:
        status, error = con.execute(
            "SELECT status, error FROM monitoring.pipeline_runs WHERE job = 'daily'"
        ).fetchone()
    assert status == "failed"
    assert error.startswith("publish: ")
    assert daily.run_daily(CFG, dsn, SECRET, _api(), MODEL).status == "succeeded"
