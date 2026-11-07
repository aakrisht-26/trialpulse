"""The real API v2 pull from the cutoff (ADR 0041, decision 2), kept on the laptop.

    uv run python -m trialpulse.gap.pull

Every record with LastUpdatePostDate from the cutoff minus `live.overlap_days` through API
v2's data date, at 30 requests a minute, through the daily job's own client, filter and
conversion (`live.pull.pull_delta`, `live.pull.convert`). Nothing is written to a database.
Under data/gap/api/<data date>/:

- `studies.jsonl.gz`: the records as pulled, already scrubbed of investigators and contacts
  (`scrub_personal_data`, as the job does before anything else), so no personal name is kept;
- `canonical.parquet` and `texts.parquet`: the canonical rows (`canonical_from_api_v2`) and
  their normalized texts by hash, for parity layer 5b;
- `compact.parquet`: the compact rows the job would ingest, under the real SPONSOR_ID_KEY;
- `quarantine.parquet` and `summary.json`: the records the contract refused, with reasons,
  and the counts.

It prints counts only.
"""

import argparse
import datetime as dt
import gzip
import json
import time as clock
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from trialpulse.cli import RefusedError, run
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import REPO_ROOT, ProjectConfig, Secrets, load_project_config
from trialpulse.contracts.versions import canonical_from_api_v2
from trialpulse.ingest.ctgov_api import ApiClient
from trialpulse.live import pull

GAP_DIR = REPO_ROOT / "data" / "gap"
PER_MINUTE = 30


def pull_since(cfg: ProjectConfig) -> dt.date:
    if cfg.dataset.cutoff is None:
        raise RefusedError("config/project.yaml records no data cutoff")
    return cfg.dataset.cutoff - dt.timedelta(days=cfg.live.overlap_days)


def canonical_frames(studies: Sequence[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The canonical rows of the records that convert, and their texts by hash."""
    rows, texts = [], {}
    for study in studies:
        try:
            row, text = canonical_from_api_v2(study)
        except Exception:  # counted by the job's own conversion as quarantined
            continue
        rows.append(row)
        texts.update(text)
    frame = pd.DataFrame(rows)
    return frame, pd.DataFrame({"text_hash": list(texts), "text": list(texts.values())})


def run_pull(
    api: Any, cfg: ProjectConfig, secret: bytes, out_root: Path = GAP_DIR
) -> dict[str, Any]:
    from trialpulse.ingest.history import refuse_on_a_runner

    refuse_on_a_runner()  # the pull is kept on the laptop (ADR 0040, ADR 0041)
    started = clock.perf_counter()
    rules = CohortRules.from_config(cfg)
    day = pull.data_date(api)
    since = pull_since(cfg)
    pulled = pull.pull_delta(api, since, day, cfg.live.page_size)
    pulled_seconds = clock.perf_counter() - started
    converted = pull.convert(pulled.studies, rules, secret)
    canonical, texts = canonical_frames(pulled.studies)
    out = out_root / "api" / day.isoformat()
    out.mkdir(parents=True, exist_ok=True)
    with gzip.open(out / "studies.jsonl.gz", "wt", encoding="utf-8") as handle:
        for study in pulled.studies:
            handle.write(json.dumps(study, sort_keys=True) + "\n")
    canonical.to_parquet(out / "canonical.parquet", index=False)
    texts.to_parquet(out / "texts.parquet", index=False)
    pd.DataFrame(converted.rows).to_parquet(out / "compact.parquet", index=False)
    quarantine_columns = ["nct_id", "effective_date", "payload_hash", "reasons"]
    quarantined = pd.DataFrame(converted.quarantined, columns=quarantine_columns)
    quarantined.to_parquet(out / "quarantine.parquet", index=False)
    posted = pd.to_datetime(canonical["effective_date"]) if len(canonical) else pd.Series([])
    summary = {
        "data_date": day.isoformat(),
        "since": since.isoformat(),
        "records": len(pulled.studies),
        "api_total": pulled.total_count,
        "pages": pulled.pages,
        "trials": int(canonical["nct_id"].nunique()) if len(canonical) else 0,
        "post_dates": {"first": str(posted.min().date()), "last": str(posted.max().date())}
        if len(posted) else {},
        "converted": len(converted.rows),
        "quarantined": len(converted.quarantined),
        "quarantine_reasons": dict(Counter(q["reasons"] for q in converted.quarantined)),
        "seconds": {"pull": round(pulled_seconds, 1),
                    "total": round(clock.perf_counter() - started, 1)},
        "per_minute": PER_MINUTE,
    }  # fmt: skip
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The API v2 pull of the gap (ADR 0041).")
    parser.parse_args(argv)
    cfg = load_project_config()
    key = Secrets().sponsor_id_key
    if key is None:
        raise RefusedError("SPONSOR_ID_KEY is not set (.env)")
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        summary = run_pull(ApiClient(client, per_minute=PER_MINUTE), cfg,
                           key.get_secret_value().encode("utf-8"))  # fmt: skip
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
