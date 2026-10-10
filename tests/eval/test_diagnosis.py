"""The calibration diagnosis of M0 (Step 9): the two forms of a training row, the categories
that tell them apart, and the mixed label sets. Hand-built cohort files; no real data."""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pytest

from trialpulse.cli import RefusedError
from trialpulse.config import ProjectConfig, load_project_config
from trialpulse.eval import diagnosis as dg
from trialpulse.eval.ipcw import censoring_groups, horizon_labels_and_weights
from trialpulse.eval.metrics import calibration_slope_intercept, ipcw_auc
from trialpulse.eval.walkforward import (
    evaluation_rows,
    horizon_days,
    load_landmark_rows,
    training_path,
    training_rows,
)
from trialpulse.models.aalen_johansen import AalenJohansenModel

D = dt.date
T = D(2016, 1, 1)
LANDMARK_COLUMNS = ["trial_id", "landmark_index", "landmark_date", "event", "event_date", "stratum"]
OUTCOME_DEFAULTS: dict[str, Any] = {
    "in_window": True,
    "ever_in_population": True,
    "reversal": False,
    "event": 0,
    "censor_reason": "cutoff",
}


def _write(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=columns)  # noqa: F841 (read by DuckDB below)
    casts = {
        "landmark_index": "BIGINT", "event": "BIGINT", "landmark_date": "DATE",
        "event_date": "DATE", "reversal": "BOOLEAN", "in_window": "BOOLEAN",
        "ever_in_population": "BOOLEAN",
    }  # fmt: skip
    select = ", ".join(f"CAST({c} AS {casts[c]}) AS {c}" if c in casts else c for c in columns)
    with duckdb.connect() as con:
        con.execute(f"COPY (SELECT {select} FROM frame) TO '{path.as_posix()}' (FORMAT parquet)")
    return path


def _landmarks(trial: str, dates: list[dt.date], event: int, end: dt.date) -> list[dict[str, Any]]:
    return [
        {"trial_id": trial, "landmark_index": k, "landmark_date": day, "event": event,
         "event_date": end, "stratum": "OTHER"}
        for k, day in enumerate(dates)
    ]  # fmt: skip


def _outcome(trial: str, end: dt.date, **fields: Any) -> dict[str, Any]:
    return {"trial_id": trial, "event_date": end, **OUTCOME_DEFAULTS, **fields}


OUTCOME_COLUMNS = ["trial_id", "event_date", *OUTCOME_DEFAULTS]
HALF_YEARS = [D(2013, 1, 1), D(2013, 7, 1), D(2014, 1, 1), D(2014, 7, 1), D(2015, 1, 1),
              D(2015, 7, 1)]  # fmt: skip


@pytest.fixture
def hand_cohort(tmp_path: Path) -> dict[str, Path]:
    """Five trials, one per category, around the origin 2016-01-01."""
    final = (
        # S stopped in March 2015: the same in both forms.
        _landmarks("S", [D(2014, 1, 1), D(2014, 7, 1)], 1, D(2015, 3, 1))
        # A completed in 2017, after its record had lapsed: hindsight sees six open landmarks.
        + _landmarks("A", HALF_YEARS, 2, D(2017, 5, 1))
        # B lapsed after the origin and never came back: censored at its verified date.
        + _landmarks("B", [D(2014, 1, 1), D(2014, 7, 1), D(2015, 1, 1)], 0, D(2015, 2, 1))
        # O ends on the same day with another event in the two forms.
        + _landmarks("O", [D(2014, 1, 1)], 1, D(2015, 5, 1))
    )
    final_outcomes = [
        _outcome("S", D(2015, 3, 1), event=1),
        _outcome("A", D(2017, 5, 1), event=2),
        _outcome("B", D(2015, 2, 1), censor_reason="unknown"),
        _outcome("R", D(2015, 1, 1), event=1, reversal=True),
        _outcome("O", D(2015, 5, 1), event=1),
    ]
    as_of = (
        _landmarks("S", [D(2014, 1, 1), D(2014, 7, 1)], 1, D(2015, 3, 1))
        # A's record had lapsed on the origin: censored when it was last verified.
        + _landmarks("A", HALF_YEARS[:1], 0, D(2013, 6, 1))
        # B was still under observation on the origin, with one more landmark.
        + _landmarks("B", [D(2014, 1, 1), D(2014, 7, 1), D(2015, 1, 1), D(2015, 7, 1)], 0, T)
        # R stopped before the origin; the reversal came later and hindsight drops the trial.
        + _landmarks("R", [D(2014, 6, 1)], 1, D(2015, 1, 1))
        + _landmarks("O", [D(2014, 1, 1)], 2, D(2015, 5, 1))
    )
    as_of_outcomes = [
        _outcome("S", D(2015, 3, 1), event=1),
        _outcome("A", D(2013, 6, 1), censor_reason="unknown"),
        _outcome("B", T),
        _outcome("R", D(2015, 1, 1), event=1),
        _outcome("O", D(2015, 5, 1), event=2),
    ]
    folder = tmp_path / "training" / "origin_2016-01-01"
    return {
        "final_landmarks": _write(tmp_path / "landmarks.parquet", final, LANDMARK_COLUMNS),
        "final_outcomes": _write(tmp_path / "outcomes.parquet", final_outcomes, OUTCOME_COLUMNS),
        "as_of_landmarks": _write(folder / "landmarks.parquet", as_of, LANDMARK_COLUMNS),
        "as_of_outcomes": _write(folder / "outcomes.parquet", as_of_outcomes, OUTCOME_COLUMNS),
    }


def _table(files: dict[str, Path]) -> dg.LabelTable:
    return dg.label_table(
        files["final_landmarks"], files["final_outcomes"], files["as_of_landmarks"],
        files["as_of_outcomes"], T,
    )  # fmt: skip


def test_every_training_row_is_put_in_one_category(hand_cohort: dict[str, Path]) -> None:
    table = _table(hand_cohort)
    by_trial = {
        trial: sorted(set(table.category[table.trial_id == trial].tolist())) for trial in "SABRO"
    }
    assert by_trial == {
        "S": [dg.SAME],
        "A": [dg.LAPSED_AS_OF_ORIGIN],
        "B": [dg.LAPSED_LATER],
        "R": [dg.REVERSAL_LATER],
        "O": [dg.OTHER],
    }
    assert dg.category_counts(table) == {
        dg.SAME: {"rows": 2, "trials": 1, "rows_as_of": 2, "rows_hindsight": 2},
        # One landmark in the as-of cohort, six in hindsight: five exist only there.
        dg.LAPSED_AS_OF_ORIGIN: {"rows": 6, "trials": 1, "rows_as_of": 1, "rows_hindsight": 6},
        dg.LAPSED_LATER: {"rows": 4, "trials": 1, "rows_as_of": 4, "rows_hindsight": 3},
        dg.REVERSAL_LATER: {"rows": 1, "trials": 1, "rows_as_of": 1, "rows_hindsight": 0},
        dg.OTHER: {"rows": 1, "trials": 1, "rows_as_of": 1, "rows_hindsight": 1},
    }
    assert len(table) == 14
    assert set(dg.CATEGORIES) == set(table.category.tolist())


def test_the_hindsight_form_is_cut_off_at_the_origin(hand_cohort: dict[str, Path]) -> None:
    """A completed in 2017: on the origin that is not known, in either form. Hindsight
    observes the trial up to the origin; the as-of cohort stopped in June 2013."""
    table = _table(hand_cohort)
    a = table.trial_id == "A"
    assert set(table.hindsight_event[a].tolist()) == {0}
    assert set(table.hindsight_date[a].astype(str).tolist()) == {"2016-01-01"}
    first = a & table.has_as_of
    assert table.as_of_date[first].astype(str).tolist() == ["2013-06-01"]
    years = dg.followup_years(table, dg.LAPSED_AS_OF_ORIGIN)
    assert years["as_of"] == pytest.approx(151 / 365.25)  # 1 January to 1 June 2013
    days = sum((T - day).days for day in HALF_YEARS)
    assert years["hindsight"] == pytest.approx(days / 365.25)


def test_a_mix_takes_each_row_in_the_form_its_category_asks_for(
    hand_cohort: dict[str, Path],
) -> None:
    table = _table(hand_cohort)
    time, event, stratum = table.mix(())
    assert len(time) == 12  # the hindsight rows: S 2, A 6, B 3, O 1
    assert sorted(event.tolist()) == [0] * 9 + [1, 1, 1]
    assert set(stratum.tolist()) == {"OTHER"}
    time, event, _ = table.mix(dg.CATEGORIES)
    assert len(time) == 9  # the as-of rows: S 2, A 1, B 4, R 1, O 1
    assert sorted(event.tolist()) == [0] * 5 + [1, 1, 1, 2]
    # Only A in its as-of form: its five later landmarks leave, and one row is cut short.
    time, event, _ = table.mix((dg.LAPSED_AS_OF_ORIGIN,))
    assert len(time) == 12 - 5
    assert 151.0 in time.tolist()
    # Only B in its as-of form: one more landmark, all four observed up to the origin.
    time, _, _ = table.mix((dg.LAPSED_LATER,))
    assert len(time) == 12 + 1
    assert (T - D(2015, 7, 1)).days in time.tolist()


def test_the_two_plain_mixes_are_the_two_cohorts(hand_cohort: dict[str, Path]) -> None:
    """All as-of is the as-of file itself, row for row; all hindsight is the final cohort's
    rows before the origin, cut off at the origin."""
    table = _table(hand_cohort)
    rows, time, event = training_rows(
        load_landmark_rows(hand_cohort["as_of_landmarks"]), np.datetime64(T, "D")
    )
    mixed_time, mixed_event, _ = table.mix(dg.CATEGORIES)
    assert sorted(zip(mixed_time.tolist(), mixed_event.tolist(), strict=True)) == sorted(
        zip(time.tolist(), event.tolist(), strict=True)
    )
    assert len(rows) == len(mixed_time)


def test_what_became_of_the_trials_censored_on_the_origin(hand_cohort: dict[str, Path]) -> None:
    fates = dg.later_fate(hand_cohort["final_outcomes"], hand_cohort["as_of_outcomes"], T)
    assert {(f["state"], f["fate"]): f["trials"] for f in fates} == {
        ("lapsed on the origin", "completed"): 1,  # A
        ("open on the origin", "lapsed at the data cutoff"): 1,  # B
    }
    completed = next(f for f in fates if f["fate"] == "completed")
    assert completed["share"] == 1.0
    assert completed["median_days_after_origin"] == (D(2017, 5, 1) - T).days


def test_an_event_dated_on_the_origin_is_not_known_in_hindsight(tmp_path: Path) -> None:
    """ADR 0015: what is dated on the origin is not known on the origin. A trial that
    stopped on the origin itself is open in the hindsight form, as it is in the cohort built
    as of the origin, so the two forms of its rows are the same."""
    final = _landmarks("E", [D(2015, 1, 1), D(2015, 7, 1)], 1, T)
    as_of = _landmarks("E", [D(2015, 1, 1), D(2015, 7, 1)], 0, T)
    folder = tmp_path / "training" / "origin_2016-01-01"
    table = dg.label_table(
        _write(tmp_path / "landmarks.parquet", final, LANDMARK_COLUMNS),
        _write(tmp_path / "outcomes.parquet", [_outcome("E", T, event=1)], OUTCOME_COLUMNS),
        _write(folder / "landmarks.parquet", as_of, LANDMARK_COLUMNS),
        _write(folder / "outcomes.parquet", [_outcome("E", T)], OUTCOME_COLUMNS),
        T,
    )
    assert table.hindsight_event.tolist() == [0, 0]
    assert table.hindsight_date.astype(str).tolist() == ["2016-01-01", "2016-01-01"]
    assert table.category.tolist() == [dg.SAME, dg.SAME]


def test_a_missing_cohort_file_names_the_command(tmp_path: Path) -> None:
    missing = tmp_path / "nothing.parquet"
    with pytest.raises(RefusedError, match=r"trialpulse\.cohort\.build"):
        dg.label_table(missing, missing, missing, missing, T)


# End to end on a simulated cohort ---------------------------------------------------------------


def _simulated(root: Path, cfg: ProjectConfig, n_trials: int = 3000, seed: int = 5) -> Path:
    """A final cohort and the cohort as of each development origin. The as-of cohort is the
    final one cut off at the origin, except that a share of the trials still open on the
    origin count as lapsed there: censored a year earlier, later landmarks dropped."""
    rng = np.random.default_rng(seed)
    classes = np.array(["INDUSTRY", "OTHER", "NIH"])
    final: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    for i in range(n_trials):
        trial = f"NCT{i:08d}"
        # One class in forty trials is FED: too few evaluation rows for a censoring curve
        # of its own, so the harness pools it.
        stratum = "FED" if i % 40 == 7 else str(classes[i % 3])
        t0 = D(2010, 1, 1) + dt.timedelta(days=int(rng.integers(0, 2900)))
        rate = {"INDUSTRY": 0.30, "OTHER": 0.12, "NIH": 0.20, "FED": 0.20}[stratum]
        days = int(rng.exponential(900)) + 30
        event = 1 if rng.random() < rate else 2
        reason = "cutoff"
        # A third of the trials of class OTHER lapse for good before they end: censored in
        # the final cohort too, so the censoring weights differ by class in the evaluation.
        if stratum == "OTHER" and rng.random() < 0.33:
            days, event, reason = max(30, int(days * rng.uniform(0.2, 0.9))), 0, "unknown"
        end = t0 + dt.timedelta(days=days)
        outcomes.append(_outcome(trial, end, event=event, stratum=stratum, censor_reason=reason))
        for k in range(7):
            landmark = (pd.Timestamp(t0) + pd.DateOffset(months=6 * k)).date()
            if landmark >= end:
                break
            final.append({"trial_id": trial, "landmark_index": k, "landmark_date": landmark,
                          "event": event, "event_date": end, "stratum": stratum})  # fmt: skip
    _write(root / "landmarks.parquet", final, LANDMARK_COLUMNS)
    _write(root / "outcomes.parquet", outcomes, OUTCOME_COLUMNS)
    for origin in (o.date for o in cfg.walk_forward.origins if o.role == "dev"):
        lapsed = {
            o["trial_id"]: o["event_date"]
            for o in outcomes
            if o["event_date"] >= origin and o["stratum"] == "OTHER" and rng.random() < 0.5
        }
        cut = origin - dt.timedelta(days=365)
        rows, trial_rows = [], []
        for row in final:
            if row["landmark_date"] >= origin:
                continue
            known = row["event_date"] < origin
            end = row["event_date"] if known else origin
            if row["trial_id"] in lapsed:
                if row["landmark_date"] >= cut:
                    continue
                end = cut
            rows.append({**row, "event": row["event"] if known else 0, "event_date": end})
        for o in outcomes:
            known = o["event_date"] < origin
            if o["trial_id"] in lapsed:
                trial_rows.append(_outcome(o["trial_id"], cut, censor_reason="unknown"))
            else:
                trial_rows.append(
                    _outcome(o["trial_id"], o["event_date"] if known else origin,
                             event=o["event"] if known else 0)
                )  # fmt: skip
        folder = training_path(root / "training", origin).parent
        _write(folder / "landmarks.parquet", rows, LANDMARK_COLUMNS)
        _write(folder / "outcomes.parquet", trial_rows, OUTCOME_COLUMNS)
    return root


def test_the_command_writes_the_diagnosis_for_the_development_origins(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], tracked_runs: list[Any]
) -> None:
    cfg = load_project_config()
    root = _simulated(tmp_path / "cohort", cfg)
    doc, out = tmp_path / "diagnosis.md", tmp_path / "m0.json"
    args = ["--input", str(root / "landmarks.parquet"), "--training-dir", str(root / "training"),
            "--doc", str(doc), "--out", str(out), "--resamples", "20"]  # fmt: skip
    assert dg.main([*args, "--no-track"]) == 0
    assert "tracking" not in json.loads(out.read_text(encoding="utf-8"))
    assert tracked_runs == []
    assert dg.main(args) == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    results = saved["origins"]
    # The run is logged like a model run, with both files and one slope per label set.
    (logged,) = tracked_runs
    assert saved["tracking"]["run_name"] == logged["name"] == "m0-calibration-diagnosis"
    assert logged["artifacts"] == [str(out), str(doc)]
    assert logged["tags"] == {"step": "9", "kind": "diagnosis", "roles": "dev"}
    assert logged["local"] is False
    assert logged["metrics"] == dg.run_metrics(results)
    dev = [o.date.isoformat() for o in cfg.walk_forward.origins if o.role == "dev"]
    assert [r["origin"] for r in results] == dev  # the locked origins are never read
    text = doc.read_text(encoding="utf-8")
    assert "Research demo. Not medical advice. Not for patient decision-making." in text
    assert "ClinicalTrials.gov" in text
    assert "Data as of" in text
    assert "—" not in text
    for origin in dev:
        assert f"## Origin {origin}" in text
    assert "2018-01-01" not in text
    # The table that follows trials past the development period says what it is (Section 10).
    assert text.count("Descriptive only") + text.count("descriptive only") >= len(dev) + 1
    first = results[0]
    # The simulation only made trials lapse on the origin; nothing lapses later.
    assert first["categories"][dg.LAPSED_AS_OF_ORIGIN]["rows"] > 100
    assert first["categories"][dg.LAPSED_LATER]["rows"] == 0
    assert first["categories"][dg.OTHER]["rows"] == 0
    assert (
        first["followup_years"][dg.LAPSED_AS_OF_ORIGIN]["as_of"]
        < (first["followup_years"][dg.LAPSED_AS_OF_ORIGIN]["hindsight"])
    )
    # The all-as-of mix is M0 as the walk-forward harness fits it on the as-of file.
    origin = dt.date.fromisoformat(dev[0])
    train, time, event = training_rows(
        load_landmark_rows(training_path(root / "training", origin)), np.datetime64(origin, "D")
    )
    final = load_landmark_rows(root / "landmarks.parquet")
    ev, ev_time, ev_event = evaluation_rows(
        final, np.datetime64(origin, "D"), cfg.walk_forward.eval_window_months
    )
    model = AalenJohansenModel().fit(time, event, train.features)
    months = max(cfg.horizons_months)
    h = horizon_days(ev.landmark_date, months)
    classes = np.asarray(ev.features["stratum"]).astype(str)
    groups = censoring_groups(classes, cfg.evaluation.censoring_min_rows)
    # FED is too small among the evaluation rows for a curve of its own: the harness pools
    # it, and so must the diagnosis.
    assert first["group_of_class"] == {
        "FED": "POOLED", "INDUSTRY": "INDUSTRY", "NIH": "NIH", "OTHER": "OTHER",
    }  # fmt: skip
    assert set(first["observed_by_group"][str(months)]) == {"INDUSTRY", "NIH", "OTHER", "POOLED"}
    as_of_score = model.predict_cif(h, ev.features)
    slope, _ = calibration_slope_intercept(ev_time, ev_event, as_of_score, h, groups)
    reported = first["horizons"][str(months)]
    assert reported[dg.AS_OF]["calibration_slope"] == pytest.approx(slope)
    # Every other column of the report, recomputed here from the files. The hindsight fit:
    table = dg.label_table(
        root / "landmarks.parquet", root / "outcomes.parquet",
        training_path(root / "training", origin),
        training_path(root / "training", origin).parent / "outcomes.parquet", origin,
    )  # fmt: skip
    h_time, h_event, h_stratum = table.mix(())
    hindsight_score = (
        AalenJohansenModel()
        .fit(h_time, h_event, {"stratum": h_stratum})
        .predict_cif(h, ev.features)
    )
    hindsight = reported[dg.HINDSIGHT]
    grouped, _ = calibration_slope_intercept(ev_time, ev_event, hindsight_score, h, groups)
    single, _ = calibration_slope_intercept(ev_time, ev_event, hindsight_score, h)
    assert hindsight["calibration_slope"] == pytest.approx(grouped)
    assert hindsight["calibration_slope_single_curve"] == pytest.approx(single)
    assert abs(grouped - single) > 1e-4  # the two are different numbers on these rows
    assert hindsight["auc"] == pytest.approx(
        ipcw_auc(ev_time, ev_event, hindsight_score, h, groups)
    )
    assert abs(hindsight["auc"] - ipcw_auc(ev_time, ev_event, hindsight_score, h)) > 1e-6
    # Predicted by class: the mean prediction of the rows of that class, not of all rows.
    predicted = first["predicted_by_class"][str(months)]
    for label in ("INDUSTRY", "OTHER", "NIH", "FED"):
        member = classes == label
        assert predicted[dg.HINDSIGHT][label] == pytest.approx(hindsight_score[member].mean())
        assert predicted[dg.AS_OF][label] == pytest.approx(as_of_score[member].mean())
    assert predicted[dg.HINDSIGHT]["INDUSTRY"] > predicted[dg.HINDSIGHT]["OTHER"] + 0.02
    # Observed by group: the IPCW rate, which is not the plain share of early stops when a
    # group has rows censored before the horizon.
    y, w = horizon_labels_and_weights(ev_time, ev_event, h, groups)
    for group, values in first["observed_by_group"][str(months)].items():
        member = groups == group
        assert values["rows"] == int(member.sum())
        assert values["observed"] == pytest.approx((w[member] * y[member]).sum() / w[member].sum())
        assert values["censored_before_horizon"] == pytest.approx((w[member] == 0).mean())
    other = first["observed_by_group"][str(months)]["OTHER"]
    assert other["censored_before_horizon"] > 0.05
    assert other["observed"] > y[groups == "OTHER"].mean() + 1e-3
    # Which class carries the move: the hindsight predictions with the rows of one class
    # taking the as-of prediction. Only OTHER has lapsed records here, so only it moves.
    one_class = first["slope_with_one_class_from_as_of"][str(months)]
    for label in ("INDUSTRY", "OTHER"):
        mixed = np.where(classes == label, as_of_score, hindsight_score)
        expected, _ = calibration_slope_intercept(ev_time, ev_event, mixed, h, groups)
        assert one_class[label] == pytest.approx(expected)
    assert abs(one_class["OTHER"] - grouped) > 10 * abs(one_class["INDUSTRY"] - grouped)
    assert one_class["OTHER"] == pytest.approx(reported[dg.AS_OF]["calibration_slope"], abs=0.02)
    # The summary table prints each slope with its difference from hindsight, signed.
    move_text = f"{slope:.3f} ({slope - grouped:+.3f})"
    assert abs(slope - grouped) > 0.005
    assert f"| {dg.AS_OF} | " in text
    assert move_text in text.split("## Summary")[1].split("## Origin")[0]
    assert reported[dg.AS_OF]["training_rows"] == len(train)
    # Swapping in the one category that differs gives the as-of labels exactly.
    one = reported["hindsight, lapsed as of the origin from as-of"]
    assert one["calibration_slope"] == pytest.approx(reported[dg.AS_OF]["calibration_slope"])
    assert reported[dg.HINDSIGHT]["calibration_slope"] != pytest.approx(slope)
    move = first["slope_move"][str(months)]
    assert move["estimate"] == pytest.approx(
        reported[dg.AS_OF]["calibration_slope"] - reported[dg.HINDSIGHT]["calibration_slope"]
    )
    assert move["ci_low"] <= move["estimate"] <= move["ci_high"]
    # A second run changes nothing.
    assert dg.main(args) == 0
    assert "up to date" in capsys.readouterr().out


def test_the_metrics_of_the_logged_run_name_each_label_set() -> None:
    results = [
        {
            "origin": "2016-01-01",
            "horizons": {
                "24": {
                    name: {"calibration_slope": 1.0 + i / 10}
                    for i, (name, _) in enumerate(dg.MIXES)
                }
            },
            "slope_move": {"24": {"estimate": 0.4}},
        }
    ]
    assert dg.run_metrics(results) == {
        "calibration_slope_24m_2016_hindsight": 1.0,
        "calibration_slope_24m_2016_lapsed_on_origin_swapped": 1.1,
        "calibration_slope_24m_2016_lapsed_later_swapped": 1.2,
        "calibration_slope_24m_2016_both_swapped": 1.3,
        "calibration_slope_24m_2016_as_of": 1.4,
        "slope_move_24m_2016": 0.4,
    }
    assert len(dg.MIX_KEYS) == len(dg.MIXES)


def test_a_tracking_failure_keeps_the_diagnosis_and_fails_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trialpulse import tracking

    def refused(*args: object, **kwargs: object) -> dict[str, str]:
        raise tracking.TrackingError("the server answered 404")

    cfg = load_project_config()
    root = _simulated(tmp_path / "cohort", cfg, n_trials=1200)
    monkeypatch.setattr(tracking, "log_run", refused)
    doc, out = tmp_path / "diagnosis.md", tmp_path / "m0.json"
    code = dg.main(["--input", str(root / "landmarks.parquet"), "--training-dir",
                    str(root / "training"), "--doc", str(doc), "--out", str(out),
                    "--resamples", "5"])  # fmt: skip
    assert code == tracking.FAILED_EXIT_CODE
    assert "not logged to MLflow (the server answered 404)" in capsys.readouterr().err
    assert "## Summary" in doc.read_text(encoding="utf-8")
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert len(saved["origins"]) == 2
    assert "tracking" not in saved
