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


@pytest.fixture(autouse=True)
def tracked_runs(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """No test logs a run to the project's MLflow server. A command that would log one adds
    a record to this list instead. A test that asks for the `real_tracking` fixture gets the
    real function, and must give it a local store of its own."""
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
