"""The dashboard's stubs are the API's real answers (ADR 0056).

`tests/app/stubs.json` holds the answers of `trialpulse.api.main` on the seeded test database
of Step 15 (tests/api/seed.py) to every request the pages make, plus the API's own 503 (the
database down), its 429 (the rate limit) and a 404. This test records them again and fails
when they differ from the committed file; `--record-stubs` writes the file instead:

    uv run pytest tests/app/test_stubs.py --record-stubs
"""

import datetime as dt
import importlib.util
import json
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from trialpulse.api import areas
from trialpulse.api.db import Database, engine_for
from trialpulse.api.main import Api, create_app
from trialpulse.config import ApiConfig, load_project_config

HERE = Path(__file__).parent
STUBS = HERE / "stubs.json"
TRIALS = ("NCT00000001", "NCT00000002", "NCT00000003", "NCT00000004", "NCT00000005")
MISSING = "NCT09999999"
EARLIER = ("NCT00000005", dt.date(2026, 9, 18), 0.07, 0.11)  # a score before 120 months


def _seed_module() -> Any:
    spec = importlib.util.spec_from_file_location("api_seed", HERE.parent / "api" / "seed.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def requests() -> list[tuple[str, dict[str, Any]]]:
    """Every request the pages make on the seeded data."""
    out: list[tuple[str, dict[str, Any]]] = [
        ("/health", {}), ("/v1/model", {}), ("/v1/areas", {}), ("/v1/reasons/summary", {}),
    ]  # fmt: skip
    slugs = [a.slug for a in areas.AREAS] + [areas.NOT_CLASSIFIED_SLUG]
    out += [(f"/v1/areas/{slug}/landscape", {}) for slug in slugs]
    out += [("/v1/sponsors", {"prefix": p}) for p in ("acm", "zzz")]
    out += [("/v1/sponsors/Acme Pharma Inc./portfolio", {"limit": 200, "offset": 0})]
    for nct in (*TRIALS, MISSING):
        out += [(f"/v1/trials/{nct}/risk", {}), (f"/v1/trials/{nct}/timeline", {})]
    return out


def _entry(name: str | None, path: str, params: dict[str, Any], response: Any) -> dict[str, Any]:
    return {
        "name": name, "path": path, "params": {k: str(v) for k, v in params.items()},
        "status": response.status_code, "retry_after": response.headers.get("retry-after"),
        "body": response.json(),
    }  # fmt: skip


def record(dsn: str) -> list[dict[str, Any]]:
    """The API's answers on a freshly migrated database at `dsn` (a libpq URL)."""
    _seed_module().seed(dsn)
    nct, day, r12, r24 = EARLIER
    with psycopg.connect(dsn) as con:
        con.execute(
            """INSERT INTO serving.trial_score_history (nct_id, as_of_date, model_version,
              risk_12m, risk_24m, reasons, landmark_index, true_landmark_index, content_hash,
              flagged) VALUES (%s, %s, 'trialpulse-m4/1', %s, %s, ARRAY['first_score'], 6,
              19, %s, true)""",
            (nct, day, r12, r24, bytes(32)),
        )
    cfg = ApiConfig(**{**load_project_config().api.model_dump(), "retry_wait_seconds": 0})
    entries = []
    db = Database(engine_for(dsn, cfg.connect_timeout_seconds), 0)
    try:
        client = TestClient(create_app(Api(db, cfg, now=lambda: 1000.0)))
        client.get("/v1/model")  # the data date is read: /health then shows it
        for path, params in requests():
            entries.append(_entry(None, path, params, client.get(path, params=params)))
        limited = ApiConfig(**{**cfg.model_dump(), "rate_limit_per_minute": 1})
        busy = TestClient(create_app(Api(db, limited, now=lambda: 1000.0)))
        busy.get("/v1/model")
        entries.append(_entry("rate limited", "/v1/model", {}, busy.get("/v1/model")))
    finally:
        db.engine.dispose()
    down = Database(engine_for("postgresql://nobody@127.0.0.1:1/none", 1), 0)
    try:
        asleep = TestClient(create_app(Api(down, cfg, now=lambda: 1000.0)))
        entries.append(_entry("database waking", "/v1/model", {}, asleep.get("/v1/model")))
    finally:
        down.engine.dispose()
    return entries


def test_the_stubs_are_the_apis_answers(scratch: str, request: pytest.FixtureRequest) -> None:
    dsn = scratch.replace("postgresql+psycopg://", "postgresql://", 1)
    entries = record(dsn)
    text = json.dumps(entries, indent=1, sort_keys=True) + "\n"
    if request.config.getoption("--record-stubs"):
        STUBS.write_text(text, encoding="utf-8")
    committed = json.loads(STUBS.read_text(encoding="utf-8"))
    assert committed == json.loads(text), (
        "The API's answers differ from tests/app/stubs.json; if the change is intended, run "
        "uv run pytest tests/app/test_stubs.py --record-stubs"
    )
    statuses = {(e["name"], e["status"]) for e in committed if e["name"]}
    assert statuses == {("rate limited", 429), ("database waking", 503)}
    assert {e["status"] for e in committed if not e["name"]} == {200, 404}
