"""Competition and text features on hand-built inputs, and the fitted text components."""

import duckdb
import numpy as np
import pytest

from trialpulse.cohort.rules import CohortRules
from trialpulse.config import ProjectConfig
from trialpulse.features.text import (
    STATISTICS,
    TextComponents,
    eligibility_statistics,
    eligibility_statistics_sql,
    hashed_counts,
)

from .conftest import D, World, compute, text_hash, version


def test_open_interventional_trials_are_counted_from_every_status_history(
    cfg: ProjectConfig, rules: CohortRules
) -> None:
    """ADR 0007. X is the row's trial; the count includes it."""
    t0 = D(2015, 1, 1)
    rows = [
        version("X", 0, t0, "RECRUITING"),
        # Opens after X's first landmark and completes before its third.
        version("B", 0, D(2015, 3, 1), "NOT_YET_RECRUITING"),
        version("B", 1, D(2015, 9, 1), "COMPLETED", t0=D(2015, 3, 1)),
        # Observational: not counted.
        version("C", 0, D(2014, 1, 1), "RECRUITING", study_type="OBSERVATIONAL"),
        # Open, but its completion date passed and nobody verified it since January 2014:
        # the UNKNOWN rule applies from 2016-02-01 (ADR 0014), and it stops counting.
        version("D", 0, D(2014, 1, 1), "RECRUITING", completion_date=D(2014, 6, 1)),
        # Suspended is an open status.
        version("E", 0, D(2014, 5, 1), "SUSPENDED"),
        # Posted on X's second landmark day: open there.
        version("F", 0, D(2015, 7, 1), "RECRUITING"),
        # Terminated on X's second landmark day: no longer open there.
        version("G", 0, D(2014, 9, 1), "RECRUITING"),
        version("G", 1, D(2015, 7, 1), "TERMINATED", t0=D(2014, 9, 1)),
    ]
    landmarks = [D(2015, 1, 1), D(2015, 7, 1), D(2016, 1, 1), D(2016, 7, 1)]
    out = compute(cfg, rules, rows, [("X", k, day) for k, day in enumerate(landmarks)])
    assert out["open_interventional_trials"].tolist() == [
        4,  # X, D, E, G
        5,  # X, B, D, E, F
        4,  # X, D, E, F
        3,  # X, E, F: D has lapsed
    ]


# Eligibility statistics ----------------------------------------------------------------------

BOTH = "\n".join(
    [
        "Inclusion Criteria:",
        "Adults aged 18 years or older",
        "Hemoglobin >= 9 g/dL",
        "ECOG status at most 2",
        "Key Exclusion Criteria:",
        "Creatinine greater than 1.5 mg/dL",
        "Pregnancy",
    ]
)
NO_HEADING = "Healthy volunteers\nBMI under 30\nNo medication"
BLANKS = "Inclusion criteria\nA\n\n  \nB\nEXCLUSION CRITERIA:\n"
IN_A_SENTENCE = "Inclusion Criteria:\nNo exclusion criteria apply to adults over 65"
EXAMPLES: dict[str, dict[str, int | None]] = {
    BOTH: {
        "eligibility_n_inclusion": 3,
        "eligibility_n_exclusion": 2,
        "eligibility_length": len(BOTH),
        "eligibility_n_thresholds": 3,  # ">= 9", "at most 2", "greater than 1.5"
    },
    NO_HEADING: {
        "eligibility_n_inclusion": 3,  # no heading at all: every line is a criterion
        "eligibility_n_exclusion": 0,
        "eligibility_length": len(NO_HEADING),
        "eligibility_n_thresholds": 1,  # "under 30"
    },
    BLANKS: {
        "eligibility_n_inclusion": 2,  # blank lines and heading lines are not criteria
        "eligibility_n_exclusion": 0,
        "eligibility_length": len(BLANKS),
        "eligibility_n_thresholds": 0,
    },
    IN_A_SENTENCE: {
        # The exclusion part starts at the first line that holds the words, heading or not.
        "eligibility_n_inclusion": 0,
        "eligibility_n_exclusion": 1,
        "eligibility_length": len(IN_A_SENTENCE),
        "eligibility_n_thresholds": 1,  # "over 65"
    },
}


def _sql_statistics(texts: list[str | None]) -> list[dict[str, int | None]]:
    with duckdb.connect() as con:
        con.execute("CREATE TABLE t (i INTEGER, text VARCHAR)")
        con.executemany("INSERT INTO t VALUES (?, ?)", list(enumerate(texts)))
        rows = con.execute(
            f"SELECT {eligibility_statistics_sql('text')} FROM t ORDER BY i"
        ).fetchall()
    return [dict(zip(STATISTICS, row, strict=True)) for row in rows]


def test_eligibility_statistics_by_hand() -> None:
    for text, expected in EXAMPLES.items():
        assert eligibility_statistics(text) == expected, text
    assert eligibility_statistics(None) == dict.fromkeys(STATISTICS)
    assert _sql_statistics(list(EXAMPLES)) == list(EXAMPLES.values())


LONG_S = "Inclusion Criteria:\nÂge ≥ 18 ans\nGewicht über 50 kg\nExclu\u017fion Criteria:\nKeine"
DOTTED_I = "\u0130nclusion Criteria:\nA\nEXCLUSION CRITERIA\nB ≤ 3\nC ≥ \u0663"
OTHER_BLANKS = (
    "Inclusion criteria\nValue >\u00a05\nValue > 5\nExclusion criteria\ne\u0301tude \U0001f44d"
)
SPELLINGS = "KEY EXCLUSION CRITERIA:\nat least\t7\nAT MOST 2\nat least seven\n\u00a0"
OUTSIDE_ASCII: dict[str, tuple[int, int, int]] = {
    # The long s is not an s: no exclusion heading, so five lines less one heading.
    LONG_S: (4, 0, 1),
    # A dotted capital I does not make a heading line, and an Arabic-Indic digit is no number.
    DOTTED_I: (2, 2, 1),
    # A no-break space is not a blank between a comparison and its number.
    OTHER_BLANKS: (2, 1, 1),
    # A tab is. A line that holds only a no-break space is not empty.
    SPELLINGS: (0, 4, 2),
}


def test_statistics_read_texts_outside_ascii_the_same_way_in_sql_and_python() -> None:
    """The two regular-expression engines fold case and class characters differently outside
    ASCII, so the patterns name ASCII letters, digits and blanks only."""
    texts = list(OUTSIDE_ASCII)
    expected = [
        dict(zip(STATISTICS, (inclusion, exclusion, len(text), thresholds), strict=True))
        for text, (inclusion, exclusion, thresholds) in OUTSIDE_ASCII.items()
    ]
    assert [eligibility_statistics(text) for text in texts] == expected
    assert _sql_statistics(list(texts)) == expected


def test_the_sql_statistics_equal_their_python_twin(world: World) -> None:
    texts = [t for t in world.registry.texts.values() if "Criteria" in t]
    assert len(texts) > 200
    assert _sql_statistics(list(texts)) == [eligibility_statistics(t) for t in texts]


def test_statistics_reach_the_feature_table(cfg: ProjectConfig, rules: CohortRules) -> None:
    t0 = D(2015, 1, 1)
    rows = [
        version("T", 0, t0, "RECRUITING", eligibility_criteria_hash=text_hash(BOTH)),
        version("N", 0, t0, "RECRUITING", eligibility_criteria_hash=None),
    ]
    out = compute(cfg, rules, rows, [("T", 0, t0), ("N", 0, t0)], {text_hash(BOTH): BOTH})
    assert out.loc[("T", 0)][list(STATISTICS)].to_dict() == EXAMPLES[BOTH]
    assert out.loc[("N", 0)][list(STATISTICS)].isna().all()
    assert out.loc[("T", 0)]["eligibility_hash"] == text_hash(BOTH)


# Hashed counts and the fitted components -------------------------------------------------------


def _corpus(n: int, seed: int) -> list[str]:
    rng = np.random.default_rng(seed)
    words = [f"term{i}" for i in range(60)]
    return [" ".join(rng.choice(words, size=int(rng.integers(5, 40)))) for _ in range(n)]


def test_hashed_counts_of_a_text_do_not_depend_on_the_other_texts() -> None:
    texts = _corpus(40, 1)
    alone = hashed_counts([texts[7]], 12)
    among = hashed_counts(texts, 12)
    reversed_order = hashed_counts(texts[::-1], 12)
    assert alone.shape == (1, 4096)
    assert (alone != among[7]).nnz == 0
    assert (among != reversed_order[::-1]).nnz == 0
    assert alone.sum() == len(texts[7].split())  # one count per word
    assert hashed_counts(["", "x 12 3"], 12).nnz == 0  # nothing but numbers and one letter


def test_text_components_are_fitted_on_the_training_texts_only(cfg: ProjectConfig) -> None:
    training, later = _corpus(300, 2), _corpus(80, 3)
    bits = 12
    fitted = TextComponents(cfg.features.text_components, cfg.seeds.default).fit(
        hashed_counts(training, bits)
    )
    again = TextComponents(cfg.features.text_components, cfg.seeds.default).fit(
        hashed_counts(training, bits)
    )
    values = fitted.transform(hashed_counts(later, bits))
    assert values.shape == (80, cfg.features.text_components)
    assert values.dtype == np.float32
    assert np.array_equal(values, again.transform(hashed_counts(later, bits)))  # reproducible
    assert np.array_equal(values, np.round(values, 6))
    # A text's components depend on the text and the fit, not on what else is transformed.
    assert np.array_equal(values[:5], fitted.transform(hashed_counts(later[:5], bits)))
    # Another training set gives another fit.
    other = TextComponents(cfg.features.text_components, cfg.seeds.default).fit(
        hashed_counts(_corpus(300, 9), bits)
    )
    assert not np.array_equal(values, other.transform(hashed_counts(later, bits)))


def test_text_components_with_few_training_texts_pad_with_zeros() -> None:
    model = TextComponents(n_components=32, seed=0).fit(hashed_counts(_corpus(6, 4), 10))
    values = model.transform(hashed_counts(_corpus(3, 5), 10))
    assert values.shape == (3, 32)
    assert np.abs(values[:, :5]).sum() > 0
    assert (values[:, 5:] == 0).all()  # 6 texts support 5 components
    assert model.transform(hashed_counts([], 10)).shape == (0, 32)


def test_text_components_refuse_to_run_unfitted_or_on_nothing() -> None:
    with pytest.raises(RuntimeError, match="fit the text components"):
        TextComponents(4, 0).transform(hashed_counts(["some words here"], 10))
    with pytest.raises(ValueError, match="no training text"):
        TextComponents(4, 0).fit(hashed_counts([], 10))


def test_the_registry_knows_how_many_components_the_config_asks_for(cfg: ProjectConfig) -> None:
    from trialpulse.features.registry import TEXT_COMPONENTS, TEXT_FIELDS, fitted_columns

    assert cfg.features.text_components == TEXT_COMPONENTS
    assert len(fitted_columns()) == 1 + TEXT_COMPONENTS * len(TEXT_FIELDS)
