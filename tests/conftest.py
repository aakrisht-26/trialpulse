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
