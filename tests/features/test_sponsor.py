"""The sponsor track record: only what was posted before the landmark, and aliases (ADR 0018).

Hand-built registries first, where every count is known. Then the one-pass computation is
compared, query by query, with a plain replay of the registry up to each landmark.
"""

import datetime as dt
import multiprocessing
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pytest

from trialpulse.cohort.rules import CohortRules
from trialpulse.config import ProjectConfig
from trialpulse.contracts.sponsor import has_identity, has_identity_sql
from trialpulse.features import sponsor as sp

from .conftest import CFG, D, World, compute, restore_inputs, version

COUNTS = ["sponsor_prior_registrations", "sponsor_prior_ended", "sponsor_prior_stopped"]
ACME, OTHER = "acme pharma", "borealis therapeutics inc"


def counts(frame: pd.DataFrame, trial: str) -> list[list[Any]]:
    rows = frame.loc[trial][COUNTS]
    return [[None if pd.isna(v) else int(v) for v in row] for row in rows.to_numpy().tolist()]


def trial(
    name: str, sponsor: str | None, *steps: tuple[dt.date, str], **fields: Any
) -> list[dict[str, Any]]:
    """Versions of one trial: (post date, status) pairs under one sponsor."""
    t0 = steps[0][0]
    return [
        version(name, i, day, status, t0=t0, sponsor_key=sponsor, **fields)
        for i, (day, status) in enumerate(steps)
    ]


def test_only_outcomes_posted_before_the_landmark_count(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """Section 8, leakage test 3. X is the row's trial; A to F are the registry around it."""
    rows = (
        trial("X", ACME, (D(2015, 1, 1), "RECRUITING"))
        # Completed the day before X's second landmark: counted from then on.
        + trial("A", ACME, (D(2014, 1, 1), "RECRUITING"), (D(2015, 6, 30), "COMPLETED"))
        # Terminated on the landmark day itself: not before it, so not counted there.
        + trial("B", ACME, (D(2014, 6, 1), "RECRUITING"), (D(2015, 7, 1), "TERMINATED"))
        + trial("C", ACME, (D(2015, 3, 1), "NOT_YET_RECRUITING"), (D(2015, 5, 1), "WITHDRAWN"))
        # Terminated, then reopened: ended while the registry showed it so, not afterwards.
        + trial("D", ACME, (D(2014, 2, 1), "RECRUITING"), (D(2014, 8, 1), "TERMINATED"),
                (D(2015, 2, 1), "RECRUITING"))
        # Not interventional, and another sponsor's: neither counts.
        + trial("E", ACME, (D(2013, 1, 1), "RECRUITING"), (D(2014, 1, 1), "COMPLETED"),
                study_type="OBSERVATIONAL")
        + trial("F", OTHER, (D(2013, 1, 1), "RECRUITING"), (D(2014, 1, 1), "TERMINATED"))
    )  # fmt: skip
    keys = [("X", 0, D(2015, 1, 1)), ("X", 1, D(2015, 7, 1)), ("X", 2, D(2016, 1, 1))]
    out = compute(cfg, rules, rows, keys)
    assert counts(out, "X") == [
        [3, 1, 1],  # registered before: A, B, D. Shown as ended: D, terminated.
        [4, 2, 1],  # and C. Ended: A completed, C withdrawn; D reopened; B only today.
        [4, 3, 2],  # B's termination is known now.
    ]
    assert out.loc["X"]["sponsor_has_identity"].tolist() == [True, True, True]


def test_the_rows_own_trial_is_not_part_of_its_sponsors_record(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    rows = trial("X", ACME, (D(2015, 1, 1), "RECRUITING")) + trial(
        "A", ACME, (D(2014, 1, 1), "RECRUITING")
    )
    out = compute(cfg, rules, rows, [("X", 0, D(2015, 1, 1)), ("X", 3, D(2016, 7, 1))])
    assert counts(out, "X") == [[1, 0, 0], [1, 0, 0]]  # A only, also long after X's own post


def test_the_rows_own_ended_trial_is_not_counted_either(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """X itself was shown as terminated before the landmark. Its sponsor's other trials are
    A alone: one registered, one ended, none stopped early."""
    rows = trial("X", ACME, (D(2014, 1, 1), "RECRUITING"), (D(2014, 6, 1), "TERMINATED")) + trial(
        "A", ACME, (D(2013, 1, 1), "RECRUITING"), (D(2013, 9, 1), "COMPLETED")
    )
    out = compute(cfg, rules, rows, [("X", 2, D(2015, 1, 1))])
    assert counts(out, "X") == [[1, 1, 0]]


def test_an_ended_trial_counts_once_whatever_it_posts_afterwards(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    ended = trial("A", ACME, (D(2013, 1, 1), "RECRUITING"), (D(2013, 9, 1), "TERMINATED"),
                  (D(2014, 1, 1), "TERMINATED"), (D(2014, 5, 1), "TERMINATED"))  # fmt: skip
    rows = trial("X", ACME, (D(2015, 1, 1), "RECRUITING")) + ended
    out = compute(cfg, rules, rows, [("X", 0, D(2015, 1, 1))])
    assert counts(out, "X") == [[1, 1, 1]]


def test_of_two_versions_posted_on_one_day_the_later_one_counts(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """Section 6: when versions share a post date, the higher version number is in effect.
    A was terminated and reopened on one day; B was reopened and completed on one day."""
    day = D(2014, 6, 1)
    rows = [
        *trial("X", ACME, (D(2015, 1, 1), "RECRUITING")),
        version("A", 0, D(2013, 1, 1), "RECRUITING", sponsor_key=ACME),
        version("A", 1, day, "TERMINATED", t0=D(2013, 1, 1), sponsor_key=ACME),
        version("A", 2, day, "RECRUITING", t0=D(2013, 1, 1), sponsor_key=ACME),
        version("B", 0, D(2013, 1, 1), "RECRUITING", sponsor_key=ACME),
        version("B", 1, day, "RECRUITING", t0=D(2013, 1, 1), sponsor_key=ACME),
        version("B", 2, day, "COMPLETED", t0=D(2013, 1, 1), sponsor_key=ACME),
    ]
    out = compute(cfg, rules, rows, [("X", 0, D(2015, 1, 1))])
    assert counts(out, "X") == [[2, 1, 0]]


def test_sponsors_without_an_identity_get_no_record(cfg: ProjectConfig, rules: CohortRules) -> None:
    """Individuals, placeholder names and person-named sponsors (ADR 0018)."""
    day = D(2015, 1, 1)
    rows = (
        trial("IND", None, (day, "RECRUITING"), sponsor_is_individual=True)
        + trial("RED", "redacted", (day, "RECRUITING"))
        + trial("RED2", "redacted", (D(2014, 1, 1), "RECRUITING"), (D(2014, 6, 1), "TERMINATED"))
        + trial("DR", "dr a example", (day, "RECRUITING"))
        + trial("DR2", "dr a example", (D(2014, 1, 1), "RECRUITING"))
        + trial("HOSP", "dr example memorial hospital", (day, "RECRUITING"))
        + trial("HOSP2", "dr example memorial hospital", (D(2014, 1, 1), "RECRUITING"))
    )
    out = compute(cfg, rules, rows, [(t, 0, day) for t in ("IND", "RED", "DR", "HOSP")])
    assert out["sponsor_has_identity"].to_dict() == {
        ("IND", 0): False, ("RED", 0): False, ("DR", 0): False, ("HOSP", 0): True,
    }  # fmt: skip
    assert counts(out, "IND") == [[None, None, None]]
    assert counts(out, "RED") == [[None, None, None]]
    assert counts(out, "DR") == [[None, None, None]]
    assert counts(out, "HOSP") == [[1, 0, 0]]  # a hospital named after a person is a sponsor
    assert not has_identity("redacted", False)
    assert not has_identity("acme pharma", True)
    assert has_identity("acme pharma", False)


def test_a_name_without_identity_passes_nothing_on(cfg: ProjectConfig, rules: CohortRules) -> None:
    """Two trials registered under a placeholder name later show a company. A placeholder
    is not a sponsor that was renamed: the company inherits nothing, not the two trials'
    registrations and not the third trial that stopped under the placeholder."""
    rows = (
        trial("U3", "redacted", (D(2013, 1, 1), "RECRUITING"), (D(2014, 1, 1), "TERMINATED"))
        + _renamed(FEBRUARY, to=ACME, name="U1", first="redacted")
        + _renamed(FEBRUARY, to=ACME, name="U2", first="redacted")
        + trial("X", ACME, (X0, "RECRUITING"))
    )
    out = compute(cfg, rules, rows, [("X", 0, X0), ("X", 1, LATER)])
    assert counts(out, "X") == [[0, 0, 0], [0, 0, 0]]


# Aliases -----------------------------------------------------------------------------------

OLD, NEW, MID, THIRD = "oldco", "newco", "midco", "thirdco"
REGISTERED = D(2012, 1, 1)


def _old_sponsor() -> list[dict[str, Any]]:
    """Two finished trials of the old name. With R and R2 below, it registered four."""
    return trial("P", OLD, (D(2013, 1, 1), "RECRUITING"), (D(2014, 1, 1), "COMPLETED")) + trial(
        "Q", OLD, (D(2013, 6, 1), "RECRUITING"), (D(2014, 3, 1), "TERMINATED")
    )


def _renamed(on: dt.date, to: str = NEW, name: str = "R", first: str = OLD) -> list[dict[str, Any]]:
    """A trial registered in 2012 under one name whose next version, posted on `on`, shows
    another."""
    return [
        version(name, 0, REGISTERED, "RECRUITING", sponsor_key=first),
        version(name, 1, on, "RECRUITING", t0=REGISTERED, sponsor_key=to),
    ]


def _posts(name: str, on: dt.date, sponsor: str = OLD) -> list[dict[str, Any]]:
    """A trial registered in 2012 that posts one more version under the same name."""
    return _renamed(on, to=sponsor, name=name, first=sponsor)


def _moves(name: str, number: int, on: dt.date, to: str = NEW) -> list[dict[str, Any]]:
    """One more version of a trial registered in 2012, under another name."""
    return [version(name, number, on, "RECRUITING", t0=REGISTERED, sponsor_key=to)]


FEBRUARY = D(2015, 2, 1)
# The wait of the alias rule, and the two days around its end for a run that starts on
# 1 February: the last day a rename does not count yet, and the first day it does.
WAIT = dt.timedelta(days=CFG.features.sponsor_alias_wait_days)
LAST_DAY_WITHOUT, FIRST_DAY_WITH = FEBRUARY + WAIT, FEBRUARY + WAIT + dt.timedelta(days=1)
# X is registered under the new name a month after the wait has passed.
X0 = FEBRUARY + WAIT + dt.timedelta(days=30)
LATER = X0 + dt.timedelta(days=180)
X = trial("X", NEW, (X0, "RECRUITING"))
RENAME = _renamed(FEBRUARY) + _renamed(FEBRUARY, name="R2")


def test_a_renamed_sponsor_keeps_its_record(cfg: ProjectConfig, rules: CohortRules) -> None:
    """The new name replaced the old one on two trials in February 2015, and no trial posted
    under the old name afterwards: X, registered under the new name once the wait has
    passed, inherits what the old name did."""
    rows = _old_sponsor() + RENAME + X
    out = compute(cfg, rules, rows, [("X", 0, X0), ("X", 1, LATER)])
    # P, Q, R and R2 registered; P and Q ended; Q stopped early.
    assert counts(out, "X") == [[4, 2, 1], [4, 2, 1]]
    assert cfg.features.sponsor_alias_min_trials == 2


def test_one_trial_changing_hands_is_not_a_rename(cfg: ProjectConfig, rules: CohortRules) -> None:
    rows = _old_sponsor() + _renamed(FEBRUARY) + X
    out = compute(cfg, rules, rows, [("X", 0, X0), ("X", 1, LATER)])
    assert counts(out, "X") == [[0, 0, 0], [0, 0, 0]]


def test_a_rename_posted_on_the_landmark_day_is_not_known_yet(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """The second trial shows the new name on X's landmark day itself: before that day the
    registry held one replacement, which is not a rename."""
    rows = _old_sponsor() + _renamed(FEBRUARY) + _renamed(X0, name="R2") + X
    out = compute(cfg, rules, rows, [("X", 0, X0), ("X", 1, LATER)])
    assert counts(out, "X") == [[0, 0, 0], [4, 2, 1]]


def test_a_rename_counts_once_its_first_replacement_is_more_than_a_year_old(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """Two trials leave the old name on 1 February. Nobody can know yet whether its other
    trials will go on posting under it, so for the length of the wait the new name inherits
    nothing: the registry asks for an update of every active record once in 12 months."""
    assert cfg.features.sponsor_alias_wait_days == 365
    early = trial("Y", NEW, (D(2015, 3, 1), "RECRUITING"))
    on_last = trial("W", NEW, (LAST_DAY_WITHOUT, "RECRUITING"))
    on_first = trial("Z", NEW, (FIRST_DAY_WITH, "RECRUITING"))
    out = compute(
        cfg, rules, _old_sponsor() + RENAME + early + on_last + on_first,
        [("Y", 0, D(2015, 3, 1)), ("W", 0, LAST_DAY_WITHOUT), ("Z", 0, FIRST_DAY_WITH)],
    )  # fmt: skip
    assert counts(out, "Y") == [[0, 0, 0]]
    assert counts(out, "W") == [[1, 0, 0]]  # Y only: the first replacement is 365 days old
    assert counts(out, "Z") == [[6, 2, 1]]  # Y and W; and P, Q, R and R2 of the old name


def test_the_wait_runs_from_the_first_replacement_not_from_the_latest(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """The second trial leaves the old name two months after the first. The wait still
    counts from the first one. When the second one comes later than the wait, the rename
    counts from the next day."""
    on_last = trial("W", NEW, (LAST_DAY_WITHOUT, "RECRUITING"))
    on_first = trial("Z", NEW, (FIRST_DAY_WITH, "RECRUITING"))
    rows = _old_sponsor() + _renamed(FEBRUARY) + _renamed(D(2015, 4, 1), name="R2")
    keys = [("W", 0, LAST_DAY_WITHOUT), ("Z", 0, FIRST_DAY_WITH)]
    out = compute(cfg, rules, rows + on_last + on_first, keys)
    assert counts(out, "W") == [[0, 0, 0]]
    assert counts(out, "Z") == [[5, 2, 1]]  # W; and P, Q, R and R2
    late = X0 + dt.timedelta(days=14)
    that_day = trial("W", NEW, (late, "RECRUITING"))
    next_day = trial("Z", NEW, (late + dt.timedelta(days=1), "RECRUITING"))
    rows = _old_sponsor() + _renamed(FEBRUARY) + _renamed(late, name="R2")
    out = compute(
        cfg, rules, rows + that_day + next_day,
        [("W", 0, late), ("Z", 0, late + dt.timedelta(days=1))],
    )  # fmt: skip
    assert counts(out, "W") == [[0, 0, 0]]
    assert counts(out, "Z") == [[5, 2, 1]]


def test_one_stray_version_under_the_old_name_does_not_undo_a_rename(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """A trial of the old name posts a version in April 2015 that still shows it (a record
    not yet touched by the rename). Two trials left, one still posts: the rename stands.
    With two trials still posting against two that left there is no majority."""
    rows = _old_sponsor() + RENAME + X
    one = compute(cfg, rules, rows + _posts("S", D(2015, 4, 1)), [("X", 0, X0)])
    assert counts(one, "X") == [[5, 2, 1]]  # P, Q, R, R2 and S
    strays = _posts("S", D(2015, 4, 1)) + _posts("S2", D(2015, 4, 2))
    two = compute(cfg, rules, rows + strays, [("X", 0, X0)])
    assert counts(two, "X") == [[0, 0, 0]]


def test_a_sponsor_that_keeps_using_its_name_only_handed_trials_over(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """Three trials go on posting under the old name in March 2015, more than the two that
    left it in February: the old name is an active sponsor, and what left it is forgotten.
    When two more trials leave it later, that is a new start: nothing is inherited before
    the wait has passed again."""
    active = [r for i in (1, 2, 3) for r in _posts(f"S{i}", D(2015, 3, i))]
    again = X0 + dt.timedelta(days=30)
    later = _renamed(again, name="R3") + _renamed(again, name="R4")
    soon, at_last = again + dt.timedelta(days=31), again + WAIT + dt.timedelta(days=1)
    v = trial("V", NEW, (soon, "RECRUITING"))
    u = trial("U", NEW, (at_last, "RECRUITING"))
    rows = _old_sponsor() + RENAME + active + X + later + v + u
    out = compute(cfg, rules, rows, [("X", 0, X0), ("V", 0, soon), ("U", 0, at_last)])
    assert counts(out, "X") == [[0, 0, 0]]
    assert counts(out, "V") == [[1, 0, 0]]  # X only: the second hand-over is a month old
    # X and V; and P, Q, R, R2, S1 to S3, R3 and R4, all registered under the old name.
    assert counts(out, "U") == [[11, 2, 1]]


def test_a_trial_that_left_the_old_name_no_longer_posts_under_it(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """S1 to S4 post under the old name after the first two trials left it, and then leave
    it too. At no time do more trials still post under it than have left, so it is one
    rename, counted from its first replacement in February 2015."""
    rows = (
        _old_sponsor() + RENAME + X
        + _posts("S1", D(2015, 2, 10)) + _posts("S2", D(2015, 2, 10))
        + _moves("S1", 2, D(2015, 2, 20))
        + _posts("S3", D(2015, 3, 1)) + _posts("S4", D(2015, 3, 1))
        + _moves("S2", 2, D(2015, 4, 1)) + _moves("S3", 2, D(2015, 4, 1))
        + _moves("S4", 2, D(2015, 4, 1))
    )  # fmt: skip
    out = compute(cfg, rules, rows, [("X", 0, X0)])
    assert counts(out, "X") == [[8, 2, 1]]  # P, Q, R, R2 and S1 to S4


def test_the_name_that_took_most_trials_inherits(cfg: ProjectConfig, rules: CohortRules) -> None:
    """Two trials of the old name went to the new name and one to a third: a rename with one
    hand-over beside it. The new name inherits. P now shows the third name, so it is an
    ended trial of the third name and no longer of the old one."""
    p_moves = [version("P", 2, D(2015, 3, 1), "COMPLETED", t0=D(2013, 1, 1), sponsor_key=THIRD)]
    t = trial("T", THIRD, (X0, "RECRUITING"))
    rows = _old_sponsor() + p_moves + RENAME + X + t
    out = compute(cfg, rules, rows, [("X", 0, X0), ("T", 0, X0)])
    assert counts(out, "X") == [[4, 1, 1]]
    assert counts(out, "T") == [[0, 1, 0]]


def test_a_name_split_in_equal_parts_has_no_successor(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """Two trials each way: exactly half is not a majority, for either name."""
    moves = [
        version("P", 2, D(2015, 3, 1), "COMPLETED", t0=D(2013, 1, 1), sponsor_key=THIRD),
        version("Q", 2, D(2015, 3, 1), "TERMINATED", t0=D(2013, 6, 1), sponsor_key=THIRD),
    ]
    t = trial("T", THIRD, (X0, "RECRUITING"))
    rows = _old_sponsor() + moves + RENAME + X + t
    out = compute(cfg, rules, rows, [("X", 0, X0), ("T", 0, X0)])
    assert counts(out, "X") == [[0, 0, 0]]
    assert counts(out, "T") == [[0, 2, 1]]  # P and Q show the third name; nothing inherited


def test_a_trial_that_left_for_a_name_without_identity_still_left(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """Two trials go to the new name and two to a placeholder name. Four trials left the
    old name and the new name took half of them: no majority."""
    hidden = _renamed(FEBRUARY, to="redacted", name="R3") + _renamed(
        FEBRUARY, to="redacted", name="R4"
    )
    out = compute(cfg, rules, _old_sponsor() + RENAME + hidden + X, [("X", 0, X0)])
    assert counts(out, "X") == [[0, 0, 0]]


def test_a_trials_name_on_a_day_is_the_one_in_effect_at_the_end_of_it(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """R and R2 post two versions on one day: the first shows a third name, the last the new
    one. That is one change, from the old name to the new. T1 to T3, trials of the third
    name, each post two versions on one day in March 2015: the first shows the old name,
    the last the third name again. Nobody posted under the old name."""
    twice = []
    for name in ("R", "R2"):
        twice += [
            version(name, 0, REGISTERED, "RECRUITING", sponsor_key=OLD),
            version(name, 1, FEBRUARY, "RECRUITING", t0=REGISTERED, sponsor_key=THIRD),
            version(name, 2, FEBRUARY, "RECRUITING", t0=REGISTERED, sponsor_key=NEW),
        ]
    slips = []
    for name in ("T1", "T2", "T3"):
        slips += [
            version(name, 0, REGISTERED, "RECRUITING", sponsor_key=THIRD),
            version(name, 1, D(2015, 3, 1), "RECRUITING", t0=REGISTERED, sponsor_key=OLD),
            version(name, 2, D(2015, 3, 1), "RECRUITING", t0=REGISTERED, sponsor_key=THIRD),
        ]
    t = trial("T", THIRD, (X0, "RECRUITING"))
    out = compute(cfg, rules, _old_sponsor() + twice + slips + X + t, [("X", 0, X0), ("T", 0, X0)])
    assert counts(out, "X") == [[4, 2, 1]]
    assert counts(out, "T") == [[3, 0, 0]]  # T1 to T3, and nothing of the old name


def test_renames_chain(cfg: ProjectConfig, rules: CohortRules) -> None:
    twice = []
    for name in ("R", "R2"):
        twice += [
            version(name, 0, REGISTERED, "RECRUITING", sponsor_key=OLD),
            version(name, 1, D(2014, 6, 1), "RECRUITING", t0=REGISTERED, sponsor_key=MID),
            version(name, 2, FEBRUARY, "RECRUITING", t0=REGISTERED, sponsor_key=NEW),
        ]
    rows = _old_sponsor() + twice + X
    out = compute(cfg, rules, rows, [("X", 0, X0)])
    assert counts(out, "X") == [[4, 2, 1]]


def test_names_that_replace_each_other_do_not_loop(cfg: ProjectConfig, rules: CohortRules) -> None:
    """Two trials go from the old name to the new one and two the other way on the same day.
    Each name looks replaced by the other; the pass must end, with one sponsor."""
    swapped = _renamed(FEBRUARY, to=OLD, name="S", first=NEW) + _renamed(
        FEBRUARY, to=OLD, name="S2", first=NEW
    )
    rows = _old_sponsor() + RENAME + swapped + X
    out = compute(cfg, rules, rows, [("X", 0, X0)])
    assert counts(out, "X") == [[6, 2, 1]]  # P, Q, R, R2, S and S2: one sponsor, two names


def test_a_finished_trial_follows_its_sponsor_name(cfg: ProjectConfig, rules: CohortRules) -> None:
    """Q was terminated under one sponsor and its record later shows another, which goes on
    using its own name elsewhere: the ended trial is counted for the sponsor shown."""
    rows = [
        *trial("Q", ACME, (D(2013, 6, 1), "RECRUITING"), (D(2014, 3, 1), "TERMINATED")),
        version("Q", 2, D(2014, 9, 1), "TERMINATED", t0=D(2013, 6, 1), sponsor_key=OTHER),
        *trial("K", ACME, (D(2013, 1, 1), "RECRUITING"), (D(2014, 12, 1), "RECRUITING")),
        *trial("XA", ACME, (D(2015, 6, 1), "RECRUITING")),
        *trial("XB", OTHER, (D(2015, 6, 1), "RECRUITING")),
    ]
    out = compute(cfg, rules, rows, [("XA", 0, D(2015, 6, 1)), ("XB", 0, D(2015, 6, 1))])
    assert counts(out, "XA") == [[2, 0, 0]]  # registered Q and K; Q now shows the other sponsor
    assert counts(out, "XB") == [[0, 1, 1]]


# The one-pass computation on events written by hand -------------------------------------------


def _events(rows: list[tuple[int, int, int, int, int]], order: Any = None) -> sp.Events:
    picked = range(len(rows)) if order is None else order
    return sp.Events(*(np.array([rows[i][f] for i in picked], dtype=np.int64) for f in range(5)))


SWAP_DAY = 100
SWAP: list[tuple[int, int, int, int, int]] = [
    (10, sp.REGISTRATION, 0, 0, 0),
    (11, sp.REGISTRATION, 1, 1, 0),
    (12, sp.REGISTRATION, 2, 2, 0),
    (SWAP_DAY, sp.REPLACEMENT, 0, 1, 3),
    (SWAP_DAY, sp.REPLACEMENT, 0, 1, 4),
    (SWAP_DAY, sp.REPLACEMENT, 1, 0, 5),
    (SWAP_DAY, sp.REPLACEMENT, 1, 0, 6),
]


def test_a_same_day_swap_with_a_third_name_has_one_answer() -> None:
    """On one day key 0 loses two trials to key 1 and one to key 2, and key 1 loses two to
    key 0. Which swap is applied first decides which link is refused as a loop, so without
    a fixed order the answer could go either way."""
    rows = [
        *SWAP,
        (SWAP_DAY, sp.REPLACEMENT, 0, 2, 7),
        (SWAP_DAY + 1, sp.QUERY, 0, 20, 0),
        (SWAP_DAY + 1, sp.QUERY, 1, 20, 1),
        (SWAP_DAY + 1, sp.QUERY, 2, 20, 2),
    ]
    answers = set()
    rng = np.random.default_rng(0)
    for _ in range(12):
        events = _events(rows, rng.permutation(len(rows)))
        out = sp.track_records(events, 3, 30, 3, alias_min_trials=2, alias_wait_days=0)
        answers.add(tuple(out[:, 0].tolist()))
    # One answer whatever the order: keys 0 and 1 are one sponsor with two registrations,
    # and key 2, which took one trial of three, inherits nothing.
    assert answers == {(2, 2, 1)}


def test_a_link_refused_as_a_loop_waits_for_the_next_change_of_majority() -> None:
    """Keys 0 and 1 swap two trials each: key 0 becomes an alias of key 1, and the link from
    key 1 to key 0 is refused, because it would close a loop. Later key 0 passes to key 2,
    so the refused link would no longer close a loop. It is not made then (ADR 0018): key 1
    stands alone until the majority of the trials that left it changes hands."""
    rows = [
        *SWAP,
        *((200, sp.REPLACEMENT, 0, 2, trial) for trial in (7, 8, 9)),
        *((300, sp.QUERY, key, 20, key) for key in (0, 1, 2)),
        *((400, sp.REPLACEMENT, 1, 2, trial) for trial in (10, 11, 12, 13, 14)),
        *((500, sp.QUERY, key, 20, 3 + key) for key in (0, 1, 2)),
    ]
    out = sp.track_records(_events(rows), 3, 30, 6, alias_min_trials=2, alias_wait_days=0)
    # Day 300: keys 0 and 2 are one sponsor, key 1 another.
    assert out[:3, 0].tolist() == [2, 1, 2]
    # Day 500: five trials left key 1 for key 2 against two for key 0: all three are one.
    assert out[3:, 0].tolist() == [3, 3, 3]


@pytest.mark.slow
def test_a_loop_of_names_ends() -> None:
    """Without the refusal above, following the links of two names that replace each other
    would never end. The pass runs in a process of its own here, so that a hang fails this
    test instead of stopping the whole run."""
    rows = [*SWAP, *((SWAP_DAY + 1, sp.QUERY, key, 20, key) for key in (0, 1, 2))]
    with multiprocessing.get_context("spawn").Pool(1) as pool:
        result = pool.apply_async(sp.track_records, (_events(rows), 3, 30, 3, 2, 0))
        out = result.get(timeout=120)
    assert out[:, 0].tolist() == [2, 2, 1]


# The one-pass computation against a plain replay ---------------------------------------------


def _replay_one(
    rows: list[tuple[int, int, int, int, int]],
    query: tuple[int, int, int, int, int],
    n_keys: int,
    min_trials: int,
    wait_days: int,
) -> np.ndarray:
    """Answer one query by replaying every event of earlier days from scratch, keeping each
    run whole and counting by walking the alias links. Slow and plain: no running totals
    and no shortcut for the majority to get wrong."""
    q_day, _, q_key, q_trial, _ = query
    runs: dict[int, dict[str, Any]] = {}  # key -> start, left_for, left, still
    successor: dict[int, int] = {}
    waiting: list[tuple[int, int]] = []  # (first day the link counts, key)
    parent: dict[int, int] = {}
    own = np.zeros((n_keys, 3), dtype=np.int64)
    registered: dict[int, int] = {}
    ended: dict[int, tuple[int, int]] = {}

    def root(k: int) -> int:
        while k in parent:
            k = parent[k]
        return k

    def start_links(today: int) -> None:
        for entry in sorted(e for e in waiting if e[0] <= today):
            waiting.remove(entry)
            key = entry[1]
            if key in successor and root(successor[key]) != key:
                parent[key] = successor[key]

    def forget(key: int) -> None:
        successor.pop(key, None)
        parent.pop(key, None)
        waiting[:] = [e for e in waiting if e[1] != key]

    def settle(key: int, day: int) -> None:
        run = runs[key]
        total = run["left"] + len(run["still"])
        winners = [k for k, n in run["left_for"].items() if n >= min_trials and 2 * n > total]
        wanted = winners[0] if winners else None
        if wanted != successor.get(key):
            forget(key)
            if wanted is not None:
                successor[key] = wanted
                waiting.append((max(day, run["start"] + wait_days) + 1, key))

    for day, kind, a, b, c in rows:
        if day >= q_day:
            break
        start_links(day)
        if kind == sp.APPEARANCE:
            run = runs.get(a)
            if run is not None and day > run["start"] and b not in run["still"]:
                run["still"].add(b)
                if len(run["still"]) > run["left"]:
                    del runs[a]
                    forget(a)
                else:
                    settle(a, day)
        elif kind == sp.REPLACEMENT:
            run = runs.setdefault(a, {"start": day, "left_for": {}, "left": 0, "still": set()})
            if b >= 0:
                run["left_for"][b] = run["left_for"].get(b, 0) + 1
            run["left"] += 1
            run["still"].discard(c)
            settle(a, day)
        elif kind == sp.REGISTRATION:
            own[a, 0] += 1
            registered[b] = a
        elif kind == sp.ENTER:
            own[a, 1] += 1
            own[a, 2] += b == sp.STOP
            ended[c] = (a, b)
        elif kind == sp.LEAVE:
            own[a, 1] -= 1
            own[a, 2] -= b == sp.STOP
            ended.pop(c, None)
    start_links(q_day)
    mine = root(q_key)
    total_counts = sum((own[k] for k in range(n_keys) if root(k) == mine), np.zeros(3, np.int64))
    if q_trial in registered and root(registered[q_trial]) == mine:
        total_counts[0] -= 1
    if q_trial in ended and root(ended[q_trial][0]) == mine:
        total_counts[1] -= 1
        total_counts[2] -= ended[q_trial][1] == sp.STOP
    return np.asarray(total_counts, dtype=np.int64)


def replay(
    events: sp.Events,
    n_keys: int,
    n_queries: int,
    min_trials: int,
    wait_days: int,
    only: set[int] | None = None,
) -> np.ndarray:
    """The answer to each query (or each query numbered in `only`); the others keep -1."""
    rows = [
        (int(events.day[i]), int(events.kind[i]), int(events.a[i]), int(events.b[i]),
         int(events.c[i]))
        for i in events.order()
    ]  # fmt: skip
    out = np.full((n_queries, 3), -1, dtype=np.int64)
    for query in rows:
        if query[1] == sp.QUERY and (only is None or query[4] in only):
            out[query[4]] = _replay_one(rows, query, n_keys, min_trials, wait_days)
    return out


def test_the_replay_gives_the_hand_made_answers() -> None:
    """The replay is the yardstick below, so it is checked against a case worked by hand."""
    rows = [
        *SWAP,
        (SWAP_DAY, sp.REPLACEMENT, 0, 2, 7),
        *((SWAP_DAY + 1, sp.QUERY, key, 20, key) for key in (0, 1, 2)),
    ]
    assert replay(_events(rows), 3, 3, 2, 0)[:, 0].tolist() == [2, 2, 1]


def _events_of_the_registry(world: World, rules: CohortRules, cfg: ProjectConfig) -> Any:
    con = world.con
    restore_inputs(con)
    con.execute(
        "CREATE OR REPLACE TEMP TABLE feature_at_landmark AS SELECT * FROM baseline_at_landmark"
    )
    # The states, the queries and the answers, afresh.
    sp.build_track_record(
        con, rules, cfg.features.sponsor_alias_min_trials, cfg.features.sponsor_alias_wait_days
    )
    return sp.collect_events(con)


def test_the_one_pass_counts_equal_a_replay_of_the_registry(
    world: World, rules: CohortRules, cfg: ProjectConfig
) -> None:
    """On the scripted registry, with its renames, hand-overs and reopened trials."""
    events, n_keys, n_trials, n_queries = _events_of_the_registry(world, rules, cfg)
    minimum, wait = cfg.features.sponsor_alias_min_trials, cfg.features.sponsor_alias_wait_days
    assert n_queries > 700
    assert (events.kind == sp.QUERY).sum() == n_queries
    kinds = set(events.kind.tolist())
    assert kinds == {sp.QUERY, sp.APPEARANCE, sp.REPLACEMENT, sp.REGISTRATION, sp.LEAVE, sp.ENTER}
    fast = sp.track_records(events, n_keys, n_trials, n_queries, minimum, wait)
    sample = set(np.random.default_rng(1).choice(n_queries, size=250, replace=False).tolist())
    rows = sorted(sample)
    slow = replay(events, n_keys, n_queries, minimum, wait, sample)
    assert np.array_equal(fast[rows], slow[rows])
    # With no wait, links start the day after they are seen: also equal.
    now = sp.track_records(events, n_keys, n_trials, n_queries, minimum, 0)
    assert np.array_equal(now[rows], replay(events, n_keys, n_queries, minimum, 0, sample)[rows])
    # A wait of two years keeps most links from counting in time: equal again, and smaller.
    long = sp.track_records(events, n_keys, n_trials, n_queries, minimum, 730)
    assert np.array_equal(long[rows], replay(events, n_keys, n_queries, minimum, 730, sample)[rows])
    assert (long[:, 0] < now[:, 0]).sum() > 30
    assert fast.min() >= 0
    assert (fast[:, 2] <= fast[:, 1]).all()  # stopped trials are among the ended ones
    # Aliases are in play: without them (a minimum no name can reach) some records are smaller.
    alone = sp.track_records(events, n_keys, n_trials, n_queries, 10**9, wait)
    assert (fast >= alone).all()
    assert (fast[:, 0] > alone[:, 0]).sum() > 30
    built = world.con.execute(
        "SELECT count(*) FROM baseline_features WHERE sponsor_prior_registrations IS NOT NULL"
    ).fetchone()
    assert built == (n_queries,)


def test_the_counts_do_not_depend_on_the_order_the_events_arrive_in(
    world: World, rules: CohortRules, cfg: ProjectConfig
) -> None:
    """The database returns events in no fixed order. The pass sorts them itself, also
    within a day, so shuffled input gives the same answers."""
    events, n_keys, n_trials, n_queries = _events_of_the_registry(world, rules, cfg)
    minimum, wait = cfg.features.sponsor_alias_min_trials, cfg.features.sponsor_alias_wait_days
    expected = sp.track_records(events, n_keys, n_trials, n_queries, minimum, wait)
    rng = np.random.default_rng(3)
    for _ in range(3):
        shuffle = rng.permutation(len(events))
        mixed = sp.Events(*(getattr(events, f)[shuffle] for f in ("day", "kind", "a", "b", "c")))
        again = sp.track_records(mixed, n_keys, n_trials, n_queries, minimum, wait)
        assert np.array_equal(again, expected)


def test_the_renamed_sponsors_of_the_registry_inherit(world: World) -> None:
    """acme pharma is shown as acme pharma global from March 2015 on. Landmark rows of the
    new name must count trials registered under the old one: more registrations than the
    new name itself had made before that landmark. The rows of the first year after the
    rename do not yet: the wait has not passed."""
    rows = world.con.execute(
        """SELECT f.sponsor_prior_registrations,
          (SELECT count(*) FROM all_versions v
           WHERE v.sponsor_key = 'acme pharma global' AND v.nct_version = 0
             AND v.study_type = 'INTERVENTIONAL' AND v.effective_date < f.landmark_date
             AND v.nct_id <> f.trial_id) AS under_the_new_name
        FROM baseline_features f JOIN baseline_at_landmark a USING (trial_id, landmark_index)
        WHERE a.sponsor_key = 'acme pharma global'"""
    ).fetchall()
    assert len(rows) > 5
    assert all(total >= own for total, own in rows)
    assert sum(total > own for total, own in rows) > 0.5 * len(rows)


# Which names are a sponsor ---------------------------------------------------------------------

IDENTITY_CASES: list[tuple[str | None, bool | None, bool]] = [
    ("pfizer", False, True),
    ("pfizer", True, False),
    ("pfizer", None, True),
    (None, False, False),
    ("", False, False),
    ("redacted", False, False),
    ("no sponsor", False, False),
    ("dr someone example", False, False),
    ("dr someone example", True, False),
    ("prof someone", False, False),
    ("dr example general hospital", False, True),
    ("drs associates research group", False, True),
    ("drexel university", False, True),
    ("mrs someone", False, False),
    # Letters outside ASCII: the two regular-expression engines must read them alike.
    ("dr étienne exemple", False, False),
    ("dr exemple hôpital universitaire", False, True),
    ("dr müller éinstitut", False, False),  # "institut" does not start a word here
    ("école dr someone", False, True),  # the title does not open the key
]


@pytest.mark.parametrize(("key", "individual", "expected"), IDENTITY_CASES)
def test_which_keys_carry_a_record(
    key: str | None, individual: bool | None, expected: bool
) -> None:
    assert has_identity(key, individual) is expected


def test_the_sql_identity_rule_equals_the_python_one() -> None:
    with duckdb.connect() as con:
        con.execute("CREATE TABLE s (i INTEGER, key VARCHAR, individual BOOLEAN)")
        con.executemany(
            "INSERT INTO s VALUES (?, ?, ?)",
            [(i, key, individual) for i, (key, individual, _) in enumerate(IDENTITY_CASES)],
        )
        rows = con.execute(
            f"SELECT {has_identity_sql('key', 'individual')} FROM s ORDER BY i"
        ).fetchall()
    assert [row[0] for row in rows] == [expected for _, _, expected in IDENTITY_CASES]
