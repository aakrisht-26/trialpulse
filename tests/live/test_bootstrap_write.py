"""The bootstrap's write on a real Postgres (ADRs 0045, 0058, 0060, W1): every row is made
before the one transaction opens, the write computes nothing, it is all or nothing, it
refuses over live rows unless it rebuilds, and the release date's scores it copies in are
the rows `serving.publish` writes into empty tables. Skipped without Postgres (failed in
CI)."""

import dataclasses
import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from trialpulse.live import contract, seed, store
from trialpulse.live.sponsors import SponsorFile
from trialpulse.scoring import predict, serving
from trialpulse.scoring.predict import Scored

from .conftest import CFG, KEEP, RULES, compact_history

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
    assert left == (0,)


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
