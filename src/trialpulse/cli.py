"""Shared command-line handling for expected refusals.

A command that refuses on purpose (a test split already scored, a provisional model sent to
test scoring, an uncommitted prompt, a missing input) raises RefusedError. The run() wrapper
prints one line to stderr and returns a non-zero exit code, with no traceback:

- "refused: <reason>", exit code 2 (the same code as the test lock's refusal in Step 8);
- "stopped: <reason>", exit code 4, for a run that stopped at a provider limit after saving
  its progress, so a daily scheduled run can tell "try again later" from a refusal.

Any other exception is a bug and keeps its traceback.
"""

import sys
from collections.abc import Callable, Sequence

REFUSED_EXIT_CODE = 2
STOPPED_EXIT_CODE = 4


class RefusedError(Exception):
    """An expected refusal: one line on stderr, exit code 2."""

    exit_code = REFUSED_EXIT_CODE
    prefix = "refused"


class StoppedEarlyError(RefusedError):
    """A run that stopped at a limit after saving its progress: one line, exit code 4."""

    exit_code = STOPPED_EXIT_CODE
    prefix = "stopped"


def run(main: Callable[[Sequence[str] | None], int], argv: Sequence[str] | None = None) -> int:
    """Call a command's main and turn an expected refusal into one line and an exit code."""
    try:
        return main(argv)
    except RefusedError as exc:
        print(f"{exc.prefix}: {' '.join(str(exc).split())}", file=sys.stderr)
        return exc.exit_code
