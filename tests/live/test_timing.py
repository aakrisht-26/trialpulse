"""The runner timing job (ADR 0045 item 6) on a small M4 fitted on simulated rows: generated
rows go through the real prediction and driver pass, and no registry data is involved."""

import httpx
import numpy as np
import respx
from tenacity import wait_none

from trialpulse.config import ProjectConfig
from trialpulse.features.transforms import SponsorPriors
from trialpulse.ingest.ctgov_api import STUDIES_URL, VERSION_URL, ApiClient
from trialpulse.live import timing
from trialpulse.scoring.predict import Bundle

from ..scoring.conftest import explain_helpers
from .conftest import CFG


def _bundle(cfg: ProjectConfig) -> Bundle:
    model = explain_helpers._fitted(cfg, seed=1)
    priors = SponsorPriors(10, 0.08, {"INDUSTRY": 0.2, "OTHER": 0.05}, {}, {})
    return Bundle(model, priors, "trialpulse-m4", "test/0", {})


def test_generated_rows_are_scored_with_drivers() -> None:
    bundle = _bundle(CFG)
    scored, features = timing.generated_rows(bundle, 300, seed=1)
    assert len(scored) == 300
    assert set(bundle.model.features) <= set(features.columns)
    assert features["landmark_index"].max() <= 6
    assert np.isnan(features.select_dtypes("number").to_numpy()).any()  # some missing values
    seconds = timing.time_scoring(bundle, 300, seed=1)
    assert seconds["rows"] == 300
    assert seconds["with_drivers"] >= seconds["drivers"] > 0


@respx.mock
def test_the_api_check_reports_counts_only() -> None:
    respx.get(VERSION_URL).mock(
        return_value=httpx.Response(200, json={"dataTimestamp": "2026-10-30T09:00:04"})
    )
    respx.get(STUDIES_URL).mock(
        return_value=httpx.Response(200, json={"totalCount": 580000, "studies": [{"x": 1}]})
    )
    api = ApiClient(httpx.Client(), per_minute=60_000, wait=wait_none(), sleep=lambda _: None)
    assert timing.api_check(api) == {"data_date": "2026-10-30", "total_count": 580000,
                                     "records": 1}  # fmt: skip
