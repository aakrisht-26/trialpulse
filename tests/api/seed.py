"""A synthetic serving database for the API's tests and its load test (Step 15).

`seed(dsn, scored, unscored)` writes `scored` served trials and `unscored` trials without a
score, with their display rows, sponsors (some without a display name), history, markers,
reason counts, the data status and the model statement; `HAND` rows come first, with known
values the tests check. No real registry data: titles and names are made up.
"""

import datetime as dt
import random
from pathlib import Path
from typing import Any

import psycopg

from trialpulse.live import statement

DAY = dt.date(2026, 9, 25)
MODEL = "trialpulse-m4/1"
REASONS = ("beyond_120_months", "band_not_validated", "reversal")
CLASSES = ("INDUSTRY", "OTHER", "NIH", "FED", "OTHER_GOV", "NETWORK")
LABELS = ("accrual", "business", "funding", "administrative", "covid19", "safety", "efficacy",
          "other")  # fmt: skip
# Hand-placed trials: (id, scored, flagged, risk_12m, sponsor id, areas, reason)
HAND: tuple[tuple[str, bool, bool, float, str | None, list[int], str | None], ...] = (
    ("NCT00000001", True, True, 0.31, "sp-acme", [2, 12], None),  # flagged, two areas
    ("NCT00000002", True, False, 0.05, "sp-acme", [2], None),
    ("NCT00000003", False, False, 0.0, "sp-acme", [24], "reversal"),
    ("NCT00000004", True, False, 0.12, "sp-hidden", [], None),  # sponsor without a name
    ("NCT00000005", False, False, 0.0, "sp-northern", [8], "beyond_120_months"),
)
SPONSORS = (("sp-acme", "Acme Pharma Inc.", "acme pharma inc", "INDUSTRY"),
            ("sp-northern", "Northern University Hospital", "northern university hospital",
             "OTHER"))  # fmt: skip


def _risk(rng: random.Random) -> float:
    """A 12-month risk between 0.002 and 0.122, skewed low, from integers only: the seeded
    database must be the same on every platform, and `random.betavariate` goes through the C
    maths library, whose last digit differs between Windows and Linux."""
    u = rng.randrange(1, 1001)
    return (2_000 + u * u * 120 // 1000) / 1_000_000


def _drivers(rng: random.Random) -> tuple[list[int], list[int], list[str], list[float]]:
    months = [12] * 5 + [24] * 5
    features = [rng.randrange(0, 57) for _ in months]
    values = [str(rng.randint(0, 60)) for _ in months]
    contributions = [round(rng.uniform(-0.8, 0.8), 3) or 0.001 for _ in months]
    return months, features, values, contributions


def seed(dsn: str, scored: int = 40, unscored: int = 6, rng_seed: int = 7) -> dict[str, int]:
    rng = random.Random(rng_seed)
    latest, history, missing, display, markers = [], [], [], [], []
    for nct, is_scored, flagged, risk, sponsor, areas, reason in HAND:
        display.append((nct, f"Hand-placed trial {nct[-1]}", sponsor, "INDUSTRY", areas))
        if is_scored:
            latest.append((nct, DAY, 2, 9 if flagged else 2, flagged, MODEL, risk, risk * 1.6,
                           *_drivers(rng), bytes(32)))  # fmt: skip
            history.append((nct, DAY, MODEL, risk, risk * 1.6, ["first_score"], 2,
                            9 if flagged else 2, bytes(32), flagged))  # fmt: skip
        else:
            missing.append((nct, DAY, 21, reason, bytes(32)))
    markers.append(("NCT00000001", [dt.date(2025, 1, 10), dt.date(2026, 3, 2)], [1, 2 | 4]))
    history.append(("NCT00000001", DAY + dt.timedelta(days=7), MODEL, 0.33, 0.5, ["calendar"],
                    2, 9, bytes(32), True))  # fmt: skip
    n_sponsors = max(10, scored // 40)
    for i in range(scored):
        nct = f"NCT{10_000_000 + i:08d}"
        flagged = rng.random() < 0.21
        risk = _risk(rng)
        latest.append((nct, DAY, min(6, i % 9), 7 + i % 10 if flagged else i % 7, flagged, MODEL,
                       risk, min(0.99, risk * 1.7), *_drivers(rng), rng.randbytes(32)))  # fmt: skip
        history.append((nct, DAY, MODEL, risk, min(0.99, risk * 1.7), ["first_score"],
                        min(6, i % 9), i % 7, rng.randbytes(32), flagged))  # fmt: skip
        sp = f"sp-{rng.randrange(n_sponsors):06d}"
        display.append((nct, f"Synthetic trial {i}", sp, rng.choice(CLASSES),
                        sorted(rng.sample(range(1, 25), rng.choice([0, 1, 1, 2, 3])))))  # fmt: skip
        if rng.random() < 0.2:
            markers.append((nct, [DAY - dt.timedelta(days=200)], [rng.choice([1, 2, 4, 8])]))
    for i in range(unscored):
        nct = f"NCT{20_000_000 + i:08d}"
        missing.append((nct, DAY, 21, rng.choice(REASONS), rng.randbytes(32)))
        display.append((nct, f"Unscored synthetic trial {i}", f"sp-{rng.randrange(n_sponsors):06d}",
                        rng.choice(CLASSES), [rng.randrange(1, 25)]))  # fmt: skip
    sponsors = list(SPONSORS) + [
        (f"sp-{i:06d}", f"Synthetic Sponsor {i:06d} Inc.", f"synthetic sponsor {i:06d} inc",
         rng.choice(CLASSES)) for i in range(n_sponsors) if i % 10  # every tenth has no name
    ]  # fmt: skip
    counts = [(dt.date(y, m, 1), label, cls, rng.randint(1, 40))
              for y in range(2008, 2027) for m in range(1, 13) if dt.date(y, m, 1) <= DAY
              for label in LABELS for cls in CLASSES[:2] if rng.random() < 0.5]  # fmt: skip
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        _copy(cur, "serving.trial_scores_latest",
              ("nct_id", "as_of_date", "landmark_index", "true_landmark_index", "flagged",
               "model_version", "risk_12m", "risk_24m", "driver_months", "driver_features",
               "driver_values", "driver_contributions", "content_hash"),
              ("text", "date", "int2", "int2", "bool", "text", "float8", "float8", "int2[]",
               "int2[]", "text[]", "float4[]", "bytea"), latest)  # fmt: skip
        _copy(cur, "serving.trial_score_history",
              ("nct_id", "as_of_date", "model_version", "risk_12m", "risk_24m", "reasons",
               "landmark_index", "true_landmark_index", "content_hash", "flagged"),
              ("text", "date", "text", "float8", "float8", "text[]", "int2", "int2", "bytea",
               "bool"), history)  # fmt: skip
        _copy(cur, "serving.trials_not_scored",
              ("nct_id", "as_of_date", "true_landmark_index", "reason", "content_hash"),
              ("text", "date", "int2", "text", "bytea"), missing)  # fmt: skip
        _copy(cur, "serving.trial_display",
              ("nct_id", "brief_title", "sponsor_id", "sponsor_class", "areas"),
              ("text", "text", "text", "text", "int2[]"), display)  # fmt: skip
        _copy(cur, "serving.sponsors",
              ("sponsor_id", "display_name", "search_key", "sponsor_class"),
              ("text", "text", "text", "text"), sponsors)  # fmt: skip
        _copy(cur, "serving.amendment_markers", ("nct_id", "post_dates", "kinds"),
              ("text", "date[]", "int2[]"), markers)  # fmt: skip
        _copy(cur, "serving.reason_counts", ("month", "reason", "sponsor_class", "n"),
              ("date", "text", "text", "int4"), counts)  # fmt: skip
        cur.execute(
            """INSERT INTO serving.data_status (status_id, data_as_of, model_version,
              labeled_through, early_stops_after_labels) VALUES (1, %s, %s, %s, 42)""",
            (DAY, MODEL, DAY),
        )
        statement.load(cur, Path(statement.STATEMENT_PATH))
        _monitoring(cur)
        con.commit()
    return {"scored": len(latest), "unscored": len(missing), "display": len(display),
            "sponsors": len(sponsors)}  # fmt: skip


def _monitoring(cur: Any) -> None:
    """Step 17's aggregates (ADRs 0076 to 0080): replay and live grades, a week of drift and
    one gate decision, all synthetic."""
    grade = """INSERT INTO monitoring.graded_predictions (prediction_date, horizon_months,
        landmark_index, row_group, model_version, source, n_rows, n_early_stop, n_completed,
        n_censored, n_open, mean_risk, auc, brier, calibration_slope, calibration_intercept,
        lift_top_10, matured) VALUES (%s, %s, -1, 'all', %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s)"""
    for week in range(3):
        day = dt.date(2023, 1, 6) + dt.timedelta(days=7 * week)
        for months, auc in ((12, 0.71 + week / 100), (24, 0.69)):
            values = (day, months, "trialpulse-m4-replay-2023-01-01", "replay", 1000, 60, 200,
                      40, 700, 0.07, auc, 0.05, 0.95, -0.1, 2.4, True)  # fmt: skip
            cur.execute(grade, values)
    for months in (12, 24):
        cur.execute(grade, (DAY, months, MODEL, "live", 900, 0, 1, 0, 899, 0.08, None, 0.0,
                            None, None, None, False))  # fmt: skip
    for name, kind, value, drifted in (("enrollment_count", "feature", 0.03, False),
                                       ("months_since_last_update", "feature", 0.18, True),
                                       ("risk_12m", "score", 0.05, False)):  # fmt: skip
        cur.execute(
            """INSERT INTO monitoring.drift (week_date, column_name, kind, method, statistic,
               threshold, drifted, n_current) VALUES (%s, %s, %s, 'psi', %s, 0.1, %s, 900)""",
            (DAY, name, kind, value, drifted),
        )
    cur.execute(
        """INSERT INTO monitoring.promotions (decided_at, champion_version, challenger_version,
           promoted, reason, challenger_calibration_slope)
           VALUES ('2026-11-10 12:00:00+00', %s, 'trialpulse-m4/challenger', false,
           'not promoted: no window after the champion''s training data with matured outcomes
           yet', 0.97)""",
        (MODEL,),
    )


def _copy(cur: Any, table: str, columns: tuple[str, ...], types: tuple[str, ...],
          rows: list[Any]) -> None:  # fmt: skip
    with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
        copy.set_types(list(types))
        for row in rows:
            copy.write_row(row)
