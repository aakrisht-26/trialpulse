"""Regenerate docs/eda.md and the figures in docs/figures/ (CLAUDE.md Step 5).

    uv run python -m trialpulse.eda.report

One command, from the cohort files of Step 4 (the cohort as of 2018-01-01 for the
modeling-relevant sections, the final cohort for the descriptive ones), the warehouse of
Step 3 and the current-record snapshot of Step 2; it takes about 15 seconds. Running it
again reproduces
the committed files byte for byte: nothing in them depends on the run date or the working
directory, and a file that already says the same thing is left untouched, whatever line
endings git checked it out with (see `trialpulse.reports`).

How the report keeps its promises:

- **Every statement cites a figure or a table,** and its numbers are formatted from the
  same results that fill that table (`results.py`), so text and table cannot disagree.
- **A finding is checked before it is written** (`findings.py`). If the numbers no longer
  support its wording, the command refuses and writes nothing.
- **Modeling-relevant numbers come from the cohort as it would have been built on
  2018-01-01** (Section 10, ADR 0016): nothing posted on or after that date shapes them.
  Everything else is labeled descriptive only, and phase (ADR 0006) is labeled
  "descriptive only, not used for modeling".
"""

import argparse
import json
import time
from collections.abc import Sequence
from pathlib import Path

import duckdb

from trialpulse.cli import RefusedError, run
from trialpulse.cohort.audit import MODELING_EDA_BEFORE
from trialpulse.cohort.build import COHORT_DIR, OUTCOMES_PATH, TRAINING_DIR_NAME
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.eda.analysis import Sources
from trialpulse.eda.charts import draw
from trialpulse.eda.document import render
from trialpulse.eda.refs import FIGURES, figure_keys
from trialpulse.eda.results import compute
from trialpulse.eval.rows import LANDMARKS_PATH, training_path
from trialpulse.reports import write_text_if_changed
from trialpulse.warehouse.build import WAREHOUSE_PATH

EDA_PATH = REPO_ROOT / "docs" / "eda.md"
FIGURES_DIR_NAME = "figures"
# The bulk API v2 snapshot of current-record fields, written by Step 2 part g. The path is
# repeated here because no module outside feasibility/ may import from it (Step 2).
CURRENT_FIELDS_PATH = REPO_ROOT / "data" / "spike" / "current_fields.parquet"
# The reason labels of Step 6 and the metadata of the model that wrote them (the paths of
# trialpulse.nlp.distill, repeated so that the report does not load the NLP stack; a test
# holds them equal).
REASONS_PATH = REPO_ROOT / "data" / "nlp" / "reasons" / "early_stop_reasons.parquet"
REASON_MODEL_METADATA = REPO_ROOT / "data" / "nlp" / "models" / "distilled.json"


def final_reasons(
    reasons: Path = REASONS_PATH, metadata: Path = REASON_MODEL_METADATA
) -> Path | None:
    """The reason labels when they come from the final model of Step 6 (trained on the whole
    LLM sample) and every row names that model; otherwise None, and the addendum waits."""
    if not reasons.is_file() or not metadata.is_file():
        return None
    meta = json.loads(metadata.read_text(encoding="utf-8"))
    if meta["training_texts"] < meta["sample_size"]:
        return None
    expected = f"tfidf-logreg n={meta['training_texts']} trained {meta['trained_at']}"
    with duckdb.connect() as con:
        names = con.execute(
            f"SELECT DISTINCT model FROM read_parquet('{reasons.as_posix()}')"
        ).fetchall()
    return reasons if names == [(expected,)] else None


def _content(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def generate(sources: Sources, out: Path, figures_dir: Path, cfg: ProjectConfig) -> list[Path]:
    """Compute everything, then write the figures and the Markdown, and return the files
    that changed. The text is rendered, and with it every finding checked, before any file
    is written. A file that already holds the same content is not rewritten."""
    needed = {
        sources.warehouse: "uv run python -m trialpulse.warehouse.build",
        sources.landmarks: "uv run python -m trialpulse.cohort.build",
        sources.outcomes: "uv run python -m trialpulse.cohort.build",
        sources.final_landmarks: "uv run python -m trialpulse.cohort.build",
        sources.final_outcomes: "uv run python -m trialpulse.cohort.build",
        sources.current_fields: "uv run python -m trialpulse.feasibility.spike --part g",
    }
    for path, command in needed.items():
        if not path.is_file():
            raise RefusedError(f"{path} is missing. Create it with: {command}")
    res = compute(sources, cfg, MODELING_EDA_BEFORE)
    text = render(res)
    targets = [figures_dir / FIGURES[key][0] for key in figure_keys(res.reasons is not None)]
    before = {path: _content(path) for path in targets}
    draw(res, figures_dir)
    changed = [path for path in targets if _content(path) != before[path]]
    if write_text_if_changed(out, text):
        changed.insert(0, out)
    return changed


def cohort_sources(
    cohort_dir: Path, warehouse: Path, current_fields: Path, reasons: Path | None = None
) -> Sources:
    """The files the report reads from a cohort build: the cohort as of the modeling date,
    and the final cohort; with `reasons`, the final reason labels of Step 6."""
    as_of = training_path(cohort_dir / TRAINING_DIR_NAME, MODELING_EDA_BEFORE)
    return Sources(
        landmarks=as_of,
        outcomes=as_of.parent / OUTCOMES_PATH.name,
        final_landmarks=cohort_dir / LANDMARKS_PATH.name,
        final_outcomes=cohort_dir / OUTCOMES_PATH.name,
        warehouse=warehouse,
        current_fields=current_fields,
        reasons=reasons,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate docs/eda.md and its figures.")
    parser.add_argument("--warehouse", type=Path, default=WAREHOUSE_PATH)
    parser.add_argument("--cohort-dir", type=Path, default=COHORT_DIR)
    parser.add_argument("--current-fields", type=Path, default=CURRENT_FIELDS_PATH)
    parser.add_argument("--out", type=Path, default=EDA_PATH)
    parser.add_argument("--reasons", type=Path, default=REASONS_PATH)
    parser.add_argument("--reason-metadata", type=Path, default=REASON_MODEL_METADATA)
    args = parser.parse_args(argv)
    started = time.monotonic()
    cohort_dir: Path = args.cohort_dir
    out: Path = args.out
    reasons = final_reasons(args.reasons, args.reason_metadata)
    sources = cohort_sources(cohort_dir, args.warehouse, args.current_fields, reasons)
    figures_dir = out.parent / FIGURES_DIR_NAME
    changed = generate(sources, out, figures_dir, load_project_config())
    figures = len(figure_keys(reasons is not None))
    if changed:
        print(f"Wrote {len(changed)} of {figures + 1} files:")
        for path in changed:
            print(f"  {path}")
    else:
        print(f"Up to date: {out} and its {figures} figures already hold this content.")
    print(f"Done in {time.monotonic() - started:.1f} s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
