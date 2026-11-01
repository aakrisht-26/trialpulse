"""The sponsor file of the live path (ADR 0037): the resumable sponsor pass of ADR 0018 and
the thin data of every trial, in one stored file.

**What it holds.** For every trial: its last post date, its sponsor (a keyed id, ADR 0044,
or none), its ended code (open, stopped, completed: interventional states only, as offline)
and whether a state of it was ever terminal. For every sponsor id, its number in the pass.
The pass itself (`features.sponsor.SponsorPass`). And the last day whose events were
applied (`through_day`) and the last run whose versions were consumed (`last_run_id`).

**Advancing it.** `advance` takes compact states in post-date order (the latest record of a
trial and post date) and turns each into the events the offline build derives from the same
state against the one before it (`features.sponsor.collect_events`): an appearance, a
replacement, a registration, a trial leaving or entering an ended code. Events are applied
day by day through the day before the run's date, so the queries of the run's date are
answered before anything posted that day, as offline. A record posted on or before the last
applied day (a late record) is applied on the day after it.

**Order within a day.** By kind, then by the sponsor ids' text and the trial ids' text,
which is the offline order when the offline build reads the same ids (ADR 0044, item 4).

**Point of commit.** The file is the pass's only commit point: a run writes it once, after
the pass, so state may run ahead of the file but never the reverse, and a rerun reads only
the versions of later runs.
"""

import datetime as dt
import hashlib
import json
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from trialpulse.cohort.rules import CohortRules
from trialpulse.features import sponsor as sp

EPOCH = dt.date(1970, 1, 1)
FORMAT = 1
OPEN_CODE = 0
QUEUED: tuple[str, ...] = ("nct_id", "effective_date", "status", "study_type", "sponsor_id")


def day_number(day: dt.date) -> int:
    return (day - EPOCH).days


def from_day_number(number: int) -> dt.date:
    return EPOCH + dt.timedelta(days=number)


@dataclass
class Thin:
    last_post: int  # day number
    key: int  # the sponsor's number in the pass, -1 without an identity
    term: int  # 0, sp.STOP or sp.COMPLETE
    ever_terminal: bool


@dataclass
class SponsorFile:
    pass_: sp.SponsorPass
    through_day: dt.date  # events of this day and earlier are applied
    last_run_id: int | None
    key_numbers: dict[str, int] = field(default_factory=dict)
    trial_numbers: dict[str, int] = field(default_factory=dict)
    thin: dict[str, Thin] = field(default_factory=dict)
    # States posted on or after the run's date: consumed from their run, applied later.
    queue: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def empty(cls, alias_min_trials: int, alias_wait_days: int, through_day: dt.date) -> (
        "SponsorFile"
    ):  # fmt: skip
        return cls(sp.SponsorPass(alias_min_trials, alias_wait_days), through_day, None)

    # Numbering ----------------------------------------------------------------------------

    def key_number(self, sponsor_id: str | None) -> int:
        if sponsor_id is None:
            return -1
        number = self.key_numbers.get(sponsor_id)
        if number is None:
            number = self.key_numbers[sponsor_id] = len(self.key_numbers)
            self.pass_.grow(len(self.key_numbers), self.pass_.n_trials)
        return number

    def trial_number(self, nct_id: str) -> int:
        number = self.trial_numbers.get(nct_id)
        if number is None:
            number = self.trial_numbers[nct_id] = len(self.trial_numbers)
            self.pass_.grow(self.pass_.n_keys, len(self.trial_numbers))
        return number

    # Advancing ----------------------------------------------------------------------------

    def advance(
        self, states: Iterable[Mapping[str, Any]], day: dt.date, rules: CohortRules
    ) -> None:
        """Apply the events of the queue and of `states` posted before `day`; the states
        posted on or after it join the queue for a later run. `states` are compact rows, at
        most one per trial and post date. Afterwards `through_day` is `day` minus one day
        (or later, if it was already)."""
        last = day - dt.timedelta(days=1)
        pending = sorted(
            [*self.queue, *({c: s.get(c) for c in QUEUED} for s in states)],
            key=lambda s: (s["effective_date"], s["nct_id"]),
        )
        self.queue = []
        events: list[tuple[int, int, int, int, int]] = []
        floor = day_number(self.through_day) + 1
        for state in pending:
            posted: dt.date = state["effective_date"]
            if posted > last:
                self.queue.append(state)
                continue
            events += self._derive(state, max(day_number(posted), floor), rules)
        names = {v: k for k, v in self.key_numbers.items()}
        trials = {v: k for k, v in self.trial_numbers.items()}
        events.sort(key=lambda event: self._sort_key(event, names, trials))
        for applied, kind, a, b, c in events:
            self.pass_.apply(applied, kind, a, b, c)
        if last > self.through_day:
            self.through_day = last

    def _derive(
        self, state: Mapping[str, Any], applied: int, rules: CohortRules
    ) -> list[tuple[int, int, int, int, int]]:
        nct_id = str(state["nct_id"])
        first = nct_id not in self.thin
        trial = self.trial_number(nct_id)
        key = self.key_number(state.get("sponsor_id"))
        interventional = state.get("study_type") == rules.study_type
        status = str(state["status"])
        term = OPEN_CODE
        if interventional and status in rules.early_stop:
            term = sp.STOP
        elif interventional and status in rules.competing:
            term = sp.COMPLETE
        before = self.thin.get(nct_id)
        prev_key = before.key if before else -1
        prev_term = before.term if before else OPEN_CODE
        out: list[tuple[int, int, int, int, int]] = []
        if key >= 0:
            out.append((applied, sp.APPEARANCE, key, trial, 0))
        if not first and prev_key >= 0 and key != prev_key:
            out.append((applied, sp.REPLACEMENT, prev_key, key, trial))
        if first and interventional and key >= 0:
            out.append((applied, sp.REGISTRATION, key, trial, 0))
        if prev_term > 0 and prev_key >= 0 and (key != prev_key or term != prev_term):
            out.append((applied, sp.LEAVE, prev_key, prev_term, trial))
        if term > 0 and key >= 0 and (first or key != prev_key or term != prev_term):
            out.append((applied, sp.ENTER, key, term, trial))
        self.thin[nct_id] = Thin(
            last_post=max(day_number(state["effective_date"]), before.last_post if before else 0),
            key=key,
            term=term,
            ever_terminal=(before.ever_terminal if before else False) or status in rules.terminal,
        )
        return out

    @staticmethod
    def _sort_key(
        event: tuple[int, int, int, int, int], names: Mapping[int, str], trials: Mapping[int, str]
    ) -> tuple[Any, ...]:
        """By day and kind, then by the ids' text: key fields by sponsor id ("" for none),
        trial fields by registry id, ended codes by number."""
        applied, kind, a, b, c = event
        if kind in (sp.APPEARANCE, sp.REGISTRATION):
            return (applied, kind, names[a], trials[b], 0)
        if kind == sp.REPLACEMENT:
            return (applied, kind, names[a], names.get(b, "") if b >= 0 else "", trials[c])
        return (applied, kind, names[a], b, trials[c])  # ENTER, LEAVE

    # Queries ------------------------------------------------------------------------------

    def answer(self, day: dt.date, sponsor_id: str | None, nct_id: str) -> tuple[int, int, int]:
        """The record of `sponsor_id` as of `day` for a row of trial `nct_id` (ADR 0018): the
        pass must have applied every day before `day` and nothing of `day`."""
        if self.through_day != day - dt.timedelta(days=1):
            raise ValueError(
                f"the sponsor pass is through {self.through_day}; a query of {day} needs it "
                f"through {day - dt.timedelta(days=1)}"
            )
        if sponsor_id is None:
            raise ValueError("a sponsor without an identity has no record")
        if sponsor_id not in self.key_numbers:  # a sponsor first seen today: nothing before
            return 0, 0, 0
        trial = self.trial_numbers.get(nct_id, -1)  # -1: a trial the pass never saw
        return self.pass_.answer(day_number(day), self.key_numbers[sponsor_id], trial)

    # Storage ------------------------------------------------------------------------------

    def to_bytes(self) -> bytes:
        trials = sorted(self.thin)
        payload = {
            "format": FORMAT,
            "through_day": self.through_day.isoformat(),
            "last_run_id": self.last_run_id,
            "keys": sorted(self.key_numbers.items(), key=lambda kv: kv[1]),
            "trials": sorted(self.trial_numbers.items(), key=lambda kv: kv[1]),
            "thin": [
                [
                    t,
                    self.thin[t].last_post,
                    self.thin[t].key,
                    self.thin[t].term,
                    int(self.thin[t].ever_terminal),
                ]
                for t in trials
            ],
            "queue": [{**q, "effective_date": q["effective_date"].isoformat()} for q in self.queue],
            "pass": self.pass_.to_bytes().hex(),
        }
        return zlib.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"), 6)

    @classmethod
    def from_bytes(cls, data: bytes) -> "SponsorFile":
        payload = json.loads(zlib.decompress(data).decode("utf-8"))
        if payload.get("format") != FORMAT:
            raise ValueError(f"a sponsor file of format {payload.get('format')}, not {FORMAT}")
        out = cls(
            sp.SponsorPass.from_bytes(bytes.fromhex(payload["pass"])),
            dt.date.fromisoformat(payload["through_day"]),
            payload["last_run_id"],
        )
        out.key_numbers = {k: int(v) for k, v in payload["keys"]}
        out.trial_numbers = {k: int(v) for k, v in payload["trials"]}
        out.thin = {t: Thin(int(p), int(k), int(e), bool(x)) for t, p, k, e, x in payload["thin"]}
        out.queue = [
            {**q, "effective_date": dt.date.fromisoformat(q["effective_date"])}
            for q in payload["queue"]
        ]
        return out

    def digest(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()
