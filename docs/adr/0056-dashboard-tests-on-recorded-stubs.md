# 0056. Dashboard tests on stubs recorded from the API

- Date: 2026-11-01
- Status: **Accepted.** Decided by Aakrisht on 2026-11-01 (Step 16 decision 5: option b).
- Refines CLAUDE.md Step 16 and the test rule of Step 16 ("Tests use Streamlit's AppTest against a stubbed API").

## Context

The dashboard calls the API only. Its tests must not need the API's database, yet a stub written by hand can drift from what the API really answers without anyone noticing.

## Decision

1. **The stubs are recorded from the real API** (`trialpulse.api.main`) on the seeded test database of Step 15 (`tests/api/seed.py`), plus one earlier score for a trial without a score, and committed in `tests/app/stubs.json`. They hold every answer the pages ask for, a 404, the API's own 503 (the database down) and its 429 (the rate limit), with status, `Retry-After` and body.
2. **A command regenerates them** (`uv run pytest tests/app/test_stubs.py --record-stubs`), and a test with Postgres (`TRIALPULSE_REQUIRE_POSTGRES=1` in CI) records them again and fails when the API's answers no longer match the committed file.
3. **Every page runs in AppTest against the stubs** through respx, with no database. The service's own failures, which the API cannot record (a connection refused while the Space starts, a 502 or 503 from the platform's proxy), are made in the tests from the same stubs.

## Alternatives

- **Hand-written JSON stubs:** quick, but they can drift from the API's answers.
- **AppTest against the real API in the same process:** no stubs, but every dashboard test needs Postgres, and the 503 and sleep cases are harder to force.

## Consequences

- An API change shows up as a failed stub test, and the stubs are regenerated in the same change.
- The page tests run without Postgres, so they run in every local `pytest`.
