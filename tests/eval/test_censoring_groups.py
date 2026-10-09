"""IPCW censoring weights estimated within sponsor classes (ADR 0017).

One censoring curve for all rows assumes that censoring does not depend on anything that
also predicts the outcome. Censoring under the UNKNOWN rule depends on the sponsor class,
and so does the outcome. These tests pin the weights on a constructed pattern, show the
bias of the single curve on synthetic data with a known answer, and check that the
walk-forward harness uses the grouped weights and reports the single curve beside them.
"""

import datetime as dt

import numpy as np
import pytest

from trialpulse.config import ProjectConfig, load_project_config
from trialpulse.dates import add_months
from trialpulse.eval.bootstrap import cluster_bootstrap
from trialpulse.eval.ipcw import (
    POOLED_GROUP,
    censoring_groups,
    censoring_survival,
    horizon_labels_and_weights,
)
from trialpulse.eval.metrics import (
    calibration_slope_intercept,
    ipcw_auc,
    ipcw_brier,
    lift_at,
)
from trialpulse.eval.walkforward import (
    MODELS,
    LandmarkRows,
    evaluate_predictions,
    evaluation_rows,
    horizon_days,
    run,
    training_rows,
)

# Group "a" is the constructed pattern of test_metrics.py, horizon 5 days: a stop at 2 (case),
# censored at 3 (unknown), completed at 1, censored at 8, stopped at 6. Its censoring curve
# is G(3) = 2/3, so its weights are 1, 0, 1, 1.5, 1.5. Group "b" has the same times and no
# censoring: every weight is 1.
TIME = np.array([2.0, 3.0, 1.0, 8.0, 6.0, 2.0, 3.0, 1.0, 8.0, 6.0])
EVENT = np.array([1, 0, 2, 0, 1, 1, 2, 2, 1, 1])
GROUPS = np.array(["a"] * 5 + ["b"] * 5)
H = 5.0


@pytest.fixture
def cfg() -> ProjectConfig:
    return load_project_config()


def test_each_row_is_weighted_by_the_curve_of_its_own_group() -> None:
    y, w = horizon_labels_and_weights(TIME, EVENT, H, GROUPS)
    assert y.tolist() == [1, 0, 0, 0, 0, 1, 0, 0, 0, 0]
    assert w == pytest.approx([1.0, 0.0, 1.0, 1.5, 1.5, 1.0, 1.0, 1.0, 1.0, 1.0])
    # One curve for all ten rows: six rows reach day 3, and the completion on day 3 in group
    # b comes before the censoring on day 3 (events first), so the censoring is 1 of the 5
    # rows still at risk. G is 4/5 from day 3 to the horizon, and the rows of both groups
    # that pass the horizon share the weight 5/4. The completion is weighted by G just
    # before day 3. The ten weights add up to the ten rows.
    _, single = horizon_labels_and_weights(TIME, EVENT, H)
    assert single == pytest.approx([1, 0, 1, 1.25, 1.25, 1, 1, 1, 1.25, 1.25])
    assert single.sum() == pytest.approx(len(TIME))


def test_the_order_of_the_rows_does_not_matter() -> None:
    order = np.random.default_rng(3).permutation(len(TIME))
    _, w = horizon_labels_and_weights(TIME, EVENT, H, GROUPS)
    _, shuffled = horizon_labels_and_weights(TIME[order], EVENT[order], H, GROUPS[order])
    assert shuffled == pytest.approx(w[order])


def test_one_group_is_the_single_curve() -> None:
    rng = np.random.default_rng(5)
    time = rng.exponential(400, 500)
    event = rng.integers(0, 3, 500)
    score = rng.uniform(size=500)
    one = np.full(500, "INDUSTRY")
    _, single = horizon_labels_and_weights(time, event, 365.0)
    _, grouped = horizon_labels_and_weights(time, event, 365.0, one)
    assert grouped == pytest.approx(single)
    assert ipcw_auc(time, event, score, 365.0, one) == pytest.approx(
        ipcw_auc(time, event, score, 365.0)
    )
    assert ipcw_brier(time, event, score, 365.0, one) == pytest.approx(
        ipcw_brier(time, event, score, 365.0)
    )


def test_small_classes_share_one_pooled_curve() -> None:
    classes = np.array(["OTHER"] * 300 + ["INDUSTRY"] * 200 + ["NIH"] * 199 + ["INDIV"] * 10)
    groups = censoring_groups(classes, min_rows=200)
    names, counts = np.unique(groups, return_counts=True)
    assert dict(zip(names.tolist(), counts.tolist(), strict=True)) == {
        "INDUSTRY": 200,  # exactly the minimum keeps its own curve
        "OTHER": 300,
        POOLED_GROUP: 209,  # NIH (one row short) and INDIV together
    }
    assert np.array_equal(censoring_groups(classes, min_rows=1), classes)
    assert set(censoring_groups(classes, min_rows=10_000).tolist()) == {POOLED_GROUP}
    assert censoring_groups(np.array([]), min_rows=5).size == 0


def _two_classes(n: int = 60_000, seed: int = 11) -> tuple[np.ndarray, ...]:
    """Class A stops early often and is censored heavily; class B rarely stops and is never
    censored. Within a class, censoring is independent of the outcome."""
    rng = np.random.default_rng(seed)
    in_a = np.arange(2 * n) < n
    stop = np.where(in_a, rng.exponential(500, 2 * n), rng.exponential(5000, 2 * n))
    censor = np.where(in_a, rng.exponential(400, 2 * n), np.inf)
    time = np.minimum(stop, censor)
    event = np.where(stop <= censor, 1, 0)
    return time, event, np.where(in_a, "A", "B"), stop


def test_the_single_curve_is_biased_when_censoring_depends_on_the_class() -> None:
    """The IPCW estimate of the share stopped early by the horizon, sum(w * y) / n, against
    the truth known from the uncensored stop times. Curves by class recover it; one curve
    for both classes gives the heavily censored class too little weight."""
    time, event, classes, stop = _two_classes()
    truth = float(np.mean(stop <= 365.0))
    assert truth == pytest.approx((1 - np.exp(-365 / 500) + 1 - np.exp(-365 / 5000)) / 2, abs=0.005)

    y, by_class = horizon_labels_and_weights(time, event, 365.0, classes)
    _, single = horizon_labels_and_weights(time, event, 365.0)
    assert float(np.sum(by_class * y) / len(y)) == pytest.approx(truth, abs=0.006)
    assert float(np.sum(single * y) / len(y)) < truth - 0.03
    # With the right weights the weights themselves add up to the number of rows.
    assert float(np.sum(by_class) / len(y)) == pytest.approx(1.0, abs=0.01)


def test_every_ipcw_metric_takes_the_groups() -> None:
    time, event, classes, _ = _two_classes(n=4_000)
    score = np.where(classes == "A", 0.5, 0.07) + np.random.default_rng(1).uniform(
        0, 0.01, len(time)
    )
    for metric in (ipcw_auc, ipcw_brier):
        assert metric(time, event, score, 365.0, classes) != pytest.approx(
            metric(time, event, score, 365.0), abs=1e-4
        )
    assert lift_at(time, event, score, 365.0, 0.1, classes) != pytest.approx(
        lift_at(time, event, score, 365.0, 0.1), abs=1e-4
    )
    grouped = calibration_slope_intercept(time, event, score, 365.0, classes)
    assert grouped != pytest.approx(
        calibration_slope_intercept(time, event, score, 365.0), abs=1e-4
    )
    # The scores are the true risks of the two classes, so by-class weights find them
    # calibrated in the large (an intercept near 0). The single curve does not.
    single = calibration_slope_intercept(time, event, score, 365.0)
    assert grouped[1] == pytest.approx(0.0, abs=0.12)
    assert abs(single[1]) > abs(grouped[1]) + 0.05


def _slice(cfg: ProjectConfig, min_rows: int) -> tuple[ProjectConfig, dict]:
    time, event, classes, _ = _two_classes(n=1_500, seed=4)
    score = np.where(classes == "A", 0.5, 0.07) + np.random.default_rng(2).uniform(
        0, 0.2, len(time)
    )
    horizon = np.full(len(time), 365.0)
    trials = np.arange(len(time))
    evaluation = cfg.evaluation.model_copy(update={"censoring_min_rows": min_rows})
    chosen = cfg.model_copy(update={"evaluation": evaluation})
    groups = censoring_groups(classes, min_rows)
    result = evaluate_predictions(time, event, score, horizon, trials, groups, chosen, 40, "slice")
    result["_inputs"] = (time, event, score, horizon, classes)
    return chosen, result


def test_the_harness_reports_by_class_weights_first_and_the_single_curve_beside(
    cfg: ProjectConfig,
) -> None:
    _, result = _slice(cfg, min_rows=200)
    time, event, score, horizon, classes = result["_inputs"]
    assert result["censoring_groups"] == {"A": 1500, "B": 1500}
    assert result["auc"]["estimate"] == pytest.approx(
        ipcw_auc(time, event, score, horizon, classes)
    )
    assert result["brier"]["estimate"] == pytest.approx(
        ipcw_brier(time, event, score, horizon, classes)
    )
    single = result["single_censoring_curve"]
    assert set(single) == {"auc", "brier", "lift", "calibration_slope", "calibration_intercept"}
    assert single["auc"] == pytest.approx(ipcw_auc(time, event, score, horizon))
    assert single["brier"] == pytest.approx(ipcw_brier(time, event, score, horizon))
    assert single["auc"] != pytest.approx(result["auc"]["estimate"], abs=1e-4)
    assert result["calibration_intercept"] != pytest.approx(
        single["calibration_intercept"], abs=0.05
    )
    top = cfg.evaluation.lift_top_fraction
    assert result["lift"]["estimate"] == pytest.approx(
        lift_at(time, event, score, horizon, top, classes)
    )
    assert single["lift"] == pytest.approx(lift_at(time, event, score, horizon, top))
    assert single["lift"] != pytest.approx(result["lift"]["estimate"], abs=1e-4)
    by_class = calibration_slope_intercept(time, event, score, horizon, classes)
    assert (result["calibration_slope"], result["calibration_intercept"]) == pytest.approx(by_class)
    # The interval comes from resamples that each fit their own curves within the groups.
    assert result["auc"]["ci_low"] < result["auc"]["estimate"] < result["auc"]["ci_high"]
    assert result["auc"]["valid_resamples"] == 40


def test_classes_too_small_for_a_curve_share_the_pooled_one(cfg: ProjectConfig) -> None:
    _, result = _slice(cfg, min_rows=5_000)
    assert result["censoring_groups"] == {POOLED_GROUP: 3000}
    single = result["single_censoring_curve"]
    assert result["auc"]["estimate"] == pytest.approx(single["auc"])  # one pooled group: one curve
    assert result["brier"]["estimate"] == pytest.approx(single["brier"])


def test_a_censoring_on_the_horizon_day_counts_for_the_rows_that_pass_the_horizon() -> None:
    """A stop on day 3, a censoring on day 5, a stop on day 8, horizon 5. The censored row is
    unknown at the horizon. Of the two rows at risk on day 5 one is censored, so G(5) = 1/2
    and the row that passes the horizon stands for two: its weight is 1 / G(H), with the drop
    on the horizon day applied. With G just before the horizon it would be 1."""
    time, event = np.array([3.0, 5.0, 8.0]), np.array([1, 0, 1])
    _, w = horizon_labels_and_weights(time, event, 5.0)
    assert w == pytest.approx([1.0, 0.0, 2.0])
    # Per-row horizons: day 5 is the horizon of the third row and after that of the fourth.
    time, event = np.array([3.0, 5.0, 8.0, 9.0]), np.array([1, 0, 1, 1])
    _, w = horizon_labels_and_weights(time, event, np.array([5.0, 5.0, 5.0, 4.0]))
    assert w == pytest.approx([1.0, 0.0, 1.5, 1.0])  # G(5) = 2/3 of three at risk, G(4) = 1
    # The same pattern in two groups, each with its own curve.
    groups = np.array(["a", "a", "a", "b", "b", "b"])
    _, w = horizon_labels_and_weights(
        np.tile([3.0, 5.0, 8.0], 2), np.tile([1, 0, 1], 2), 5.0, groups
    )
    assert w == pytest.approx([1.0, 0.0, 2.0, 1.0, 0.0, 2.0])


def test_group_labels_are_read_as_text_and_must_match_the_rows() -> None:
    time, event = np.array([1.0, 2.0, 8.0, 8.0]), np.array([1, 1, 1, 1])
    # A missing label is a group of its own, not a row that silently loses its weight.
    _, w = horizon_labels_and_weights(time, event, 5.0, np.array([1.0, np.nan, 1.0, np.nan]))
    assert w == pytest.approx([1.0, 1.0, 1.0, 1.0])
    assert set(censoring_groups(np.array(["A", None, "A"], dtype=object), 2).tolist()) == {
        "A",
        POOLED_GROUP,
    }
    with pytest.raises(ValueError, match="3 group labels for 4 rows"):
        horizon_labels_and_weights(time, event, 5.0, np.array(["a", "b", "a"]))


def test_the_groups_are_fixed_before_the_bootstrap_and_each_resample_refits_its_curves(
    cfg: ProjectConfig,
) -> None:
    """A class just above the minimum keeps its own curve in every resample, even when a
    resample draws fewer of its rows. The interval must be the one `cluster_bootstrap` gives
    with the groups decided once, before resampling."""
    rng = np.random.default_rng(8)
    # C is just above the minimum of 200 and heavily censored; D is below it and never censored.
    sizes = {"A": 1200, "B": 1200, "C": 208, "D": 152}
    classes = np.concatenate([np.full(n, name) for name, n in sizes.items()])
    n = len(classes)
    stop = rng.exponential(np.select([classes == "A", classes == "B"], [500, 3000], 900), n)
    censor = np.where(classes == "C", rng.exponential(250, n), np.inf)
    censor = np.where(classes == "A", rng.exponential(1500, n), censor)
    time, event = np.minimum(stop, censor), (stop <= censor).astype(np.int64)
    score = np.select([classes == "A", classes == "B"], [0.5, 0.1], 0.3) + rng.uniform(0, 0.2, n)
    horizon = np.full(n, 365.0)
    trials = np.repeat(np.arange(n // 2), 2)
    rng.shuffle(trials)  # a trial's two rows may sit in different classes

    groups = censoring_groups(classes, cfg.evaluation.censoring_min_rows)
    result = evaluate_predictions(time, event, score, horizon, trials, groups, cfg, 60, "slice")
    assert result["censoring_groups"] == {"A": 1200, "B": 1200, "C": 208, POOLED_GROUP: 152}
    expected = cluster_bootstrap(
        trials,
        lambda idx: ipcw_auc(time[idx], event[idx], score[idx], horizon[idx], groups[idx]),
        60,
        cfg.seeds.default,
        cfg.evaluation.confidence_level,
    )
    assert result["auc"] == expected
    # Deciding the groups inside each resample would put C on D's pooled curve whenever fewer
    # than 200 of its rows are drawn, and give another interval.
    regrouped = cluster_bootstrap(
        trials,
        lambda idx: ipcw_auc(
            time[idx],
            event[idx],
            score[idx],
            horizon[idx],
            censoring_groups(classes[idx], cfg.evaluation.censoring_min_rows),
        ),
        60,
        cfg.seeds.default,
        cfg.evaluation.confidence_level,
    )
    assert (regrouped["ci_low"], regrouped["ci_high"]) != (expected["ci_low"], expected["ci_high"])


def _walk_forward_rows(n_trials: int = 4_000, seed: int = 12) -> LandmarkRows:
    """Trials first posted 2013 to 2016 with two landmarks each. The "slow" strata stop less
    often and the censored ones are censored heavily, so the strata need different curves and
    are not spread alike over the two landmark indices. The two rare strata are large enough
    for a curve of their own on all the evaluation rows of the 2016 origin (111 and 112 rows
    against a minimum of 80), and too small for one at a single landmark index (67 and 55,
    44 and 57)."""
    rng = np.random.default_rng(seed)
    t0 = np.datetime64("2013-01-01") + rng.integers(0, 4 * 365, n_trials).astype("timedelta64[D]")
    names = ["fast", "slow", "rare_fast", "rare_slow"]
    stratum = rng.choice(names, n_trials, p=[0.44, 0.44, 0.06, 0.06])
    stop = rng.exponential(np.where(np.isin(stratum, ["fast", "rare_fast"]), 700, 2500))
    complete = rng.exponential(1500, n_trials)
    censor = np.select(
        [stratum == "slow", stratum == "rare_fast"],
        [rng.exponential(600, n_trials), rng.exponential(900, n_trials)],
        5000.0,
    )
    days = np.floor(np.minimum.reduce([stop, complete, censor])).astype(np.int64) + 1
    event = np.select([days > censor, stop < complete], [0, 1], default=2)
    end = t0 + days.astype("timedelta64[D]")
    parts: dict[str, list] = {k: [] for k in ("id", "k", "landmark", "event", "end", "stratum")}
    for i in range(n_trials):
        for k in range(2):
            landmark = add_months(np.array([t0[i]]), 6 * k)[0]
            if end[i] <= landmark:
                continue
            for key, value in zip(parts, (f"NCT{i:08d}", k, landmark, event[i], end[i], stratum[i]),
                                  strict=True):  # fmt: skip
                parts[key].append(value)
    return LandmarkRows(
        np.array(parts["id"]),
        np.array(parts["k"], dtype=np.int64),
        np.array(parts["landmark"], dtype="datetime64[D]"),
        np.array(parts["event"], dtype=np.int64),
        np.array(parts["end"], dtype="datetime64[D]"),
        {"stratum": np.array(parts["stratum"])},
    )


def test_the_walk_forward_run_decides_the_groups_once_per_origin(
    cfg: ProjectConfig,
) -> None:
    """The values a run writes, pooled and per landmark index, are the by-class metrics of
    exactly the rows of that slice, with the groups decided once on all the evaluation rows
    of the origin (ADR 0017, decision 2): a class keeps the same scheme at every landmark
    index, and the curves are fitted on the rows of the slice."""
    rows = _walk_forward_rows()
    origin = dt.date(2016, 1, 1)
    t = np.datetime64(origin, "D")

    def training(day: dt.date) -> LandmarkRows:
        before = rows.subset(rows.landmark_date < t)
        late = before.event_date >= t
        return LandmarkRows(
            before.trial_id,
            before.landmark_index,
            before.landmark_date,
            np.where(late, 0, before.event).astype(np.int64),
            np.where(late, t, before.event_date).astype("datetime64[D]"),
            before.features,
        )

    min_rows = 80
    evaluation = cfg.evaluation.model_copy(update={"censoring_min_rows": min_rows})
    chosen = cfg.model_copy(update={"evaluation": evaluation})
    result = run(chosen, "m0", "2016", lambda: rows, training, n_resamples=8)["origins"][0]

    train, train_time, train_event = training_rows(training(origin), t)
    ev, time, event = evaluation_rows(rows, t, chosen.walk_forward.eval_window_months)
    model = MODELS["m0"](chosen).fit_rows(train, train_time, train_event, origin)
    stratum = ev.features["stratum"]
    for months in chosen.horizons_months:
        horizon = horizon_days(ev.landmark_date, months)
        score = model.predict_cif(horizon, ev.features)
        written = result["horizons"][str(months)]
        slices = {"pooled": np.ones(len(ev), dtype=bool)}
        slices |= {str(k): ev.landmark_index == k for k in (0, 1)}
        for name, mask in slices.items():
            mine = written["pooled"] if name == "pooled" else written["by_landmark_index"][name]
            groups = censoring_groups(stratum, min_rows)[mask]
            names, counts = np.unique(groups, return_counts=True)
            assert mine["censoring_groups"] == dict(
                zip(names.tolist(), counts.tolist(), strict=True)
            )
            args = (time[mask], event[mask], score[mask], horizon[mask])
            assert mine["auc"]["estimate"] == pytest.approx(ipcw_auc(*args, groups)), (months, name)
            assert mine["brier"]["estimate"] == pytest.approx(ipcw_brier(*args, groups))
            assert mine["single_censoring_curve"]["auc"] == pytest.approx(ipcw_auc(*args))
            assert mine["auc"]["estimate"] != pytest.approx(ipcw_auc(*args), abs=1e-5)
    # The strata are not spread alike over the landmark indices, so classes taken from other
    # rows than the slice's own would give other groups and other weights.
    written = result["horizons"]["12"]
    by_index = written["by_landmark_index"]
    assert by_index["0"]["censoring_groups"] != by_index["1"]["censoring_groups"]
    # Each rare stratum reaches the minimum on all the rows of the origin (111 and 112 rows)
    # and not at one landmark index (67 and 55, 44 and 57). It keeps its own curve at each
    # index all the same; deciding the groups again on the slice would pool the two.
    own = {"fast", "slow", "rare_fast", "rare_slow"}
    assert set(written["pooled"]["censoring_groups"]) == own
    assert result["censoring_groups"] == written["pooled"]["censoring_groups"]
    for k in ("0", "1"):
        assert set(by_index[k]["censoring_groups"]) == own
        assert (
            max(by_index[k]["censoring_groups"][s] for s in ("rare_fast", "rare_slow")) < min_rows
        )
        mask = ev.landmark_index == int(k)
        horizon = horizon_days(ev.landmark_date, 12)
        score = model.predict_cif(horizon, ev.features)
        decided_on_the_slice = censoring_groups(stratum[mask], min_rows)
        assert set(decided_on_the_slice.tolist()) == {"fast", "slow", POOLED_GROUP}
        other = ipcw_auc(time[mask], event[mask], score[mask], horizon[mask], decided_on_the_slice)
        assert by_index[k]["auc"]["estimate"] != pytest.approx(other, abs=1e-6)


def test_at_a_tied_time_events_come_before_censorings() -> None:
    """A stop and a censoring on day 2, a completion on day 4, a stop on day 6, horizon 5.
    The row that stopped on day 2 was observed through day 2, so it is not at risk of the
    censoring on day 2: that censoring is 1 of 3 rows, G = 2/3, and the two rows known at
    the horizon after day 2 get the weight 3/2. Had the stopped row counted as at risk, G
    would be 3/4 and the weights 4/3: 3.67 rows of weight for 4 rows."""
    time, event = np.array([2.0, 2.0, 4.0, 6.0]), np.array([1, 0, 2, 1])
    assert censoring_survival(time, event).at(np.array([1.0, 2.0, 5.0])) == pytest.approx(
        [1.0, 2 / 3, 2 / 3]
    )
    _, w = horizon_labels_and_weights(time, event, 5.0)
    assert w == pytest.approx([1.0, 0.0, 1.5, 1.5])
    assert w.sum() == pytest.approx(4.0)
    # A day on which every row that reaches it ends with an event has no censoring risk.
    done = censoring_survival(np.array([1.0, 3.0, 3.0]), np.array([0, 1, 2]))
    assert done.at(np.array([1.0, 3.0, 9.0])) == pytest.approx([2 / 3, 2 / 3, 2 / 3])


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_the_weights_at_one_horizon_add_up_to_the_rows(seed: int) -> None:
    """With whole-day times and many ties, in one group and in several."""
    rng = np.random.default_rng(seed)
    n = 5_000
    stop, complete = rng.exponential(300, n), rng.exponential(400, n)
    censor = rng.exponential(250, n)
    time = np.ceil(np.minimum.reduce([stop, complete, censor]) / 7)  # weeks: heavy ties
    event = np.select([censor < np.minimum(stop, complete), stop < complete], [0, 1], default=2)
    groups = rng.choice(["a", "b", "c"], n)
    assert (np.unique(time[event == 0])[:, None] == np.unique(time[event != 0])).any()
    for horizon in (10.0, 26.0, 52.0):
        for labels in (None, groups):
            _, w = horizon_labels_and_weights(time, event, horizon, labels)
            assert w.sum() == pytest.approx(n, rel=1e-12), (horizon, labels is None)


def test_the_min_rows_of_the_configuration_is_what_adr_0017_says(cfg: ProjectConfig) -> None:
    assert cfg.evaluation.censoring_min_rows == 200


def test_evaluation_rows_without_a_sponsor_class_are_refused(cfg: ProjectConfig) -> None:
    rng = np.random.default_rng(0)
    n = 300
    landmark = np.datetime64("2016-03-01") + rng.integers(0, 200, n).astype("timedelta64[D]")
    rows = LandmarkRows(
        np.array([f"NCT{i:08d}" for i in range(n)]),
        np.zeros(n, dtype=np.int64),
        landmark,
        rng.integers(0, 3, n),
        landmark + rng.integers(30, 900, n).astype("timedelta64[D]"),
        {},
    )
    empty = rows.subset(np.zeros(n, dtype=bool))

    def training(origin: dt.date) -> LandmarkRows:
        return LandmarkRows(*[getattr(empty, f) for f in ("trial_id", "landmark_index",
                            "landmark_date", "event", "event_date")], {})  # fmt: skip

    with pytest.raises(ValueError, match=r'no "stratum" column.*ADR 0017'):
        run(cfg, "m0", "2016", lambda: rows, training, n_resamples=5)
