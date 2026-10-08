"""Build the point-in-time features (CLAUDE.md Step 7, Section 8).

    uv run python -m trialpulse.features.build

One row per landmark key, where the keys are the landmark rows of the final cohort and of
the cohort as of each walk-forward origin (ADR 0016). A feature at landmark L reads only
versions posted on or before L, so its value does not depend on which cohort the row comes
from, and each row is computed once.

Outputs, in data/features/:

- features.parquet: the key columns and every feature computed as of the landmark;
- text_keys.parquet: the hashes of the eligibility and summary texts in effect at each
  landmark (identifiers for the text components, not features);
- text_cache/: hashed word counts per distinct text, computed once and reused (a cache
  made under other settings, or one that does not match its own digest, is recomputed);
- origin_<date>/: what is fitted on that origin's training rows only (Section 6): the class
  rates of the sponsor track record, and the text components of every text of its training
  and evaluation rows;
- sensitivity.parquet: the current-record phase, for the labeled sensitivity analysis only
  (ADR 0006);
- build.json: row counts and checksums, and whether they match the previous build.

It also writes docs/features.md from the registry, with the share of missing values of each
feature among the landmark rows before 2018-01-01 (Section 10: nothing later may inform
modeling). The build reads the warehouse and the cohort files read-only and is
deterministic. DuckDB works in memory and spills what does not fit into data/duckdb_tmp/.
"""

import argparse
import datetime as dt
import hashlib
import json
import pickle
import time
import zlib
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import duckdb
import joblib
import numpy as np
import pandas as pd

from trialpulse.cli import RefusedError, run
from trialpulse.cohort.audit import MODELING_EDA_BEFORE
from trialpulse.cohort.build import COHORT_DIR, TRAINING_DIR_NAME
from trialpulse.cohort.rules import CohortRules
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.features import amendments, design
from trialpulse.features.competition import COMPETITION_TABLE, build_competition
from trialpulse.features.registry import (
    FEATURES,
    KEY_COLUMNS,
    TEXT_COMPONENTS,
    TEXT_FIELDS,
    built_columns,
    features_markdown,
)
from trialpulse.features.sponsor import TRACK_RECORD_TABLE, build_track_record
from trialpulse.features.states import (
    AT_LANDMARK_TABLE,
    KEYS_TABLE,
    build_at_landmark,
    build_states,
    expose_versions,
    month_index,
)
from trialpulse.features.text import (
    Sparse,
    TextComponents,
    counts_signature,
    eligibility_statistics_sql,
    hashed_counts,
)
from trialpulse.features.transforms import MISSING_CLASS, SponsorPriors
from trialpulse.ingest.history import RAW_HISTORY_DIR, config_glob
from trialpulse.parquet import checksum, write_table
from trialpulse.reports import write_text_if_changed
from trialpulse.warehouse.build import TEMP_DIR, WAREHOUSE_PATH

FEATURES_DIR = REPO_ROOT / "data" / "features"
FEATURES_NAME = "features.parquet"
TEXT_KEYS_NAME = "text_keys.parquet"
SENSITIVITY_NAME = "sensitivity.parquet"
BUILD_LOG_NAME = "build.json"
PRIORS_NAME = "sponsor_priors.json"
DOC_PATH = REPO_ROOT / "docs" / "features.md"
CURRENT_FIELDS_PATH = REPO_ROOT / "data" / "spike" / "current_fields.parquet"
FINAL_SOURCE = "final"
FEATURE_TABLE = "feature_table"
TEXT_KEYS_TABLE = "feature_text_keys"
KEY_SOURCES_TABLE = "feature_key_sources"
ORDER = "trial_id, landmark_index"
TEXT_CHUNK = 20_000
COMPONENT_COLUMNS: tuple[str, ...] = tuple(f"c{i:02d}" for i in range(1, TEXT_COMPONENTS + 1))


def connect(temp_dir: Path = TEMP_DIR) -> duckdb.DuckDBPyConnection:
    """An in-memory database that spills into `temp_dir` (inside data/, which git ignores),
    not into the working directory."""
    temp_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET temp_directory = '{temp_dir.as_posix()}'")
    return con


def origin_source(origin: dt.date) -> str:
    return f"origin_{origin.isoformat()}"


def components_name(field: str) -> str:
    return f"{field}_components.parquet"


def cohort_key_files(cohort_dir: Path, origins: Sequence[dt.date]) -> dict[str, Path]:
    """The landmark files whose rows need features: the final cohort and each origin's."""
    files = {FINAL_SOURCE: cohort_dir / "landmarks.parquet"}
    for origin in origins:
        files[origin_source(origin)] = (
            cohort_dir / TRAINING_DIR_NAME / origin_source(origin) / "landmarks.parquet"
        )
    return files


def register_keys(con: duckdb.DuckDBPyConnection, files: Mapping[str, Path]) -> None:
    """`feature_key_sources` (which cohort holds which landmark row) and `feature_keys`
    (each landmark row once)."""
    for path in files.values():
        if not path.is_file():
            raise RefusedError(
                f"{path} is missing. Create it with: uv run python -m trialpulse.cohort.build"
            )
    parts = " UNION ALL ".join(
        f"SELECT '{name}' AS source, trial_id, landmark_index, landmark_date "
        f"FROM read_parquet('{path.as_posix()}')"
        for name, path in files.items()
    )
    con.execute(f"CREATE OR REPLACE TEMP TABLE {KEY_SOURCES_TABLE} AS {parts}")
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE {KEYS_TABLE} AS
        SELECT DISTINCT trial_id, landmark_index, landmark_date FROM {KEY_SOURCES_TABLE}"""
    )
    clash = con.execute(
        f"""SELECT count(*) FROM (SELECT trial_id, landmark_index FROM {KEYS_TABLE}
        GROUP BY 1, 2 HAVING count(*) > 1)"""
    ).fetchone()
    if clash and clash[0]:
        raise RefusedError(f"{clash[0]} landmark keys have two dates in the cohort files")


def prepare(
    con: duckdb.DuckDBPyConnection,
    warehouse: Path,
    interventions: str,
    files: Mapping[str, Path],
    rules: CohortRules,
) -> None:
    """Attach the warehouse and create what the feature queries read: the whitelisted
    `versions` view, `texts`, `intervention_versions` and the landmark keys."""
    if not warehouse.is_file():
        raise RefusedError(
            f"no warehouse at {warehouse}; run: uv run python -m trialpulse.warehouse.build"
        )
    if not list(Path(interventions).parent.glob(Path(interventions).name)):
        raise RefusedError(
            f"no file matches {interventions}. Download the interventions config with: "
            "uv run python -m trialpulse.ingest.history --config interventions"
        )
    con.execute(f"ATTACH '{warehouse.as_posix()}' AS wh (READ_ONLY)")
    expose_versions(con, "wh.versions", rules)
    con.execute("CREATE OR REPLACE TEMP VIEW texts AS SELECT text_hash, text FROM wh.texts")
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE intervention_versions AS
        SELECT nct_id, nct_version, list(intervention_type ORDER BY intervention_type)
          AS intervention_types
        FROM read_parquet('{interventions}') GROUP BY nct_id, nct_version"""
    )
    register_keys(con, files)


def _title_phases(con: duckdb.DuckDBPyConnection) -> None:
    """`feature_title_phase`: the phase each title in effect at a landmark states."""
    rows = con.execute(
        f"""SELECT t.text_hash, t.text FROM texts t JOIN (
          SELECT official_title_hash AS h FROM {AT_LANDMARK_TABLE}
          UNION SELECT brief_title_hash FROM {AT_LANDMARK_TABLE}) d ON d.h = t.text_hash"""
    ).fetchall()
    frame = pd.DataFrame(
        {
            "text_hash": pd.Series([h for h, _ in rows], dtype="object"),
            "phase": pd.Series([design.title_phase(text) for _, text in rows], dtype="object"),
        }
    )
    con.register("title_phase_frame", frame)
    con.execute(
        """CREATE OR REPLACE TEMP TABLE feature_title_phase AS
        SELECT CAST(text_hash AS VARCHAR) AS text_hash, CAST(phase AS VARCHAR) AS phase
        FROM title_phase_frame"""
    )
    con.unregister("title_phase_frame")


def compute_features(
    con: duckdb.DuckDBPyConnection, rules: CohortRules, cfg: ProjectConfig
) -> None:
    """From `versions`, `texts`, `intervention_versions` and `feature_keys` to
    `feature_table` (keys and features) and `feature_text_keys` (text hashes in effect)."""
    build_states(con, rules)
    build_at_landmark(con, cfg.features.recent_versions_months, rules.spacing_months)
    build_competition(con, rules)
    build_track_record(
        con,
        rules,
        cfg.features.sponsor_alias_min_trials,
        cfg.features.sponsor_alias_wait_days,
    )
    _title_phases(con)
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE feature_eligibility AS
        SELECT t.text_hash, {eligibility_statistics_sql("t.text")}
        FROM texts t JOIN (SELECT DISTINCT eligibility_criteria_hash AS h
                           FROM {AT_LANDMARK_TABLE}) d ON d.h = t.text_hash"""
    )
    row_level = {**design.EXPRESSIONS, **amendments.EXPRESSIONS}
    own = ", ".join(f"{sql} AS {name}" for name, sql in row_level.items())
    interventions = ", ".join(
        f"{sql} AS {name}"
        for name, sql in design.intervention_expressions("i.intervention_types").items()
    )
    months = f"{month_index('landmark_date')} - {month_index('study_first_post_date')}"
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE {FEATURE_TABLE} AS
        WITH a AS (
          SELECT trial_id, landmark_index, landmark_date, nct_id, nct_version, {own},
            coalesce(lead_sponsor_class, '{MISSING_CLASS}') AS sponsor_class, organization_class,
            maximum_age_years - minimum_age_years AS eligibility_age_span_years,
            {months} AS months_since_t0,
            official_title_hash, brief_title_hash, eligibility_criteria_hash
          FROM {AT_LANDMARK_TABLE}
        )
        SELECT a.* EXCLUDE (nct_id, nct_version, official_title_hash, brief_title_hash,
            eligibility_criteria_hash),
          CASE WHEN op.phase IS NOT NULL AND op.phase <> '{design.NO_PHASE}' THEN op.phase
               ELSE coalesce(bp.phase, '{design.NO_PHASE}') END AS title_phase,
          {interventions},
          r.sponsor_has_identity, r.sponsor_prior_registrations, r.sponsor_prior_ended,
          r.sponsor_prior_stopped, c.open_interventional_trials,
          e.eligibility_n_inclusion, e.eligibility_n_exclusion, e.eligibility_length,
          e.eligibility_n_thresholds
        FROM a
        LEFT JOIN {TRACK_RECORD_TABLE} r USING (trial_id, landmark_index)
        LEFT JOIN {COMPETITION_TABLE} c USING (landmark_date)
        LEFT JOIN feature_eligibility e ON e.text_hash = a.eligibility_criteria_hash
        LEFT JOIN feature_title_phase op ON op.text_hash = a.official_title_hash
        LEFT JOIN feature_title_phase bp ON bp.text_hash = a.brief_title_hash
        LEFT JOIN intervention_versions i
          ON i.nct_id = a.nct_id AND i.nct_version = a.nct_version"""
    )
    hashes = ", ".join(f"{column} AS {field}_hash" for field, column in TEXT_FIELDS.items())
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE {TEXT_KEYS_TABLE} AS
        SELECT trial_id, landmark_index, {hashes} FROM {AT_LANDMARK_TABLE}"""
    )


# Text counts and what is fitted per origin --------------------------------------------------


def _text_hashes(con: duckdb.DuckDBPyConnection, field: str, where: str = "") -> list[str]:
    rows = con.execute(
        f"""SELECT DISTINCT k.{field}_hash FROM {TEXT_KEYS_TABLE} k {where}
        {"AND" if where else "WHERE"} k.{field}_hash IS NOT NULL ORDER BY 1"""
    ).fetchall()
    return [row[0] for row in rows]


def _matrix_digest(hashes: Sequence[str], counts: Sparse) -> str:
    digest = hashlib.sha256()
    digest.update("\n".join(hashes).encode("utf-8"))
    for part in (counts.indptr, counts.indices, counts.data):
        digest.update(np.ascontiguousarray(part).tobytes())
    return digest.hexdigest()


def cache_path(cache_dir: Path, field: str) -> Path:
    return cache_dir / f"{field}_counts.joblib"


def load_cached_counts(path: Path, signature: str) -> tuple[list[str], Sparse] | None:
    """The cached counts, or None when they cannot be trusted: no file, a file that cannot
    be read, counts made under another signature, or contents that do not match the digest
    stored with them. The file is written by this build only, inside data/."""
    if not path.is_file():
        return None
    try:
        cached = joblib.load(path)
        hashes, counts = list(cached["hashes"]), cached["counts"]
        trusted = (
            cached["signature"] == signature
            and counts.shape[0] == len(hashes)
            and cached["digest"] == _matrix_digest(hashes, counts)
        )
    except (OSError, EOFError, KeyError, TypeError, ValueError, AttributeError, IndexError,
            pickle.UnpicklingError, zlib.error):  # fmt: skip
        return None
    return (hashes, counts) if trusted else None


def store_cached_counts(path: Path, signature: str, hashes: list[str], counts: Sparse) -> None:
    """Write the cache in one step: to a file beside it, then renamed over it, so that an
    interrupted build leaves the old cache or none, never half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writing = path.with_name(path.name + ".writing")
    content = {
        "signature": signature,
        "hashes": hashes,
        "counts": counts,
        "digest": _matrix_digest(hashes, counts),
    }
    joblib.dump(content, writing, compress=3)
    writing.replace(path)


def text_counts(
    con: duckdb.DuckDBPyConnection, field: str, cache_dir: Path, hash_bits: int
) -> tuple[list[str], Sparse]:
    """Hashed word counts of every distinct text of one field in effect at a landmark, in
    hash order. They are computed once: a cache that already holds these texts is reused."""
    needed = _text_hashes(con, field)
    path = cache_path(cache_dir, field)
    signature = counts_signature(hash_bits)
    cached = load_cached_counts(path, signature)
    if cached is not None:
        position = {h: i for i, h in enumerate(cached[0])}
        if all(h in position for h in needed):
            return needed, cached[1][[position[h] for h in needed]]
    cursor = con.execute(
        f"""SELECT t.text FROM (SELECT DISTINCT {field}_hash AS h FROM {TEXT_KEYS_TABLE}
                              WHERE {field}_hash IS NOT NULL) d
        LEFT JOIN texts t ON t.text_hash = d.h ORDER BY d.h"""
    )

    def texts() -> Iterator[str]:
        while rows := cursor.fetchmany(TEXT_CHUNK):
            for (text,) in rows:
                yield text or ""

    counts = hashed_counts(texts(), hash_bits)
    if counts.shape[0] != len(needed):
        raise RuntimeError(f"{field}: {counts.shape[0]} texts hashed for {len(needed)} hashes")
    store_cached_counts(path, signature, needed, counts)
    return needed, counts


def evaluation_window(origin: dt.date, months: int) -> tuple[dt.date, dt.date]:
    from trialpulse.cohort.rules import add_months

    return origin, add_months(origin, months)


def origin_rows_sql(origin: dt.date, window_months: int) -> dict[str, str]:
    """SQL conditions on `feature_key_sources s` for an origin's training and evaluation rows."""
    start, end = evaluation_window(origin, window_months)
    return {
        "training": f"s.source = '{origin_source(origin)}'",
        "evaluation": (
            f"s.source = '{FINAL_SOURCE}' AND s.landmark_date >= DATE '{start.isoformat()}' "
            f"AND s.landmark_date < DATE '{end.isoformat()}'"
        ),
    }


def fit_origin(
    con: duckdb.DuckDBPyConnection,
    cfg: ProjectConfig,
    origin: dt.date,
    training_landmarks: Path,
    counts: Mapping[str, tuple[list[str], Sparse]],
    out_dir: Path,
) -> dict[str, tuple[int, str]]:
    """Fit on the origin's training rows only, and write the class rates and the text
    components of the texts of its training and evaluation rows."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = con.execute(
        f"""SELECT trial_id, landmark_index, landmark_date, event, event_date, stratum
        FROM read_parquet('{training_landmarks.as_posix()}') ORDER BY trial_id, landmark_index"""
    ).fetchnumpy()
    priors = SponsorPriors.fit(
        np.asarray(rows["trial_id"]).astype(str),
        np.asarray(rows["landmark_index"], dtype=np.int64),
        np.asarray(rows["landmark_date"]).astype("datetime64[D]"),
        np.asarray(rows["event"], dtype=np.int64),
        np.asarray(rows["event_date"]).astype("datetime64[D]"),
        np.asarray(rows["stratum"]).astype(str),
        origin,
        cfg.features.sponsor_prior_weight,
    )
    text = json.dumps(priors.to_json(), indent=2, sort_keys=True) + "\n"
    (out_dir / PRIORS_NAME).write_text(text, encoding="utf-8", newline="\n")
    written = {PRIORS_NAME: (len(priors.classes), hashlib.sha256(text.encode("utf-8")).hexdigest())}
    conditions = origin_rows_sql(origin, cfg.walk_forward.eval_window_months)
    join = f"JOIN {KEY_SOURCES_TABLE} s USING (trial_id, landmark_index) WHERE "
    for field in TEXT_FIELDS:
        hashes, matrix = counts[field]
        position = {h: i for i, h in enumerate(hashes)}
        train = _text_hashes(con, field, join + conditions["training"])
        both = _text_hashes(
            con, field, join + f"(({conditions['training']}) OR ({conditions['evaluation']}))"
        )
        model = TextComponents(cfg.features.text_components, cfg.seeds.default)
        model.fit(matrix[[position[h] for h in train]])
        values = model.transform(matrix[[position[h] for h in both]])
        frame = pd.DataFrame(values, columns=list(COMPONENT_COLUMNS))
        frame.insert(0, "text_hash", pd.Series(both, dtype="object"))
        con.register("components_frame", frame)
        path = out_dir / components_name(field)
        written[path.name] = write_table(
            con,
            "components_frame",
            path,
            ["CAST(text_hash AS VARCHAR) AS text_hash", *COMPONENT_COLUMNS],
            "1",
        )
        con.unregister("components_frame")
    return written


# The report and the command -----------------------------------------------------------------


def null_rates(con: duckdb.DuckDBPyConnection, source: str) -> dict[str, float | None]:
    """The share of missing values per built feature, among the rows of one cohort."""
    columns = built_columns()
    select = ", ".join(f"avg(CASE WHEN f.{c} IS NULL THEN 1.0 ELSE 0.0 END)" for c in columns)
    join = (
        f"JOIN {KEY_SOURCES_TABLE} s USING (trial_id, landmark_index) WHERE s.source = '{source}'"
    )
    row = con.execute(f"SELECT {select}, count(*) FROM {FEATURE_TABLE} f {join}").fetchone()
    if row is None or not row[-1]:
        return dict.fromkeys(columns)
    return dict(zip(columns, (float(v) for v in row[:-1]), strict=True))


def document(
    con: duckdb.DuckDBPyConnection, cfg: ProjectConfig, stamp: str, nulls_source: str
) -> str:
    rates = null_rates(con, nulls_source)
    counts = con.execute(
        f"SELECT count(*), count(DISTINCT trial_id) FROM {FEATURE_TABLE}"
    ).fetchone() or (0, 0)
    modeling = con.execute(
        f"SELECT count(*) FROM {KEY_SOURCES_TABLE} WHERE source = '{nulls_source}'"
    ).fetchone() or (0,)
    registered = sum(1 for f in FEATURES if f.main)
    families = len({f.family for f in FEATURES if f.main})
    fitted = sum(1 for f in FEATURES if f.fitted)
    before = MODELING_EDA_BEFORE.isoformat()
    notes = [
        "- **Source:** ClinicalTrials.gov registry records, through the version-history dataset "
        f"`{cfg.dataset.repo_id}` (license CC-BY-NC-4.0), configs `core` and `interventions`: "
        f"{stamp}. Data as of {cfg.dataset.cutoff}. TrialPulse normalizes the records and "
        "derives every feature.",
        f"- **Rows:** {counts[0]:,} landmark rows of {counts[1]:,} trials: the landmark rows of "
        "the final cohort and of the cohort as of each walk-forward origin (ADR 0016), each "
        "computed once.",
        f"- **Features:** {registered} main features in {families} "
        f"families, of which {fitted} are fitted per origin, and "
        f"{sum(1 for f in FEATURES if not f.main)} kept apart for a sensitivity analysis.",
        "- **Point-in-time (Section 8, rule 1).** A feature at landmark L reads only versions "
        "posted on or before L, of this trial and of every other trial. The sponsor track "
        "record reads only versions posted before L. The leakage tests alter every later "
        "version and require the features at L to stay the same.",
        "- **Whitelist (rule 2).** Features read only the source fields listed here. Every one "
        "has version history, except the sensitivity set. The reason a trial stopped, the "
        "type of a post date and sponsor names are never read. Nor is the registry's UNKNOWN "
        "label, which it computes long after the version it sits on: the status read is the "
        "submitted one (ADR 0014).",
        "- **Calendar months.** Every feature that compares dates compares calendar months, so "
        "that the registry's change from month to day precision in 2017 cannot become a signal.",
        "- **Fitted per origin (Section 6).** The class rates behind `sponsor_stop_rate` and the "
        "text components are fitted on each origin's training rows only and stored per origin "
        "in `data/features/origin_<date>/`.",
        f"- **Missing** is the share of landmark rows without a value, among the "
        f"{modeling[0]:,} rows of the cohort as of {before}. Rows of later landmarks are left "
        "out of this report on purpose (Section 10).",
        "- **Elevated early-stop risk** is an operational statement about a trial record. "
        "Nothing here says whether a treatment works.",
    ]
    return features_markdown(rates, notes)


def write_sensitivity(
    con: duckdb.DuckDBPyConnection, current_fields: Path, path: Path
) -> tuple[int, str] | None:
    """The current-record phase of the trials with a landmark row, if the Step 2 snapshot is
    on disk. Sensitivity analysis only (ADR 0006)."""
    if not current_fields.is_file():
        return None
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE feature_sensitivity AS
        SELECT c.nct_id AS trial_id,
          coalesce(array_to_string(list_sort(c.phases), '|'), '') AS phase_current_record
        FROM read_parquet('{current_fields.as_posix()}') c
        WHERE c.nct_id IN (SELECT DISTINCT trial_id FROM {KEYS_TABLE})"""
    )
    return write_table(
        con, "feature_sensitivity", path, ["trial_id", "phase_current_record"], "trial_id"
    )


def warehouse_stamp(con: duckdb.DuckDBPyConnection) -> str:
    info = dict(con.execute("SELECT key, value FROM wh.build_info").fetchall())
    return (
        f"dataset revision {info.get('dataset.revision', 'not recorded')}, warehouse schema "
        f"version {info.get('schema_version')}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the point-in-time features.")
    parser.add_argument("--warehouse", type=Path, default=WAREHOUSE_PATH)
    parser.add_argument("--cohort-dir", type=Path, default=COHORT_DIR)
    parser.add_argument("--interventions", default=None, help="glob of the interventions config")
    parser.add_argument("--current-fields", type=Path, default=CURRENT_FIELDS_PATH)
    parser.add_argument("--out-dir", type=Path, default=FEATURES_DIR)
    parser.add_argument("--doc", type=Path, default=DOC_PATH)
    parser.add_argument("--temp-dir", type=Path, default=TEMP_DIR, help="where DuckDB spills")
    args = parser.parse_args(argv)
    cfg = load_project_config()
    rules = CohortRules.from_config(cfg)
    started = time.monotonic()
    origins = [origin.date for origin in cfg.walk_forward.origins]
    files = cohort_key_files(args.cohort_dir, origins)
    nulls_source = origin_source(MODELING_EDA_BEFORE)
    if nulls_source not in files:
        # Section 10: the report of missing values may read only rows of the cohort as of
        # that date. Without that cohort there is no report, and no fallback to all rows.
        raise RefusedError(
            f"{MODELING_EDA_BEFORE} is not a walk-forward origin in config/project.yaml, so "
            "the cohort as of that date does not exist and docs/features.md cannot report "
            "missing values without reading later rows"
        )
    interventions = args.interventions or config_glob(
        RAW_HISTORY_DIR, str(cfg.dataset.revision), "interventions"
    )
    out_dir: Path = args.out_dir
    tables: dict[str, dict[str, Any]] = {}

    def record(name: str, result: tuple[int, str]) -> None:
        tables[name] = {"rows": result[0], "checksum": result[1]}

    with connect(args.temp_dir) as con:
        prepare(con, args.warehouse, interventions, files, rules)
        compute_features(con, rules, cfg)
        columns = [*KEY_COLUMNS, *built_columns()]
        record(
            FEATURES_NAME, write_table(con, FEATURE_TABLE, out_dir / FEATURES_NAME, columns, ORDER)
        )
        text_columns = ["trial_id", "landmark_index", *(f"{field}_hash" for field in TEXT_FIELDS)]
        record(
            TEXT_KEYS_NAME,
            write_table(con, TEXT_KEYS_TABLE, out_dir / TEXT_KEYS_NAME, text_columns, ORDER),
        )
        seconds = time.monotonic() - started
        print(f"features: {tables[FEATURES_NAME]['rows']:,} rows in {seconds:.0f} s")
        counts = {}
        for field in TEXT_FIELDS:
            counts[field] = text_counts(
                con, field, out_dir / "text_cache", cfg.features.text_hash_bits
            )
            record(
                f"text_cache/{field}",
                (len(counts[field][0]), _matrix_digest(*counts[field])),
            )
        print(f"text counts ready after {time.monotonic() - started:.0f} s")
        for origin in origins:
            folder = origin_source(origin)
            written = fit_origin(con, cfg, origin, files[folder], counts, out_dir / folder)
            for name, result in written.items():
                record(f"{folder}/{name}", result)
            print(f"fitted {folder} after {time.monotonic() - started:.0f} s")
        sensitivity = write_sensitivity(con, args.current_fields, out_dir / SENSITIVITY_NAME)
        if sensitivity:
            record(SENSITIVITY_NAME, sensitivity)
        text = document(con, cfg, warehouse_stamp(con), nulls_source)
    changed = write_text_if_changed(args.doc, text)
    log_path = out_dir / BUILD_LOG_NAME
    previous = (
        json.loads(log_path.read_text(encoding="utf-8")).get("tables")
        if log_path.is_file()
        else None
    )
    identical = None if previous is None else previous == tables
    log = {
        "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "duration_seconds": round(time.monotonic() - started, 1),
        "dataset_revision": cfg.dataset.revision,
        "identical_to_previous": identical,
        "tables": tables,
    }
    log_path.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
    for name, entry in tables.items():
        print(f"{name}: {entry['rows']:,} rows, checksum {entry['checksum']}")
    print(f"{args.doc}: {'written' if changed else 'up to date'}")
    verdict = {True: "yes", False: "NO", None: "no previous build"}[identical]
    print(f"Identical to the previous build: {verdict}")
    print(f"Built in {log['duration_seconds']} s.")
    return 0


# `checksum` is re-exported for the tests that compare two builds.
__all__ = ["checksum", "compute_features", "main", "prepare"]

if __name__ == "__main__":
    raise SystemExit(run(main))
