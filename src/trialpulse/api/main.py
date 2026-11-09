"""The read-only API of CLAUDE.md Section 13 (Step 15, ADRs 0046 to 0051).

    uvicorn trialpulse.api.main:app

It reads the serving tables only, through `API_DATABASE_URL` (ADR 0049): no model, no
dataset, no data/ folder. Every response, errors included, carries the research-demo notice,
the ClinicalTrials.gov attribution and the data-as-of date (ADR 0008); every score carries
its "outside the validated range" flag, and every trial without a score its reason code. No
response holds a person's name: sponsors without a display name show their class only.

`/health` never touches the database, so a health check never wakes Neon.
"""

import datetime as dt
import logging
import re
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from trialpulse.api import areas as area_list
from trialpulse.api import schemas as s
from trialpulse.api.db import Database, DatabaseRefused, Unavailable, engine_for
from trialpulse.api.guards import (
    AnswerCache,
    DataClock,
    ForwardedShape,
    RateLimiter,
    client_address,
)
from trialpulse.config import ApiConfig, Secrets, load_project_config
from trialpulse.contracts.sponsor import sponsor_key
from trialpulse.features.registry import model_columns

NCT = r"^NCT\d{8}$"
FLAG = "outside the validated range"
REASON_TEXT = {
    "reversal": "A terminal status was later replaced by an open one; such trials are not scored "
    "(CLAUDE.md Section 6).",
    "beyond_120_months": "Registered 120 months or more ago: beyond every trial the model was "
    "trained or checked on.",
    "band_not_validated": "Registered in an age band whose development check failed (ADR 0034).",
}
MARKER_BITS = {1: "status", 2: "dates", 4: "enrollment", 8: "design", 16: "sponsor",
               32: "title phase or interventions"}  # fmt: skip
LOG = logging.getLogger("trialpulse.api")
REFUSED = (
    "the API's database refused the query: a configuration error (logged by its SQLSTATE), "
    "not the database waking up"
)
INTERNAL = "the API failed to answer this request (logged by the error's class)"
NOT_FOUND = "no trial with this registry id is served"
NO_SPONSOR = "no sponsor with a public portfolio has this name"
DESCRIPTION = (
    f"**{s.NOTICE}**\n\n{s.SOURCE_NOTE}\n\nRead-only early-stop risk estimates for open "
    "interventional trials on ClinicalTrials.gov: operational risk analytics for trial "
    "portfolios. Scores describe elevated early-stop risk, never whether a treatment works. "
    "Every response carries this notice, the attribution and the data-as-of date."
)


class Api:
    """The state of one app: the database, the data clock, the cache and the rate limit."""

    def __init__(self, db: Database, cfg: ApiConfig,
                 now: Callable[[], float] | None = None) -> None:  # fmt: skip
        import time

        clock = now or time.monotonic
        self.db = db
        self.cfg = cfg
        self.clock = DataClock(self._read_data_as_of, cfg.data_status_refresh_minutes * 60,
                               clock)  # fmt: skip
        self.cache = AnswerCache(cfg.cache_entries)
        self.limiter = RateLimiter(cfg.rate_limit_per_minute, clock)
        self.shape = ForwardedShape()

    def _read_data_as_of(self) -> dt.date | None:
        rows = self.db.rows("SELECT data_as_of FROM serving.data_status WHERE status_id = 1")
        value: dt.date | None = rows[0]["data_as_of"] if rows else None
        return value

    def answer(self, key: tuple[Any, ...], compute: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """A cached answer for the current data date, with the envelope's date."""
        day = self.clock.current()
        if day is None and self.clock.last_read_failed:  # say now why there is no date
            if isinstance(self.clock.last_error, DatabaseRefused):
                raise self.clock.last_error
            raise Unavailable("the database did not answer")
        body = self.cache.get_or((day, *key), compute)
        return {**body, "data_as_of": day}


def _envelope(api: Api | None, status: int, detail: str,
              headers: dict[str, str] | None = None) -> JSONResponse:  # fmt: skip
    body = s.Problem(detail=detail, data_as_of=api.clock.peek() if api else None)
    return JSONResponse(body.model_dump(mode="json"), status_code=status, headers=headers)


def _drivers(row: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    names = model_columns()
    out: dict[str, list[dict[str, Any]]] = {"12": [], "24": []}
    for m, f, v, c in zip(row["driver_months"], row["driver_features"], row["driver_values"],
                          row["driver_contributions"], strict=True):  # fmt: skip
        out.setdefault(str(m), []).append(
            {"feature": names[f], "value": v, "contribution": round(float(c), 3),
             "direction": "raises" if c > 0 else "lowers"}
        )  # fmt: skip
    return out


def _portfolio_trial(r: dict[str, Any]) -> dict[str, Any]:
    scored = r["risk_12m"] is not None
    return {
        "nct_id": r["nct_id"], "brief_title": r["brief_title"],
        "status": "scored" if scored else "not_scored",
        "as_of_date": r["as_of_date"], "risk_12m": r["risk_12m"], "risk_24m": r["risk_24m"],
        "flagged": r["flagged"], "flag": FLAG if r["flagged"] else None, "reason": r["reason"],
    }  # fmt: skip


TRIALS_SQL = """
SELECT d.nct_id, d.brief_title, l.risk_12m, l.risk_24m, l.flagged,
  coalesce(l.as_of_date, n.as_of_date) AS as_of_date, n.reason
FROM serving.trial_display d
LEFT JOIN serving.trial_scores_latest l ON l.nct_id = d.nct_id
LEFT JOIN serving.trials_not_scored n ON n.nct_id = d.nct_id
WHERE (l.nct_id IS NOT NULL OR n.nct_id IS NOT NULL) AND {where}
"""


def create_app(api: Api) -> FastAPI:
    app = FastAPI(title="TrialPulse API", version="1", description=DESCRIPTION,
                  docs_url="/docs", redoc_url=None)  # fmt: skip
    app.state.api = api

    @app.middleware("http")
    async def rate_limit(request: Request, call_next: Any) -> Any:
        if request.url.path.startswith("/v1/"):
            peer = request.client.host if request.client else None
            if api.cfg.forwarded_diagnostic:  # the headers' shape only (ADR 0065)
                head = request.headers
                LOG.info(api.shape.describe(head.get("x-forwarded-for"),
                                            head.get("cf-connecting-ip"),
                                            head.get("true-client-ip"), peer))  # fmt: skip
            who = client_address(request.headers.get("x-forwarded-for"), peer,
                                 api.cfg.trusted_proxies)  # fmt: skip
            wait = api.limiter.allow(who)
            if wait:
                return _envelope(api, 429, "too many requests from this address",
                                 {"Retry-After": str(max(1, round(wait)))})  # fmt: skip
        return await call_next(request)

    @app.exception_handler(Unavailable)
    async def unavailable(request: Request, exc: Unavailable) -> JSONResponse:
        wait = str(api.cfg.unavailable_retry_after_seconds)
        return _envelope(api, 503, "the database is waking up; try again shortly",
                         {"Retry-After": wait})  # fmt: skip

    @app.exception_handler(DatabaseRefused)
    async def refused(request: Request, exc: DatabaseRefused) -> JSONResponse:
        # The SQLSTATE and class only: an error's text can hold a row (ADR 0061).
        LOG.error("database refused a query: SQLSTATE %s (%s) on %s", exc.sqlstate or "none",
                  exc.kind, request.url.path)  # fmt: skip
        return _envelope(api, 500, REFUSED)

    @app.exception_handler(Exception)
    async def internal(request: Request, exc: Exception) -> JSONResponse:
        LOG.error("unhandled %s on %s", type(exc).__name__, request.url.path)
        return _envelope(api, 500, INTERNAL)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _envelope(api, exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        where = "; ".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())
        return _envelope(api, 422, f"invalid request: {where}")

    def missing(detail: str) -> StarletteHTTPException:
        return StarletteHTTPException(404, detail)

    @app.get("/health", response_model=s.Health, tags=["service"])
    def health() -> dict[str, Any]:
        """Whether the API is up. It never touches the database."""
        return {"status": "ok", "data_as_of": api.clock.peek()}

    @app.get("/v1/trials/{nct_id}/risk", response_model=s.TrialRisk, tags=["trials"],
             responses={404: {"model": s.Problem}})  # fmt: skip
    def risk(nct_id: Annotated[str, Path(pattern=NCT)]) -> dict[str, Any]:
        """A trial's latest early-stop risk at 12 and 24 months with its five drivers per
        horizon, or the reason it is not scored."""

        def compute() -> dict[str, Any]:
            rows = api.db.rows(
                """SELECT l.*, n.reason, n.as_of_date AS not_scored_date, d.brief_title,
                  d.sponsor_class, d.areas, sp.display_name
                FROM (SELECT CAST(:id AS text) AS nct_id) k
                LEFT JOIN serving.trial_scores_latest l ON l.nct_id = k.nct_id
                LEFT JOIN serving.trials_not_scored n ON n.nct_id = k.nct_id
                LEFT JOIN serving.trial_display d ON d.nct_id = k.nct_id
                LEFT JOIN serving.sponsors sp ON sp.sponsor_id = d.sponsor_id""",
                {"id": nct_id},
            )
            r = rows[0]
            if r["model_version"] is None and r["reason"] is None:
                return {"missing": True}
            base = {"nct_id": nct_id, "brief_title": r["brief_title"],
                    "sponsor_name": r["display_name"], "sponsor_class": r["sponsor_class"],
                    "areas": area_list.slugs(r["areas"] or [])}  # fmt: skip
            if r["model_version"] is not None:
                score = {
                    "as_of_date": r["as_of_date"], "risk_12m": r["risk_12m"],
                    "risk_24m": r["risk_24m"], "flagged": r["flagged"],
                    "flag": FLAG if r["flagged"] else None,
                    "flag_note": FLAG_NOTE if r["flagged"] else None,
                    "landmark_index": r["landmark_index"],
                    "true_landmark_index": r["true_landmark_index"],
                    "model_version": r["model_version"], "drivers": _drivers(r),
                }  # fmt: skip
                return {**base, "status": "scored", "score": score, "not_scored": None}
            reason = str(r["reason"])
            not_scored = {"as_of_date": r["not_scored_date"], "reason": reason,
                          "reason_text": REASON_TEXT.get(reason, reason)}  # fmt: skip
            return {**base, "status": "not_scored", "score": None, "not_scored": not_scored}

        body = api.answer(("risk", nct_id), compute)
        if body.get("missing"):
            raise missing(NOT_FOUND)
        return body

    @app.get("/v1/trials/{nct_id}/timeline", response_model=s.Timeline, tags=["trials"],
             responses={404: {"model": s.Problem}})  # fmt: skip
    def timeline(nct_id: Annotated[str, Path(pattern=NCT)]) -> dict[str, Any]:
        """A trial's served risk over time since go-live, with its amendment markers."""

        def compute() -> dict[str, Any]:
            points = api.db.rows(
                """SELECT as_of_date, risk_12m, risk_24m, flagged, reasons, model_version
                FROM serving.trial_score_history WHERE nct_id = :id
                ORDER BY as_of_date, history_id""",
                {"id": nct_id},
            )
            marks = api.db.rows(
                "SELECT post_dates, kinds FROM serving.amendment_markers WHERE nct_id = :id",
                {"id": nct_id},
            )
            if not points and not marks:
                return {"missing": True}
            markers = [
                {"post_date": d, "changed": [n for b, n in MARKER_BITS.items() if k & b]}
                for m in marks for d, k in zip(m["post_dates"], m["kinds"], strict=True)
            ]  # fmt: skip
            return {"nct_id": nct_id, "points": points, "markers": markers}

        body = api.answer(("timeline", nct_id), compute)
        if body.get("missing"):
            raise missing(NOT_FOUND)
        return body

    @app.get("/v1/sponsors", response_model=s.SponsorSearch, tags=["sponsors"])
    def sponsors(prefix: Annotated[str, Query(min_length=3, max_length=100)]) -> dict[str, Any]:
        """Sponsors with a display name that starts with `prefix` (at least 3 characters)."""
        key = sponsor_key(prefix) or ""

        def compute() -> dict[str, Any]:
            rows = api.db.rows(
                """SELECT display_name AS name, sponsor_class FROM serving.sponsors
                WHERE search_key LIKE :p ESCAPE '\\' ORDER BY search_key LIMIT 50""",
                {"p": key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"},
            )
            return {"prefix": prefix, "sponsors": rows}

        return api.answer(("sponsors", key), compute)

    @app.get("/v1/sponsors/{name}/portfolio", response_model=s.Portfolio, tags=["sponsors"],
             responses={404: {"model": s.Problem}})  # fmt: skip
    def portfolio(
        name: Annotated[str, Path(min_length=1, max_length=300)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """A sponsor's open trials ranked by risk at 12 months, then its trials without a
        score. Only sponsors with a display name can be found (ADR 0047)."""
        key = sponsor_key(name) or ""
        limit = min(limit, api.cfg.page_size_max)

        def compute() -> dict[str, Any]:
            found = api.db.rows(
                """SELECT sponsor_id, display_name, sponsor_class FROM serving.sponsors
                WHERE search_key = :k ORDER BY sponsor_id LIMIT 1""",
                {"k": key},
            )
            if not found:
                return {"missing": True}
            who = found[0]
            where = "d.sponsor_id = :sid"
            total = api.db.rows(
                f"SELECT count(*) AS n FROM ({TRIALS_SQL.format(where=where)}) t",
                {"sid": who["sponsor_id"]},
            )[0]["n"]
            rows = api.db.rows(
                TRIALS_SQL.format(where=where)
                + " ORDER BY l.risk_12m DESC NULLS LAST, d.nct_id LIMIT :lim OFFSET :off",
                {"sid": who["sponsor_id"], "lim": limit, "off": offset},
            )
            return {"sponsor_name": who["display_name"], "sponsor_class": who["sponsor_class"],
                    "trials_total": total, "offset": offset, "limit": limit,
                    "trials": [_portfolio_trial(r) for r in rows]}  # fmt: skip

        body = api.answer(("portfolio", key, limit, offset), compute)
        if body.get("missing"):
            raise missing(NO_SPONSOR)
        return body

    @app.get("/v1/areas", response_model=s.AreaList, tags=["areas"])
    def areas() -> dict[str, Any]:
        """Every area with its number of open trials (ADR 0046)."""

        def compute() -> dict[str, Any]:
            rows = api.db.rows(
                """WITH open_trials AS (
                  SELECT d.areas FROM serving.trial_display d
                  WHERE EXISTS (SELECT 1 FROM serving.trial_scores_latest l
                                WHERE l.nct_id = d.nct_id)
                     OR EXISTS (SELECT 1 FROM serving.trials_not_scored n
                                WHERE n.nct_id = d.nct_id))
                SELECT code, count(*) AS n FROM (
                  SELECT unnest(CASE WHEN cardinality(areas) = 0 THEN ARRAY[0::smallint]
                                     ELSE areas END) AS code FROM open_trials) c
                GROUP BY code UNION ALL
                SELECT -1, count(*) FROM open_trials"""
            )
            counts = {int(r["code"]): int(r["n"]) for r in rows}
            listed = [{"slug": a.slug, "name": a.name, "mesh_tree": a.tree,
                       "open_trials": counts.get(a.code, 0)} for a in area_list.AREAS]  # fmt: skip
            listed.append({"slug": area_list.NOT_CLASSIFIED_SLUG, "name": "Not classified",
                           "mesh_tree": None,
                           "open_trials": counts.get(area_list.NOT_CLASSIFIED, 0)})  # fmt: skip
            return {"areas": listed, "open_trials": counts.get(-1, 0), "note": area_list.NOTE}

        return api.answer(("areas",), compute)

    @app.get("/v1/areas/{area}/landscape", response_model=s.Landscape, tags=["areas"],
             responses={404: {"model": s.Problem}})  # fmt: skip
    def landscape(area: Annotated[str, Path(pattern=r"^[a-z-]{2,60}$")]) -> dict[str, Any]:
        """Risk and competition in one area today (descriptive; ADR 0046)."""
        if area == area_list.NOT_CLASSIFIED_SLUG:
            code, title = area_list.NOT_CLASSIFIED, "Not classified"
            where = "cardinality(d.areas) = 0"
        elif area in area_list.BY_SLUG:
            code, title = area_list.BY_SLUG[area].code, area_list.BY_SLUG[area].name
            where = "CAST(:code AS smallint) = ANY(d.areas)"
        else:
            raise missing("no area has this name; see /v1/areas")

        def compute() -> dict[str, Any]:
            sql = TRIALS_SQL.format(where=where)
            stats = api.db.rows(
                f"""SELECT count(*) AS open_trials, count(risk_12m) AS scored,
                  count(*) FILTER (WHERE flagged) AS flagged,
                  avg(risk_12m) AS mean_12, percentile_cont(0.5) WITHIN GROUP
                    (ORDER BY risk_12m) AS median_12,
                  avg(risk_24m) AS mean_24, percentile_cont(0.5) WITHIN GROUP
                    (ORDER BY risk_24m) AS median_24
                FROM ({sql}) t""",
                {"code": code},
            )[0]
            reasons = api.db.rows(
                f"SELECT reason, count(*) AS n FROM ({sql}) t WHERE reason IS NOT NULL "
                "GROUP BY reason ORDER BY reason",
                {"code": code},
            )
            top = api.db.rows(
                sql + " AND l.risk_12m IS NOT NULL ORDER BY l.risk_12m DESC, d.nct_id LIMIT 20",
                {"code": code},
            )
            return {
                "slug": area, "name": title, "open_trials": stats["open_trials"],
                "scored": stats["scored"], "flagged": stats["flagged"],
                "not_scored": {r["reason"]: r["n"] for r in reasons},
                "mean_risk_12m": stats["mean_12"], "median_risk_12m": stats["median_12"],
                "mean_risk_24m": stats["mean_24"], "median_risk_24m": stats["median_24"],
                "top_trials": [_portfolio_trial(r) for r in top], "note": area_list.NOTE,
            }  # fmt: skip

        return api.answer(("landscape", area), compute)

    @app.get("/v1/reasons/summary", response_model=s.ReasonSummary, tags=["reasons"])
    def reasons(
        since: Annotated[dt.date | None, Query(description="First month, inclusive")] = None,
        until: Annotated[dt.date | None, Query(description="Last month, inclusive")] = None,
    ) -> dict[str, Any]:
        """Why trials stopped: labeled early stops by month, reason and sponsor class, through
        the labels' date (ADR 0050). Later early stops are counted, not labeled."""

        def compute() -> dict[str, Any]:
            counts = api.db.rows(
                """SELECT month, reason, sponsor_class, n FROM serving.reason_counts
                WHERE (CAST(:a AS date) IS NULL OR month >= date_trunc('month', CAST(:a AS date)))
                  AND (CAST(:b AS date) IS NULL OR month <= CAST(:b AS date))
                ORDER BY month, reason, sponsor_class""",
                {"a": since, "b": until},
            )
            status = api.db.rows(
                """SELECT labeled_through, early_stops_after_labels FROM serving.data_status
                WHERE status_id = 1"""
            )
            totals: dict[str, int] = {}
            for c in counts:
                totals[c["reason"]] = totals.get(c["reason"], 0) + int(c["n"])
            state = status[0] if status else {}
            return {
                "labeled_through": state.get("labeled_through"),
                "early_stops_after_labels": state.get("early_stops_after_labels"),
                "totals": totals, "counts": counts,
                "note": "Reasons are labels of a text classifier (Step 6) of each stop's "
                "free-text reason, counted by the month the stop became public. Stops after "
                "the labels' date are counted, not labeled.",
            }  # fmt: skip

        return api.answer(("reasons", since, until), compute)

    @app.get("/v1/model", response_model=s.ModelInfo, tags=["model"])
    def model() -> dict[str, Any]:
        """The served model, its registered results and scope (ADR 0048), and today's flagged
        and not-scored shares."""

        def compute() -> dict[str, Any]:
            statement = api.db.rows(
                "SELECT statement FROM serving.model_statement WHERE statement_id = 1"
            )
            served = api.db.rows(
                """SELECT count(*) AS n, avg(CASE WHEN flagged THEN 1.0 ELSE 0.0 END) AS share
                FROM serving.trial_scores_latest"""
            )[0]
            missing_reasons = api.db.rows(
                "SELECT reason, count(*) AS n FROM serving.trials_not_scored GROUP BY reason"
            )
            version = api.db.rows(
                "SELECT model_version FROM serving.data_status WHERE status_id = 1"
            )
            return {
                "model_version": version[0]["model_version"] if version else None,
                "statement": statement[0]["statement"] if statement else None,
                "scored_trials": served["n"],
                "flagged_share": float(served["share"]) if served["share"] is not None else None,
                "not_scored": {r["reason"]: r["n"] for r in missing_reasons},
            }

        return api.answer(("model",), compute)

    return app


FLAG_NOTE = (
    "Scored as at landmark 6: this trial is older than every row the model was tested on. In "
    "development, flagged scores ran above the observed risk, so this score may overstate it."
)


def from_environment() -> FastAPI:
    """The app of the container: settings from config/project.yaml, the URL from
    API_DATABASE_URL (or DATABASE_URL locally)."""
    cfg = load_project_config().api
    if not LOG.handlers:  # the container's log: one line per refusal or failure
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        LOG.addHandler(handler)
        LOG.setLevel(logging.INFO)
    secrets = Secrets()
    secret = secrets.api_database_url or secrets.database_url
    if secret is None:
        raise RuntimeError("API_DATABASE_URL is not set")
    url = secret.get_secret_value()
    if not re.match(r"^postgres(ql)?(\+psycopg)?://", url):
        raise RuntimeError("API_DATABASE_URL is not a Postgres URL")
    db = Database(engine_for(url, cfg.connect_timeout_seconds), cfg.retry_wait_seconds)
    return create_app(Api(db, cfg))


def __getattr__(name: str) -> Any:  # `uvicorn trialpulse.api.main:app` builds the app lazily
    if name == "app":
        return from_environment()
    raise AttributeError(name)
