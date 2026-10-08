"""Text features (CLAUDE.md Section 8, text family; ADR 0019).

**Eligibility statistics.** Texts are stored in one canonical form (ADR 0012): one line per
paragraph or list item. The exclusion criteria start at the first line that holds the words
"exclusion criteria", and the lines before it are the inclusion criteria; lines that are
only a heading, and empty lines, are not counted. A text without those words is all
inclusion criteria. A numeric threshold is a comparison with a number ("> 2", "at least
18", "less than 1.5"). Each statistic has a SQL form, used by the build, and a Python twin,
used by the tests and later by live scoring.

The two forms must read every text alike, also outside ASCII, where the two
regular-expression engines fold case and class characters differently. So the patterns
spell out both cases of each ASCII letter and name their digits and blanks, and use neither
engine's own case-insensitive mode, lowercasing, or shorthand classes for digits and spaces.

**Text components.** Sentence embeddings of the 835,000 distinct texts in effect at a
landmark would take about 20 hours on this project's CPU budget, against the 2 hours
Section 8 allows, so the fallback of Section 8 applies (ADR 0019): TF-IDF and truncated SVD.

- Words are hashed into 2^18 columns (`hashed_counts`). Hashing has no fitted state, so it
  is computed once per distinct text and cached, like the embeddings it replaces.
- The inverse document frequencies and the SVD are fitted per walk-forward origin, on the
  distinct texts of that origin's training rows only (`TextComponents`). No text of an
  evaluation row, and no text posted after the origin, shapes them.
- The fit runs in double precision on one BLAS thread. A randomized SVD in single precision
  gives slightly different numbers for different thread counts (up to 3.5e-05 here, far
  more than the 6 decimals kept), so a build would otherwise depend on the machine's cores.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import chain
from typing import Any

import numpy as np
import numpy.typing as npt
import sklearn
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import HashingVectorizer, TfidfTransformer
from threadpoolctl import threadpool_limits

FloatArray = npt.NDArray[np.float32]
Sparse = Any  # a SciPy CSR matrix of word counts, as scikit-learn returns it
FIT_THREADS = 1


def _any_case(phrase: str) -> str:
    """A pattern for a phrase in any mix of ASCII upper and lower case, letter by letter."""
    return "".join(f"[{c.lower()}{c.upper()}]" if c.isalpha() else c for c in phrase)


def _one_of(*phrases: str) -> str:
    return "(?:" + "|".join(_any_case(phrase) for phrase in phrases) + ")"


# The words that start the exclusion criteria, anywhere in a line.
EXCLUSION_HEADING = _any_case("exclusion criteria")
# A line that is only a criteria heading ("Inclusion Criteria:", "Key Exclusion Criteria").
HEADING_LINE = (
    f"^ *(?:{_one_of('key', 'main', 'major', 'principal')} )?"
    f"{_one_of('inclusion', 'exclusion')} {_any_case('criteria')} *:? *$"
)
_COMPARISONS = (
    "greater than", "less than", "more than", "at least", "at most", "no more than",
    "no less than", "not less than", "not more than", "over", "under", "above", "below",
)  # fmt: skip
THRESHOLD = f"(?:[<>≤≥]=?|{_one_of(*_COMPARISONS)})[ \\t]*[0-9]"
_EXCLUSION_HEADING = re.compile(EXCLUSION_HEADING)
_HEADING_LINE = re.compile(HEADING_LINE)
_THRESHOLD = re.compile(THRESHOLD)
STATISTICS: tuple[str, ...] = (
    "eligibility_n_inclusion",
    "eligibility_n_exclusion",
    "eligibility_length",
    "eligibility_n_thresholds",
)


def _criteria(lines: list[str]) -> int:
    return sum(1 for line in lines if line.strip(" ") and not _HEADING_LINE.search(line))


def eligibility_statistics(text: str | None) -> dict[str, int | None]:
    """The four statistics of one eligibility text (Python twin of the SQL below)."""
    if text is None:
        return dict.fromkeys(STATISTICS)
    lines = text.split("\n")
    first = next((i for i, line in enumerate(lines) if _EXCLUSION_HEADING.search(line)), None)
    inclusion, exclusion = (lines, []) if first is None else (lines[:first], lines[first:])
    return {
        "eligibility_n_inclusion": _criteria(inclusion),
        "eligibility_n_exclusion": _criteria(exclusion),
        "eligibility_length": len(text),
        "eligibility_n_thresholds": len(_THRESHOLD.findall(text)),
    }


def _criteria_sql(lines: str) -> str:
    return (
        f"len(list_filter({lines}, line -> len(trim(line, ' ')) > 0 "
        f"AND NOT regexp_matches(line, '{HEADING_LINE}')))"
    )


def eligibility_statistics_sql(text: str) -> str:
    """SQL select list for the four statistics of a text column."""
    lines = f"string_split({text}, chr(10))"
    first = (
        f"list_position(list_transform({lines}, line -> "
        f"regexp_matches(line, '{EXCLUSION_HEADING}')), true)"
    )
    inclusion = (
        f"CASE WHEN {first} IS NULL THEN {lines} ELSE list_slice({lines}, 1, {first} - 1) END"
    )
    exclusion = f"list_slice({lines}, {first}, len({lines}))"
    return (
        f"{_criteria_sql(inclusion)} AS eligibility_n_inclusion, "
        f"CASE WHEN {first} IS NULL THEN 0 ELSE {_criteria_sql(exclusion)} END "
        "AS eligibility_n_exclusion, "
        f"length({text}) AS eligibility_length, "
        f"len(regexp_extract_all({text}, '{THRESHOLD}')) AS eligibility_n_thresholds"
    )


# Hashed word counts ------------------------------------------------------------------------


def _vectorizer(hash_bits: int) -> HashingVectorizer:
    return HashingVectorizer(
        n_features=2**hash_bits,
        alternate_sign=False,
        norm=None,
        lowercase=True,
        token_pattern=r"(?u)\b[^\W\d_][\w\-]+\b",
        dtype=np.float32,
    )


def counts_signature(hash_bits: int) -> str:
    """Everything the hashed counts of a text depend on besides the text: the settings of
    the vectorizer and the scikit-learn version. Counts cached under another signature are
    not reused."""
    settings = {name: str(value) for name, value in _vectorizer(hash_bits).get_params().items()}
    return json.dumps({"scikit-learn": sklearn.__version__, **settings}, sort_keys=True)


def hashed_counts(texts: Iterable[str], hash_bits: int) -> Sparse:
    """Word counts of each text in 2^hash_bits hashed columns. Nothing is fitted: the same
    text gives the same row whatever the other texts are."""
    vectorizer = _vectorizer(hash_bits)
    remaining = iter(texts)
    first = next(remaining, None)
    if first is None:  # no text: a matrix without rows
        return vectorizer.transform([""])[:0]
    counts = vectorizer.transform(chain([first], remaining)).tocsr()
    counts.sort_indices()
    return counts


@dataclass
class TextComponents:
    """TF-IDF and truncated SVD, fitted on the texts of one origin's training rows."""

    n_components: int
    seed: int
    columns: npt.NDArray[np.int64] | None = None  # the hashed columns the training texts use
    tfidf: TfidfTransformer | None = None
    svd: TruncatedSVD | None = None

    def fit(self, counts: Sparse) -> "TextComponents":
        """`counts` holds the distinct texts of the origin's training rows, one row each. A
        hashed column that no training text uses is zero for all of them and can carry no
        component, so the fit keeps only the columns in use."""
        if counts.shape[0] == 0:
            raise ValueError("no training text to fit the text components on")
        self.columns = np.flatnonzero(counts.getnnz(axis=0)).astype(np.int64)
        used = counts[:, self.columns].astype(np.float64)
        self.tfidf = TfidfTransformer(sublinear_tf=True, smooth_idf=True, norm="l2").fit(used)
        k = max(1, min(self.n_components, min(used.shape) - 1))
        svd = TruncatedSVD(n_components=k, algorithm="randomized", n_iter=5, random_state=self.seed)
        with threadpool_limits(limits=FIT_THREADS):  # the result must not depend on the cores
            self.svd = svd.fit(self.tfidf.transform(used))
        return self

    def transform(self, counts: Sparse) -> FloatArray:
        """One row of `n_components` values per text, rounded to 6 decimals. Components the
        training texts could not support (fewer texts than components) are 0."""
        if self.tfidf is None or self.svd is None or self.columns is None:
            raise RuntimeError("fit the text components before transforming")
        out = np.zeros((counts.shape[0], self.n_components), dtype=np.float64)
        if counts.shape[0]:
            used = counts[:, self.columns].astype(np.float64)
            values = self.svd.transform(self.tfidf.transform(used))
            out[:, : values.shape[1]] = values
        rounded = np.round(out, 6)
        rounded[rounded == 0] = 0.0  # no negative zero: it would hash unlike zero
        result: FloatArray = rounded.astype(np.float32)
        return result
