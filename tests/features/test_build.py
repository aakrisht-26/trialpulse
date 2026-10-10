"""The feature build end to end on a synthetic registry on disk: reproducible outputs, what
is fitted per origin, and the matrix a model reads."""

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from trialpulse.cli import RefusedError
from trialpulse.cohort import build as cohort_build
from trialpulse.config import ProjectConfig
from trialpulse.features import build as features_build
from trialpulse.features import frame
from trialpulse.features import registry as reg
from trialpulse.features.leakage import REWRITTEN_INTERVENTIONS, REWRITTEN_VERSIONS, rewrite_after
from trialpulse.features.text import counts_signature, hashed_counts
from trialpulse.features.transforms import SponsorPriors

from .conftest import RULES, Registry, load_interventions, load_texts, load_versions, registry

ORIGIN = dt.date(2017, 1, 1)
LATER_ORIGIN = dt.date(2019, 1, 1)


@dataclass(frozen=True)
class Built:
    root: Path
    args: list[str]
    first: dict
    second: dict

    @property
    def features_dir(self) -> Path:
        return self.root / "features"

    @property
    def cohort_dir(self) -> Path:
        return self.root / "cohort"


def _write_inputs(root: Path, data: Registry, rewrite_from: dt.date | None = None) -> list[str]:
    """A warehouse file, an interventions config and a current-record snapshot under `root`,
    then the cohort build. With `rewrite_from`, every version posted on or after that day is
    rewritten first. Returns the arguments of the feature build."""
    root.mkdir(parents=True, exist_ok=True)
    warehouse = root / "warehouse.duckdb"
    interventions = root / "interventions"
    interventions.mkdir()
    with duckdb.connect(str(warehouse)) as con:
        load_versions(con, data.versions, "versions")
        load_texts(con, data.texts)
        load_interventions(con, data.interventions)
        if rewrite_from is not None:
            day = rewrite_from - dt.timedelta(days=1)
            rewrite_after(con, "versions", "base_interventions", day, RULES)
            # The rewrite is a pair of views over the two tables: copy them, then swap.
            con.execute(f"CREATE TABLE new_versions AS SELECT * FROM {REWRITTEN_VERSIONS}")
            con.execute(
                f"CREATE TABLE new_interventions AS SELECT * FROM {REWRITTEN_INTERVENTIONS}"
            )
            con.execute(f"DROP VIEW {REWRITTEN_VERSIONS}")
            con.execute(f"DROP VIEW {REWRITTEN_INTERVENTIONS}")
            con.execute("DROP VIEW IF EXISTS intervention_versions")
            con.execute("DROP TABLE versions")
            con.execute("DROP TABLE base_interventions")
            con.execute("ALTER TABLE new_versions RENAME TO versions")
            con.execute("ALTER TABLE new_interventions RENAME TO base_interventions")
        con.execute(
            f"""COPY (SELECT nct_id, nct_version, 'name' AS intervention_name,
              unnest(intervention_types) AS intervention_type, NULL::VARCHAR AS description
            FROM base_interventions) TO '{(interventions / "part-0.parquet").as_posix()}'
            (FORMAT parquet)"""
        )
        con.execute(
            f"""COPY (SELECT DISTINCT nct_id, ['PHASE2'] AS phases FROM versions
            WHERE hash(nct_id) % 7 <> 0) TO '{(root / "current_fields.parquet").as_posix()}'
            (FORMAT parquet)"""
        )
        con.execute("DROP VIEW IF EXISTS intervention_versions")
        con.execute("DROP TABLE base_interventions")
        con.execute("CREATE TABLE build_info (key VARCHAR, value VARCHAR)")
        con.execute(
            "INSERT INTO build_info VALUES ('schema_version', '3'), "
            "('dataset.revision', 'synthetic')"
        )
    cohort_args = ["--warehouse", str(warehouse), "--out-dir", str(root / "cohort"),
                   "--audit", str(root / "data_audit.md")]  # fmt: skip
    assert cohort_build.main(cohort_args) == 0
    return [
        "--warehouse", str(warehouse),
        "--cohort-dir", str(root / "cohort"),
        "--interventions", (interventions / "*.parquet").as_posix(),
        "--current-fields", str(root / "current_fields.parquet"),
        "--out-dir", str(root / "features"),
        "--doc", str(root / "features.md"),
        "--temp-dir", str(root / "duckdb_tmp"),
    ]  # fmt: skip


def _log(root: Path) -> dict:
    result: dict = json.loads((root / "features" / "build.json").read_text(encoding="utf-8"))
    return result


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Built:
    root = tmp_path_factory.mktemp("feature_build")
    args = _write_inputs(root, registry())
    assert features_build.main(args) == 0
    first = _log(root)
    assert features_build.main(args) == 0
    return Built(root, args, first, _log(root))


def test_building_twice_gives_identical_outputs(built: Built) -> None:
    """Step 7 acceptance: the same inputs give the same rows, checksum by checksum."""
    assert built.first["identical_to_previous"] is None
    assert built.second["identical_to_previous"] is True
    assert built.first["tables"] == built.second["tables"]
    names = set(built.second["tables"])
    assert {"features.parquet", "text_keys.parquet", "sensitivity.parquet"} <= names
    assert {"text_cache/eligibility", "text_cache/summary"} <= names
    for year in (2016, 2017, 2018, 2019, 2020):
        for part in ("sponsor_priors.json", "eligibility_components.parquet",
                     "summary_components.parquet"):  # fmt: skip
            assert f"origin_{year}-01-01/{part}" in names


def test_the_feature_table_holds_each_landmark_row_once_with_the_registered_columns(
    built: Built,
) -> None:
    features = (built.features_dir / "features.parquet").as_posix()
    cohort = built.cohort_dir.as_posix()
    with duckdb.connect() as con:
        columns = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM '{features}'").fetchall()]
        assert columns == [*reg.KEY_COLUMNS, *reg.built_columns()]
        rows, distinct = con.execute(
            f"SELECT count(*), count(DISTINCT (trial_id, landmark_index)) FROM '{features}'"
        ).fetchone() or (0, 0)
        wanted = con.execute(
            f"""SELECT count(*) FROM (
              SELECT trial_id, landmark_index FROM '{cohort}/landmarks.parquet'
              UNION SELECT trial_id, landmark_index
              FROM read_parquet('{cohort}/training/*/landmarks.parquet'))"""
        ).fetchone() or (0,)
        assert rows == distinct == wanted[0] > 500
        # The stratum of the cohort is the sponsor class of the feature table.
        mismatch = con.execute(
            f"""SELECT count(*) FROM '{cohort}/landmarks.parquet' l
            JOIN '{features}' f USING (trial_id, landmark_index)
            WHERE l.stratum <> f.sponsor_class OR l.landmark_date <> f.landmark_date"""
        ).fetchone()
        assert mismatch == (0,)


def test_the_document_lists_every_feature_with_its_missing_share(built: Built) -> None:
    text = (built.root / "features.md").read_text(encoding="utf-8")
    for feature in reg.FEATURES:
        assert f"`{feature.name}`" in text
    assert "Research demo. Not medical advice. Not for patient decision-making." in text
    assert "ClinicalTrials.gov" in text
    assert "Data as of" in text
    assert "rows of the cohort as of 2018-01-01" in text
    assert "—" not in text
    assert "\r" not in text


@pytest.mark.parametrize("role", ["training", "evaluation"])
def test_the_matrix_of_an_origin_has_every_main_feature(
    built: Built, cfg: ProjectConfig, role: frame.Role
) -> None:
    matrix = frame.load(cfg, ORIGIN, role, built.features_dir, built.cohort_dir)
    assert tuple(matrix.columns) == reg.matrix_columns()
    assert set(matrix.columns) == {*reg.KEY_COLUMNS, *reg.model_columns()}
    assert matrix.columns.is_unique
    cohort = built.cohort_dir.as_posix()
    with duckdb.connect() as con:
        if role == "training":
            keys = con.execute(
                f"""SELECT trial_id, landmark_index
                FROM '{cohort}/training/origin_{ORIGIN}/landmarks.parquet' ORDER BY 1, 2"""
            ).fetchall()
        else:
            keys = con.execute(
                f"""SELECT trial_id, landmark_index FROM '{cohort}/landmarks.parquet'
                WHERE landmark_date >= DATE '2017-01-01' AND landmark_date < DATE '2018-01-01'
                ORDER BY 1, 2"""
            ).fetchall()
    assert len(keys) > 50
    assert list(zip(matrix["trial_id"], matrix["landmark_index"], strict=True)) == keys
    rate = matrix["sponsor_stop_rate"].to_numpy(dtype=float)
    assert np.isfinite(rate).all()
    assert ((rate >= 0) & (rate <= 1)).all()
    components = matrix[[c for c in matrix.columns if "_svd_" in c]]
    assert components.shape[1] == 2 * cfg.features.text_components
    assert components.notna().all().all()  # every row of the test registry has both texts
    assert float(np.abs(components.to_numpy(dtype=float)).sum()) > 0
    if role == "training":
        assert (matrix["landmark_date"] < pd.Timestamp(ORIGIN)).all()


def test_the_sponsor_rate_is_smoothed_toward_the_class_rate_of_the_origin(
    built: Built, cfg: ProjectConfig
) -> None:
    matrix = frame.load(cfg, ORIGIN, "training", built.features_dir, built.cohort_dir)
    folder = built.features_dir / f"origin_{ORIGIN}"
    priors = SponsorPriors.from_json(json.loads((folder / "sponsor_priors.json").read_text()))
    weight = cfg.features.sponsor_prior_weight
    assert priors.weight == weight == 10
    known = matrix[matrix["sponsor_has_identity"]].iloc[0]
    expected = (known["sponsor_prior_stopped"] + weight * priors.prior(known["sponsor_class"])) / (
        known["sponsor_prior_ended"] + weight
    )
    assert known["sponsor_stop_rate"] == pytest.approx(expected)
    # A sponsor without an identity gets the class rate itself (ADR 0018).
    unknown = matrix[~matrix["sponsor_has_identity"].astype(bool)]
    assert len(unknown) > 5
    for _, row in unknown.head(20).iterrows():
        assert pd.isna(row["sponsor_prior_ended"])
        assert row["sponsor_stop_rate"] == pytest.approx(priors.prior(row["sponsor_class"]))


def test_what_is_fitted_for_an_origin_depends_only_on_its_training_rows(
    built: Built, tmp_path: Path
) -> None:
    """Section 6: fitted transforms are fit on each origin's training rows only. Every
    version posted on or after 2017-01-01 is rewritten, and the cohort and the features are
    built again. For the 2017 origin the class rates, the features of the training rows and
    the text components of their texts must be the same; for a later origin they change."""
    args = _write_inputs(tmp_path / "rewritten", registry(), rewrite_from=ORIGIN)
    assert features_build.main(args) == 0
    other = tmp_path / "rewritten"
    same = f"origin_{ORIGIN}"
    for name in ("sponsor_priors.json",):
        mine = (built.features_dir / same / name).read_text(encoding="utf-8")
        assert mine == (other / "features" / same / name).read_text(encoding="utf-8")
    later = f"origin_{LATER_ORIGIN}"
    assert (built.features_dir / later / "sponsor_priors.json").read_text() != (
        other / "features" / later / "sponsor_priors.json"
    ).read_text()
    with duckdb.connect() as con:
        for root, name in ((built.root, "a"), (other, "b")):
            folder = root.as_posix()
            con.execute(
                f"""CREATE VIEW training_{name} AS
                SELECT f.*, k.eligibility_hash, k.summary_hash
                FROM '{folder}/cohort/training/{same}/landmarks.parquet' l
                JOIN '{folder}/features/features.parquet' f USING (trial_id, landmark_index)
                JOIN '{folder}/features/text_keys.parquet' k USING (trial_id, landmark_index)"""
            )
            for field in reg.TEXT_FIELDS:
                con.execute(
                    f"""CREATE VIEW {field}_{name} AS SELECT c.*
                    FROM '{folder}/features/{same}/{field}_components.parquet' c
                    WHERE c.text_hash IN (SELECT {field}_hash FROM training_{name})"""
                )
        tables = ["training", *reg.TEXT_FIELDS]
        for table in tables:
            rows = con.execute(f"SELECT count(*) FROM {table}_a").fetchone()
            differing = con.execute(
                f"""SELECT count(*) FROM (
                  (SELECT * FROM {table}_a EXCEPT ALL SELECT * FROM {table}_b)
                  UNION ALL (SELECT * FROM {table}_b EXCEPT ALL SELECT * FROM {table}_a))"""
            ).fetchone()
            assert rows is not None
            assert rows[0] > 50, table
            assert differing == (0,), table


def test_the_leakage_command_rewrites_the_registry_after_each_origin(
    built: Built, capsys: pytest.CaptureFixture[str]
) -> None:
    """`python -m trialpulse.features.leakage`, here on the synthetic registry: for each
    origin every version posted after it is rewritten and the features up to it must equal
    the build's."""
    from trialpulse.features import leakage

    args = [built.args[i] for i in range(len(built.args))]
    keep = {"--warehouse", "--cohort-dir", "--interventions", "--temp-dir"}
    chosen = [x for i in range(0, len(args), 2) if args[i] in keep for x in args[i : i + 2]]
    chosen += ["--features-dir", str(built.features_dir)]
    assert leakage.main(chosen) == 0
    out = capsys.readouterr().out
    assert out.count(" 0 differ from the build") == 5
    assert "No feature at a landmark changed" in out


def test_the_leakage_command_reports_a_leak(built: Built, tmp_path: Path) -> None:
    """Against a feature table that is not what the registry gives (here: built from a
    registry rewritten from 2017 on), the command refuses."""
    import shutil

    from trialpulse.cli import RefusedError
    from trialpulse.features import leakage

    other = tmp_path / "other"
    args = _write_inputs(other, registry(), rewrite_from=ORIGIN)
    assert features_build.main(args) == 0
    mixed = tmp_path / "mixed"
    shutil.copytree(other / "features", mixed)
    command = ["--warehouse", str(built.root / "warehouse.duckdb"),
               "--cohort-dir", str(other / "cohort"),
               "--interventions", (built.root / "interventions" / "*.parquet").as_posix(),
               "--features-dir", str(mixed),
               "--temp-dir", str(tmp_path / "duckdb_tmp")]  # fmt: skip
    with pytest.raises(RefusedError, match="a leak"):
        leakage.main(command)


def test_the_text_counts_are_cached_once_and_reused(built: Built, tmp_path: Path) -> None:
    """The two builds of the fixture left one cache per text field. It is one file, written
    whole, and it holds what it says it holds."""
    cache = built.features_dir / "text_cache"
    assert sorted(p.name for p in cache.iterdir()) == [
        "eligibility_counts.joblib",
        "summary_counts.joblib",
    ]
    bits = features_build.load_project_config().features.text_hash_bits
    for field in reg.TEXT_FIELDS:
        loaded = features_build.load_cached_counts(
            features_build.cache_path(cache, field), counts_signature(bits)
        )
        assert loaded is not None
        hashes, counts = loaded
        assert hashes == sorted(set(hashes))
        assert counts.shape == (len(hashes), 2**bits)
        entry = built.second["tables"][f"text_cache/{field}"]
        assert entry["rows"] == len(hashes) > 100


def test_a_cache_that_cannot_be_trusted_is_not_used(tmp_path: Path) -> None:
    """Counts made under other settings, a file cut short, and contents that do not match
    their digest are all treated as no cache at all."""
    bits = 10
    signature = counts_signature(bits)
    assert counts_signature(bits) == signature
    assert counts_signature(bits + 1) != signature
    hashes = ["h1", "h2", "h3"]
    counts = hashed_counts(["alpha beta", "beta gamma gamma", "delta"], bits)
    path = features_build.cache_path(tmp_path, "eligibility")
    assert features_build.load_cached_counts(path, signature) is None  # no file
    features_build.store_cached_counts(path, signature, hashes, counts)
    assert [p.name for p in tmp_path.iterdir()] == [path.name]  # nothing half written beside it
    loaded = features_build.load_cached_counts(path, signature)
    assert loaded is not None
    assert loaded[0] == hashes
    assert (loaded[1] != counts).nnz == 0
    assert features_build.load_cached_counts(path, counts_signature(bits + 1)) is None
    # Contents that are not what the digest was taken from.
    import joblib

    content = joblib.load(path)
    content["hashes"] = ["h1", "h2", "other"]
    joblib.dump(content, path, compress=3)
    assert features_build.load_cached_counts(path, signature) is None
    # A file cut short, as an interrupted copy would leave it.
    features_build.store_cached_counts(path, signature, hashes, counts)
    whole = path.read_bytes()
    path.write_bytes(whole[: len(whole) // 2])
    assert features_build.load_cached_counts(path, signature) is None
    path.write_bytes(b"not a cache")
    assert features_build.load_cached_counts(path, signature) is None


def test_the_build_recomputes_counts_it_cannot_trust_and_reuses_the_rest(
    built: Built, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a good cache the texts are not hashed again. With a damaged one they are, and
    the build gives the same counts as before."""
    import shutil

    features = tmp_path / "features"
    shutil.copytree(built.features_dir, features)
    args = list(built.args)
    args[args.index("--out-dir") + 1] = str(features)
    args[args.index("--doc") + 1] = str(tmp_path / "features.md")
    calls: list[int] = []
    real = features_build.hashed_counts

    def counting(texts: object, hash_bits: int) -> object:
        calls.append(hash_bits)
        return real(texts, hash_bits)  # type: ignore[arg-type]

    monkeypatch.setattr(features_build, "hashed_counts", counting)
    assert features_build.main(args) == 0
    assert calls == []  # both caches were good
    features_build.cache_path(features / "text_cache", "summary").write_bytes(b"damaged")
    assert features_build.main(args) == 0
    assert len(calls) == 1  # the summary texts only
    log = json.loads((features / "build.json").read_text(encoding="utf-8"))
    assert log["tables"] == built.second["tables"]


def test_the_report_of_missing_values_needs_the_cohort_as_of_2018(
    built: Built, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Section 10: the report reads only rows of the cohort as of 2018-01-01. If that date
    is not a walk-forward origin there is no such cohort, and the build refuses instead of
    reporting over every row."""
    from trialpulse.cli import RefusedError

    cfg = features_build.load_project_config()
    origins = tuple(o for o in cfg.walk_forward.origins if o.date != dt.date(2018, 1, 1))
    assert len(origins) == len(cfg.walk_forward.origins) - 1
    walk_forward = cfg.walk_forward.model_copy(update={"origins": origins})
    changed = cfg.model_copy(update={"walk_forward": walk_forward})
    monkeypatch.setattr(features_build, "load_project_config", lambda: changed)
    with pytest.raises(RefusedError, match="cannot report missing values"):
        features_build.main(built.args)


def test_the_build_names_the_command_for_a_missing_input(built: Built, tmp_path: Path) -> None:
    from trialpulse.cli import RefusedError

    args = list(built.args)
    args[args.index("--interventions") + 1] = (tmp_path / "nothing" / "*.parquet").as_posix()
    with pytest.raises(RefusedError, match=r"trialpulse\.ingest\.history --config interventions"):
        features_build.main(args)
    args = list(built.args)
    args[args.index("--cohort-dir") + 1] = str(tmp_path / "no_cohort")
    with pytest.raises(RefusedError, match=r"trialpulse\.cohort\.build"):
        features_build.main(args)
    with pytest.raises(RefusedError, match=r"trialpulse\.features\.build"):
        frame.load(None, ORIGIN, "training", tmp_path / "no_features", built.cohort_dir)  # type: ignore[arg-type]


# What a model reads ------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["training", "evaluation"])
def test_the_matrix_can_be_cut_to_some_landmark_indices(
    built: Built, cfg: ProjectConfig, role: frame.Role
) -> None:
    """M1 reads landmark 0 only: the same rows, in the same order, as the full matrix cut
    to that landmark index."""
    full = frame.load(cfg, ORIGIN, role, built.features_dir, built.cohort_dir)
    first = frame.load(cfg, ORIGIN, role, built.features_dir, built.cohort_dir, (0,))
    assert 10 < len(first) < len(full)
    expected = full[full["landmark_index"] == 0].reset_index(drop=True)
    pd.testing.assert_frame_equal(first.reset_index(drop=True), expected)
    two = frame.load(cfg, ORIGIN, role, built.features_dir, built.cohort_dir, (0, 2))
    assert set(two["landmark_index"].tolist()) == {0, 2}
    assert len(two) == int(full["landmark_index"].isin([0, 2]).sum())


def test_arrays_for_a_model_keep_a_missing_value_missing() -> None:
    """A missing number is NaN, never 0; a missing category is None, never a level; a flag
    is 1.0 or 0.0; the keys stay out, except the landmark index, which is a feature too."""
    matrix = pd.DataFrame(
        {
            "trial_id": ["NCT1", "NCT2", "NCT3"],
            "landmark_index": pd.array([0, 1, 2], dtype="Int64"),
            "landmark_date": pd.to_datetime(["2016-01-01", "2016-07-01", "2017-01-01"]),
            "enrollment_count": pd.array([100, None, 0], dtype="Int64"),
            "healthy_volunteers": pd.array([True, None, False], dtype="boolean"),
            "masking": ["NONE", None, "DOUBLE"],
        }
    )
    arrays = frame.as_arrays(matrix)
    assert set(arrays) == {"landmark_index", "enrollment_count", "healthy_volunteers", "masking"}
    assert arrays["enrollment_count"].dtype == np.float64
    assert arrays["enrollment_count"][[0, 2]].tolist() == [100.0, 0.0]
    assert np.isnan(arrays["enrollment_count"][1])
    assert arrays["healthy_volunteers"][[0, 2]].tolist() == [1.0, 0.0]
    assert np.isnan(arrays["healthy_volunteers"][1])
    assert arrays["masking"].dtype == object
    assert arrays["masking"].tolist() == ["NONE", None, "DOUBLE"]
    assert arrays["landmark_index"].tolist() == [0.0, 1.0, 2.0]


def test_the_arrays_of_a_built_matrix_hold_every_model_feature(
    built: Built, cfg: ProjectConfig
) -> None:
    matrix = frame.load(cfg, ORIGIN, "training", built.features_dir, built.cohort_dir)
    arrays = frame.as_arrays(matrix)
    assert set(arrays) == set(reg.model_columns())
    assert len(arrays) == len(reg.model_columns())
    missing_somewhere = 0
    for name, values in arrays.items():
        assert len(values) == len(matrix)
        absent = matrix[name].isna().to_numpy()
        if reg.BY_NAME[name].kind == reg.CATEGORY:
            assert [v is None for v in values] == absent.tolist(), name
        else:
            assert values.dtype == np.float64
            assert np.isnan(values).tolist() == absent.tolist(), name
        missing_somewhere += int(absent.any())
    assert missing_somewhere > 5  # the test registry leaves fields empty, as the real one does


def test_the_harness_gets_the_matrix_of_an_origin_through_its_loader(
    built: Built, cfg: ProjectConfig
) -> None:
    from trialpulse.eval import walkforward

    load = walkforward.feature_loader(cfg, built.features_dir, built.cohort_dir)
    for role in (walkforward.TRAINING, walkforward.EVALUATION):
        matrix = frame.load(cfg, ORIGIN, role, built.features_dir, built.cohort_dir, (0,))  # type: ignore[arg-type]
        got = load(ORIGIN, role, (0,))
        assert got.trial_id.tolist() == matrix["trial_id"].tolist()
        assert got.landmark_index.tolist() == [0] * len(matrix)
        expected = frame.as_arrays(matrix)
        assert tuple(got.features) == tuple(expected)
        assert set(got.features) == set(reg.model_columns())
        for name, values in expected.items():
            if values.dtype == object:
                assert got.features[name].tolist() == values.tolist()
            else:
                assert np.array_equal(got.features[name], values, equal_nan=True)
    # Training and evaluation rows are different rows, and all landmark indices come when
    # none is asked for.
    training, evaluation = load(ORIGIN, "training", None), load(ORIGIN, "evaluation", None)
    assert set(training.landmark_index.tolist()) > {0}
    assert training.trial_id.tolist() != evaluation.trial_id.tolist()
    with pytest.raises(ValueError, match="unknown role"):
        load(ORIGIN, "test", None)
    # A build that is not there is one line naming the command, as the lock gives.
    absent = walkforward.feature_loader(cfg, built.root / "no_features", built.cohort_dir)
    with pytest.raises(ValueError, match=r"trialpulse\.features\.build"):
        absent(ORIGIN, "training", None)


def test_the_cox_models_read_the_landmark_0_rows_of_the_cohort_as_of_the_origin(
    built: Built, cfg: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trialpulse.models import cox

    features, time, event = cox.load_rows(cfg, ORIGIN, built.features_dir, built.cohort_dir)
    training = (built.cohort_dir / "training" / f"origin_{ORIGIN}" / "landmarks.parquet").as_posix()
    with duckdb.connect() as con:
        rows = con.execute(
            f"""SELECT trial_id, date_diff('day', landmark_date, event_date), event
            FROM '{training}' WHERE landmark_index = 0 ORDER BY trial_id"""
        ).fetchall()
    assert len(rows) > 30
    assert time.tolist() == [float(days) for _, days, _ in rows]
    assert event.tolist() == [e for _, _, e in rows]
    matrix = frame.load(cfg, ORIGIN, "training", built.features_dir, built.cohort_dir, (0,))
    assert matrix["trial_id"].tolist() == [trial for trial, _, _ in rows]
    assert set(features) == set(reg.model_columns())
    assert np.array_equal(
        features["sponsor_stop_rate"], matrix["sponsor_stop_rate"].to_numpy(dtype=float)
    )
    # Features of other rows, or of the same rows in another order, are refused.
    real = frame.load

    def reversed_rows(*args: object, **kwargs: object) -> pd.DataFrame:
        return real(*args, **kwargs).iloc[::-1].reset_index(drop=True)  # type: ignore[arg-type]

    monkeypatch.setattr(cox.frame, "load", reversed_rows)
    with pytest.raises(RefusedError, match="hold different landmark 0 rows"):
        cox.load_rows(cfg, ORIGIN, built.features_dir, built.cohort_dir)
