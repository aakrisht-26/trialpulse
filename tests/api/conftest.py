"""The API on a seeded scratch database (tests/api/seed.py), with settings of its own."""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from trialpulse.api.db import Database, engine_for
from trialpulse.api.main import Api, create_app
from trialpulse.config import ApiConfig, load_project_config

from . import seed as seeding

CFG = load_project_config().api


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def seeded(scratch: str) -> str:
    """The libpq URL of a scratch database holding the small seed."""
    dsn = scratch.replace("postgresql+psycopg://", "postgresql://", 1)
    seeding.seed(dsn)
    return dsn


@pytest.fixture
def make_client(seeded: str) -> Iterator[Callable[..., tuple[TestClient, Api, FakeClock]]]:
    """A client on the seeded database; settings may be overridden per test."""
    made: list[Any] = []

    def make(**overrides: Any) -> tuple[TestClient, Api, FakeClock]:
        cfg = ApiConfig(**{**CFG.model_dump(), "retry_wait_seconds": 0, **overrides})
        clock = FakeClock()
        db = Database(engine_for(seeded, cfg.connect_timeout_seconds), cfg.retry_wait_seconds)
        api = Api(db, cfg, now=clock)
        client = TestClient(create_app(api))
        made.append(db.engine)
        return client, api, clock

    yield make
    for engine in made:
        engine.dispose()
