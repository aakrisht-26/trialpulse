"""The rules the cohort is built from, in one place (CLAUDE.md Section 6, ADRs 0013 and 0014).

- **Submitted status.** The registry computes UNKNOWN; no sponsor submits it. Where a
  version shows UNKNOWN, its submitted status is `last_known_status` (ADR 0014).
- **State at t.** The latest version posted on or before t; when versions share a post date,
  the higher version number (Section 6, and the Step 3 audit's tie-break).
- **Lapsed (the registry's UNKNOWN rule, ADR 0014).** A state is lapsed at t when its
  submitted status is one of `unknown_rule.statuses`, its completion reference is before t
  (the completion date, else the primary completion date, each at the end of its precision
  period; both missing counts as passed), and the end of the month that lies
  `verification_lapse_months` after its status-verified month is before t. A missing
  status-verified date falls back to the version's post date. The first such day is the
  state's `lapse_from`.
- **A bare UNKNOWN.** A version shown UNKNOWN without `last_known_status` (none exists in the
  pinned dataset; a live record could carry one) has no submitted status to judge. The
  registry itself says the trial is lapsed, so the state is lapsed from the day it is posted.

- **As of an origin (ADR 0016).** Walk-forward training rows for origin T come from the
  cohort as it would have been built on T: only versions posted before T are known, and
  observation ends on T. `CohortRules.as_of(T)` gives the rules for that build, so a
  training label never depends on anything posted on or after its origin.

Every rule has a SQL form (used by the build over millions of rows) and a Python twin (used
by tests, and later by live scoring); a test checks that they agree.
"""

import calendar
import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, replace

from trialpulse.config import ProjectConfig


@dataclass(frozen=True)
class CohortRules:
    """The constants a cohort build depends on, read from config/project.yaml."""

    open_statuses: tuple[str, ...]
    early_stop: tuple[str, ...]
    competing: tuple[str, ...]
    unknown_labels: tuple[str, ...]
    lapse_statuses: tuple[str, ...]
    lapse_months: int
    study_type: str
    min_first_post_date: dt.date
    cutoff: dt.date  # the end of observation: open trials are censored here
    spacing_months: int
    max_index: int
    interval_months: int
    n_intervals: int
    # When set, only versions posted before this date are known (a build as of an origin,
    # ADR 0016). Otherwise every version posted through the cutoff is known.
    versions_before: dt.date | None = None

    @classmethod
    def from_config(cls, cfg: ProjectConfig, cutoff: dt.date | None = None) -> "CohortRules":
        chosen = cutoff or cfg.dataset.cutoff
        if chosen is None:
            raise ValueError("no data cutoff: config/project.yaml has no dataset.cutoff")
        return cls(
            open_statuses=cfg.statuses.open,
            early_stop=cfg.statuses.early_stop,
            competing=cfg.statuses.competing,
            unknown_labels=cfg.statuses.unknown,
            lapse_statuses=cfg.unknown_rule.statuses,
            lapse_months=cfg.unknown_rule.verification_lapse_months,
            study_type=cfg.population.study_type,
            min_first_post_date=cfg.population.min_first_post_date,
            cutoff=chosen,
            spacing_months=cfg.landmarks.spacing_months,
            max_index=cfg.landmarks.max_index,
            interval_months=cfg.discrete_time.interval_months,
            n_intervals=cfg.discrete_time.n_intervals,
        )

    @property
    def terminal(self) -> tuple[str, ...]:
        return (*self.early_stop, *self.competing)

    @property
    def last_post_date(self) -> dt.date:
        """The latest post date a known version can have."""
        if self.versions_before is None:
            return self.cutoff
        return self.versions_before - dt.timedelta(days=1)

    def as_of(self, origin: dt.date) -> "CohortRules":
        """The rules for the cohort as it would have been built on a walk-forward origin:
        versions posted before the origin, and observation ending on the origin."""
        return replace(self, cutoff=origin, versions_before=origin)


def sql_list(values: Iterable[str]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def sql_date(day: dt.date) -> str:
    return f"DATE '{day.isoformat()}'"


# SQL forms --------------------------------------------------------------------------------


def submitted_status_sql(rules: CohortRules, alias: str = "v") -> str:
    return (
        f"(CASE WHEN {alias}.overall_status IN {sql_list(rules.unknown_labels)} "
        f"AND {alias}.last_known_status IS NOT NULL THEN {alias}.last_known_status "
        f"ELSE {alias}.overall_status END)"
    )


def period_end_sql(date: str, precision: str) -> str:
    """The last day of a partial date's period: a month-precision date ends on the month's
    last day, a year-precision date on 31 December."""
    return (
        f"(CASE {precision} WHEN 'year' THEN make_date(year({date}), 12, 31) "
        f"WHEN 'month' THEN last_day({date}) ELSE {date} END)"
    )


def completion_ref_sql(alias: str = "v") -> str:
    completion = period_end_sql(f"{alias}.completion_date", f"{alias}.completion_date_precision")
    primary = period_end_sql(
        f"{alias}.primary_completion_date", f"{alias}.primary_completion_date_precision"
    )
    return f"coalesce({completion}, {primary})"


def verified_sql(alias: str = "v") -> str:
    return f"coalesce({alias}.status_verified_date, {alias}.effective_date)"


def lapse_from_sql(
    rules: CohortRules, status: str, effective: str, completion: str, verified: str
) -> str:
    """The first day a state is lapsed, or NULL for a status the rule does not apply to. A
    status that is still the registry's UNKNOWN label is lapsed from the state's first day."""
    verification_ends = f"last_day({verified} + INTERVAL {rules.lapse_months} MONTH)"
    return (
        f"(CASE WHEN {status} IN {sql_list(rules.unknown_labels)} THEN {effective} "
        f"WHEN {status} IN {sql_list(rules.lapse_statuses)} THEN greatest({effective}, "
        f"coalesce({completion} + 1, {effective}), {verification_ends} + 1) END)"
    )


# Python twins -----------------------------------------------------------------------------


def add_months(day: dt.date, months: int) -> dt.date:
    """Calendar months, clamping the day to the target month (as DuckDB and
    trialpulse.dates.add_months do)."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return dt.date(year, month + 1, min(day.day, last))


def last_day(day: dt.date) -> dt.date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def period_end(day: dt.date | None, precision: str | None) -> dt.date | None:
    if day is None:
        return None
    if precision == "year":
        return dt.date(day.year, 12, 31)
    if precision == "month":
        return last_day(day)
    return day


def completion_ref(
    completion: dt.date | None,
    completion_precision: str | None,
    primary_completion: dt.date | None,
    primary_completion_precision: str | None,
) -> dt.date | None:
    """The date the rule compares: the completion date, else the primary completion date,
    each at the end of its precision period (twin of completion_ref_sql)."""
    return period_end(completion, completion_precision) or period_end(
        primary_completion, primary_completion_precision
    )


def submitted_status(rules: CohortRules, overall_status: str, last_known_status: str | None) -> str:
    if overall_status in rules.unknown_labels and last_known_status is not None:
        return last_known_status
    return overall_status


def lapse_from(
    rules: CohortRules,
    status: str,
    effective: dt.date,
    completion_ref: dt.date | None,
    verified: dt.date | None,
) -> dt.date | None:
    if status in rules.unknown_labels:
        return effective
    if status not in rules.lapse_statuses:
        return None
    verification_ends = last_day(add_months(verified or effective, rules.lapse_months))
    candidates = [effective, verification_ends + dt.timedelta(days=1)]
    if completion_ref is not None:
        candidates.append(completion_ref + dt.timedelta(days=1))
    return max(candidates)


def is_lapsed(
    rules: CohortRules,
    status: str,
    effective: dt.date,
    completion_ref: dt.date | None,
    verified: dt.date | None,
    on: dt.date,
) -> bool:
    start = lapse_from(rules, status, effective, completion_ref, verified)
    return start is not None and start <= on
