"""The review file of the first memos (ADR 0084, change C), for Aakrisht's review before
`api.memo_public` is turned on.

    uv run python -m trialpulse.memo.review --target neon

Writes data/memo_review/<date>.md (gitignored; <date>-2.md and so on when that file exists,
so a review is never overwritten): the first 20 stored memos of the current memo
version (oldest first), each with its payload and the result of the fabrication check run
again on it, and up to 5 rejected memos of the latest runs of that version with their reasons
and payloads. It reads Neon only and writes
nothing there. The file holds no names: payloads hold none, and rejected memos are quoted as
they were built, so the reasons say what each broke.
"""

import argparse
import datetime as dt
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from trialpulse.cli import run
from trialpulse.config import REPO_ROOT, load_project_config
from trialpulse.memo import check as checks
from trialpulse.memo.generate import version

OUT_DIR = REPO_ROOT / "data" / "memo_review"
FIRST = 20
REJECTED = 5


Rows = list[dict[str, Any]]


def read(con: Any, version: str, first: int = FIRST,
         rejected: int = REJECTED) -> tuple[Rows, Rows, list[str]]:  # fmt: skip
    """The first stored memos of a prompt version, the latest rejected samples of its runs and
    the sponsor display names."""
    from psycopg.rows import dict_row

    cur = con.cursor(row_factory=dict_row)
    memos = cur.execute(
        """SELECT nct_id, paragraphs, payload, chosen_as, valid_as_of, generated_at, model,
                  prompt_version, run_id
           FROM serving.trial_memos WHERE prompt_version = %s
           ORDER BY generated_at, nct_id LIMIT %s""",
        (version, first),
    ).fetchall()
    samples: list[dict[str, Any]] = []
    for row in cur.execute(
        """SELECT run_id, rejected_samples FROM monitoring.memo_runs
           WHERE jsonb_array_length(rejected_samples) > 0 AND prompt_version = %s
           ORDER BY run_id DESC""",
        (version,),
    ):
        for sample in row["rejected_samples"]:
            if len(samples) < rejected:
                samples.append({**sample, "run_id": row["run_id"]})
    names = [r["display_name"] for r in cur.execute(
        "SELECT display_name FROM serving.sponsors WHERE display_name IS NOT NULL")]  # fmt: skip
    return [dict(m) for m in memos], samples, names


def render(memos: Sequence[dict[str, Any]], samples: Sequence[dict[str, Any]],
           names: Sequence[str], day: dt.date) -> str:  # fmt: skip
    lines = [
        f"# Risk memo review, {day.isoformat()}",
        "",
        "For Aakrisht's review before `api.memo_public` is turned on (ADR 0084, change C). "
        f"The first {len(memos)} stored memos, each with its payload and the fabrication check "
        f"run again on it, then {len(samples)} rejected memos with their reasons. Research "
        "demo. Not medical advice. Not for patient decision-making.",
        "",
    ]
    for i, m in enumerate(memos, start=1):
        result = checks.check(m["paragraphs"], m["payload"], names)
        verdict = "passed" if result.passed else "FAILED: " + "; ".join(result.details)
        lines += [
            f"## {i}. {m['nct_id']} ({', '.join(m['chosen_as'])})",
            "",
            f"Generated {m['generated_at']:%Y-%m-%d %H:%M} UTC by {m['model']} "
            f"({m['prompt_version']}), run {m['run_id']}, for the score of {m['valid_as_of']}. "
            f"Check run again: {verdict}.",
            "",
            *[f"> {p}\n>" for p in m["paragraphs"]],
            "",
            "```json",
            json.dumps(m["payload"], indent=1, ensure_ascii=False),
            "```",
            "",
        ]
    lines += ["## Rejected memos", ""]
    if not samples:
        lines += ["None in the runs so far.", ""]
    for i, s in enumerate(samples, start=1):
        lines += [f"### R{i}. {s['nct_id']} (run {s['run_id']})", "",
                  "Reasons: " + ", ".join(s["reasons"]), ""]  # fmt: skip
        lines += [f"- {d}" for d in s.get("details") or []] + [""]
        if s.get("paragraphs"):
            lines += [*[f"> {p}\n>" for p in s["paragraphs"]], ""]
        else:
            lines += ["(no readable answer)", ""]
        lines += ["```json", json.dumps(s["payload"], indent=1, ensure_ascii=False), "```", ""]
    return "\n".join(lines)


def free_path(out_dir: Path, day: dt.date) -> Path:
    """The review file of `day` in `out_dir`, never an existing file: <day>.md, else
    <day>-2.md, <day>-3.md and so on (a second review of a day keeps the first)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path, n = out_dir / f"{day.isoformat()}.md", 1
    while path.exists():
        n += 1
        path = out_dir / f"{day.isoformat()}-{n}.md"
    return path


def main(argv: Sequence[str] | None = None) -> int:
    import psycopg

    from trialpulse.live.daily import _dsn

    parser = argparse.ArgumentParser(description="The memo review file of ADR 0084.")
    parser.add_argument("--target", choices=("local", "neon"), required=True)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    day = dt.datetime.now(dt.UTC).date()
    with psycopg.connect(_dsn(args.target), connect_timeout=30) as con:
        memos, samples, names = read(con, version(load_project_config().memo))
    path = free_path(args.out_dir, day)
    path.write_text(render(memos, samples, names, day), encoding="utf-8")
    print(json.dumps({"file": str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT)
                      else path.name, "memos": len(memos), "rejected": len(samples)}))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
