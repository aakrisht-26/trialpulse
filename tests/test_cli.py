"""The command wrapper's opt-out from Windows' throttling (ADR 0036): a no-op outside Windows,
the right request on Windows, and never a reason for a command to fail."""

import ctypes
import sys
from collections.abc import Sequence
from typing import Any

import pytest

from trialpulse import cli
from trialpulse.warehouse import build


class FakeKernel32:
    """Stands in for kernel32, and keeps what SetProcessInformation was asked."""

    def __init__(self, result: int = 1) -> None:
        self.calls: list[tuple[Any, ...]] = []

        def current_process() -> int:
            return 1234

        def set_information(*arguments: Any) -> int:
            self.calls.append(arguments)
            return result

        # Plain functions, which take the restype and argtypes the real calls are given.
        self.GetCurrentProcess = current_process
        self.SetProcessInformation = set_information


def _on_windows(monkeypatch: pytest.MonkeyPatch, fake: FakeKernel32 | Exception) -> None:
    def load(name: str, use_last_error: bool = False) -> FakeKernel32:
        assert name == "kernel32"
        if isinstance(fake, Exception):
            raise fake
        return fake

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", load, raising=False)


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_the_opt_out_does_nothing_outside_windows(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    def fails(*arguments: Any, **options: Any) -> None:
        raise AssertionError("no Windows call outside Windows")

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(ctypes, "WinDLL", fails, raising=False)
    assert cli.opt_out_of_throttling() is False


def test_on_windows_execution_speed_throttling_is_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeKernel32()
    _on_windows(monkeypatch, fake)
    assert cli.opt_out_of_throttling() is True
    ((process, information_class, pointer, size),) = fake.calls
    assert process == 1234
    assert information_class == 4  # ProcessPowerThrottling
    assert size == 12  # three 32-bit fields
    state = pointer._obj
    assert (state.Version, state.ControlMask, state.StateMask) == (1, 1, 0)


@pytest.mark.parametrize("problem", [FakeKernel32(result=0), OSError("no such call")])
def test_a_refused_request_is_only_a_false(
    monkeypatch: pytest.MonkeyPatch, problem: FakeKernel32 | Exception
) -> None:
    _on_windows(monkeypatch, problem)
    assert cli.opt_out_of_throttling() is False


@pytest.mark.skipif(sys.platform != "win32", reason="the real call needs Windows")
def test_the_real_call_is_accepted_on_windows() -> None:
    assert cli.opt_out_of_throttling() is True


def test_run_asks_before_the_command_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    def main(argv: Sequence[str] | None) -> int:
        order.append("command")
        return 0

    monkeypatch.setattr(cli, "opt_out_of_throttling", lambda: order.append("opt-out") or True)
    assert cli.run(main) == 0
    assert order == ["opt-out", "command"]


def test_each_worker_of_the_warehouse_build_opts_out_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def pool(**options: Any) -> str:
        seen.update(options)
        return "pool"

    monkeypatch.setattr(build, "ProcessPoolExecutor", pool)
    assert build.worker_pool(3) == "pool"
    assert seen == {"max_workers": 3, "initializer": cli.opt_out_of_throttling}
    assert isinstance(build.worker_pool(1), build._InlineExecutor)


def test_run_goes_on_when_the_request_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "opt_out_of_throttling", lambda: False)
    assert cli.run(lambda argv: 7) == 7


def test_a_refusal_still_prints_one_line_and_its_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def main(argv: Sequence[str] | None) -> int:
        raise cli.RefusedError("not today")

    monkeypatch.setattr(cli, "opt_out_of_throttling", lambda: False)
    assert cli.run(main) == cli.REFUSED_EXIT_CODE
    assert capsys.readouterr().err == "refused: not today\n"
