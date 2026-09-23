"""Part h: render docs/feasibility_report.md from the saved part results.

The report states each criterion's measured value and whether it is met. It does not
state the overall GO or NO-GO decision; Aakrisht records that after review.
"""

import csv
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trialpulse.config import ProjectConfig
from trialpulse.feasibility.checks import wilson_interval

Result = Mapping[str, Any] | None


@dataclass(frozen=True)
class Criterion:
    label: str
    threshold: str
    part: str
    measure: Callable[[Mapping[str, Any]], tuple[str, bool | None]]


def _pct(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def _num(x: float | int | None) -> str:
    return "n/a" if x is None else f"{x:,}"


def _at_least(value: float | int | None, floor: float) -> bool | None:
    return None if value is None else value >= floor


def read_manual_results(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _manual_measure(manual: list[dict[str, str]]) -> tuple[str, bool | None]:
    done = [r for r in manual if r.get("result", "").strip()]
    if len(done) < 10:
        return f"{len(done)} of 10 recorded", None
    passed = sum(1 for r in done if r["result"].strip().casefold() == "pass")
    return f"{passed} of {len(done)} pass", passed >= 9


CRITERIA: tuple[Criterion, ...] = (
    Criterion(
        "Cohort trials",
        ">= 200,000",
        "c",
        lambda r: (_num(r["cohort_trials"]), _at_least(r["cohort_trials"], 200_000)),
    ),
    Criterion(
        "last_update_post_date present on versions",
        ">= 99%",
        "c",
        lambda r: (
            _pct(r["last_update_post_date_coverage"], 2),
            _at_least(r["last_update_post_date_coverage"], 0.99),
        ),
    ),
    Criterion(
        "Automated agreement on compared fields",
        ">= 95%",
        "d",
        lambda r: (_pct(r["overall"]["share"]), _at_least(r["overall"]["share"], 0.95)),
    ),
    Criterion(
        "Early stops in the cohort",
        ">= 20,000",
        "c",
        lambda r: (_num(r["early_stops"]), _at_least(r["early_stops"], 20_000)),
    ),
    Criterion(
        "why_stopped present among early stops",
        ">= 80%",
        "c",
        lambda r: (_pct(r["why_stopped_coverage"]), _at_least(r["why_stopped_coverage"], 0.80)),
    ),
    Criterion(
        "API v2 delta pull works end to end",
        "works",
        "g",
        lambda r: (
            f"{_num(r['delta']['records'])} records in {r['delta']['pages']} pages",
            r["delta"]["records"] == r["delta"]["total_count"],
        ),
    ),
)


def _verdict(met: bool | None, unknown: str) -> str:
    return unknown if met is None else ("Yes" if met else "No")


def _status_word(result: Result) -> str:
    if result is None:
        return "not run"
    return str(result.get("status", "unknown")).replace("_", " ")


def _criteria_table(results: Mapping[str, Result], manual: list[dict[str, str]]) -> list[str]:
    lines = ["| Criterion | Threshold | Measured | Met? |", "| --- | --- | --- | --- |"]
    rows: list[tuple[str, str, str, str]] = []
    for c in CRITERIA:
        result = results.get(c.part)
        if result is None or result.get("status") != "done":
            rows.append((c.label, c.threshold, f"part {c.part} {_status_word(result)}", "Blocked"))
            continue
        measured, met = c.measure(result)
        rows.append((c.label, c.threshold, measured, _verdict(met, "?")))
    e = results.get("e")
    if e is None or e.get("status") not in {"done", "pending_manual"}:
        rows.insert(
            3, ("Manual check (part e)", ">= 9 of 10", f"part e {_status_word(e)}", "Blocked")
        )
    else:
        measured, met = _manual_measure(manual)
        rows.insert(3, ("Manual check (part e)", ">= 9 of 10", measured, _verdict(met, "Pending")))
    lines += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]
    return lines


def _part_a(r: Mapping[str, Any]) -> list[str]:
    return [
        f"- Revision `{r['revision']}` (commit `{r['commit']}`), {r['files']} Parquet files, "
        f"{r['bytes'] / 1e6:,.1f} MB, in `{r['path']}`."
    ]


def _part_b(r: Mapping[str, Any]) -> list[str]:
    vpt = r.get("versions_per_trial", {})
    lines = [
        f"- {_num(r['rows'])} versions across {_num(r['trials'])} trials; "
        f"{_num(r['duplicate_version_rows'])} duplicate (nct_id, nct_version) rows.",
        f"- Versions per trial: median {vpt.get('median')}, p90 {vpt.get('p90')}, "
        f"max {vpt.get('max')}, mean {vpt.get('mean', 0):.2f}.",
        f"- {len(r['columns'])} columns; full list in "
        "[data_dictionary_history.md](data_dictionary_history.md).",
        f"- Missing expected columns: {', '.join(r['missing_key_columns']) or 'none'}.",
        "- Date ranges: "
        + "; ".join(f"`{k}` {v[0]} to {v[1]}" for k, v in r["date_ranges"].items())
        + ".",
        "",
        "List-type fields found as columns:",
        "",
        "| Field | Matching columns |",
        "| --- | --- |",
    ]
    for name, cols in r["list_fields"].items():
        lines.append(f"| {name} | {', '.join(f'`{c}`' for c in cols) or 'none'} |")
    lines += ["", "Share of ESTIMATED post dates by year of last_update_post_date:", ""]
    lines += ["| Year | Versions | ESTIMATED |", "| --- | --- | --- |"]
    for row in r["estimated_post_date_share_by_year"]:
        lines.append(f"| {row['year']} | {_num(row['versions'])} | {_pct(row['share'])} |")
    return lines


def _part_c(r: Mapping[str, Any]) -> list[str]:
    groups = ["TERMINATED", "WITHDRAWN", "COMPLETED", "UNKNOWN", "open", "other"]
    lines = [
        f"- Cohort: {_num(r['cohort_trials'])} trials; early stops {_num(r['early_stops'])}; "
        f"why_stopped present for {_pct(r['why_stopped_coverage'])} of early stops.",
        f"- Data cutoff (max last_update_post_date): {r['data_cutoff']}.",
        "",
        "| First-post year | " + " | ".join(groups) + " |",
        "| --- |" + " --- |" * len(groups),
    ]
    for year, counts in sorted(r["status_by_first_post_year"].items()):
        lines.append(f"| {year} | " + " | ".join(_num(counts.get(g, 0)) for g in groups) + " |")
    return lines


def _part_d(r: Mapping[str, Any]) -> list[str]:
    lines = [
        f"- Sampled {r['sampled']} cohort trials (seeded); {r['comparable']} comparable. "
        f"Excluded: {r['excluded'] or 'none'}.",
        f"- Overall agreement: {_pct(r['overall']['share'])} "
        f"({r['overall']['agree']} of {r['overall']['total']} field comparisons); "
        f"{r['trials_fully_agreeing']} trials agree on every field.",
        "",
        "| Field | Agree | Share |",
        "| --- | --- | --- |",
    ]
    for name, v in r["per_field"].items():
        lines.append(f"| {name} | {v['agree']} of {v['total']} | {_pct(v['share'])} |")
    return lines


def _part_f(r: Mapping[str, Any]) -> list[str]:
    lines = [
        f"- Sampled {r['sampled']} trials from the {r.get('sample_source', 'cohort')}; "
        f"audited {r['trials_audited']} ({r['multi_version_trials']} with more than one "
        f"version); {len(r['failures'])} failed.",
        f"- Mode: `{r['mode']}`; {_num(r['versions_fetched'])} version snapshots fetched of "
        f"{_num(r['versions_in_change_logs'])} versions in the change logs; "
        f"{_num(r['requests_this_run'])} requests in the last run at 20 per minute or less.",
        "",
        "| Field | Changed after version 0 | Share of sampled | 95% interval (Wilson) "
        "| Share among multi-version | Under 3%? |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, v in r["fields"].items():
        ci = wilson_interval(v["changed"], v["trials"])
        shown_ci = f"{_pct(ci[0])} to {_pct(ci[1])}" if ci else "n/a"
        lines.append(
            f"| {name} | {v['changed']} of {v['trials']} | {_pct(v['share'])} | {shown_ci} | "
            f"{_pct(v['share_among_multi_version'])} | {'Yes' if v['under_threshold'] else 'No'} |"
        )
    lines += [
        "",
        "The rule compares the measured share with 3%. The interval shows the sampling "
        "uncertainty of a 150-trial sample and does not change the rule.",
    ]
    for name, why in r.get("not_audited", {}).items():
        lines.append(f"\nNot audited: `{name}`: {why}.")
    return lines


def _part_g(r: Mapping[str, Any]) -> list[str]:
    d, b = r["delta"], r["bulk"]
    s = b["summary"]
    lines = [
        f"- API version {r['api_version'].get('apiVersion')}, data timestamp "
        f"{r['api_version'].get('dataTimestamp')}.",
        f"- Delta pull since {d['since']}: filter `{d['filter']}`; {_num(d['records'])} records "
        f"of {_num(d['total_count'])} reported, {d['pages']} pages, "
        f"{d['elapsed_seconds_this_run']} s in the last run "
        f"({d['pages_from_cache']} pages from cache).",
        f"- Cohort bulk pull: filter `{b['filter']}`; {_num(b['records'])} records of "
        f"{_num(b['total_count'])} reported, {b['pages']} pages, "
        f"{b['elapsed_seconds_this_run']} s in the last run ({b['pages_from_cache']} pages from "
        f"cache); {_num(s['trials'])} unique trials in `{b['parquet']}` "
        f"({s['file_bytes'] / 1e6:,.1f} MB, not committed).",
        f"- Current records (official API, not the dataset): "
        f"{_num(s['current_early_stops'])} TERMINATED or WITHDRAWN, why_stopped present for "
        f"{_pct(s['current_why_stopped_coverage'])} of them.",
        f"- Present in the bulk pull: phases {_pct(s['share_with_phases'])}, conditions "
        f"{_pct(s['share_with_conditions'])}, MeSH browse branches "
        f"{_pct(s['share_with_browse_branches'])}, MeSH terms {_pct(s['share_with_mesh_terms'])}, "
        f"MeSH ancestors {_pct(s['share_with_mesh_ancestors'])}, interventions "
        f"{_pct(s['share_with_interventions'])}, arm groups {_pct(s['share_with_arm_groups'])}, "
        f"locations {_pct(s['share_with_locations'])}.",
        "",
        "JSON paths of every field TrialPulse will ingest, with presence in the delta pull "
        "(among the records each field applies to):",
        "",
        "| Field | JSON path | Present |",
        "| --- | --- | --- |",
    ]
    for name, v in r["field_presence"].items():
        lines.append(f"| {name} | `{v['path']}` | {_pct(v['share'])} of {_num(v['applicable'])} |")
    return lines


RENDERERS: dict[str, Callable[[Mapping[str, Any]], list[str]]] = {
    "a": _part_a,
    "b": _part_b,
    "c": _part_c,
    "d": _part_d,
    "f": _part_f,
    "g": _part_g,
}
TITLES = {
    "a": "a. Download",
    "b": "b. Profile",
    "c": "c. Cohort counts",
    "d": "d. Automated check against API v2",
    "e": "e. Manual check (Aakrisht)",
    "f": "f. Stability audit of list fields",
    "g": "g. API v2 check",
}


def _stable_fields(results: Mapping[str, Result]) -> list[str]:
    f = results.get("f")
    if f is None or f.get("status") != "done":
        return ["Not yet measured (part f has not completed)."]
    passing = [n for n, v in f["fields"].items() if v["under_threshold"]]
    failing = [n for n, v in f["fields"].items() if not v["under_threshold"]]
    return [
        f"- Pass (changed in under 3% of sampled trials): {', '.join(passing) or 'none'}.",
        f"- Fail: {', '.join(failing) or 'none'}.",
        "- Not audited: MeSH terms, MeSH ancestors and browse branches. NLM derives them from "
        "the current conditions, and version snapshots carry no derivedSection.",
    ]


def render_report(
    results: Mapping[str, Result], manual: list[dict[str, str]], cfg: ProjectConfig
) -> str:
    lines = [
        "# Feasibility report (Step 2)",
        "",
        "Draft generated by `uv run python -m trialpulse.feasibility.spike --part h` from the "
        "saved part results. Rerun it after any part changes.",
        "",
        "**Decision: not stated.** This draft reports the measured numbers only. GO or NO-GO "
        "is recorded after Aakrisht reviews it and the blocked parts have run.",
        "",
        f"Dataset: `{cfg.dataset.repo_id}`, config `{cfg.dataset.config_name}`, pinned "
        f"revision `{cfg.dataset.revision or 'not pinned yet'}`.",
        "",
        "## Criteria",
        "",
        *_criteria_table(results, manual),
        "",
        "## Current-record-only fields and the under-3% stability rule",
        "",
        *_stable_fields(results),
    ]
    for part, title in TITLES.items():
        result = results.get(part)
        lines += ["", f"## {title}", "", f"Status: **{_status_word(result)}**."]
        if result is None:
            continue
        if result.get("status") in {"blocked", "failed"}:
            lines.append(f"Reason: {result.get('reason') or result.get('error')}")
            continue
        if part == "e":
            lines.append(
                f"Checklist of {len(result['trials'])} trials in `{result['checklist']}` "
                f"(not committed); record pass or fail in `{result['results_file']}`."
            )
            continue
        renderer = RENDERERS.get(part)
        if renderer and result.get("status") == "done":
            lines += ["", *renderer(result)]
    return "\n".join(lines) + "\n"
