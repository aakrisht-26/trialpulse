"""The dashboard against its recorded stubs (ADR 0056): a respx router answers every request
to the stub address from tests/app/stubs.json, records each request, and can be told to fail
first (the service starting, the platform's proxy, the database waking). Waits are counted on
a fake clock, never slept."""

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import respx
from streamlit.delta_generator import DeltaGenerator

HERE = Path(__file__).resolve().parent
for folder in (HERE.parents[1] / "app", HERE):  # app/ as `streamlit run app/Home.py` puts it
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from stubbed_api import StubApi  # noqa: E402


@pytest.fixture(autouse=True)
def the_main_module_comes_back() -> Iterator[None]:
    """AppTest leaves the page it ran as `sys.modules["__main__"]`. A process that a later
    test starts with "spawn" runs the parent's main module first, so it would run that page,
    which then waits out its 3-minute budget for an API that is not there (this hung
    tests/features/test_sponsor.py::test_a_loop_of_names_ends in CI). The real main module
    is put back after every dashboard test."""
    main = sys.modules["__main__"]
    yield
    sys.modules["__main__"] = main


@pytest.fixture
def stub_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubApi]:
    from dashboard import client, ui

    stub = StubApi()

    def sleep(seconds: float) -> None:
        stub.slept.append(seconds)
        stub.now += seconds

    monkeypatch.setattr(client, "sleep", sleep)
    shown = DeltaGenerator.info

    def info(self: DeltaGenerator, body: Any, *args: Any, **kwargs: Any) -> Any:
        if "retrying in" in str(body):  # a wait message: gone from the page once it answers
            stub.waits.append(str(body))
        return shown(self, body, *args, **kwargs)

    monkeypatch.setattr(DeltaGenerator, "info", info)
    monkeypatch.setattr(client, "monotonic", lambda: stub.now)
    monkeypatch.delenv("TRIALPULSE_API_URL", raising=False)
    ui.reset()
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.route(host="api.test").mock(side_effect=stub.answer)
        yield stub
    ui.reset()
