"""Shared test setup."""

import ssl
from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture(scope="session", autouse=True)
def one_ssl_context_for_the_session() -> Iterator[None]:
    """Creating an httpx client loads the CA bundle, about 0.15 seconds each time. The tests
    build dozens of clients and send nothing over the network (respx answers every request),
    so they share one default TLS context. Non-default settings still get their own."""
    import httpx._transports.default as transport

    original = getattr(transport, "create_ssl_context", None)
    if original is None:  # a future httpx without this helper: keep its normal behavior
        yield
        return
    cache: dict[bool, ssl.SSLContext] = {}

    def cached(verify: Any = True, cert: Any = None, trust_env: bool = True) -> ssl.SSLContext:
        if verify is not True or cert is not None:
            return original(verify=verify, cert=cert, trust_env=trust_env)
        if trust_env not in cache:
            cache[trust_env] = original(verify=verify, cert=cert, trust_env=trust_env)
        return cache[trust_env]

    patch = pytest.MonkeyPatch()
    patch.setattr(transport, "create_ssl_context", cached)
    yield
    patch.undo()


@pytest.fixture(scope="session", autouse=True)
def default_model_settings(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """The tests fit the discrete-time models with their default settings, whatever the
    tuning last wrote to config/tuned: those files change with every tuning, and a test
    that read them failed on the day M4's were committed. A test that needs tuned settings
    writes a file of its own. For the whole session, so that a model fitted once per module
    is covered too. `tests/models/test_tuned_files.py` reads the committed files on purpose."""
    from trialpulse.models import discrete_time

    patch = pytest.MonkeyPatch()
    patch.setattr(discrete_time, "TUNED_DIR", tmp_path_factory.mktemp("no_tuned_settings"))
    yield
    patch.undo()


@pytest.fixture(autouse=True)
def tracked_runs(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """No test logs a run to the project's MLflow server. A command that would log one adds
    a record to this list instead, with everything the command handed over (the local
    option and the git state included, so a test can follow them from the command line to
    the store). A test that asks for the `real_tracking` fixture gets the real function, and
    must give it a local store of its own."""
    from trialpulse import tracking

    runs: list[Any] = []
    if "real_tracking" in request.fixturenames:
        return runs

    def recorded(name: str, params: Any, metrics: Any, artifacts: Any, cfg: Any,
                 tags: Any = None, **kwargs: Any) -> dict[str, str]:  # fmt: skip
        runs.append(
            {
                "name": name,
                "params": dict(params),
                "metrics": dict(metrics),
                "artifacts": [str(a) for a in artifacts],
                "tags": dict(tags or {}),
                "local": kwargs.get("local", False),
                "state": kwargs.get("state"),
            }
        )
        return {"store": "test", "store_description": "a list kept by the test", "run_id": "0",
                "experiment": tracking.EXPERIMENT, "run_name": name, "git_commit": "test",
                "git_dirty": "no"}  # fmt: skip

    monkeypatch.setattr(tracking, "log_run", recorded)
    return runs


@pytest.fixture
def real_tracking() -> None:
    """Asking for this fixture leaves `tracking.log_run` as it is (see `tracked_runs`)."""


@pytest.fixture(autouse=True)
def not_on_a_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI runs the tests under GitHub Actions, where the dataset modules refuse (ADR 0040).
    The tests use synthetic data only, so they run as on the laptop; a test of the refusal
    sets the variable itself."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


# A scratch Postgres database ----------------------------------------------------------------

REQUIRE_POSTGRES = "TRIALPULSE_REQUIRE_POSTGRES"


def no_postgres(reason: str) -> None:
    """Skip a database test when no Postgres answers, except where Postgres is required: CI's
    workflow sets TRIALPULSE_REQUIRE_POSTGRES, so there an unreachable server fails the test
    instead of hiding it behind a skip."""
    import os

    if os.environ.get(REQUIRE_POSTGRES):
        pytest.fail(f"{reason}, and {REQUIRE_POSTGRES} is set", pytrace=False)
    pytest.skip(reason)


@pytest.fixture
def scratch(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A migrated scratch database on the server of DATABASE_URL, dropped afterwards; its
    SQLAlchemy URL, also set as DATABASE_URL. Skipped without a server (failed in CI)."""
    import uuid
    from urllib.parse import quote

    from alembic import command
    from alembic.config import Config

    from trialpulse.config import REPO_ROOT, Secrets

    try:
        import psycopg
        from psycopg import conninfo
    except ImportError:
        no_postgres("psycopg is not installed")
        raise
    secret = Secrets().database_url
    if secret is None:
        no_postgres("DATABASE_URL is not set")
        raise AssertionError("unreachable")
    base = secret.get_secret_value().replace("postgresql+psycopg://", "postgresql://", 1)
    admin = conninfo.make_conninfo(base, dbname="postgres", connect_timeout=10)
    name = f"trialpulse_test_{uuid.uuid4().hex[:12]}"
    try:
        with psycopg.connect(admin, autocommit=True) as con:
            con.execute(f"CREATE DATABASE {name}")
    except psycopg.OperationalError as exc:  # the address is not printed: it holds a password
        no_postgres(f"no Postgres answers at DATABASE_URL ({type(exc).__name__})")
    parts = conninfo.conninfo_to_dict(base)
    user = quote(str(parts.get("user", "")), safe="")
    password = quote(str(parts.get("password", "")), safe="")
    host, port = parts.get("host", "localhost"), parts.get("port", 5432)
    url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"
    monkeypatch.setenv("DATABASE_URL", url)
    try:
        command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
        yield url
    finally:
        with psycopg.connect(admin, autocommit=True) as con:
            con.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
