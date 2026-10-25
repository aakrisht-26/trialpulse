"""Example risk timelines (Step 12, ADR 0033).

Three trials are drawn with a seed from the trials of the locked test origins: one that
stopped early, one that completed and one still open at the data cutoff. A trial is eligible
when it has evaluation rows at both test origins, so its timeline crosses the two models,
and its outcome is one of the three (a trial censored by the lapse rule of ADR 0014 is not).
Within each kind, the eligible trials are sorted by registry id and one is chosen with
numpy's generator seeded by the project's seed, in the order stopped, completed, open.

A timeline holds the champion's scores at each of the trial's test landmarks, as the locked
run saved them: every score is the one its origin's model gave with the information posted
by the landmark. Beside them are the registry updates of the trial (changes of status, start
date, primary completion date or enrollment) and its outcome. Nothing here reads a name or a
contact: the versions are read by column, and the sponsor appears only as its class.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

STOPPED, COMPLETED, OPEN = "stopped early", "completed", "open at the data cutoff"
KINDS: tuple[str, ...] = (STOPPED, COMPLETED, OPEN)
EVENT_OF = {STOPPED: 1, COMPLETED: 2}
MAX_UPDATES = 40  # updates listed per trial; the rest are counted, not dropped silently
# The version columns a timeline reads: no name, contact or free text.
VERSION_COLUMNS = (
    "nct_version",
    "effective_date",
    "overall_status",
    "last_known_status",
    "start_date",
    "primary_completion_date",
    "enrollment_count",
    "enrollment_type",
    "lead_sponsor_class",
    "study_first_post_date",
)


@dataclass(frozen=True)
class Point:
    origin: str
    landmark_index: int
    landmark_date: dt.date
    score_12: float
    score_24: float


@dataclass(frozen=True)
class Update:
    date: dt.date
    changes: tuple[str, ...]


@dataclass
class Timeline:
    kind: str
    trial_id: str
    points: list[Point]
    outcome_date: dt.date  # the early stop or completion, or the data cutoff when open
    sponsor_class: str = ""
    updates: list[Update] = field(default_factory=list)
    updates_not_listed: int = 0
    first_post: dt.date | None = None  # t0, from the record (CLAUDE.md Section 6)


def outcome_kind(event: int, event_date: dt.date, cutoff: dt.date) -> str | None:
    """The kind of a trial's outcome through the data cutoff, or None for a censoring before
    it (the lapse rule of ADR 0014)."""
    if event == 1:
        return STOPPED
    if event == 2:
        return COMPLETED
    return OPEN if event_date == cutoff else None


def scored_rows(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """The saved scores of the test origins in one frame, with the origin and the event date
    of each row (its landmark date plus its follow-up time)."""
    parts = []
    for origin, frame in sorted(frames.items()):
        part = frame.copy()
        part["origin"] = origin
        part["landmark_date"] = pd.to_datetime(part["landmark_date"]).dt.date
        part["event_date"] = [
            d + dt.timedelta(days=int(t))
            for d, t in zip(part["landmark_date"], part["time"], strict=True)
        ]
        parts.append(part)
    return pd.concat(parts, ignore_index=True)


def eligible(rows: pd.DataFrame, cutoff: dt.date, origins: Sequence[str]) -> dict[str, list[str]]:
    """The eligible trials of each kind, sorted by registry id (see the module text)."""
    out: dict[str, list[str]] = {kind: [] for kind in KINDS}
    for trial_id, group in rows.groupby("trial_id", sort=True):
        if set(group["origin"]) != set(origins):
            continue
        last = group.sort_values(["landmark_date", "landmark_index"]).iloc[-1]
        kind = outcome_kind(int(last["event"]), last["event_date"], cutoff)
        if kind is not None:
            out[kind].append(str(trial_id))
    return out


def draw(candidates: dict[str, list[str]], seed: int) -> dict[str, str]:
    """One trial of each kind, by a generator seeded once, in the order of KINDS."""
    generator = np.random.default_rng(seed)
    chosen = {}
    for kind in KINDS:
        pool = sorted(candidates[kind])
        if not pool:
            raise ValueError(f"no eligible trial {kind}")
        chosen[kind] = pool[int(generator.integers(len(pool)))]
    return chosen


def timeline(rows: pd.DataFrame, trial_id: str, kind: str) -> Timeline:
    """The scores of one trial at its test landmarks, in time order."""
    own = rows[rows["trial_id"] == trial_id].sort_values(["landmark_date", "landmark_index"])
    if own.empty:
        raise ValueError(f"{trial_id} has no saved score")
    points = [
        Point(str(r.origin), int(r.landmark_index), r.landmark_date, float(r.score_12),
              float(r.score_24))
        for r in own.itertuples()
    ]  # fmt: skip
    return Timeline(kind, trial_id, points, own["event_date"].iloc[-1])


def _month(value: object) -> str:
    if value is None or pd.isna(value):
        return "none"
    return str(pd.Timestamp(value).strftime("%Y-%m"))


def _status(row: pd.Series) -> str:
    """The submitted status: the registry's UNKNOWN label shows the last known one (ADR 0014)."""
    status = row["overall_status"]
    return str(row["last_known_status"]) if status == "UNKNOWN" else str(status)


def _enrollment(row: pd.Series) -> str:
    count = row["enrollment_count"]
    if count is None or pd.isna(count):
        return "none"
    kind = str(row["enrollment_type"]).lower() if pd.notna(row["enrollment_type"]) else "no type"
    return f"{int(count)} ({kind})"


def updates_of(versions: pd.DataFrame, end: dt.date) -> list[Update]:
    """The registry updates of a trial up to `end`: each version posted on or before it that
    changed the status, the start month, the primary completion month or the enrollment. The
    first version is the registration."""
    ordered = versions.sort_values(["effective_date", "nct_version"]).reset_index(drop=True)
    out: list[Update] = []
    previous: pd.Series | None = None
    for _, row in ordered.iterrows():
        date = pd.Timestamp(row["effective_date"]).date()
        if date > end:
            break
        if previous is None:
            out.append(Update(date, (f"registered as {_status(row)}",)))
        else:
            changes = []
            pairs = (
                ("status", _status(previous), _status(row)),
                ("start", _month(previous["start_date"]), _month(row["start_date"])),
                ("primary completion", _month(previous["primary_completion_date"]),
                 _month(row["primary_completion_date"])),
                ("enrollment", _enrollment(previous), _enrollment(row)),
            )  # fmt: skip
            for what, before, after in pairs:
                if before != after:
                    changes.append(f"{what} {before} to {after}")
            if changes:
                out.append(Update(date, tuple(changes)))
        previous = row
    return out


def read_versions(warehouse: Path, trial_id: str) -> pd.DataFrame:
    """The versions of one trial, the columns of VERSION_COLUMNS only."""
    columns = ", ".join(VERSION_COLUMNS)
    with duckdb.connect(str(warehouse), read_only=True) as con:
        return con.execute(
            f"SELECT {columns} FROM versions WHERE nct_id = ? ORDER BY effective_date, nct_version",
            [trial_id],
        ).df()


def with_updates(line: Timeline, versions: pd.DataFrame) -> Timeline:
    """The timeline with the trial's registry updates up to its outcome (or the cutoff), the
    first MAX_UPDATES of them listed and the rest counted, its sponsor class at the last
    landmark and its first-post date (t0, the same in every version)."""
    every = updates_of(versions, line.outcome_date)
    line.updates = every[:MAX_UPDATES]
    line.updates_not_listed = len(every) - len(line.updates)
    posted = versions["study_first_post_date"].dropna()
    if not posted.empty:
        line.first_post = pd.Timestamp(posted.iloc[0]).date()
    last = line.points[-1].landmark_date
    known = versions[pd.to_datetime(versions["effective_date"]).dt.date <= last]
    if not known.empty:
        line.sponsor_class = str(
            known.sort_values(["effective_date", "nct_version"]).iloc[-1]["lead_sponsor_class"]
        )
    return line
