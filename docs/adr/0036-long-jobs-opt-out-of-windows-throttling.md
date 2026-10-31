# 0036. Long jobs opt out of Windows' throttling

- Date: 2026-10-31
- Status: **Accepted.** Approved by Aakrisht in the review of Step 13 on 2026-10-31, as a one-call opt-out in `trialpulse.cli.run`, a no-op outside Windows, with a test.
- Changes how commands start, not what they compute. No definition, threshold or result changes.

## Context

Windows can hold a background process on the four efficiency cores of this laptop (an i7-12650H: 6 performance cores, 4 efficiency cores), through its execution-speed throttling. The review of Step 13 found it behind the flat timings of ADR 0035. 8 spin processes ran at 31.6 million counts a second as started and at 121 to 126 million when each opted out, alternating twice. A full scoring run at the cutoff with drivers took 304 s with the opt-out against 1,443 s and 1,495 s without it, with identical hashes.

The state comes and goes by itself: two later runs without the opt-out took 485 s and 368 s. Why, and whether a run from a terminal in the foreground is affected, are not established.

## Decision

1. `trialpulse.cli.run` calls `SetProcessInformation(ProcessPowerThrottling)` before the command starts, with the execution-speed setting controlled and off. Every command started through it is covered, with its threads (DuckDB and LightGBM run in threads).
2. Outside Windows the call does nothing, so GitHub's Linux runners are unaffected.
3. The request is a hint. If Windows refuses it, the command goes on.
4. A child process does not inherit the setting: with `GetProcessInformation`, the parent read control 1 and state 0 after the call, and a new worker read 0 and 0. So the warehouse build's worker processes make the same call when they start.
5. **Long jobs still run only with the charger in.** The opt-out draws more power, and the laptop has already shut down on battery during a long run (Step 11).

## Alternatives

- **Leaving it.** The jobs known today fit under the hour, but timings would keep varying by up to about 4.7 times, and later long jobs would pay for it.
- **Changing the Windows power plan or its scheduling policy.** It would cover everything, but it is a setting of the machine, not of the project, and the repository could not carry it.
- **Worker processes instead of threads.** They were throttled in the same way.

## Consequences

- Long jobs should run several times faster when Windows would otherwise throttle them, with more heat and power draw. Results do not change: the run above gave identical hashes.
- Timings in the reports of earlier steps stay as measured. They were probably throttled in part, so they do not compare with later ones. Standby is a separate effect, and an unattended run's timing stays suspect.
- A script that does not go through `run`, such as a scratch measurement, gets no opt-out.
