"""ClinicalTrials.gov's undocumented internal history endpoint. VERIFICATION ONLY.

CLAUDE.md Section 7 allows it only for small verification samples in Step 2, at 20
requests per minute or less, and never as a production dependency. Only part f of the
spike uses it. No module outside trialpulse.feasibility may import this one (tested).

Version snapshots contain names and contact details of people. Nothing raw is cached:
the cache holds only the projection of the audited fields.
"""

import time
from collections import Counter
from collections.abc import Sequence
from typing import Any

import httpx

from trialpulse.feasibility.checks import stability_summary
from trialpulse.feasibility.ctgov_v2 import get_path
from trialpulse.feasibility.dataset import BlockedError
from trialpulse.feasibility.fetch import JsonCache, JsonFetcher, RateLimiter, RetryableStatusError

INT_STUDIES_URL = "https://clinicaltrials.gov/api/int/studies"
REQUESTS_PER_MINUTE = 20
MAX_RUNTIME_MINUTES = 60  # CLAUDE.md Section 3: jobs over about an hour need a cheaper plan
MAX_CHANGE_SHARE = 0.03  # CLAUDE.md Section 8, rule 2: allowed if changed in under 3%
AUDITED_FIELDS: tuple[str, ...] = (
    "phases",
    "phase_group",
    "conditions",
    "interventions",
    "n_arm_groups",
    "n_locations",
)
_RELEVANT_LABEL_WORDS = ("design", "condition", "arm", "intervention", "location")
_IRRELEVANT_LABEL_WORDS = (
    "status",
    "identification",
    "sponsor",
    "oversight",
    "description",
    "outcome",
    "eligibility",
    "ipd",
    "reference",
    "result",
    "document",
    "more info",
    "adverse",
    "baseline",
    "participant flow",
    "agreement",
    "limitation",
)
_UNAVAILABLE_AFTER = 5  # consecutive failed trials before the endpoint counts as unavailable


# Phase group of a version's phase list: early (Early Phase 1, Phase 1), mid (Phase 1/2,
# Phase 2), late (Phase 2/3, Phase 3), post-approval (Phase 4), and N/A (NA, or no phase
# recorded). Any other combination is "other", so it is counted instead of hidden.
PHASE_GROUPS: dict[frozenset[str], str] = {
    frozenset({"EARLY_PHASE1"}): "early",
    frozenset({"PHASE1"}): "early",
    frozenset({"PHASE1", "PHASE2"}): "mid",
    frozenset({"PHASE2"}): "mid",
    frozenset({"PHASE2", "PHASE3"}): "late",
    frozenset({"PHASE3"}): "late",
    frozenset({"PHASE4"}): "post_approval",
    frozenset({"NA"}): "na",
    frozenset(): "na",
}


def phase_group(phases: Sequence[str]) -> str:
    return PHASE_GROUPS.get(frozenset(phases), "other")


def label_may_touch_audited_fields(label: str) -> bool:
    """Whether a change-log module label can cover an audited field. Unknown labels
    count as relevant, so the filter can only fetch too much, never too little."""
    text = label.casefold()
    if any(word in text for word in _RELEVANT_LABEL_WORDS):
        return True
    return not any(word in text for word in _IRRELEVANT_LABEL_WORDS)


def project_change_log(payload: dict[str, Any]) -> list[dict[str, Any]]:
    changes = get_path(payload, "history.changes") or []
    log = [
        {
            "version": int(c["version"]),
            "date": c.get("date"),
            "status": c.get("status"),
            "module_labels": list(c.get("moduleLabels") or []),
        }
        for c in changes
        if isinstance(c, dict) and "version" in c
    ]
    return sorted(log, key=lambda c: c["version"])


def project_version(payload: dict[str, Any]) -> dict[str, Any]:
    """The audited fields of one version snapshot, normalized for comparison."""
    ps = get_path(payload, "study.protocolSection") or {}
    conditions = get_path(ps, "conditionsModule.conditions") or []
    interventions = get_path(ps, "armsInterventionsModule.interventions") or []
    return {
        "phases": sorted(str(p) for p in (get_path(ps, "designModule.phases") or [])),
        "conditions": sorted({str(c).strip().casefold() for c in conditions if str(c).strip()}),
        "interventions": sorted(
            {
                f"{str(i.get('type') or '').upper()}|{str(i.get('name') or '').strip().casefold()}"
                for i in interventions
                if isinstance(i, dict)
            }
        ),
        "n_arm_groups": len(get_path(ps, "armsInterventionsModule.armGroups") or []),
        "n_locations": len(get_path(ps, "contactsLocationsModule.locations") or []),
        "overall_status": get_path(ps, "statusModule.overallStatus"),
    }


def fetch_change_log(fetcher: JsonFetcher, cache: JsonCache, nct_id: str) -> list[dict[str, Any]]:
    key = f"{nct_id}/changes"
    cached = cache.get(key)
    if cached is None:
        payload = fetcher.get(f"{INT_STUDIES_URL}/{nct_id}", {"history": "true"})
        cached = project_change_log(payload)
        cache.put(key, cached)
    return list(cached)


def fetch_version(
    fetcher: JsonFetcher, cache: JsonCache, nct_id: str, version: int
) -> dict[str, Any]:
    key = f"{nct_id}/v{version:04d}"
    cached = cache.get(key)
    if cached is None:
        payload = fetcher.get(f"{INT_STUDIES_URL}/{nct_id}/history/{version}")
        cached = project_version(payload)
        cache.put(key, cached)
    return dict(cached)


def versions_to_fetch(change_log: Sequence[dict[str, Any]], mode: str) -> list[int]:
    """All versions, or ("changed_modules_only") the first version plus every version
    whose module labels may touch an audited field. The skipped versions leave the
    audited fields as they were, so the measured change rate is the same."""
    versions = [int(c["version"]) for c in change_log]
    if mode == "all_versions" or not versions:
        return versions
    first = versions[0]
    return [
        int(c["version"])
        for c in change_log
        if int(c["version"]) == first
        or any(label_may_touch_audited_fields(label) for label in c["module_labels"])
    ]


def _uncached(cache: JsonCache, keys: Sequence[str]) -> int:
    return sum(1 for key in keys if not cache.path(key).is_file())


def run_stability_audit(
    client: httpx.Client,
    cache: JsonCache,
    sample: Sequence[str],
    manual_fallback: Sequence[str],
    fetcher: JsonFetcher | None = None,
) -> dict[str, Any]:
    """Part f: measure how often the audited list fields change after version 0."""
    fetcher = fetcher or JsonFetcher(client, RateLimiter(REQUESTS_PER_MINUTE))
    start = time.monotonic()
    logs: dict[str, list[dict[str, Any]]] = {}
    failures: dict[str, str] = {}
    for nct_id in sample:
        try:
            logs[nct_id] = fetch_change_log(fetcher, cache, nct_id)
        except (httpx.HTTPError, RetryableStatusError, ValueError) as exc:
            failures[nct_id] = f"{type(exc).__name__}: {exc}"
            if not logs and len(failures) >= _UNAVAILABLE_AFTER:
                raise BlockedError(
                    "internal history endpoint unavailable; manual fallback: inspect the "
                    f"Record History tab of these trials: {', '.join(manual_fallback)}"
                ) from exc

    versions_total = sum(len(v) for v in logs.values())
    full_keys = [f"{n}/v{c['version']:04d}" for n, log in logs.items() for c in log]
    full_minutes = _uncached(cache, full_keys) / REQUESTS_PER_MINUTE
    elapsed_minutes = (time.monotonic() - start) / 60
    mode = (
        "all_versions"
        if elapsed_minutes + full_minutes <= MAX_RUNTIME_MINUTES
        else "changed_modules_only"
    )

    snapshots: dict[str, list[dict[str, Any]]] = {}
    fetched_versions = 0
    for nct_id, log in logs.items():
        trial_snapshots: list[dict[str, Any]] = []
        try:
            for version in versions_to_fetch(log, mode):
                trial_snapshots.append(fetch_version(fetcher, cache, nct_id, version))
                fetched_versions += 1
        except (httpx.HTTPError, RetryableStatusError, ValueError) as exc:
            failures[nct_id] = f"{type(exc).__name__}: {exc}"
            continue
        snapshots[nct_id] = trial_snapshots

    for trial_snapshots in snapshots.values():
        for snapshot in trial_snapshots:
            snapshot["phase_group"] = phase_group(snapshot["phases"])
    labels = Counter(label for log in logs.values() for c in log for label in c["module_labels"])
    summary = stability_summary(snapshots, AUDITED_FIELDS, MAX_CHANGE_SHARE)
    groups_at_v0 = Counter(s[0]["phase_group"] for s in snapshots.values() if s)
    return {
        **summary,
        "sampled": len(sample),
        "failures": failures,
        "mode": mode,
        "estimated_minutes_all_versions": round(full_minutes, 1),
        "versions_in_change_logs": versions_total,
        "versions_fetched": fetched_versions,
        # One request per change log and per version, whether fetched now or from cache.
        "requests_needed": len(logs) + fetched_versions,
        "requests_this_run": fetcher.requests_made,
        "elapsed_minutes_this_run": round((time.monotonic() - start) / 60, 1),
        "module_labels_seen": dict(labels.most_common()),
        "phase_group_at_version_0": dict(groups_at_v0.most_common()),
        "not_audited": {
            "condition_mesh_fields": (
                "version snapshots carry no derivedSection, so MeSH terms, ancestors and "
                "browse branches have no history to audit"
            )
        },
    }
