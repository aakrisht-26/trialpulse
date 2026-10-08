"""Amendment signals as of the landmark (CLAUDE.md Section 8, amendment family).

Each entry is a SQL expression over one row of `states.AT_LANDMARK_TABLE`: the version in
effect at the landmark, the running values of its history, and the first version's dates
and enrollment target (`first_*`). Dates are compared by calendar month.

**Enrollment** (decided in the Step 5 review). An enrollment count is a target while its
type is ESTIMATED and the number enrolled once it is ACTUAL. So the change against the first
version is two features: `enrollment_target_ratio` while the count is still a target, and
`enrolled_to_first_target_ratio` once enrollment has closed. `enrollment_closed` says which
one applies.

**Primary completion.** The registry says whether a primary completion date is anticipated
or ACTUAL, in every year of the data. A date that is ACTUAL has been reached, not missed:
`primary_completion_overdue` is true only for a date still anticipated whose month has
ended, and `primary_completion_reached` says the registry shows it as done.
"""

from trialpulse.features.states import month_index

_L = month_index("landmark_date")
_HAD_TARGET = "first_enrollment_type = 'ESTIMATED' AND first_enrollment_count > 0"
_RATIO = "CAST(enrollment_count AS DOUBLE) / first_enrollment_count"


def _slip(field: str) -> str:
    return f"{month_index(field)} - {month_index('first_' + field)}"


EXPRESSIONS: dict[str, str] = {
    "status": "status",
    "n_versions": "n_versions",
    "n_versions_recent": "n_versions - n_versions_before_window",
    "months_since_last_update": f"{_L} - {month_index('effective_date')}",
    "primary_completion_slip_months": _slip("primary_completion_date"),
    "completion_slip_months": _slip("completion_date"),
    "start_slip_months": _slip("start_date"),
    "enrollment_closed": (
        "CASE enrollment_type WHEN 'ACTUAL' THEN true WHEN 'ESTIMATED' THEN false END"
    ),
    "enrollment_target_ratio": (
        f"CASE WHEN enrollment_type = 'ESTIMATED' AND {_HAD_TARGET} THEN {_RATIO} END"
    ),
    "enrolled_to_first_target_ratio": (
        f"CASE WHEN enrollment_type = 'ACTUAL' AND {_HAD_TARGET} THEN {_RATIO} END"
    ),
    "ever_suspended": "ever_suspended",
    "currently_suspended": "status = 'SUSPENDED'",
    "start_overdue": (
        "CASE WHEN status <> 'NOT_YET_RECRUITING' THEN false "
        f"ELSE {month_index('start_date')} < {_L} END"
    ),
    "primary_completion_overdue": (
        "CASE WHEN primary_completion_date_type = 'ACTUAL' THEN false "
        f"ELSE {month_index('primary_completion_date')} < {_L} END"
    ),
    "primary_completion_reached": "primary_completion_date_type = 'ACTUAL'",
    "months_since_status_verified": f"{_L} - {month_index('status_verified_date')}",
}
