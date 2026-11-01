"""The export adapter (ADR 0037): compact rows back into what the offline feature code reads.

Compact rows hold the phase the titles state, the intervention types and a keyed sponsor id
instead of titles, interventions and a sponsor key. Read through unchanged offline code
(`features.states.expose_versions`, then `features.build.compute_features`), they would
silently blank 14 of the 54 features. `register_export` gives that code:

- `live_export_versions`: canonical version rows with the whitelisted fields, the sponsor id
  in `sponsor_key` (an id without an identity is NULL, and `sponsor_is_individual` says
  so), the submitted status in `overall_status`, version numbers continuing each trial's
  history, and the feature hash in `content_hash`;
- `texts`: one stand-in title per phase, whose stated phase is that phase;
- `intervention_versions`: the types per version;
- the `versions` view over them.

So layer 5a of ADR 0038 and the laptop retrain of ADR 0040 run the offline path on live
rows. The offline sponsor pass then orders ties by the ids, as the live pass does.
"""

import hashlib
from collections.abc import Iterable, Mapping
from typing import Any

import duckdb
import pandas as pd

from trialpulse.cohort.rules import CohortRules
from trialpulse.contracts.versions import COLUMN_TYPES
from trialpulse.features import design
from trialpulse.features.states import expose_versions
from trialpulse.live.contract import COPIED

EXPORT_TABLE = "live_export_versions"
# A title whose stated phase is each label (`design.title_phase`).
PHASE_TITLES: dict[str, str] = {
    design.NO_PHASE: "No phase stated",
    "EARLY_PHASE1": "Phase 0",
    "PHASE1": "Phase 1",
    "PHASE1_PHASE2": "Phase 1/2",
    "PHASE2": "Phase 2",
    "PHASE2_PHASE3": "Phase 2/3",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
    design.OTHER_PHASES: "Phase 1/3",
}


def title_hash(phase: str) -> str:
    return hashlib.sha256(PHASE_TITLES[phase].encode("utf-8")).hexdigest()


def numbered(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rows with a version number: kept where given, else continuing the trial's highest
    number in (post date, order given)."""
    out = [dict(r) for r in rows]
    top: dict[str, int] = {}
    for r in out:
        if r.get("nct_version") is not None:
            top[r["nct_id"]] = max(top.get(r["nct_id"], -1), int(r["nct_version"]))
    for r in sorted(
        (r for r in out if r.get("nct_version") is None),
        key=lambda r: (r["nct_id"], r["effective_date"]),
    ):
        top[r["nct_id"]] = top.get(r["nct_id"], -1) + 1
        r["nct_version"] = top[r["nct_id"]]
    return out


def export_frame(rows: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    """Canonical version rows from compact rows (see the module text)."""
    out = []
    for r in numbered(rows):
        row: dict[str, Any] = dict.fromkeys(COLUMN_TYPES)
        row.update({name: r.get(name) for name in COPIED})
        row.update(
            nct_id=r["nct_id"],
            nct_version=r["nct_version"],
            source="api_v2",
            overall_status=r["status"],
            last_known_status=None,
            sponsor_key=r.get("sponsor_id"),
            sponsor_is_individual=r.get("sponsor_id") is None,
            official_title_hash=title_hash(r["title_phase"]),
            content_hash=r["feature_hash"],
        )
        row["_types"] = list(r.get("intervention_types") or [])
        out.append(row)
    return pd.DataFrame(out, columns=[*COLUMN_TYPES, "_types"])


def register_export(
    con: duckdb.DuckDBPyConnection, rows: Iterable[Mapping[str, Any]], rules: CohortRules
) -> None:
    """Create the export tables and the `versions` view in `con` (see the module text)."""
    frame = export_frame(rows)
    types = frame.pop("_types")
    source = frame.astype(object)  # noqa: F841 (read by DuckDB)
    columns = ", ".join(f"CAST({c} AS {t}) AS {c}" for c, t in COLUMN_TYPES.items())
    con.execute(f"CREATE OR REPLACE TABLE {EXPORT_TABLE} AS SELECT {columns} FROM source")
    texts = pd.DataFrame(  # noqa: F841 (read by DuckDB)
        {"text_hash": [title_hash(p) for p in PHASE_TITLES], "text": list(PHASE_TITLES.values())}
    )
    con.execute(
        "CREATE OR REPLACE TABLE texts AS SELECT CAST(text_hash AS VARCHAR) AS text_hash, "
        "CAST(text AS VARCHAR) AS text FROM texts"
    )
    lists = pd.DataFrame(  # noqa: F841 (read by DuckDB)
        {"nct_id": frame["nct_id"], "nct_version": frame["nct_version"], "types": types}
    )
    con.execute(
        """CREATE OR REPLACE TABLE intervention_versions AS
        SELECT CAST(nct_id AS VARCHAR) AS nct_id, CAST(nct_version AS BIGINT) AS nct_version,
          CAST(types AS VARCHAR[]) AS intervention_types
        FROM lists WHERE len(types) > 0"""
    )
    expose_versions(con, EXPORT_TABLE, rules)
