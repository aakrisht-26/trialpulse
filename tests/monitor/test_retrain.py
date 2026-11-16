"""The retrain's own logic (`monitor.retrain`, ADR 0080): the dated configuration, the
agreement of two models' risks, and the decision row. The fit runs on the laptop's data."""

import datetime as dt
from pathlib import Path

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


def test_reusing_parity_5a_needs_a_5a_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trialpulse.cli import RefusedError
    from trialpulse.live import parity_5a

    monkeypatch.setattr(parity_5a, "OUT_DIR", tmp_path)
    with pytest.raises(RefusedError, match="no parity 5a run"):
        retrain.last_5a()
    (tmp_path / "warehouse.duckdb").write_bytes(b"")
    record = '{"day": "2026-11-09", "warehouse": {"live_versions": 5112}}'
    (tmp_path / "parity_5a.json").write_text(record, encoding="utf-8")
    day, warehouse, glob, counts = retrain.last_5a()
    assert warehouse == tmp_path / "warehouse.duckdb"
    assert day == dt.date(2026, 11, 9)
    assert counts == {"live_versions": 5112}
    assert glob.endswith("interventions/*.parquet")


def test_a_registered_challenger_fetches_as_the_registrys_version(tmp_path: Path) -> None:
    """The challenger's meta.json names no version, so once registered (a promotion by hand,
    docs/deploy.md Part 5) the daily job's fetch accepts it as the registry's version."""
    import json
    from types import SimpleNamespace

    from trialpulse.live import registry

    record = retrain.challenger_record(dt.date(2026, 11, 9), dt.date(2026, 11, 10))
    assert "version" not in record
    assert record["name"] == "trialpulse-m4-challenger-2026-11-09"

    class Client:  # the registry, with the challenger registered as version 2
        def get_model_version_by_alias(self, name: str, alias: str) -> SimpleNamespace:
            return SimpleNamespace(version="2", run_id="run-of-the-challenger")

    def download(uri: str, dst_path: str) -> str:
        (Path(dst_path) / registry.META).write_text(json.dumps({**record, "files": {}}), "utf-8")
        return dst_path

    registry.fetch("champion", tmp_path, Client(), download)
    meta = json.loads((tmp_path / registry.META).read_text(encoding="utf-8"))
    assert meta["version"] == f"{registry.MODEL_NAME}/2"


def test_the_window_starts_after_the_champions_own_training_cutoff() -> None:
    assert retrain.champion_training_cutoff({"data_cutoff": "2027-11-09"}, CFG) == dt.date(
        2027, 11, 9)  # fmt: skip
    assert retrain.champion_training_cutoff({}, CFG) == CFG.dataset.cutoff  # the first champion
    record = retrain.challenger_record(dt.date(2027, 11, 9), dt.date(2027, 11, 10))
    assert retrain.champion_training_cutoff(record, CFG) == dt.date(2027, 11, 9)
