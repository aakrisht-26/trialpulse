"""Shared command-line handling: expected refusals, and Windows' throttling.

A command that refuses on purpose (a test split already scored, a provisional model sent to
test scoring, an uncommitted prompt, a missing input) raises RefusedError. The run() wrapper
prints one line to stderr and returns a non-zero exit code, with no traceback:

- "refused: <reason>", exit code 2 (the same code as the test lock's refusal in Step 8);
- "stopped: <reason>", exit code 4, for a run that stopped after saving its progress
  because of a provider limit or outage (a daily or long rate limit, or network or server
  errors that outlasted the retries), so a daily scheduled run can tell "try again later"
  from a refusal. A client error such as a wrong key or model is a refusal (exit code 2).

Any other exception is a bug and keeps its traceback.

run() also asks Windows not to throttle the process before the command starts (ADR 0036).
Windows can hold a background job on the efficiency cores, which made a full scoring run
about 4.7 times slower with the same results. Outside Windows the request does nothing.
"""

import ctypes
import sys
from collections.abc import Callable, Sequence

REFUSED_EXIT_CODE = 2
STOPPED_EXIT_CODE = 4
PROCESS_POWER_THROTTLING = 4  # ProcessPowerThrottling, a PROCESS_INFORMATION_CLASS
THROTTLING_VERSION = 1  # PROCESS_POWER_THROTTLING_CURRENT_VERSION
EXECUTION_SPEED = 1  # PROCESS_POWER_THROTTLING_EXECUTION_SPEED


class RefusedError(Exception):
    """An expected refusal: one line on stderr, exit code 2."""

    exit_code = REFUSED_EXIT_CODE
    prefix = "refused"


class StoppedEarlyError(RefusedError):
    """A run that stopped at a limit after saving its progress: one line, exit code 4."""

    exit_code = STOPPED_EXIT_CODE
    prefix = "stopped"


class ThrottlingState(ctypes.Structure):
    """PROCESS_POWER_THROTTLING_STATE: its three fields are 32-bit on Windows."""

    _fields_ = (
        ("Version", ctypes.c_uint32),
        ("ControlMask", ctypes.c_uint32),
        ("StateMask", ctypes.c_uint32),
    )


def opt_out_of_throttling() -> bool:
    """Ask Windows not to throttle this process's execution speed: the setting is controlled
    here and switched off. Whether the request was accepted; False outside Windows. A speed
    hint never stops a job, so a refused request is only a False."""
    if sys.platform != "win32":
        return False
    state = ThrottlingState(THROTTLING_VERSION, EXECUTION_SPEED, 0)
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetProcessInformation.argtypes = (
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
        )  # fmt: skip
        done = kernel32.SetProcessInformation(
            kernel32.GetCurrentProcess(), PROCESS_POWER_THROTTLING,
            ctypes.byref(state), ctypes.sizeof(state),
        )  # fmt: skip
    except (AttributeError, OSError):  # a Windows too old to have the call
        return False
    return bool(done)


def run(main: Callable[[Sequence[str] | None], int], argv: Sequence[str] | None = None) -> int:
    """Call a command's main, opted out of Windows' throttling, and turn an expected refusal
    into one line and an exit code."""
    opt_out_of_throttling()
    try:
        return main(argv)
    except RefusedError as exc:
        print(f"{exc.prefix}: {' '.join(str(exc).split())}", file=sys.stderr)
        return exc.exit_code
