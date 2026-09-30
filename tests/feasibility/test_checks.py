"""Sampling, the dataset-versus-API comparison and the stability measure."""

import datetime as dt

import pytest

from trialpulse.feasibility.checks import (
    compare_trial,
    date_parts,
    dates_agree,
    field_changed_after_first,
    seeded_sample,
    stability_summary,
    summarize_comparisons,
    wilson_interval,
)

CUTOFF = dt.date(2026, 5, 15)


def test_seeded_sample_is_reproducible_and_sorted() -> None:
    ids = [f"NCT{i:08d}" for i in range(1000)]

    first = seeded_sample(ids, 20, seed=42)

    assert first == seeded_sample(reversed(ids), 20, seed=42)
    assert first == sorted(first)
    assert len(set(first)) == 20
    assert set(first) <= set(ids)
    assert first != seeded_sample(ids, 20, seed=7)
    assert seeded_sample(ids[:5], 20, seed=42) == ids[:5]


def test_date_parts_respect_precision() -> None:
    assert date_parts("2019-03") == (2019, 3)
    assert date_parts("2019-03-14") == (2019, 3, 14)
    assert date_parts(dt.date(2019, 3, 1), "month") == (2019, 3)
    assert date_parts(dt.date(2019, 3, 1), "YEAR") == (2019,)
    assert date_parts(None) is None


def test_dates_agree_at_the_coarser_precision() -> None:
    assert dates_agree(dt.date(2019, 3, 1), "2019-03", "month")
    assert dates_agree(dt.date(2019, 3, 14), "2019-03")
    assert not dates_agree(dt.date(2019, 4, 1), "2019-03")
    assert dates_agree(None, None)
    assert not dates_agree(None, "2019-03")


def _rows() -> tuple[dict[str, object], dict[str, object]]:
    dataset = {
        "overall_status": "TERMINATED",
        "start_date": dt.date(2019, 3, 1),
        "start_date_precision": "month",
        "primary_completion_date": dt.date(2020, 1, 31),
        "enrollment_count": 12,
        "lead_sponsor_class": "INDUSTRY",
        "why_stopped": "Slow  accrual",
    }
    api = {
        "overall_status": "TERMINATED",
        "start_date": "2019-03",
        "primary_completion_date": "2020-01-31",
        "enrollment_count": 12,
        "lead_sponsor_class": "INDUSTRY",
        "why_stopped": "slow accrual",
        "last_update_post_date": "2021-02-02",
    }
    return dataset, api


def test_compare_trial_agrees_after_normalization() -> None:
    dataset, api = _rows()
    result = compare_trial("NCT1", dataset, api, CUTOFF)
    assert result.comparable
    assert all(result.agreements.values())


def test_compare_trial_flags_a_real_difference() -> None:
    dataset, api = _rows()
    api["enrollment_count"] = 40
    result = compare_trial("NCT1", dataset, api, CUTOFF)
    assert result.agreements["enrollment_count"] is False
    assert sum(result.agreements.values()) == 5


def test_records_updated_after_cutoff_are_not_compared() -> None:
    dataset, api = _rows()
    api["last_update_post_date"] = "2026-06-01"
    api["overall_status"] = "COMPLETED"
    result = compare_trial("NCT1", dataset, api, CUTOFF)
    assert not result.comparable
    assert result.reason == "official record updated after the dataset cutoff"


def test_missing_rows_are_not_compared() -> None:
    dataset, api = _rows()
    assert compare_trial("NCT1", None, api, CUTOFF).reason == "not in dataset"
    assert compare_trial("NCT1", dataset, None, CUTOFF).reason == "no official record"


def test_summary_shares() -> None:
    dataset, api = _rows()
    changed = dict(api, lead_sponsor_class="OTHER")
    late = dict(api, last_update_post_date="2026-09-01")
    comparisons = [
        compare_trial("A", dataset, api, CUTOFF),
        compare_trial("B", dataset, changed, CUTOFF),
        compare_trial("C", dataset, late, CUTOFF),
    ]

    summary = summarize_comparisons(comparisons)

    assert summary["sampled"] == 3
    assert summary["comparable"] == 2
    assert summary["excluded"] == {"official record updated after the dataset cutoff": 1}
    assert summary["per_field"]["lead_sponsor_class"]["share"] == pytest.approx(0.5)
    assert summary["overall"] == {"agree": 11, "total": 12, "share": pytest.approx(11 / 12)}
    assert summary["trials_fully_agreeing"] == 1
    assert summary["mismatches"] == [
        {"nct_id": "B", "fields": ["lead_sponsor_class"], "explanations": {}}
    ]


def test_wilson_interval_known_values() -> None:
    # 0 of 10: the upper bound is z^2 / (n + z^2).
    assert wilson_interval(0, 10) == pytest.approx((0.0, 1.96**2 / (10 + 1.96**2)))
    low, high = wilson_interval(6, 150)  # type: ignore[misc]
    assert (low, high) == pytest.approx((0.01846, 0.08452), abs=1e-4)
    assert wilson_interval(0, 0) is None


def test_a_reverted_change_still_counts() -> None:
    snapshots = [{"phases": ["PHASE2"]}, {"phases": ["PHASE3"]}, {"phases": ["PHASE2"]}]
    assert field_changed_after_first(snapshots, "phases")
    assert not field_changed_after_first(snapshots[:1], "phases")
    assert not field_changed_after_first([], "phases")


def test_stability_summary_threshold() -> None:
    stable = [{"n_locations": 1}, {"n_locations": 1}]
    moved = [{"n_locations": 1}, {"n_locations": 2}]
    single = [{"n_locations": 3}]
    by_trial = {"A": moved, "B": stable, "C": single, "D": stable}

    summary = stability_summary(by_trial, ["n_locations"], max_change_share=0.03)

    field = summary["fields"]["n_locations"]
    assert summary["trials_audited"] == 4
    assert summary["multi_version_trials"] == 3
    assert field["changed"] == 1
    assert field["share"] == pytest.approx(0.25)
    assert field["share_among_multi_version"] == pytest.approx(1 / 3)
    assert field["under_threshold"] is False
    assert stability_summary({"B": stable}, ["n_locations"], 0.03)["fields"]["n_locations"][
        "under_threshold"
    ]


def test_mismatches_with_a_known_cause_are_explained() -> None:
    from trialpulse.feasibility.checks import explain_mismatch

    open_statuses = ("NOT_YET_RECRUITING", "RECRUITING", "ACTIVE_NOT_RECRUITING")
    unknown = "UNKNOWN status set by the registry without a new version"
    ds = {"overall_status": "RECRUITING", "last_update_post_date": dt.date(2024, 4, 2)}
    api = {"overall_status": "UNKNOWN", "last_update_post_date": "2024-04-02"}
    assert explain_mismatch("overall_status", ds, api, open_statuses) == unknown
    later = {**api, "last_update_post_date": "2025-01-10"}  # a real new version: not explained
    assert explain_mismatch("overall_status", ds, later, open_statuses) is None
    # The dataset side being UNKNOWN is not the registry's lapse rule: left unexplained.
    flipped = {"overall_status": "UNKNOWN", "last_update_post_date": dt.date(2020, 7, 31)}
    current = {"overall_status": "ACTIVE_NOT_RECRUITING", "last_update_post_date": "2020-07-31"}
    assert explain_mismatch("overall_status", flipped, current, open_statuses) is None
    # A terminal status never lapses to UNKNOWN.
    ended = {"overall_status": "TERMINATED", "last_update_post_date": dt.date(2024, 4, 2)}
    assert explain_mismatch("overall_status", ended, api, open_statuses) is None
    encoding = "encoding only: the same text once HTML entities and backslash escapes go"
    assert (
        explain_mismatch(
            "why_stopped",
            {"why_stopped": "Sponsor&#x27;s decision &gt; 2 sites"},
            {"why_stopped": "Sponsor's decision \\> 2 sites"},
        )
        == encoding
    )
    assert (
        explain_mismatch("why_stopped", {"why_stopped": "Funding"}, {"why_stopped": "Other"})
        is None
    )


def test_a_dataset_version_newer_than_the_official_record_is_labeled() -> None:
    from trialpulse.feasibility.checks import explain_mismatch

    ds = {"enrollment_count": 20, "last_update_post_date": dt.date(2026, 5, 13)}
    api = {"enrollment_count": 25, "last_update_post_date": "2023-08-28"}
    assert explain_mismatch("enrollment_count", ds, api) == (
        "the dataset holds a version newer than the official record shows"
    )
    older = {**ds, "last_update_post_date": dt.date(2023, 8, 28)}
    assert explain_mismatch("enrollment_count", older, api) is None


def test_version_histories_are_compared_version_by_version() -> None:
    from trialpulse.feasibility.checks import (
        compare_version_history,
        summarize_version_histories,
    )

    dataset = [
        {"version": 0, "submitted": dt.date(2020, 1, 5), "status": "RECRUITING"},
        {"version": 1, "submitted": dt.date(2021, 2, 1), "status": "RECRUITING"},
        {"version": 2, "submitted": dt.date(2026, 9, 24), "status": "COMPLETED"},  # after the log
    ]
    log = [
        {"version": 0, "date": "2020-01-05", "status": "RECRUITING"},
        {"version": 1, "date": "2021-02-01", "status": "UNKNOWN"},
    ]
    result = compare_version_history(dataset, log, ["RECRUITING"])
    assert result["compared"] == 2
    assert result["posted_after_log"] == [2]
    assert result["missing_from_log"] == []
    assert result["date_mismatches"] == []
    assert result["status_mismatches"] == [1]
    assert result["explained"] == {
        1: "UNKNOWN shown by the registry for the latest version, without a new version"
    }
    gap = compare_version_history(dataset[:1] + dataset[2:], log, ["RECRUITING"])
    assert gap["only_in_log"] == [1]  # a logged version the dataset lacks
    shifted = [{**dataset[0], "submitted": dt.date(2020, 1, 6)}]
    assert compare_version_history(shifted, log[:1])["date_mismatches"] == [0]

    summary = summarize_version_histories({"NCT1": result, "NCT2": gap})
    assert summary["trials"] == 2
    assert summary["trials_with_the_same_versions"] == 1
    assert summary["status_agreement"] == {"agree": 2, "total": 3}  # 1 of 2, 1 of 1
    assert summary["versions_posted_after_logs"] == 2
    assert [m["nct_id"] for m in summary["mismatches"]] == ["NCT1", "NCT2"]


def test_version_fields_are_compared_with_causes() -> None:
    from trialpulse.feasibility.checks import (
        NO_KNOWN_CAUSE,
        compare_version_fields,
        summarize_version_check,
    )

    ds = {
        "last_update_submit_date": dt.date(2020, 1, 5), "overall_status": "RECRUITING",
        "study_type": "INTERVENTIONAL", "study_first_post_date": dt.date(2020, 1, 7),
        "start_date": dt.date(2019, 12, 1), "start_date_precision": "month",
        "primary_completion_date": dt.date(2021, 6, 1),
        "primary_completion_date_precision": "month",
        "enrollment_count": 40, "enrollment_type": "ESTIMATED", "lead_sponsor_class": "OTHER",
    }  # fmt: skip
    official = {
        "submitted_date": "2020-01-05", "overall_status": "RECRUITING",
        "study_type": "INTERVENTIONAL", "study_first_post_date": "2020-01-07",
        "start_date": "2019-12", "primary_completion_date": "2021-06",
        "enrollment_count": 40, "enrollment_type": "ESTIMATED", "lead_sponsor_class": "OTHER",
    }  # fmt: skip
    same = compare_version_fields(ds, official, is_latest=False)
    assert all(r["agree"] for r in same.values())
    off = {
        **official,
        "overall_status": "UNKNOWN",
        "enrollment_type": "ANTICIPATED",
        "enrollment_count": 41,
    }
    result = compare_version_fields(ds, off, is_latest=True, open_statuses=["RECRUITING"])
    assert result["overall_status"]["cause"] == (
        "UNKNOWN shown by the registry for the latest version, without a new version"
    )
    assert result["enrollment_type"]["cause"] == "legacy name: ANTICIPATED is today's ESTIMATED"
    assert result["enrollment_count"]["cause"] == NO_KNOWN_CAUSE
    not_latest = compare_version_fields(ds, off, is_latest=False, open_statuses=["RECRUITING"])
    assert not_latest["overall_status"]["cause"] == NO_KNOWN_CAUSE
    summary = summarize_version_check({("NCT1", 0): same, ("NCT1", 3): result})
    assert summary["versions"] == 2
    assert summary["overall"] == {"agree": 15, "total": 18, "share": 15 / 18}
    assert summary["unexplained"] == 1
    assert [m["field"] for m in summary["mismatches"]] == [
        "overall_status", "enrollment_count", "enrollment_type",
    ]  # fmt: skip
