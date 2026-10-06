"""Regenerate docs/eda.md and the figures in docs/figures/ (CLAUDE.md Step 5).

    uv run python -m trialpulse.eda.report

One command, from the cohort files of Step 4, the warehouse of Step 3 and the
current-record snapshot of Step 2; it takes about 15 seconds. Running it twice gives the
same Markdown and the same PNG bytes.

How the report keeps its promises:

- **Every statement cites a figure or a table,** and its numbers are formatted from the
  same results that fill that table (`results.py`), so text and table cannot disagree.
- **A finding is checked before it is written** (`findings.py`). If the numbers no longer
  support its wording, the command refuses and writes nothing.
- **Modeling-relevant numbers come from landmarks before 2018-01-01 only** (Section 10).
  Everything else is labeled descriptive only, and phase (ADR 0006) is labeled
  "descriptive only, not used for modeling".
"""

import argparse
import time
from collections.abc import Sequence
from pathlib import Path

from trialpulse.cli import RefusedError, run
from trialpulse.cohort.audit import MODELING_EDA_BEFORE
from trialpulse.cohort.build import COHORT_DIR, OUTCOMES_PATH
from trialpulse.config import REPO_ROOT, ProjectConfig, load_project_config
from trialpulse.eda.analysis import Sources
from trialpulse.eda.charts import draw
from trialpulse.eda.document import render
from trialpulse.eda.results import compute
from trialpulse.eval.walkforward import LANDMARKS_PATH
from trialpulse.warehouse.build import WAREHOUSE_PATH

EDA_PATH = REPO_ROOT / "docs" / "eda.md"
FIGURES_DIR_NAME = "figures"
# The bulk API v2 snapshot of current-record fields, written by Step 2 part g. The path is
# repeated here because no module outside feasibility/ may import from it (Step 2).
CURRENT_FIELDS_PATH = REPO_ROOT / "data" / "spike" / "current_fields.parquet"


def generate(sources: Sources, out: Path, figures_dir: Path, cfg: ProjectConfig) -> list[Path]:
    """Compute everything, then write the figures and the Markdown. The text is rendered,
    and with it every finding checked, before any file is written."""
    needed = {
        sources.warehouse: "uv run python -m trialpulse.warehouse.build",
        sources.landmarks: "uv run python -m trialpulse.cohort.build",
        sources.outcomes: "uv run python -m trialpulse.cohort.build",
        sources.current_fields: "uv run python -m trialpulse.feasibility.spike --part g",
    }
    for path, command in needed.items():
        if not path.is_file():
            raise RefusedError(f"{path} is missing. Create it with: {command}")
    res = compute(sources, cfg, MODELING_EDA_BEFORE)
    text = render(res)
    written = draw(res, figures_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    return [out, *written]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate docs/eda.md and its figures.")
    parser.add_argument("--warehouse", type=Path, default=WAREHOUSE_PATH)
    parser.add_argument("--cohort-dir", type=Path, default=COHORT_DIR)
    parser.add_argument("--current-fields", type=Path, default=CURRENT_FIELDS_PATH)
    parser.add_argument("--out", type=Path, default=EDA_PATH)
    args = parser.parse_args(argv)
    started = time.monotonic()
    cohort_dir: Path = args.cohort_dir
    out: Path = args.out
    sources = Sources(
        landmarks=cohort_dir / LANDMARKS_PATH.name,
        outcomes=cohort_dir / OUTCOMES_PATH.name,
        warehouse=args.warehouse,
        current_fields=args.current_fields,
    )
    figures_dir = out.parent / FIGURES_DIR_NAME
    written = generate(sources, out, figures_dir, load_project_config())
    print(f"Wrote {written[0]} and {len(written) - 1} figures in {figures_dir}.")
    print(f"Done in {time.monotonic() - started:.1f} s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
