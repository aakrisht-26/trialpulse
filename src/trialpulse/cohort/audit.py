"""Data audit, part 2 (CLAUDE.md Step 4): the cohort funnel, landmark exclusions, outcomes by
first-post year, the UNKNOWN rule against the registry's label, person-period rows, and the
Aalen-Johansen sanity table. Counts only; no names, no free text.
"""

import datetime as dt
from collections.abc import Iterable, Sequence
from typing import Any

import duckdb
import numpy as np

from trialpulse.cohort.landmarks import EXCLUSION_ORDER
from trialpulse.cohort.rules import CohortRules
from trialpulse.dates import days_between
from trialpulse.eval import EVENT_STOP
from trialpulse.models.aalen_johansen import aalen_johansen

PART_2_HEADING = "# Data audit, part 2"
# Section 10: analysis that informs modeling uses landmarks before 2018-01-01 only.
MODELING_EDA_BEFORE = dt.date(2018, 1, 1)
DAYS_PER_MONTH = 365.25 / 12
EXCLUSION_LABELS = {
    "ended_or_censored": (
        "Follow-up over at L (event or censoring on or before L, or L after the cutoff)"
    ),
    "no_version_yet": "No version public yet at L",
    "not_interventional": "Not interventional at L (ADR 0013)",
    "not_open": "Status not open at L",
    "lapsed": "Lapsed at L under the registry's UNKNOWN rule (ADR 0014)",
}


def _n(value: Any) -> str:
    return f"{int(value):,}" if value is not None else "n/a"


def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return lines


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[Any, ...]:
    row = con.execute(sql).fetchone()
    assert row is not None
    return tuple(row)


def aj_sanity_table(
    con: duckdb.DuckDBPyConnection,
    horizons_months: Sequence[int],
    before: dt.date = MODELING_EDA_BEFORE,
) -> list[dict[str, Any]]:
    """Aalen-Johansen CIF of early stop at each horizon from L0, by stratum (the sponsor
    class at L0, ADR 0006), over L0 landmarks before `before`, plus a pooled row."""
    data = con.execute(
        f"""SELECT stratum, landmark_date, event, event_date FROM cohort_landmarks
        WHERE landmark_index = 0 AND landmark_date < DATE '{before.isoformat()}'"""
    ).fetchnumpy()
    strata = np.asarray(data["stratum"]).astype(str)
    time = days_between(
        np.asarray(data["landmark_date"]).astype("datetime64[D]"),
        np.asarray(data["event_date"]).astype("datetime64[D]"),
    )
    event = np.asarray(data["event"], dtype=np.int64)
    rows = []
    groups = [(s, strata == s) for s in sorted(set(strata.tolist()))] + [
        ("All", np.ones(len(strata), dtype=bool))
    ]
    for name, mask in groups:
        curve = aalen_johansen(time[mask], event[mask])
        row: dict[str, Any] = {
            "stratum": name,
            "trials": int(mask.sum()),
            "early_stops": int((event[mask] == EVENT_STOP).sum()),
        }
        for months in horizons_months:
            row[f"cif_{months}m"] = float(curve.at(months * DAYS_PER_MONTH))
        rows.append(row)
    return rows


def render_part_2(
    con: duckdb.DuckDBPyConnection,
    rules: CohortRules,
    aj_rows: Sequence[dict[str, Any]],
    horizons_months: Sequence[int],
    outputs: Sequence[tuple[str, int, str]],
    source: str = "",
) -> str:
    """`source` identifies the warehouse the cohort was built from (revision, schema
    version, versions checksum), so a part 2 older than part 1 can be told apart."""
    window = f"DATE '{rules.min_first_post_date.isoformat()}'"
    f = _one(
        con,
        """SELECT count(*),
          count(*) FILTER (WHERE in_window),
          count(*) FILTER (WHERE in_window AND ever_in_population),
          count(*) FILTER (WHERE in_window AND ever_in_population AND reversal),
          count(*) FILTER (WHERE t0 IS NULL)
        FROM cohort_outcomes""",
    )
    cohort = _one(con, "SELECT count(DISTINCT trial_id), count(*) FROM cohort_landmarks")
    none = _one(
        con,
        """WITH kept AS (SELECT DISTINCT trial_id FROM cohort_landmarks)
        SELECT count(*),
          count(*) FILTER (WHERE o.event > 0 AND o.event_date <= o.t0),
          count(*) FILTER (WHERE o.censor_reason = 'unknown' AND o.event_date <= o.t0)
        FROM cohort_outcomes o LEFT JOIN kept k USING (trial_id)
        WHERE o.in_window AND o.ever_in_population AND NOT o.reversal AND k.trial_id IS NULL""",
    )
    funnel = [
        ("Trials in the warehouse", _n(f[0])),
        (f"First posted on or after {rules.min_first_post_date} (t0)", _n(f[1])),
        ("... interventional in at least one version", _n(f[2])),
        ("... of which excluded for a status reversal (Section 6)", _n(f[3])),
        ("**Cohort: trials with at least one landmark row**", f"**{_n(cohort[0])}**"),
        ("Eligible but no landmark row", _n(none[0])),
        ("... already terminal (completed, terminated, withdrawn) when first posted", _n(none[1])),
        (
            "... censored on or before t0 under the UNKNOWN rule (last status verification "
            "not after registration)",
            _n(none[2]),
        ),
        (
            "... other (not interventional, or lapsed, at every landmark)",
            _n(none[0] - none[1] - none[2]),
        ),
    ]
    reasons = dict(
        con.execute(
            "SELECT excluded, count(*) FROM cohort_landmark_candidates GROUP BY excluded"
        ).fetchall()
    )
    candidate_rows = [(EXCLUSION_LABELS[r], _n(reasons.get(r, 0))) for r in EXCLUSION_ORDER]
    candidate_rows.append(("**Kept: landmark rows**", f"**{_n(reasons.get(None, 0))}**"))
    by_index = con.execute(
        """SELECT landmark_index, count(*), count(*) FILTER (WHERE event = 1),
          count(*) FILTER (WHERE event = 2), count(*) FILTER (WHERE event = 0)
        FROM cohort_landmarks GROUP BY 1 ORDER BY 1"""
    ).fetchall()
    years = con.execute(
        """WITH c AS (SELECT DISTINCT trial_id FROM cohort_landmarks)
        SELECT o.first_post_year, count(*),
          count(*) FILTER (WHERE o.terminal_status = 'TERMINATED'),
          count(*) FILTER (WHERE o.terminal_status = 'WITHDRAWN'),
          count(*) FILTER (WHERE o.event = 2),
          count(*) FILTER (WHERE o.censor_reason = 'unknown'),
          count(*) FILTER (WHERE o.censor_reason = 'cutoff')
        FROM cohort_outcomes o JOIN c USING (trial_id)
        GROUP BY 1 ORDER BY 1"""
    ).fetchall()
    reversal_years = dict(
        con.execute(
            f"""SELECT first_post_year, count(*) FROM cohort_outcomes
            WHERE t0 >= {window} AND ever_in_population AND reversal GROUP BY 1"""
        ).fetchall()
    )
    totals = [sum(r[i] for r in years) for i in range(1, 7)]
    year_rows = [(y, *(_n(v) for v in r), _n(reversal_years.get(y, 0))) for y, *r in years]
    year_rows.append(
        ("**All**", *(f"**{_n(v)}**" for v in totals), f"**{_n(sum(reversal_years.values()))}**")
    )
    u = _one(
        con,
        """SELECT count(*) FILTER (WHERE registry_unknown),
          count(*) FILTER (WHERE censor_reason = 'unknown'),
          count(*) FILTER (WHERE registry_unknown AND censor_reason = 'unknown'),
          count(*) FILTER (WHERE censor_reason = 'unknown' AND NOT registry_unknown),
          count(*) FILTER (WHERE registry_unknown AND censor_reason IS DISTINCT FROM 'unknown')
        FROM cohort_outcomes WHERE in_window AND ever_in_population AND NOT reversal""",
    )
    resolved = _one(
        con,
        """SELECT count(DISTINCT s.nct_id),
          count(DISTINCT s.nct_id) FILTER (WHERE o.censor_reason = 'unknown')
        FROM cohort_states s JOIN cohort_outcomes o ON o.trial_id = s.nct_id
        WHERE s.next_effective IS NOT NULL AND s.lapse_from < s.next_effective
          AND o.in_window AND o.ever_in_population AND NOT o.reversal""",
    )
    pp = con.execute(
        """SELECT interval, count(*), count(*) FILTER (WHERE outcome = 0),
          count(*) FILTER (WHERE outcome = 1), count(*) FILTER (WHERE outcome = 2)
        FROM cohort_person_period GROUP BY 1 ORDER BY 1"""
    ).fetchall()
    aj_headers = ["Sponsor class at L0", "Trials", "Early stops (any time)"] + [
        f"Early-stop CIF at {m} months" for m in horizons_months
    ]
    aj_table = [
        (
            r["stratum"] if r["stratum"] != "All" else "**All**",
            _n(r["trials"]),
            _n(r["early_stops"]),
            *(f"{r[f'cif_{m}m']:.3f}" for m in horizons_months),
        )
        for r in aj_rows
    ]
    lines = [
        "# Data audit, part 2: cohort, outcomes and landmarks",
        "",
        "Generated by `uv run python -m trialpulse.cohort.build` from the warehouse (`data/"
        f"warehouse.duckdb`{', ' + source if source else ''}), with the data cutoff "
        f"{rules.cutoff} from `config/project.yaml`. Counts only. Rules: CLAUDE.md Section 6, "
        "ADR 0013 (population decided at each landmark) and ADR 0014 (UNKNOWN from the "
        "registry's rule on versioned fields).",
        "",
        "## Cohort funnel",
        "",
        *_table(["Step", "Trials"], funnel),
        "",
        f"Trials without a first-post date: {_n(f[4])}. The population is decided at each "
        "landmark from the version in effect then (ADR 0013), so a trial counts in the cohort "
        "if at least one of its landmarks passes every rule below.",
        "",
        "## Landmark candidates",
        "",
        f"Candidates are L_k = t0 + {rules.spacing_months}k months for k = 0 to "
        f"{rules.max_index}, for eligible trials (in the window, interventional in some version, "
        "no reversal). Each rejected candidate counts under the first rule it fails, in this "
        "order.",
        "",
        *_table(["Rule", "Candidates"], candidate_rows),
        "",
        *_table(
            ["Landmark index k", "Rows", "Early stop later", "Completion later", "Censored"],
            [(k, *(_n(v) for v in r)) for k, *r in by_index],
        ),
        "",
        "## Outcomes by first-post year (cohort trials)",
        "",
        "Early stop: the first version whose status is TERMINATED or WITHDRAWN; completion: "
        "the first COMPLETED version, whichever comes first. Censored (UNKNOWN rule): the "
        "trial's last state is lapsed at the data cutoff, censored at its status verified "
        "date. Censored (cutoff): open at the data cutoff. Reversals (in the window and "
        "interventional in some version) are excluded from the cohort and counted in the "
        "last column.",
        "",
        *_table(
            [
                "First posted",
                "Trials",
                "Terminated",
                "Withdrawn",
                "Completed",
                "Censored (UNKNOWN rule)",
                "Censored (cutoff)",
                "Reversals excluded",
            ],
            year_rows,
        ),
        "",
        "## UNKNOWN: the registry's label and the derived rule",
        "",
        "Among trials in the window, interventional in some version and without a reversal:",
        "",
        f"- latest version shown as UNKNOWN by the registry: {_n(u[0])};",
        f"- censored by the derived rule (last state lapsed at the cutoff): {_n(u[1])};",
        f"- both: {_n(u[2])}; the rule only: {_n(u[3])} (records the registry marked after the "
        f"dataset last fetched them, or not yet marked); the label only: {_n(u[4])};",
        f"- trials with a lapsed state that a later version followed: {_n(resolved[0])}. "
        f"{_n(resolved[1])} of them are censored under the rule anyway, because their last "
        f"state is lapsed too; the other {_n(resolved[0] - resolved[1])} have an outcome or "
        "are open at the cutoff, which censoring at the first lapse would have discarded "
        "(ADR 0014).",
        "",
        "## Person-period rows",
        "",
        f"Each landmark row expands into up to {rules.n_intervals} intervals of "
        f"{rules.interval_months} months; the sequence ends at the first event, and an "
        "interval censored before its end is dropped.",
        "",
        *_table(
            ["Interval j", "Rows", "Continue", "Early stop", "Completion"],
            [(j, *(_n(v) for v in r)) for j, *r in pp],
        ),
        "",
        "## Aalen-Johansen sanity table",
        "",
        "Early-stop CIF from the registration landmark (L0), by the lead sponsor class at "
        "L0 (M0's stratum; phase is not versioned, ADR 0006), over L0 landmarks before "
        f"{MODELING_EDA_BEFORE} (Section 10). Outcomes are observed through the data "
        "cutoff; completion is a competing event.",
        "",
        *_table(aj_headers, aj_table),
        "",
        "## Outputs",
        "",
        *_table(
            ["File", "Rows", "Checksum"],
            [(f"`{name}`", _n(rows), checksum) for name, rows, checksum in outputs],
        ),
        "",
    ]
    return "\n".join(lines)
