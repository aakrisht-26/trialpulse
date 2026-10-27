"""Training-serving parity (CLAUDE.md Step 13): on a historical date, score(as_of_date) with
a development origin's champion reproduces the evaluation harness's saved scores of the
landmark rows dated that day. Runs on the real data and the cached development model
(written by `uv run python -m trialpulse.scoring.checks`); skipped where they are missing.
Development origins only: no locked origin is read."""

import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from trialpulse.config import load_project_config
from trialpulse.eval.walkforward import FEATURES_DIR, PREDICTIONS_DIR
from trialpulse.explain.report import load_cached
from trialpulse.features.transforms import SponsorPriors
from trialpulse.scoring import checks, score
from trialpulse.warehouse.build import WAREHOUSE_PATH

ORIGIN = dt.date(2016, 1, 1)
DAY = dt.date(2016, 6, 30)  # a busy landmark date of the 2016 evaluation window
SAVED = PREDICTIONS_DIR / "m4_2016" / "origin_2016-01-01.parquet"
PRIORS = FEATURES_DIR / "origin_2016-01-01" / "sponsor_priors.json"
SERVE_EVERY_BAND = {
    "between_landmarks": "B",
    "bands": {band: {"served": True} for band, _, _ in checks.BANDS},
}


@pytest.mark.slow
def test_scores_on_a_landmark_day_equal_the_harness_scores() -> None:
    cfg = load_project_config()
    model = load_cached(cfg, ORIGIN, checks.MODELS_DIR)
    if model is None or not SAVED.is_file() or not WAREHOUSE_PATH.is_file():
        pytest.skip("needs the real data and the cached development model of 2016")
    saved = pd.read_parquet(SAVED)
    saved = saved[pd.to_datetime(saved["landmark_date"]).dt.date == DAY]
    assert len(saved) > 100
    priors = SponsorPriors.from_json(json.loads(PRIORS.read_text(encoding="utf-8")))
    bundle = score.Bundle(model, priors, "m4-2016", "development")
    result = score.score(cfg, DAY, bundle, SERVE_EVERY_BAND, with_drivers=False,
                         only=set(saved["trial_id"]))  # fmt: skip
    joined = saved.merge(
        result.scores, left_on=["trial_id", "landmark_index"],
        right_on=["nct_id", "true_index"], how="left",
    )  # fmt: skip
    missing = int(joined["risk_12m"].isna().sum())
    assert missing == 0, f"{missing} harness rows dated {DAY} were not scored"
    for months in (12, 24):
        gap = np.abs(joined[f"risk_{months}m"] - joined[f"score_{months}"]).max()
        assert gap <= 1e-12, (months, gap)
