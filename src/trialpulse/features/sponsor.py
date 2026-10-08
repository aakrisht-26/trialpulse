"""The sponsor's track record as of the landmark (CLAUDE.md Section 8, ADR 0018).

For the sponsor of a landmark row, three counts over the sponsor's other interventional
trials, from versions posted strictly before the landmark:

- registrations: trials first posted under the sponsor;
- ended: trials the registry showed as completed, terminated or withdrawn;
- stopped: of those, the terminated or withdrawn ones.

"Shown as" means the version in effect: a trial that was terminated and reopened before the
landmark is not ended. The early-stop rate is stopped over ended, smoothed toward the class
rate per origin (`trialpulse.features.transforms`).

**Identity.** A sponsor is its normalized name (`sponsor_key`). Individuals, person-named
sponsors and placeholder names have no identity (`contracts.sponsor.has_identity`): no
count is computed for them, and the rate falls back to the class rate.

**Aliases (ADR 0018).** The registry renames organizations without posting new versions: a
record shows the new name only from its next version on. A key's history therefore passes
to the key that replaced it, under a rule that uses nothing posted on or after the landmark.
A trial's key on a day is the key of the version in effect at the end of that day. Take
the versions posted before day L, and for key A the run that starts with the first
replacement of A (a trial posts a version, and its key is another one where it was A).
Count the trials that have left A since, by the key they went to, and the trials that still
post under A: those with a version posted after the run's first day whose key is A, and
that have not left A since.

    Key A is an alias of key B on day L if B took at least two of the trials that left A,
    those are more than half of the trials that left A or still post under A, and the
    run's first replacement was posted more than 365 days before L.

The run is forgotten, and A stands alone again, once more trials still post under A than
have left it: a sponsor that goes on using its name handed trials over and was not renamed.
The year gives the old name's other trials time to show up: the registry asks for an
update of every active record at least once in 12 months. Three choices follow from
keeping the rule this simple:

- a trial that left A for a name without an identity counts as having left A, for nobody;
- a trial that left A and came back counts once as having left and once as still posting,
  and one that leaves twice counts twice;
- chains follow (A to B, later B to C), and a link that would close a loop is not made. It
  stays unmade until the run's majority changes hands, even if the loop opens again.

The record of a sponsor is the sum over its key and every key that is an alias of it on
that day.

`track_records` computes all of this in one pass over the registry in date order. On each
day the landmark rows of that day are answered first, from what earlier days left; then the
day's versions are applied. That order is what makes the counts point-in-time.
"""

import heapq
from collections.abc import Iterator
from dataclasses import dataclass

import duckdb
import numpy as np
import numpy.typing as npt
import pandas as pd

from trialpulse.cohort.rules import CohortRules, sql_list, submitted_status_sql
from trialpulse.contracts.sponsor import has_identity_sql
from trialpulse.features.states import AT_LANDMARK_TABLE

IntArray = npt.NDArray[np.int64]
TRACK_RECORD_TABLE = "feature_sponsor_record"
EPOCH = "DATE '1970-01-01'"

# Event kinds. The number is also the order within a day: the day's landmark rows are
# answered before anything posted that day is applied.
QUERY = 0
APPEARANCE = 1  # trial b posts a version and its key is a
REPLACEMENT = 2  # trial c's key changes from a to b (b is -1 for a name without identity)
REGISTRATION = 3  # a trial is first posted under a key
LEAVE = 4  # a trial stops being shown as ended under a key
ENTER = 5  # a trial is shown as ended under a key (b: 1 early stop, 2 completion)

STOP, COMPLETE = 1, 2
CHUNK = 500_000  # events turned into Python numbers at a time


@dataclass(frozen=True)
class Events:
    """Registry events and landmark queries, one entry each, in any order."""

    day: IntArray  # days since 1970-01-01
    kind: IntArray
    a: IntArray
    b: IntArray
    c: IntArray

    def __len__(self) -> int:
        return len(self.day)

    def order(self) -> IntArray:
        """The order events are applied in: by day, then by kind, then by their fields. The
        last part only makes the order total, so that the result never depends on the order
        the database returned the rows in (two names that replace each other on one day are
        the one case where it could)."""
        result: IntArray = np.lexsort((self.c, self.b, self.a, self.kind, self.day))
        return result

    def in_order(self) -> Iterator[tuple[int, int, int, int, int]]:
        """Each event as (day, kind, a, b, c), in the order they are applied. They become
        Python numbers a chunk at a time: one list of all of them would take gigabytes."""
        order = self.order()
        columns = (self.day, self.kind, self.a, self.b, self.c)
        for start in range(0, len(order), CHUNK):
            part = order[start : start + CHUNK]
            yield from zip(*(column[part].tolist() for column in columns), strict=True)


def track_records(
    events: Events,
    n_keys: int,
    n_trials: int,
    n_queries: int,
    alias_min_trials: int,
    alias_wait_days: int,
) -> IntArray:
    """Answer every query: (registrations, ended, stopped) of the sponsor's other trials,
    from the events of earlier days. Event fields by kind:

    - QUERY: a = the row's sponsor key, b = the row's trial, c = the query's number;
    - APPEARANCE: a = the trial's key after a version it posted that day, b = the trial;
    - REPLACEMENT: a = the key replaced, b = the key that replaced it, or -1 when the new
      name has no identity, c = the trial;
    - REGISTRATION: a = the key, b = the trial;
    - ENTER and LEAVE: a = the key, b = STOP or COMPLETE, c = the trial.
    """
    parent = [-1] * n_keys  # the key this key is an alias of today
    # The run of a key: since its first replacement, how many trials each key took over,
    # and which trials still post under the old key (a version after the run's first day).
    run_start = [-1] * n_keys
    left_for: list[dict[int, int] | None] = [None] * n_keys
    left = [0] * n_keys
    still: list[set[int] | None] = [None] * n_keys
    successor = [-1] * n_keys  # the key that holds the majority of the run, if any
    stamp = [0] * n_keys  # changes whenever the successor does: a waiting link is then stale
    waiting: list[tuple[int, int, int]] = []  # (first day the link counts, key, stamp)
    regs, ended, stopped = [0] * n_keys, [0] * n_keys, [0] * n_keys  # of the key and its aliases
    reg_key = [-1] * n_trials
    term_key, term_kind = [-1] * n_trials, [0] * n_trials
    out = np.full((n_queries, 3), -1, dtype=np.int64)

    def root(k: int) -> int:
        while parent[k] != -1:
            k = parent[k]
        return k

    def add(k: int, dr: int, de: int, ds: int) -> None:
        while k != -1:
            regs[k] += dr
            ended[k] += de
            stopped[k] += ds
            k = parent[k]

    def cut(k: int) -> None:
        above = parent[k]
        if above != -1:
            parent[k] = -1
            add(above, -regs[k], -ended[k], -stopped[k])

    def choose(k: int, day: int, risen: int) -> None:
        """Set the successor of key k after its run changed. Only `risen`, the key whose
        count just rose, or the successor so far can hold the majority."""
        takers, posting = left_for[k], still[k]
        assert takers is not None
        assert posting is not None
        total = left[k] + len(posting)
        wanted = -1
        for candidate in (risen, successor[k]):
            taken = takers.get(candidate, 0) if candidate >= 0 else 0
            if taken >= alias_min_trials and 2 * taken > total:
                wanted = candidate
                break
        if wanted != successor[k]:
            cut(k)
            successor[k] = wanted
            stamp[k] += 1
            if wanted != -1:  # it counts once the run is old enough
                first_day = max(day, run_start[k] + alias_wait_days) + 1
                heapq.heappush(waiting, (first_day, k, stamp[k]))

    for day, kind, a, b, c in events.in_order():
        while waiting and waiting[0][0] <= day:  # links that count from today on
            _, key, issued = heapq.heappop(waiting)
            target = successor[key]
            if issued == stamp[key] and target != -1 and root(target) != key:  # never a loop
                parent[key] = target
                add(target, regs[key], ended[key], stopped[key])
        if kind == QUERY:
            r = root(a)
            n_reg, n_ended, n_stopped = regs[r], ended[r], stopped[r]
            own = reg_key[b]
            if own != -1 and root(own) == r:  # the row's own trial is not "another trial"
                n_reg -= 1
            own = term_key[b]
            if own != -1 and root(own) == r:
                n_ended -= 1
                n_stopped -= term_kind[b] == STOP
            out[c] = (n_reg, n_ended, n_stopped)
        elif kind == APPEARANCE:
            posting = still[a]
            if posting is not None and day > run_start[a] and b not in posting:
                posting.add(b)
                if len(posting) > left[a]:  # the name is in use: it was not renamed
                    cut(a)
                    left_for[a], left[a], still[a], run_start[a] = None, 0, None, -1
                    successor[a] = -1
                    stamp[a] += 1
                else:
                    choose(a, day, -1)
        elif kind == REPLACEMENT:
            takers = left_for[a]
            posting = still[a]
            if takers is None or posting is None:  # the first replacement opens a run
                takers = left_for[a] = {}
                posting = still[a] = set()
                run_start[a] = day
            if b >= 0:
                takers[b] = takers.get(b, 0) + 1
            left[a] += 1
            posting.discard(c)  # the trial no longer posts under the old key
            choose(a, day, b)
        elif kind == REGISTRATION:
            add(a, 1, 0, 0)
            reg_key[b] = a
        elif kind == ENTER:
            add(a, 0, 1, int(b == STOP))
            term_key[c], term_kind[c] = a, b
        else:  # LEAVE
            add(a, 0, -1, -int(b == STOP))
            term_key[c], term_kind[c] = -1, 0
    return out


def _numbers(con: duckdb.DuckDBPyConnection, sql: str, n: int) -> list[IntArray]:
    data = con.execute(sql).fetchnumpy()
    return [np.asarray(data[f"c{i}"], dtype=np.int64) for i in range(n)]


def build_sponsor_states(con: duckdb.DuckDBPyConnection, rules: CohortRules) -> None:
    """`feature_sponsor_states`: every trial's states with the sponsor key (NULL without an
    identity) and whether the state is an ended interventional trial, beside the values of
    the state before. `feature_sponsor_keys` and `feature_sponsor_trials` number the keys
    and the trials."""
    status = submitted_status_sql(rules)
    identity = has_identity_sql("v.sponsor_key", "v.sponsor_is_individual")
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE feature_sponsor_versions AS
        SELECT v.nct_id, v.nct_version, v.effective_date,
          CASE WHEN {identity} THEN v.sponsor_key END AS key,
          coalesce(v.study_type = '{rules.study_type}', false) AS interventional,
          CASE WHEN coalesce(v.study_type = '{rules.study_type}', false) THEN
            CASE WHEN {status} IN {sql_list(rules.early_stop)} THEN {STOP}
                 WHEN {status} IN {sql_list(rules.competing)} THEN {COMPLETE} ELSE 0 END
          ELSE 0 END AS term
        FROM versions v"""
    )
    con.execute(
        """CREATE OR REPLACE TEMP TABLE feature_sponsor_states AS
        WITH s AS (
          SELECT * FROM feature_sponsor_versions
          QUALIFY row_number() OVER (
            PARTITION BY nct_id, effective_date ORDER BY nct_version DESC) = 1
        )
        SELECT s.*, lag(key) OVER history AS prev_key, lag(term) OVER history AS prev_term,
          row_number() OVER history = 1 AS is_first
        FROM s WINDOW history AS (PARTITION BY nct_id ORDER BY effective_date)"""
    )
    con.execute(
        """CREATE OR REPLACE TEMP TABLE feature_sponsor_keys AS
        SELECT key, row_number() OVER (ORDER BY key) - 1 AS code
        FROM (SELECT DISTINCT key FROM feature_sponsor_versions WHERE key IS NOT NULL)"""
    )
    con.execute(
        """CREATE OR REPLACE TEMP TABLE feature_sponsor_trials AS
        SELECT nct_id, row_number() OVER (ORDER BY nct_id) - 1 AS code
        FROM (SELECT DISTINCT nct_id FROM feature_sponsor_versions)"""
    )


def collect_events(con: duckdb.DuckDBPyConnection) -> tuple[Events, int, int, int]:
    """The events of `feature_sponsor_states` and one query per landmark row whose sponsor
    has an identity. Returns the events and the numbers of keys, trials and queries.
    Needs `feature_sponsor_queries` (trial_id, landmark_index, landmark_date, key)."""
    day = f"date_diff('day', {EPOCH}, {{}})"
    parts = [
        f"""SELECT {day.format("q.landmark_date")} AS c0, {QUERY} AS c1, k.code AS c2,
              t.code AS c3, q.number AS c4
            FROM feature_sponsor_queries q
            JOIN feature_sponsor_keys k USING (key)
            JOIN feature_sponsor_trials t ON t.nct_id = q.trial_id""",
        f"""SELECT {day.format("s.effective_date")}, {APPEARANCE}, k.code, t.code, 0
            FROM feature_sponsor_states s
            JOIN feature_sponsor_keys k USING (key)
            JOIN feature_sponsor_trials t USING (nct_id)""",
        f"""SELECT {day.format("s.effective_date")}, {REPLACEMENT}, a.code,
              coalesce(b.code, -1), t.code
            FROM feature_sponsor_states s
            JOIN feature_sponsor_keys a ON a.key = s.prev_key
            LEFT JOIN feature_sponsor_keys b ON b.key = s.key
            JOIN feature_sponsor_trials t USING (nct_id)
            WHERE s.key IS DISTINCT FROM s.prev_key""",
        f"""SELECT {day.format("s.effective_date")}, {REGISTRATION}, k.code, t.code, 0
            FROM feature_sponsor_states s
            JOIN feature_sponsor_keys k USING (key)
            JOIN feature_sponsor_trials t USING (nct_id)
            WHERE s.is_first AND s.interventional""",
        f"""SELECT {day.format("s.effective_date")}, {LEAVE}, k.code, s.prev_term, t.code
            FROM feature_sponsor_states s
            JOIN feature_sponsor_keys k ON k.key = s.prev_key
            JOIN feature_sponsor_trials t USING (nct_id)
            WHERE s.prev_term > 0
              AND (s.key IS DISTINCT FROM s.prev_key OR s.term <> s.prev_term)""",
        f"""SELECT {day.format("s.effective_date")}, {ENTER}, k.code, s.term, t.code
            FROM feature_sponsor_states s
            JOIN feature_sponsor_keys k USING (key)
            JOIN feature_sponsor_trials t USING (nct_id)
            WHERE s.term > 0 AND (s.is_first OR s.key IS DISTINCT FROM s.prev_key
              OR s.term IS DISTINCT FROM s.prev_term)""",
    ]
    columns = _numbers(con, " UNION ALL ".join(parts), 5)
    counts = con.execute(
        """SELECT (SELECT count(*) FROM feature_sponsor_keys),
          (SELECT count(*) FROM feature_sponsor_trials),
          (SELECT count(*) FROM feature_sponsor_queries)"""
    ).fetchone() or (0, 0, 0)
    events = Events(columns[0], columns[1], columns[2], columns[3], columns[4])
    return events, int(counts[0]), int(counts[1]), int(counts[2])


def build_track_record(
    con: duckdb.DuckDBPyConnection,
    rules: CohortRules,
    alias_min_trials: int,
    alias_wait_days: int,
) -> None:
    """Writes `feature_sponsor_record`: per landmark key, whether the sponsor has an
    identity and its three counts (NULL without one)."""
    build_sponsor_states(con, rules)
    identity = has_identity_sql("sponsor_key", "sponsor_is_individual")
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE feature_sponsor_queries AS
        SELECT trial_id, landmark_index, landmark_date, sponsor_key AS key,
          row_number() OVER (ORDER BY trial_id, landmark_index) - 1 AS number
        FROM {AT_LANDMARK_TABLE} WHERE {identity}"""
    )
    events, n_keys, n_trials, n_queries = collect_events(con)
    answers = track_records(events, n_keys, n_trials, n_queries, alias_min_trials, alias_wait_days)
    con.register(
        "feature_sponsor_answers",
        pd.DataFrame(
            {
                "number": np.arange(n_queries, dtype=np.int64),
                "registrations": answers[:, 0],
                "ended": answers[:, 1],
                "stopped": answers[:, 2],
            }
        ),
    )
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE {TRACK_RECORD_TABLE} AS
        SELECT k.trial_id, k.landmark_index, q.number IS NOT NULL AS sponsor_has_identity,
          a.registrations AS sponsor_prior_registrations, a.ended AS sponsor_prior_ended,
          a.stopped AS sponsor_prior_stopped
        FROM {AT_LANDMARK_TABLE} k
        LEFT JOIN feature_sponsor_queries q USING (trial_id, landmark_index)
        LEFT JOIN feature_sponsor_answers a USING (number)"""
    )
    con.unregister("feature_sponsor_answers")
