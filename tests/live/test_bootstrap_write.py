"""The bootstrap's write on a real Postgres (ADRs 0045, 0058, 0060, W1): every row is made
before the one transaction opens, the write computes nothing, it is all or nothing, it
refuses over live rows unless it rebuilds, and the release date's scores it copies in are
the rows `serving.publish` writes into empty tables. Skipped without Postgres (failed in
CI)."""

import dataclasses
import datetime as dt
import json
import re
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from trialpulse.cli import RefusedError
from trialpulse.live import bootstrap, contract, daily, reasons, seed, store
from trialpulse.live.sponsors import SponsorFile
from trialpulse.scoring import bundle as bundle_module
from trialpulse.scoring import predict, serving
from trialpulse.scoring import score as score_module
from trialpulse.scoring.predict import Scored

from .conftest import (
    _FEATURES,
    CFG,
    DECISIONS,
    KEEP,
    RULES,
    SECRET,
    compact_history,
    compact_history_of,
    registry,
)

psycopg = pytest.importorskip("psycopg")

RELEASE = dt.date(2026, 9, 25)
ALIASES = (CFG.features.sponsor_alias_min_trials, CFG.features.sponsor_alias_wait_days)
DRIVERS = json.dumps({
    "12": [{"feature": "n_versions", "value": "3", "contribution": 0.5, "direction": "raises"}],
    "24": [{"feature": "status", "value": "RECRUITING", "contribution": -0.12345,
            "direction": "lowers"}],
})  # fmt: skip
REASONS = [(dt.date(2020, 4, 1), "covid19", "OTHER", 3), (dt.date(2021, 1, 1), "accrual",
                                                          "INDUSTRY", 2)]  # fmt: skip


@pytest.fixture
def dsn(scratch: str) -> str:
    return scratch.replace("postgresql+psycopg://", "postgresql://", 1)


def _seeded() -> seed.Seed:
    return seed.seed(compact_history(120), RELEASE, RULES, KEEP, *ALIASES)


def _scored(seeded: seed.Seed) -> Scored:
    """The release date's scores of four trials and one unscored trial, with the wide rows'
    feature hashes as content hashes (as the bootstrap regenerates them)."""
    trials = sorted(seeded.wides)[:5]
    hashes = [seeded.wides[n].row["feature_hash"] for n in trials]
    scores = pd.DataFrame({
        "nct_id": trials[:4], "as_of_date": np.datetime64(RELEASE, "D"),
        "true_index": [1, 2, 8, 0], "landmark_index": [1, 2, 6, 0],
        "flagged": [False, False, True, False], "risk_12m": [0.1, 0.2, 0.3, 0.04],
        "risk_24m": [0.2, 0.3, 0.4, 0.08], "content_hash": hashes[:4],
        "model_name": "trialpulse-m4", "model_version": "trialpulse-m4/1", "drivers": DRIVERS,
    })  # fmt: skip
    unscored = pd.DataFrame({"nct_id": trials[4:], "as_of_date": np.datetime64(RELEASE, "D"),
                             "true_index": [21], "reason": ["beyond_120_months"],
                             "content_hash": hashes[4:]})  # fmt: skip
    return Scored(scores, unscored, {})


def _lease(con: "psycopg.Connection[tuple[object, ...]]") -> int:
    with con.cursor() as cur, con.transaction():
        return store.start_bootstrap(cur, RELEASE, 120)


def _counts(con: "psycopg.Connection[tuple[object, ...]]") -> dict[str, int]:
    tables = (*store.REBUILT_TABLES, "serving.reason_counts")
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}


def _plain(value: object) -> bool:
    """Bytes and plain values only: what a write can send without computing."""
    if isinstance(value, tuple | list):
        return all(_plain(v) for v in value)
    return value is None or isinstance(value, bytes | str | int | float | dt.date)


def test_the_write_receives_prepared_rows_and_computes_nothing(
    dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = _seeded()
    scored = _scored(seeded)
    with psycopg.connect(dsn, autocommit=True) as con:
        run_id = _lease(con)
        with con.cursor() as cur:
            rows = store.prepare_bootstrap(cur, run_id, RELEASE, seeded.wides, seeded.sponsors,
                                           seeded.markers, scored, REASONS)  # fmt: skip
        for payload in rows.copies:
            assert isinstance(payload.data, bytes)
        assert _plain(rows.file)
        assert _plain(rows.reasons)
        assert all(_plain(dataclasses.astuple(p)) for p in rows.copies)

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("computed inside the write's transaction")

        for owner, name in (
            (store, "_wide_values"), (store, "_marker_values"), (store, "file_values"),
            (store, "copy_payload"), (store, "prepare_bootstrap"), (SponsorFile, "to_bytes"),
            (serving, "first_rows"), (serving, "latest_frame"), (serving, "compact_drivers"),
            (serving, "history_rows"), (contract, "feature_hash"), (contract, "compact_row"),
            (seed, "seed_trial"), (seed, "sponsor_pass"), (predict, "predict"),
        ):  # fmt: skip
            monkeypatch.setattr(owner, name, forbidden)
        with con.cursor() as cur, con.transaction():
            assert store.load_bootstrap(cur, rows) == run_id
        monkeypatch.undo()
        counts = _counts(con)
        with con.cursor() as cur:
            wides = store.load_wides(cur)
            markers = store.load_markers(cur, seeded.markers)
            sponsors = store.load_file(cur)
            run = con.execute(
                "SELECT status, watermark_to, data_timestamp FROM monitoring.pipeline_runs "
                "WHERE run_id = %s",
                (run_id,),
            ).fetchone()
            labeled = con.execute("SELECT labeled_through FROM serving.data_status").fetchone()
    assert wides == seeded.wides  # every field round trips
    assert markers == seeded.markers
    seeded.sponsors.last_run_id = run_id
    assert sponsors.to_bytes() == seeded.sponsors.to_bytes()
    assert run == ("succeeded", RELEASE, RELEASE)
    assert labeled == (RELEASE,)
    assert counts["live.trial_state"] == len(seeded.wides)
    assert counts["serving.amendment_markers"] == len(seeded.markers)
    assert counts["serving.trial_scores_latest"] == counts["serving.trial_score_history"] == 4
    assert counts["serving.trials_not_scored"] == 1
    assert counts["serving.reason_counts"] == len(REASONS)
    assert counts["live.sponsor_file"] == 1


def test_the_write_is_all_or_nothing(dsn: str) -> None:
    seeded = _seeded()
    with psycopg.connect(dsn, autocommit=True) as con:
        run_id = _lease(con)
        with con.cursor() as cur:
            rows = store.prepare_bootstrap(cur, run_id, RELEASE, seeded.wides, seeded.sponsors,
                                           seeded.markers, _scored(seeded), REASONS)  # fmt: skip
        broken = dataclasses.replace(rows.copies[-1], data=rows.copies[-1].data + b"not a row\n")
        bad = dataclasses.replace(rows, copies=(*rows.copies[:-1], broken))
        with pytest.raises(psycopg.errors.DataError), con.cursor() as cur, con.transaction():
            store.load_bootstrap(cur, bad)
        assert set(_counts(con).values()) == {0}  # the copies before the broken one are gone
        status = con.execute("SELECT status FROM monitoring.pipeline_runs WHERE run_id = %s",
                             (run_id,)).fetchone()  # fmt: skip
        assert status == ("running",)  # the lease stays until the job records its failure
        with con.cursor() as cur, con.transaction():
            store.load_bootstrap(cur, rows)  # the computed rows are written on a retry
        assert _counts(con)["live.trial_state"] == len(seeded.wides)


def test_a_bootstrap_refuses_over_live_rows_unless_it_rebuilds(dsn: str) -> None:
    seeded = _seeded()
    with psycopg.connect(dsn, autocommit=True) as con:
        with con.cursor() as cur, con.transaction():  # an earlier state's daily run
            old = store.start_run(cur, "daily", "full", RELEASE, 120)
            store.finish_run(cur, old, "succeeded", {"watermark_to": RELEASE})
            store.set_data_status(cur, RELEASE, "old-model/1", RULES.early_stop)
        con.execute("INSERT INTO serving.trials_not_scored (nct_id, as_of_date, "
                    "true_landmark_index, reason) VALUES ('NCT99999999', %s, 21, "
                    "'beyond_120_months')", (RELEASE,))  # fmt: skip
        with con.cursor() as cur:
            assert store.occupied_tables(cur) == ["serving.trials_not_scored"]
        run_id = _lease(con)
        with con.cursor() as cur:
            rows = store.prepare_bootstrap(cur, run_id, RELEASE, seeded.wides, seeded.sponsors,
                                           seeded.markers, _scored(seeded))  # fmt: skip
        with (
            pytest.raises(RuntimeError, match="live state exists"),
            con.cursor() as cur,
            con.transaction(),
        ):
            store.load_bootstrap(cur, rows)
        with con.cursor() as cur, con.transaction():
            store.load_bootstrap(cur, rows, rebuild=True)
        left = con.execute("SELECT count(*) FROM serving.trials_not_scored WHERE nct_id = "
                           "'NCT99999999'").fetchone()  # fmt: skip
        runs = con.execute("SELECT run_id, job FROM monitoring.pipeline_runs").fetchall()
        status = con.execute("SELECT data_as_of, model_version FROM serving.data_status"
                             ).fetchone()  # fmt: skip
    assert left == (0,)
    assert runs == [(run_id, "bootstrap")]  # every other run's row is gone
    assert status == (None, None)  # the daily job's columns start again


def test_a_write_after_its_lease_was_taken_over_writes_nothing(
    dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lease is renewed first in the write (ADR 0060): a bootstrap whose run was taken
    over as stale while it computed sends no row at all, and leaves every table as it was."""
    seeded = _seeded()
    sent: list[str] = []
    real_file = store._write_file
    monkeypatch.setattr(store, "_write_file", lambda cur, values: (
        sent.append("file"), real_file(cur, values)))  # fmt: skip
    with psycopg.connect(dsn, autocommit=True) as con:
        run_id = _lease(con)
        with con.cursor() as cur:
            rows = store.prepare_bootstrap(cur, run_id, RELEASE, seeded.wides, seeded.sponsors,
                                           seeded.markers, _scored(seeded), REASONS)  # fmt: skip
        con.execute("UPDATE monitoring.pipeline_runs SET status = 'failed' WHERE run_id = %s",
                    (run_id,))  # fmt: skip
        with pytest.raises(store.LeaseLostError), con.cursor() as cur, con.transaction():
            store.load_bootstrap(cur, rows)
        assert sent == []  # stopped before its first write, not only at the end
        assert set(_counts(con).values()) == {0}


def test_the_copied_scores_are_the_rows_publish_writes_into_empty_tables(dsn: str) -> None:
    seeded = _seeded()
    scored = _scored(seeded)
    serving.publish(scored, CFG, dsn)
    published = serving.table_digest(dsn)
    with psycopg.connect(dsn, autocommit=True) as con, con.cursor() as cur:
        con.execute("TRUNCATE serving.trial_scores_latest, serving.trial_score_history, "
                    "serving.trials_not_scored")  # fmt: skip
        latest, history, not_scored = serving.first_rows(scored, None)
        payloads = [
            store.copy_payload(cur, "serving.trial_scores_latest", serving.LATEST_COLUMNS,
                               latest),
            store.copy_payload(cur, "serving.trial_score_history", serving.HISTORY_COLUMNS,
                               history),
            store.copy_payload(cur, "serving.trials_not_scored", serving.NOT_SCORED_COLUMNS,
                               not_scored),
        ]  # fmt: skip
        with con.transaction():
            for p in payloads:
                with cur.copy(f"COPY {p.table} ({', '.join(p.columns)}) FROM STDIN") as copy:
                    copy.write(p.data)
    assert serving.table_digest(dsn) == published
    reasons = {tuple(r[5]) for r in history}
    assert reasons == {(serving.FIRST,)}


# The command's own flow (the item 2 check, finding 1) -------------------------------------

DATA = registry(300, 7)


def _registry_warehouse() -> duckdb.DuckDBPyConnection:
    """The synthetic registry in the warehouse's layout, as the bootstrap's stream reads it."""
    con = duckdb.connect()
    _FEATURES.load_versions(con, DATA.versions, "versions")
    _FEATURES.load_texts(con, DATA.texts)
    return con


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> bootstrap.Inputs:
    """The bootstrap's inputs on the synthetic registry: Step 13's scoring and files, the
    bundle and the reason counts stood in for, the stream reading the registry."""
    seeded = seed.seed(compact_history_of(DATA.versions, DATA.texts, DATA.interventions),
                       RELEASE, RULES, KEEP, *ALIASES)  # fmt: skip
    scored = _scored(seeded)
    scored.scores.to_parquet(tmp_path / f"scores_{RELEASE}.parquet")
    scored.unscored.to_parquet(tmp_path / f"unscored_{RELEASE}.parquet")
    monkeypatch.setattr(score_module, "score", lambda *a, **k: Scored(
        scored.scores.copy(), scored.unscored.copy(), {}))  # fmt: skip
    monkeypatch.setattr(score_module, "served_decisions", lambda *a, **k: DECISIONS)
    monkeypatch.setattr(bundle_module, "load_bundle", lambda *a, **k: None)
    monkeypatch.setattr(bootstrap, "open_warehouse", lambda path: _registry_warehouse())
    monkeypatch.setattr(reasons, "counts_from_files", lambda *a: REASONS)
    return bootstrap.Inputs(tmp_path / "warehouse.duckdb", tmp_path, tmp_path / "checks.json",
                            tmp_path / "outcomes.parquet", tmp_path)  # fmt: skip


def _runs(dsn: str) -> list[tuple[object, ...]]:
    with psycopg.connect(dsn) as con:
        return con.execute("SELECT job, status, error FROM monitoring.pipeline_runs").fetchall()


def test_the_command_prepares_every_row_then_writes_them_in_one_transaction(
    dsn: str, stubbed: bootstrap.Inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, object]] = []
    real_prepare, real_load = store.prepare_bootstrap, store.load_bootstrap

    def prepare(cur: "psycopg.Cursor[tuple[object, ...]]", *args: object) -> object:
        seen.append(("prepare", cur.connection.info.transaction_status))
        return real_prepare(cur, *args)  # type: ignore[arg-type]

    def load(cur: "psycopg.Cursor[tuple[object, ...]]", *args: object, **kwargs: object) -> int:
        seen.append(("load", cur.connection.info.transaction_status))
        return real_load(cur, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "prepare_bootstrap", prepare)
    monkeypatch.setattr(store, "load_bootstrap", load)
    report = bootstrap.run_bootstrap(CFG, dsn, SECRET, False, stubbed, say=lambda _: None)
    status = psycopg.pq.TransactionStatus
    assert seen == [("prepare", status.IDLE), ("load", status.INTRANS)]
    assert report["counts"]["scored"] == 4
    assert report["next"] == bootstrap.DISPLAY_NEXT
    assert _runs(dsn) == [("bootstrap", "succeeded", None)]
    with psycopg.connect(dsn) as con:
        counts = _counts(con)
    assert counts["serving.trial_scores_latest"] == 4
    assert counts["live.trial_state"] == report["counts"]["wides"] > 0


def test_a_failed_write_records_its_stage_type_and_sqlstate_and_writes_nothing(
    dsn: str, stubbed: bootstrap.Inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_prepare = store.prepare_bootstrap

    def broken(cur: object, *args: object) -> object:
        rows = real_prepare(cur, *args)  # type: ignore[arg-type]
        bad = dataclasses.replace(rows.copies[-1], data=rows.copies[-1].data + b"secret words\n")
        return dataclasses.replace(rows, copies=(*rows.copies[:-1], bad))

    monkeypatch.setattr(store, "prepare_bootstrap", broken)
    with pytest.raises(daily.JobFailedError) as failed:
        bootstrap.run_bootstrap(CFG, dsn, SECRET, False, stubbed, say=lambda _: None)
    assert "secret" not in str(failed.value)
    [(job, state, error)] = _runs(dsn)
    assert (job, state) == ("bootstrap", "failed")
    # The stage, the exception's type and the SQLSTATE, and nothing of the row.
    assert re.fullmatch(r"write: [A-Za-z]+ [0-9A-Z]{5}", str(error)), error
    assert "secret" not in str(error)
    with psycopg.connect(dsn) as con:
        assert set(_counts(con).values()) == {0}


def test_the_command_refuses_before_computing_or_taking_a_lease(
    dsn: str, stubbed: bootstrap.Inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    with psycopg.connect(dsn, autocommit=True) as con:
        con.execute("INSERT INTO serving.trials_not_scored (nct_id, as_of_date, "
                    "true_landmark_index, reason) VALUES ('NCT99999999', %s, 21, "
                    "'beyond_120_months')", (RELEASE,))  # fmt: skip
    computed = score_module.score
    monkeypatch.setattr(score_module, "score", lambda *a, **k: pytest.fail("it computed"))
    with pytest.raises(RefusedError, match="--rebuild"):
        bootstrap.run_bootstrap(CFG, dsn, SECRET, False, stubbed, say=lambda _: None)
    assert _runs(dsn) == []
    monkeypatch.setattr(score_module, "score", computed)
    moved = pd.read_parquet(stubbed.scores_dir / f"scores_{RELEASE}.parquet")
    moved.loc[0, "risk_12m"] += 0.01
    moved.to_parquet(stubbed.scores_dir / f"scores_{RELEASE}.parquet")
    with pytest.raises(RefusedError, match="differ from Step 13"):
        bootstrap.run_bootstrap(CFG, dsn, SECRET, True, stubbed, say=lambda _: None)
    assert _runs(dsn) == []  # held to Step 13's files before any lease


def test_drivers_are_held_at_the_cutoff_and_where_step_13_wrote_them(
    dsn: str, stubbed: bootstrap.Inputs
) -> None:
    """Step 13 wrote drivers into the cutoff's file only: a bootstrap dated earlier (the
    replay of parity layer 2) is held on every other column, and one at the cutoff refuses
    a file without drivers."""
    files = stubbed.scores_dir
    without = pd.read_parquet(files / f"scores_{RELEASE}.parquet").drop(columns="drivers")
    earlier = RELEASE - dt.timedelta(days=1)
    without.to_parquet(files / f"scores_{earlier}.parquet")
    (files / f"unscored_{earlier}.parquet").write_bytes(
        (files / f"unscored_{RELEASE}.parquet").read_bytes()
    )
    report = bootstrap.run_bootstrap(CFG, dsn, SECRET, False, stubbed, say=lambda _: None,
                                     day=earlier)  # fmt: skip
    assert report["held_columns"] == list(bootstrap.SCORE_COLUMNS_BEFORE_THE_CUTOFF)
    # An earlier file without a risk column is refused, not held on what is left.
    without.drop(columns="risk_24m").to_parquet(files / f"scores_{earlier}.parquet")
    with pytest.raises(RefusedError, match="lacks risk_24m"):
        bootstrap.run_bootstrap(CFG, dsn, SECRET, True, stubbed, say=lambda _: None,
                                day=earlier)  # fmt: skip
    without.to_parquet(files / f"scores_{RELEASE}.parquet")
    with pytest.raises(RefusedError, match="lacks drivers"):
        bootstrap.run_bootstrap(CFG, dsn, SECRET, True, stubbed, say=lambda _: None)
