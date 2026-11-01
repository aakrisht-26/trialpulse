"""The runner timing job (rule 3 of the Step 13 review, ADR 0045 item 6).

    uv run python -m trialpulse.live.timing --bundle <folder> --rows 5000

Before Neon exists, a manually dispatched workflow times what a weekly full run spends most
on, on the runner itself: the champion's predictions and its driver pass on rows generated
from the feature types (no dataset-derived row reaches the runner; driver time did not depend
on feature values on the laptop). It also prints the runner's CPU (`lscpu`) and checks that
API v2 answers from the runner: `/version` and one page of one record, counts only.
"""

import argparse
import datetime as dt
import shutil
import subprocess
import time as clock
from collections.abc import Sequence
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from trialpulse.cli import run
from trialpulse.config import load_project_config
from trialpulse.features.registry import BY_NAME, CATEGORY, FLAG
from trialpulse.ingest.ctgov_api import STUDIES_URL, ApiClient
from trialpulse.live.pull import data_date
from trialpulse.scoring.predict import Bundle, predict

SPONSOR_CLASSES = ("INDUSTRY", "OTHER", "NIH", "FED", "OTHER_GOV", "NETWORK", "INDIV")


def generated_rows(bundle: Bundle, n: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`n` served rows of the model's features drawn from their types: categories from the
    model's own levels, flags 0 or 1, numbers from 0 to 60 with one in ten missing. The
    scored trials (as `scoring.predict.predict` reads them) and their features."""
    rng = np.random.default_rng(seed)
    model = bundle.model
    data: dict[str, object] = {
        "trial_id": [f"NCT{i:08d}" for i in range(n)],
        "true_index": rng.integers(0, 15, n),
    }
    for name in model.features:
        kind = BY_NAME[name].kind
        if kind == CATEGORY:
            levels = list(model.levels.get(name) or ["NONE"])
            data[name] = rng.choice(np.array(levels, dtype=object), n)
        elif kind == FLAG:
            data[name] = rng.integers(0, 2, n).astype(float)
        else:
            values = rng.uniform(0, 60, n).round()
            values[rng.random(n) < 0.1] = np.nan
            data[name] = values
    data["landmark_index"] = np.minimum(np.asarray(data["true_index"]), 6).astype(float)
    data["sponsor_class"] = rng.choice(np.array(SPONSOR_CLASSES, dtype=object), n)
    ended = rng.integers(0, 40, n).astype(float)
    data["sponsor_prior_ended"] = ended
    data["sponsor_prior_stopped"] = np.floor(ended * rng.uniform(0, 0.4, n))
    features = pd.DataFrame(data)
    scored = features[["trial_id", "true_index"]].copy()
    scored["k_star"] = np.minimum(scored["true_index"], 6)
    scored["flagged"] = scored["true_index"] >= 7
    scored["content_hash"] = ["0" * 64] * n
    return scored, features


def time_scoring(bundle: Bundle, n: int, seed: int) -> dict[str, float]:
    """Seconds for the predictions alone and with the drivers, on generated rows."""
    scored, features = generated_rows(bundle, n, seed)
    day = dt.date.today()
    tick = clock.perf_counter()
    predict(scored, features, bundle, day, with_drivers=False)
    plain = clock.perf_counter() - tick
    tick = clock.perf_counter()
    _, seconds = predict(scored, features, bundle, day, with_drivers=True)
    return {"rows": float(n), "predict": plain, "with_drivers": clock.perf_counter() - tick,
            "drivers": seconds.get("drivers", float("nan"))}  # fmt: skip


def cpu_text() -> str:
    tool = shutil.which("lscpu")
    if tool is None:
        return "lscpu not found"
    return subprocess.run([tool], capture_output=True, text=True, check=False).stdout


def api_check(api: ApiClient) -> dict[str, object]:
    """Whether API v2 answers from here: its data date and one record's page (counts only)."""
    fields = "protocolSection.identificationModule.nctId"
    page = api.get_json(STUDIES_URL, {"pageSize": 1, "countTotal": "true", "fields": fields})
    return {"data_date": data_date(api).isoformat(), "total_count": page.get("totalCount"),
            "records": len(page.get("studies", []))}  # fmt: skip


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Time the champion's scoring on this machine.")
    parser.add_argument("--bundle", type=Path, required=True, help="the champion's bundle folder")
    parser.add_argument("--rows", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-api", action="store_true")
    args = parser.parse_args(argv)
    from trialpulse.scoring.bundle import load_bundle

    cfg = load_project_config()
    print(cpu_text())
    bundle = load_bundle(cfg, args.bundle)
    for name, value in time_scoring(bundle, args.rows, args.seed).items():
        print(f"{name}: {value:.1f}")
    if not args.no_api:
        api = ApiClient(httpx.Client(timeout=60), per_minute=cfg.live.requests_per_minute)
        for key, answer in api_check(api).items():
            print(f"api {key}: {answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
