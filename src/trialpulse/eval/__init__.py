"""Evaluation harness: IPCW metrics at a horizon, the cluster bootstrap, the walk-forward
runner and the test lock (CLAUDE.md Sections 6 and 10, ADR 0004).

Event codes used throughout: 0 censored, 1 early stop (event of interest), 2 completion
(competing event). Times are days since the landmark.
"""

EVENT_CENSORED = 0
EVENT_STOP = 1
EVENT_COMPLETE = 2
