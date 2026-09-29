"""Version-history dataset: download (part a), profile (part b), cohort counts (part c),
and the dataset side of the automated check (part d).

Column names follow the dataset card of brbk/clinical_trials_history (config core).
"""

import datetime as dt
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import duckdb

from trialpulse.config import ProjectConfig


class BlockedError(Exception):
    """A part cannot run until a person acts, for example to grant dataset access."""


# Columns the spike relies on, and the list-type fields whose presence part b checks.
KEY_COLUMNS: tuple[str, ...] = (
    "nct_id",
    "nct_version",
    "overall_status",
    "study_type",
    "study_first_post_date",
    "last_update_post_date",
    "last_update_post_date_type",
    "why_stopped",
    "start_date",
    "primary_completion_date",
    "completion_date",
    "enrollment_count",
    "lead_sponsor_class",
    "status_verified_date",
)
# Column-name patterns for the fields part b looks for. The first five are the list fields
# whose per-version presence decides whether current-record values are needed at all.
LIST_FIELD_PATTERNS: dict[str, str] = {
    "phases": r"^phases?(_list)?$",
    "conditions": r"^conditions?(_list|_count)?$",
    "interventions": r"^interventions?(_list|_count)?$|^intervention_(types?|names?)$",
    "arm_count": r"^(number_of_arms|n_arms|arms?_count|arm_group_count|arm_groups?)$",
    "locations": r"^locations?(_count)?$|^(number_of_)?(sites|facilities)$|^site_count$",
    "status_verified_date": r"^status_verified_date$",
    "eligibility_text": r"^eligibility_criteria$|^criteria$",
}
# Loose name fragments per list field, reported as evidence next to the exact patterns above.
FIELD_NAME_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "phases": ("phase",),
    "conditions": ("condition", "mesh", "keyword"),
    "interventions": ("interv", "drug", "device"),
    "arm_count": ("arm", "group"),
    "locations": ("location", "site", "facilit", "countr", "city"),
}
# Columns that can hold a person's name or contact details (CLAUDE.md Section 2). Their
# values are never read by the spike; Step 3 drops them at ingestion.
PERSONAL_DATA_PATTERN = r"investigator|old_name_title|contact|official_name|email|phone"
PER_VERSION_LIST_FIELDS: tuple[str, ...] = (
    "phases",
    "conditions",
    "interventions",
    "arm_count",
    "locations",
)
COMPARED_DATASET_COLUMNS: tuple[str, ...] = (
    "overall_status",
    "start_date",
    "start_date_precision",
    "primary_completion_date",
    "primary_completion_date_precision",
    "enrollment_count",
    "lead_sponsor_class",
    "why_stopped",
    "last_update_post_date",
)


def resolve_revision(configured: str | None, list_tags: Callable[[], list[str]]) -> str:
    """Return the pinned revision, or explain how to pin one."""
    if configured:
        return configured
    tags = sorted(list_tags())
    latest = tags[-1] if tags else "none found"
    raise BlockedError(
        "dataset.revision is not pinned in config/project.yaml "
        f"(latest tag: {latest}). Pin it, or rerun part a with --pin-latest."
    )


def pin_revision_in_config(config_path: Path, revision: str) -> None:
    """Write revision into the `revision: null` line of project.yaml, keeping comments."""
    text = config_path.read_text(encoding="utf-8")
    new_text, count = re.subn(
        r"^(\s*revision:\s*)null\b", rf"\g<1>{revision}", text, count=1, flags=re.MULTILINE
    )
    if count != 1:
        raise ValueError("no `revision: null` line to replace in project.yaml")
    config_path.write_text(new_text, encoding="utf-8")


def pin_cutoff_in_config(config_path: Path, cutoff: dt.date) -> None:
    """Write the data cutoff into the `cutoff: null` line of project.yaml, keeping comments."""
    text = config_path.read_text(encoding="utf-8")
    new_text, count = re.subn(
        r"^(\s*cutoff:\s*)null\b", rf"\g<1>{cutoff.isoformat()}", text, count=1, flags=re.MULTILINE
    )
    if count != 1:
        raise ValueError("no `cutoff: null` line to replace in project.yaml")
    config_path.write_text(new_text, encoding="utf-8")


def core_dir(raw_dir: Path, revision: str, config_name: str) -> Path:
    return raw_dir / revision / config_name


def parquet_glob(raw_dir: Path, revision: str, config_name: str) -> str:
    return (core_dir(raw_dir, revision, config_name) / "*.parquet").as_posix()


def download_core(
    cfg: ProjectConfig, raw_dir: Path, token: str | None, pin_latest: bool
) -> dict[str, Any]:
    """Part a: download the pinned revision of the core config (resumable)."""
    from huggingface_hub import HfApi, snapshot_download
    from huggingface_hub.errors import GatedRepoError

    repo_id = cfg.dataset.repo_id
    api = HfApi(token=token)

    def list_tags() -> list[str]:
        return [tag.name for tag in api.list_repo_refs(repo_id, repo_type="dataset").tags]

    try:
        try:
            revision = resolve_revision(cfg.dataset.revision, list_tags)
        except BlockedError:
            if not pin_latest:
                raise
            tags = sorted(list_tags())
            if not tags:
                raise
            revision = tags[-1]
            from trialpulse.config import PROJECT_CONFIG_PATH

            pin_revision_in_config(PROJECT_CONFIG_PATH, revision)
        info = api.dataset_info(repo_id, revision=revision)
        commit = info.sha
        configs = sorted(
            {s.rfilename.split("/")[0] for s in info.siblings or [] if "/" in s.rfilename}
        )
        target = raw_dir / revision
        snapshot_download(
            repo_id=repo_id,
            repo_type="dataset",
            revision=revision,
            allow_patterns=[f"{cfg.dataset.config_name}/*.parquet"],
            local_dir=target,
            token=token,
        )
    except GatedRepoError as exc:
        raise BlockedError(
            "access to the gated dataset has not been granted to this HF account yet"
        ) from exc
    files = sorted(core_dir(raw_dir, revision, cfg.dataset.config_name).glob("*.parquet"))
    return {
        "revision": revision,
        "commit": commit,
        "configs": configs,
        "files": len(files),
        "bytes": sum(f.stat().st_size for f in files),
        "path": core_dir(raw_dir, revision, cfg.dataset.config_name).as_posix(),
    }


def _columns(con: duckdb.DuckDBPyConnection, glob: str) -> list[tuple[str, str]]:
    """Column names and types, unified by name across the files of the glob."""
    rows = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{glob}', union_by_name = true)"
    ).fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]


def profile(con: duckdb.DuckDBPyConnection, glob: str) -> dict[str, Any]:
    """Part b: columns, counts, versions per trial, date ranges, null rates, the share of
    ESTIMATED post dates by year, and which list-type fields exist."""
    # union_by_name: some columns have a different type in some files (see _type_drift).
    src = f"read_parquet('{glob}', union_by_name = true)"
    columns = _columns(con, glob)
    names = {name for name, _ in columns}
    missing = [c for c in KEY_COLUMNS if c not in names]
    rows, trials, distinct_pairs = con.execute(
        f"SELECT count(*), count(DISTINCT nct_id), count(DISTINCT (nct_id, nct_version)) FROM {src}"
    ).fetchone() or (0, 0, 0)
    vpt = (
        con.execute(
            f"""SELECT min(n), quantile_cont(n, 0.5), quantile_cont(n, 0.9), max(n), avg(n)
        FROM (SELECT count(*) AS n FROM {src} GROUP BY nct_id)"""
        ).fetchone()
        or (None,) * 5
    )
    all_names = [n for n, _ in columns]
    null_exprs = ", ".join(f'avg(CASE WHEN "{c}" IS NULL THEN 1 ELSE 0 END)' for c in all_names)
    null_row = con.execute(f"SELECT {null_exprs} FROM {src}").fetchone() or ()
    type_drift = _type_drift(con, glob)
    personal_in_text = _personal_data_in_text(con, src, columns)
    date_ranges: dict[str, list[str | None]] = {}
    for col in ("study_first_post_date", "last_update_post_date", "start_date"):
        if col in names:
            lo, hi = con.execute(f"SELECT min({col}), max({col}) FROM {src}").fetchone() or (
                None,
                None,
            )
            date_ranges[col] = [str(lo) if lo else None, str(hi) if hi else None]
    estimated_by_year: list[dict[str, Any]] = []
    if {"last_update_post_date", "last_update_post_date_type"} <= names:
        for year, n, share in con.execute(
            f"""SELECT year(last_update_post_date) AS y, count(*),
                avg(CASE WHEN upper(last_update_post_date_type) = 'ESTIMATED' THEN 1 ELSE 0 END)
            FROM {src} WHERE last_update_post_date IS NOT NULL GROUP BY y ORDER BY y"""
        ).fetchall():
            estimated_by_year.append({"year": int(year), "versions": int(n), "share": float(share)})
    list_fields = {
        field: sorted(n for n in names if re.search(pattern, n))
        for field, pattern in LIST_FIELD_PATTERNS.items()
    }
    name_matches = {  # a name token (split on "_") that starts with a fragment
        field: sorted(
            n for n in names
            if any(tok.startswith(f) for tok in n.lower().split("_") for f in fragments)
        )
        for field, fragments in FIELD_NAME_FRAGMENTS.items()
    }  # fmt: skip
    nested = sorted(
        n for n, t in columns if "[]" in t or t.upper().startswith(("STRUCT", "MAP", "LIST"))
    )
    personal = sorted(n for n in names if re.search(PERSONAL_DATA_PATTERN, n))
    return {
        "columns": [{"name": n, "type": t} for n, t in columns],
        "missing_key_columns": missing,
        "rows": int(rows),
        "trials": int(trials),
        "duplicate_version_rows": int(rows) - int(distinct_pairs),
        "versions_per_trial": dict(
            zip(("min", "median", "p90", "max", "mean"), [float(v) for v in vpt], strict=True)
        )
        if vpt[0] is not None
        else {},
        "date_ranges": date_ranges,
        "null_rates": {c: float(v) for c, v in zip(all_names, null_row, strict=True)},
        "type_drift": type_drift,
        "personal_data_in_text": personal_in_text,
        "estimated_post_date_share_by_year": estimated_by_year,
        "list_fields": list_fields,
        # True when the core config has a per-version column for the field.
        "per_version_columns": {f: bool(list_fields[f]) for f in PER_VERSION_LIST_FIELDS},
        "field_name_matches": name_matches,
        "nested_columns": nested,
        "personal_data_columns": personal,
    }


EMAIL_PATTERN = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}"
DEGREE_PATTERN = r"(,\s*|\s)(PhD|Ph[.]D[.]|MD|M[.]D[.]|DrPH|MPH)([\s,.]|$)"


def _type_drift(con: duckdb.DuckDBPyConnection, glob: str) -> dict[str, dict[str, list[str]]]:
    """Columns whose Parquet type differs between the files of the glob: type to files."""
    pattern = Path(glob)
    seen: dict[str, dict[str, list[str]]] = {}
    for file in sorted(pattern.parent.glob(pattern.name)):
        for name, kind in _columns(con, file.as_posix()):
            seen.setdefault(name, {}).setdefault(kind, []).append(file.name)
    return {name: kinds for name, kinds in seen.items() if len(kinds) > 1}


def _personal_data_in_text(
    con: duckdb.DuckDBPyConnection, src: str, columns: list[tuple[str, str]]
) -> dict[str, int]:
    """Counts only, over each trial's latest version: text columns holding an email address,
    sponsor names carrying a personal degree, and INDIV-class sponsors. No value is read out."""
    texts = [n for n, t in columns if t == "VARCHAR" and not re.search(PERSONAL_DATA_PATTERN, n)]
    exprs = [
        f"sum(CASE WHEN regexp_matches(\"{c}\", '{EMAIL_PATTERN}') THEN 1 ELSE 0 END)"
        for c in texts
    ]
    names = {n for n, _ in columns}
    extra: list[tuple[str, str]] = []
    if "lead_sponsor_name" in names:
        extra.append((
            "lead_sponsor_name with a degree title (PhD, MD, MPH and similar)",
            f"sum(CASE WHEN regexp_matches(lead_sponsor_name, '{DEGREE_PATTERN}') "
            "THEN 1 ELSE 0 END)",
        ))  # fmt: skip
    if "lead_sponsor_class" in names:
        extra.append(
            (
                "INDIV sponsor class",
                "sum(CASE WHEN upper(lead_sponsor_class) = 'INDIV' THEN 1 ELSE 0 END)",
            )
        )
    row = (
        con.execute(
            f"""SELECT {", ".join(exprs + [e for _, e in extra])} FROM (SELECT * FROM {src}
            QUALIFY row_number() OVER (PARTITION BY nct_id ORDER BY nct_version DESC) = 1)"""
        ).fetchone()
        or ()
    )
    labels = [f"{c} with an email address" for c in texts] + [label for label, _ in extra]
    return {label: int(v or 0) for label, v in zip(labels, row, strict=True) if v}


def write_data_dictionary(prof: dict[str, Any], revision: str, path: Path) -> None:
    lines = [
        "# Data dictionary: version history (core config)",
        "",
        f"Generated by `trialpulse.feasibility.spike --part b` from revision `{revision}` of "
        "`brbk/clinical_trials_history`. Types are as DuckDB reads the Parquet files; null "
        "rates are over all versions.",
        "",
        "| Column | Type | Null rate |",
        "| --- | --- | --- |",
    ]
    null_rates: dict[str, float] = prof["null_rates"]
    drift: dict[str, dict[str, list[str]]] = prof.get("type_drift", {})
    for col in prof["columns"]:
        rate = null_rates.get(col["name"])
        shown = f"{rate:.2%}" if rate is not None else "not computed"
        if rate == 1.0:
            shown += " (always empty)"
        kind = col["type"]
        if col["name"] in drift:
            kind += (
                " (differs by file: "
                + "; ".join(
                    f"{t} in {len(files)}" for t, files in sorted(drift[col["name"]].items())
                )
                + ")"
            )
        lines.append(f"| `{col['name']}` | {kind} | {shown} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _sql_list(values: Iterable[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def cohort_counts(con: duckdb.DuckDBPyConnection, glob: str, cfg: ProjectConfig) -> dict[str, Any]:
    """Part c: cohort size, latest-status groups by first-post year, early stops and
    why_stopped coverage, plus version-level coverage of last_update_post_date.

    The spike takes study type and status from each trial's latest version; Step 4
    builds the full population and outcome logic (reversals, UNKNOWN rule)."""
    st = cfg.statuses
    study_type = cfg.population.study_type.replace("'", "''")
    min_date = cfg.population.min_first_post_date.isoformat()
    # The latest version first, then the population filter: a trial whose latest version is
    # no longer interventional is not in the cohort.
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE spike_cohort AS
        SELECT nct_id, status, why_stopped, post_year FROM (
            SELECT nct_id, upper(overall_status) AS status, why_stopped,
                   year(study_first_post_date) AS post_year, upper(study_type) AS study_type,
                   study_first_post_date
            FROM read_parquet('{glob}')
            QUALIFY row_number() OVER (PARTITION BY nct_id ORDER BY nct_version DESC) = 1
        ) WHERE study_type = '{study_type}' AND study_first_post_date >= DATE '{min_date}'"""
    )
    changed_type = con.execute(
        f"""SELECT count(*) FROM (SELECT nct_id FROM read_parquet('{glob}') GROUP BY nct_id
            HAVING count(DISTINCT coalesce(study_type, '')) > 1)"""
    ).fetchone()
    group_case = f"""CASE
        WHEN status = 'TERMINATED' THEN 'TERMINATED'
        WHEN status = 'WITHDRAWN' THEN 'WITHDRAWN'
        WHEN status IN ({_sql_list(st.competing)}) THEN 'COMPLETED'
        WHEN status IN ({_sql_list(st.unknown)}) THEN 'UNKNOWN'
        WHEN status IN ({_sql_list(st.open)}) THEN 'open'
        ELSE 'other' END"""
    by_year: dict[str, dict[str, int]] = {}
    for year, group, n in con.execute(
        f"SELECT post_year, {group_case} AS g, count(*) FROM spike_cohort GROUP BY 1, 2 ORDER BY 1"
    ).fetchall():
        by_year.setdefault(str(year), {})[str(group)] = int(n)
    early = _sql_list(st.early_stop)
    total, stops, with_reason = con.execute(
        f"""SELECT count(*),
            sum(CASE WHEN status IN ({early}) THEN 1 ELSE 0 END),
            sum(CASE WHEN status IN ({early}) AND nullif(trim(why_stopped), '') IS NOT NULL
                THEN 1 ELSE 0 END)
        FROM spike_cohort"""
    ).fetchone() or (0, 0, 0)
    versions, with_post = con.execute(
        f"SELECT count(*), count(last_update_post_date) FROM read_parquet('{glob}')"
    ).fetchone() or (0, 0)
    cutoff = con.execute(
        f"SELECT max(last_update_post_date) FROM read_parquet('{glob}')"
    ).fetchone()
    status_totals: dict[str, int] = {}
    for groups in by_year.values():
        for group, n in groups.items():
            status_totals[group] = status_totals.get(group, 0) + n
    stops_i, reason_i = int(stops or 0), int(with_reason or 0)
    return {
        "cohort_trials": int(total),
        "early_stops": stops_i,
        "why_stopped_coverage": reason_i / stops_i if stops_i else None,
        "status_totals": status_totals,
        "status_by_first_post_year": by_year,
        "versions_total": int(versions),
        "last_update_post_date_coverage": int(with_post) / int(versions) if versions else None,
        "data_cutoff": str(cutoff[0]) if cutoff and cutoff[0] else None,
        "trials_with_changing_study_type": int(changed_type[0]) if changed_type else 0,
    }


def cohort_ids(con: duckdb.DuckDBPyConnection) -> list[str]:
    """NCT IDs of the cohort built by cohort_counts (same connection)."""
    return [str(r[0]) for r in con.execute("SELECT nct_id FROM spike_cohort").fetchall()]


def latest_rows(
    con: duckdb.DuckDBPyConnection, glob: str, nct_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Latest-version values of the compared fields for the given trials (part d)."""
    names = {name for name, _ in _columns(con, glob)}
    cols = [c for c in COMPARED_DATASET_COLUMNS if c in names]
    rows = con.execute(
        f"""SELECT nct_id, {", ".join(cols)} FROM read_parquet('{glob}')
        WHERE nct_id IN ({_sql_list(nct_ids)})
        QUALIFY row_number() OVER (PARTITION BY nct_id ORDER BY nct_version DESC) = 1"""
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        values = dict(zip(cols, row[1:], strict=True))
        for key, value in values.items():
            if isinstance(value, dt.datetime):
                values[key] = value.date()
        result[str(row[0])] = values
    return result
