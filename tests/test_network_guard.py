"""The network guard of tests/conftest.py: a test that reaches another machine fails, even when
the code under test swallows the error; this machine stays reachable (the test Postgres)."""

import contextlib
import socket

import httpx
import pytest


def _refused(guard: object, call: object) -> str:
    attempts: list[str] = guard.attempts  # type: ignore[attr-defined]
    before = len(attempts)
    with pytest.raises(RuntimeError, match="tried to reach the network"):
        call()  # type: ignore[operator]
    tried = attempts[before:]
    del attempts[before:]  # handled here, so this test does not fail at teardown
    return tried[0]


def test_a_connection_to_another_machine_is_refused(no_real_network: object) -> None:
    def connect() -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect(("203.0.113.7", 443))  # TEST-NET-3: never routed

    assert _refused(no_real_network, connect) == "connect to 203.0.113.7:443"


def test_a_name_lookup_is_refused(no_real_network: object) -> None:
    def lookup() -> None:
        socket.getaddrinfo("clinicaltrials.gov", 443)

    assert _refused(no_real_network, lookup) == "name lookup of clinicaltrials.gov"


def test_an_unmocked_api_request_is_refused_before_it_leaves(no_real_network: object) -> None:
    """What went wrong in Step 17: a daily-job test without respx called the live API."""

    def request() -> None:
        httpx.get("https://clinicaltrials.gov/api/v2/version", timeout=5)

    assert "clinicaltrials.gov" in _refused(no_real_network, request)


def test_a_database_connection_to_an_address_is_refused(no_real_network: object) -> None:
    """libpq opens its own sockets: an IP literal never passes through Python's socket."""
    import psycopg

    def connect() -> None:
        psycopg.connect("host=203.0.113.7 port=5432 dbname=x connect_timeout=2")

    assert _refused(no_real_network, connect) == "database connection to 203.0.113.7"


def test_an_asyncio_connection_to_an_address_is_refused(no_real_network: object) -> None:
    import asyncio

    def connect() -> None:
        asyncio.run(asyncio.wait_for(asyncio.open_connection("203.0.113.7", 443), 2))

    assert _refused(no_real_network, connect) == "connect to 203.0.113.7"


def test_a_swallowed_attempt_is_still_recorded(no_real_network: object) -> None:
    attempts: list[str] = no_real_network.attempts  # type: ignore[attr-defined]
    before = len(attempts)
    with contextlib.suppress(Exception):  # code under test that hides every error
        socket.create_connection(("example.org", 80), timeout=1)
    assert attempts[before:] == ["name lookup of example.org"]  # the teardown would fail it
    del attempts[before:]


def test_this_machine_stays_reachable() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        with socket.create_connection(("localhost", port), timeout=5):
            pass
