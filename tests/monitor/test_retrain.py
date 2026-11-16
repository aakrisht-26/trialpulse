"""The retrain's own logic (`monitor.retrain`, ADR 0080): the dated configuration, the
agreement of two models' risks, and the decision row. The fit runs on the laptop's data."""

import datetime as dt

import pandas as pd
import psycopg
import pytest

from trialpulse.config import load_project_config
from trialpulse.monitor import gate, retrain

CFG = load_project_config()


def test_the_dated_configuration_moves_the_cutoff_and_nothing_else() -> None:
    dated = retrain.as_of(CFG, dt.date(2026, 11, 13))
    assert dated.dataset.cutoff == dt.date(2026, 11, 13)
    assert dated.dataset.revision == CFG.dataset.revision
    assert dated.model_dump(exclude={"dataset"}) == CFG.model_dump(exclude={"dataset"})


def test_the_agreement_of_two_models_is_described_on_the_trials_both_score() -> None:
    champion = pd.DataFrame({"nct_id": ["A", "B", "C"], "risk_12m": [0.1, 0.2, 0.3],
                             "risk_24m": [0.2, 0.3, 0.4]})  # fmt: skip
    challenger = pd.DataFrame({"nct_id": ["A", "B", "D"], "risk_12m": [0.15, 0.25, 0.9],
                               "risk_24m": [0.3, 0.2, 0.9]})  # fmt: skip
    found = retrain.compare_scores(champion, challenger)
    assert found["trials_both_scored"] == 2
    assert found["spearman_12m"] == pytest.approx(1.0)
    assert found["spearman_24m"] == pytest.approx(-1.0)
    assert found["mean_risk_12m_challenger"] == pytest.approx(0.2)


def test_the_decision_row_is_written_to_promotions(scratch: str) -> None:
    dsn = scratch.replace("postgresql+psycopg://", "postgresql://", 1)
    decision = gate.decide(CFG.retraining, None, None, None, None)
    retrain.write_promotion(dsn, {
        "champion_version": "trialpulse-m4/1",
        "challenger_version": "trialpulse-m4-challenger-2026-11-13",
        "window_start": None, "window_end": None, "champion_auc_12m": None,
        "challenger_auc_12m": None, "challenger_calibration_slope": None,
        "promoted": decision.promoted, "reason": decision.reason, "mlflow_run_id": "abc",
    })  # fmt: skip
    with psycopg.connect(dsn) as con:
        rows = con.execute("SELECT promoted, reason FROM serving.monitoring_promotions").fetchall()
    assert rows == [(False, gate.NO_WINDOW)]
