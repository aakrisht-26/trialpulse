"""H3 and H5 of the pre-registration (Step 11), on made-up prediction files and reason
labels whose answers are known. No real data, no model, no locked origin: the lock is
replaced where the unlocked path is tested."""

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
from trialpulse.eval import compare, hypotheses, lock
from trialpulse.eval.ipcw import horizon_labels_and_weights
from trialpulse.eval.metrics import weighted_auc
from trialpulse.eval.walkforward import predictions_path

DEV = (dt.date(2016, 1, 1), dt.date(2017, 1, 1))
TEST = (dt.date(2018, 1, 1), dt.date(2019, 1, 1))
STRESS = dt.date(2020, 1, 1)


@pytest.fixture(scope="module")
def cfg() -> ProjectConfig:
    return load_project_config()


# The kind of each trial, fixed across its rows, as the reasons table has one row per trial.
KINDS = np.random.default_rng(99).random(10_000)


def _kind(number: int) -> str:
    draw = KINDS[number]
    if draw < 0.1:
        return "other"
    if draw < 0.2:
        return "none"  # no reason text
    return "operational" if draw < 0.75 else "scientific"


def _frame(origin: dt.date, n: int, sharp: float, seed: int) -> pd.DataFrame:
    """Evaluation rows of one origin at landmarks 0 and 1. A trial of a scientific kind stops
    early by its scientific risk, every other trial by its operational risk; the score
    follows the operational risk with weight `sharp`. Half the trials of an origin are
    trials of the origin before (one cluster)."""
    rng = np.random.default_rng(seed + origin.year)
    first = 0 if origin.year % 2 == 0 else n // 2
    numbers = first + np.arange(n)
    kind = np.array([_kind(i) for i in numbers])
    rows = []
    for k in (0, 1):
        op, sci = rng.normal(size=n), rng.normal(size=n)
        risk = np.where(kind == "scientific", sci, op)
        stop = rng.exponential(1 / (0.0008 * np.exp(1.2 * risk)))
        complete = rng.exponential(1 / 0.0012, n)
        censor = rng.exponential(2500.0, n)
        end = np.minimum(np.minimum(stop, complete), censor)
        event = np.where(censor <= np.minimum(stop, complete), 0, np.where(stop < complete, 1, 2))
        noise = rng.normal(size=n)
        frame = pd.DataFrame({
            "trial_id": [f"NCT{i:08d}" for i in numbers],
            "landmark_index": k,
            "landmark_date": np.datetime64(origin, "D") + np.timedelta64(20 + 182 * k, "D"),
            "time": np.floor(end) + 1.0,
            "event": event,
            "censoring_group": np.where(op > 0, "INDUSTRY", "OTHER"),
            "kind": kind,
        })  # fmt: skip
        for months in (12, 24):
            frame[f"score_{months}"] = 1 / (1 + np.exp(-(sharp * op + noise - 3 + months / 24)))
        rows.append(frame)
    return pd.concat(rows, ignore_index=True).sort_values(["trial_id", "landmark_index"])


def _save(folder: Path, model: str, spec: str, origin: dt.date, frame: pd.DataFrame) -> None:
    out = frame.drop(columns=["kind"])  # noqa: F841 (read by DuckDB below)
    path = predictions_path(folder, model, spec, origin)
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as con:
        con.execute(
            f"""COPY (SELECT * REPLACE (CAST(landmark_date AS DATE) AS landmark_date) FROM out)
            TO '{path.as_posix()}' (FORMAT parquet)"""
        )


def _reasons(path: Path, frames: list[pd.DataFrame]) -> None:
    """One reason row per trial with a reason (the kind of its last row), as distill writes."""
    every = pd.concat(frames).drop_duplicates("trial_id")
    kept = every[every["kind"] != "none"]
    reason_table = pd.DataFrame({  # noqa: F841 (read by DuckDB below)
        "nct_id": kept["trial_id"], "status": "TERMINATED", "text_sha256": "x",
        "label": kept["kind"], "reason_group": kept["kind"], "confidence": 0.9, "model": "test",
    })  # fmt: skip
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as con:
        con.execute(f"COPY reason_table TO '{path.as_posix()}' (FORMAT parquet)")


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Any]:
    """Prediction files of m4 at the development, test and stress origins (the stress
    origin's score is blunter), reason labels and a stand-in model file."""
    frames: dict[dt.date, pd.DataFrame] = {}
    for origin in DEV:
        frames[origin] = _frame(origin, 900, 1.2, seed=1)
        _save(tmp_path / "pred", "m4", "dev", origin, frames[origin])
    for origin in TEST:
        frames[origin] = _frame(origin, 900, 1.2, seed=2)
        _save(tmp_path / "pred", "m4", "test", origin, frames[origin])
    frames[STRESS] = _frame(STRESS, 900, 0.3, seed=3)
    _save(tmp_path / "pred", "m4", "stress", STRESS, frames[STRESS])
    reasons = tmp_path / "reasons.parquet"
    _reasons(reasons, list(frames.values()))
    model = tmp_path / "distilled.joblib"
    model.write_bytes(b"a stand-in for the distilled model")
    return {"root": tmp_path, "frames": frames, "reasons": reasons, "model": model}


def _args(world: dict[str, Any], *more: str) -> list[str]:
    root = world["root"]
    return ["--predictions-dir", str(root / "pred"), "--reasons", str(world["reasons"]),
            "--reasons-model", str(world["model"]), "--out-dir", str(root / "out"),
            "--resamples", "40", "--no-track", *more]  # fmt: skip


def test_the_reason_paths_are_those_the_distillation_writes() -> None:
    from trialpulse.nlp import distill

    assert hypotheses.MODEL_PATH == distill.MODEL_PATH
    assert hypotheses.REASONS_PATH == distill.REASONS_DIR / "early_stop_reasons.parquet"


def test_the_weights_come_from_all_rows_before_any_row_is_left_out() -> None:
    frame = _frame(DEV[0], 1500, 1.2, seed=4)
    time, event = frame["time"].to_numpy(float), frame["event"].to_numpy(np.int64)
    score, groups = frame["score_12"].to_numpy(float), frame["censoring_group"].to_numpy()
    kind = frame["kind"].to_numpy(dtype=str)
    horizon = np.full(len(frame), 365.0)
    found = hypotheses.cause_aucs(time, event, score, horizon, groups, kind)
    y, w = horizon_labels_and_weights(time, event, horizon, groups)
    for which in ("operational", "scientific"):
        keep = (y == 0) | (kind == which)
        assert found[which] == pytest.approx(weighted_auc(y[keep], score[keep], w[keep]))
        # Not the weights of the rows that remain: leaving the other stops out changes the
        # risk sets of the censoring curve, and with them the weights.
        y2, w2 = horizon_labels_and_weights(time[keep], event[keep], horizon[keep], groups[keep])
        assert found[which] != pytest.approx(weighted_auc(y2, score[keep], w2), abs=1e-12)
    # The score follows the operational risk only.
    assert found["operational"] > 0.65
    assert abs(found["scientific"] - 0.5) < 0.08


@pytest.mark.slow  # over half a second; CI runs every test
def test_h3_on_the_development_origins(world: dict[str, Any], cfg: ProjectConfig) -> None:
    assert hypotheses.main(["h3", *_args(world)]) == 0
    saved = json.loads((world["root"] / "out" / "h3-dev.json").read_text(encoding="utf-8"))
    assert saved["origins"] == ["2016-01-01", "2017-01-01"]
    assert saved["unlock"] is None
    assert saved["reason_labels"] == {
        "model_sha256": hypotheses.sha256(world["model"]),
        "reasons_sha256": hypotheses.sha256(world["reasons"]),
    }
    part = saved["horizons"]["12"]
    assert part["difference"]["ci_low"] > 0  # operational stops are the predictable ones
    # Each origin has its own paired interval (ADR 0028), around its own values.
    for by in part["by_origin"].values():
        intervals = by["intervals"]
        assert intervals["difference"]["estimate"] == pytest.approx(
            by["operational"] - by["scientific"]
        )
        assert intervals["operational"]["estimate"] == pytest.approx(by["operational"])
        assert intervals["difference"]["ci_low"] < intervals["difference"]["ci_high"]
    # The scores files it read, with their hashes.
    folder = world["root"] / "pred"
    assert [(i["model"], i["origin"]) for i in saved["inputs"]] == [
        ("m4", "2016-01-01"), ("m4", "2017-01-01"),
    ]  # fmt: skip
    for item in saved["inputs"]:
        assert item["sha256"] == hypotheses.sha256(folder / item["path"])
    # The difference is the mean over the origins of the difference at each.
    by = part["by_origin"]
    expected = np.mean([by[d]["operational"] - by[d]["scientific"] for d in by])
    assert part["difference"]["estimate"] == pytest.approx(expected)
    # The cases by kind, counted from the made-up rows.
    frame = world["frames"][DEV[0]]
    landmark = pd.to_datetime(frame["landmark_date"])
    days = (landmark + pd.DateOffset(months=12) - landmark).dt.days
    stop = (frame["event"] == 1) & (frame["time"] <= days)
    for kind in ("operational", "scientific", "other", "none"):
        assert by["2016-01-01"]["cases"][kind] == int((stop & (frame["kind"] == kind)).sum())


def test_h3_on_the_locked_origins_needs_the_unlock_first(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The test files of the made-up world exist; any read of them, or of the reason labels,
    fails the test: the refusal of the lock comes first."""

    def no_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("a file was read before the lock")

    for name in ("load_pair", "reason_hashes", "reasons_of", "inputs_of"):
        monkeypatch.setattr(hypotheses, name, no_read)
    with pytest.raises(RefusedError, match="--unlock-test"):
        hypotheses.main(["h3", *_args(world, "--origins", "test")])
    with pytest.raises(RefusedError, match="--unlock-test"):
        hypotheses.main(["h5", *_args(world)])


def _unlocked(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    """The lock as if every condition of ADR 0004 held, and the registration's text."""
    record = lock.Unlock(tag=lock.TAG, tag_object="a" * 40, commit="b" * 40,
                         registered=dt.date(2026, 11, 1), remote=lock.REMOTE)  # fmt: skip

    def opened(repo: Path, unlock_flag: bool, remote: str = lock.REMOTE) -> lock.Unlock:
        if not unlock_flag:
            raise lock.TestLockError("locked origins need the --unlock-test flag")
        return record

    monkeypatch.setattr(lock, "check_unlock", opened)
    monkeypatch.setattr(hypotheses, "registration_text", lambda repo: text)


@pytest.mark.slow  # over half a second; CI runs every test
def test_h3_on_the_locked_origins_runs_only_with_the_registered_labels(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    model, reasons = hypotheses.sha256(world["model"]), hypotheses.sha256(world["reasons"])
    locked = ["h3", *_args(world, "--origins", "test", "--unlock-test")]
    _unlocked(monkeypatch, "Registered: 2026-11-01\nNo hashes here.\n")
    with pytest.raises(RefusedError, match=r"names no distilled.joblib hash"):
        hypotheses.main(locked)
    _unlocked(monkeypatch, f"distilled.joblib sha256:{model}\n"
                           f"early_stop_reasons.parquet sha256:{'0' * 64}\n")  # fmt: skip
    with pytest.raises(RefusedError, match="differ from the registration"):
        hypotheses.main(locked)
    _unlocked(monkeypatch, f"- distilled.joblib sha256:{model}\n"
                           f"- early_stop_reasons.parquet sha256:{reasons}\n")  # fmt: skip
    assert hypotheses.main(locked) == 0
    saved = json.loads((world["root"] / "out" / "h3-test.json").read_text(encoding="utf-8"))
    assert saved["origins"] == ["2018-01-01", "2019-01-01"]
    assert saved["unlock"]["tag_object"] == "a" * 40
    assert saved["unlock"]["registered"] == "2026-11-01"


@pytest.mark.slow  # over half a second; CI runs every test
def test_h5_takes_the_stress_origin_against_the_mean_of_the_test_origins(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, cfg: ProjectConfig
) -> None:
    _unlocked(monkeypatch, "")
    assert hypotheses.main(["h5", *_args(world, "--unlock-test")]) == 0
    saved = json.loads((world["root"] / "out" / "h5.json").read_text(encoding="utf-8"))
    assert saved["origins"] == ["2018-01-01", "2019-01-01", "2020-01-01"]
    by = saved["by_origin"]
    expected = by["2020-01-01"]["auc"] - (by["2018-01-01"]["auc"] + by["2019-01-01"]["auc"]) / 2
    assert saved["difference"]["estimate"] == pytest.approx(expected)
    assert saved["difference"]["ci_high"] < 0  # the blunter score of 2020
    assert saved["months"] == 12


def test_rows_of_several_loads_keep_their_origins_apart(
    world: dict[str, Any], cfg: ProjectConfig
) -> None:
    folder = world["root"] / "pred"
    test = compare.load_pair(folder, "m4", "m4", TEST, (12, 24), "test")
    stress = compare.load_pair(folder, "m4", "m4", (STRESS,), (12, 24), "stress")
    both = hypotheses.concat([test, stress], [0, 2])
    assert len(both) == len(test) + len(stress)
    assert sorted(np.unique(both.origin).tolist()) == [0, 1, 2]
    assert int(np.sum(both.origin == 2)) == len(stress)


def test_registered_hashes_are_read_from_their_lines() -> None:
    model, reasons = "1" * 64, "2" * 64
    text = f"x\n- distilled.joblib sha256:{model}\n- early_stop_reasons.parquet sha256:{reasons}\n"
    assert hypotheses.registered_hashes(text) == {"model_sha256": model, "reasons_sha256": reasons}
    hypotheses.check_reason_hashes({"model_sha256": model, "reasons_sha256": reasons},
                                   hypotheses.registered_hashes(text))  # fmt: skip
    with pytest.raises(RefusedError, match="early_stop_reasons"):
        hypotheses.registered_hashes(f"distilled.joblib sha256:{model}\n")


def test_a_hash_line_is_read_in_its_usual_forms_and_only_once() -> None:
    model, reasons = "1" * 64, "2" * 64
    forms = [
        f"distilled.joblib sha256:{model}",
        f"distilled.joblib sha256: {model}",
        f"`distilled.joblib sha256:` {model}",
        f"`distilled.joblib` sha256: `{model}`",
        f"- distilled.joblib sha256:{model.upper()}",
    ]
    for form in forms:
        text = f"{form}\nearly_stop_reasons.parquet sha256:{reasons}\n"
        assert hypotheses.registered_hashes(text)["model_sha256"] == model, form
    # A hash with one hex character too many is no hash; two different hashes are refused.
    with pytest.raises(RefusedError, match=r"names no distilled.joblib hash"):
        hypotheses.registered_hashes(
            f"distilled.joblib sha256:{model}f\nearly_stop_reasons.parquet sha256:{reasons}\n"
        )
    with pytest.raises(RefusedError, match="names 2 different early_stop_reasons"):
        hypotheses.registered_hashes(
            f"distilled.joblib sha256:{model}\nearly_stop_reasons.parquet sha256:{reasons}\n"
            f"early_stop_reasons.parquet sha256:{'3' * 64}\n"
        )
    # The same hash named twice is one hash.
    twice = (
        f"distilled.joblib sha256:{model}\n" * 2 + f"early_stop_reasons.parquet sha256:{reasons}"
    )
    assert hypotheses.registered_hashes(twice) == {"model_sha256": model, "reasons_sha256": reasons}
