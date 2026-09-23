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
    assert summary["mismatches"] == [{"nct_id": "B", "fields": ["lead_sponsor_class"]}]


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
