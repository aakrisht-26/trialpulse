"""The memo run (ADR 0084): started by memo.yml after a successful daily run, or by hand.

    uv run python -m trialpulse.memo.generate --target neon

One run, in order:

1. records itself in `monitoring.memo_runs` (status running);
2. reads, in one read-only snapshot, the served scores, the last two score-history rows of the
   trials that moved recently, the stored memos, the sponsor display names and, for the trials
   it needs, their state, display row, amendment markers and versions held since go-live;
3. chooses the trials (`payload.choose`) and builds their payloads; a stored memo whose payload
   hash and prompt are unchanged is current, and only its `valid_as_of` moves to the score
   date; a stored memo whose trial is no longer chosen and whose payload changed, or whose
   trial is no longer scored, is deleted;
4. asks the model for each chosen trial without a current memo, the top 100 first, at most
   `memo.cap_per_run` calls; each answer goes through the fabrication check (`check.check`),
   and only a memo that passes is stored, each in its own short transaction (ADR 0058);
5. ends the run row with its counts, rejections by reason, tokens and up to
   `memo.rejected_samples` rejected memos with their reasons (for the review; never served).

A daily limit of the provider (TPD or RPD) stops the run cleanly (status stopped, exit 0): the
rest waits for the next run. Rejections over `memo.rejection_alert_share` of the run's calls
write `memo_alert=rejections` to the step output, and memo.yml opens or reuses an issue. Any
other failure ends the run row as failed and exits non-zero, and memo.yml opens its failure
issue. No connection is held while the model answers. GROQ_API_KEY is never printed.
"""

import argparse
import datetime as dt
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trialpulse.cli import run
from trialpulse.config import MemoConfig, load_project_config
from trialpulse.features.registry import model_columns
from trialpulse.memo import check as checks
from trialpulse.memo import payload as pl

PROMPTS = Path(__file__).resolve().parent / "prompts"
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"paragraphs": {"type": "array", "items": {"type": "string"}}},
    "required": ["paragraphs"],
    "additionalProperties": False,
}
ESTIMATE_TOKENS = 2000  # one memo call, for the per-minute budget


@dataclass
class Inputs:
    """What one run reads from the database, before any model call."""

    scores: list[dict[str, Any]]
    history: list[dict[str, Any]]
    memos: dict[str, dict[str, Any]]
    names: list[str]
    states: dict[str, dict[str, Any]] = field(default_factory=dict)
    displays: dict[str, dict[str, Any]] = field(default_factory=dict)
    markers: dict[str, dict[str, Any]] = field(default_factory=dict)
    versions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass
class Tally:
    """The counts of one run, as `monitoring.memo_runs` holds them."""

    n_chosen: int = 0
    n_current: int = 0
    n_called: int = 0
    n_stored: int = 0
    n_rejected: int = 0
    n_deleted: int = 0
    n_over_cap: int = 0
    rejections: dict[str, int] = field(default_factory=dict)
    samples: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    status: str = "succeeded"
    stopped_reason: str | None = None

    def reject(self, reasons: Sequence[str], sample: dict[str, Any], keep: int) -> None:
        self.n_rejected += 1
        for reason in reasons:
            self.rejections[reason] = self.rejections.get(reason, 0) + 1
        if len(self.samples) < keep:
            self.samples.append(sample)

    def alert(self, share: float) -> bool:
        return self.n_called > 0 and self.n_rejected / self.n_called > share


def prompt_version(cfg: MemoConfig) -> str:
    return Path(cfg.prompt).stem


def load_prompt(cfg: MemoConfig) -> str:
    return (PROMPTS / cfg.prompt).read_text(encoding="utf-8")


def request_body(cfg: MemoConfig, system: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """One chat request: the prompt, and the payload's canonical JSON as the user message."""
    return {
        "model": cfg.model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": pl.canonical(payload).decode("utf-8")}],
        "temperature": 0.0,
        "reasoning_effort": cfg.reasoning_effort,
        "include_reasoning": False,
        "max_completion_tokens": cfg.max_completion_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "risk_memo", "strict": True, "schema": RESPONSE_SCHEMA},
        },
    }  # fmt: skip


def paragraphs_of(answer: Mapping[str, Any]) -> list[str] | None:
    """The memo's paragraphs from a chat completion, or None if it is not the expected JSON."""
    try:
        content = answer["choices"][0]["message"]["content"]
        data = json.loads(content)
        paragraphs = data["paragraphs"]
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not isinstance(paragraphs, list) or not all(isinstance(p, str) for p in paragraphs):
        return None
    return list(paragraphs)


# Reading ----------------------------------------------------------------------------------

SCORES_SQL = """SELECT nct_id, as_of_date, risk_12m, risk_24m, flagged, driver_months,
    driver_features, driver_values, driver_contributions FROM serving.trial_scores_latest"""
HISTORY_SQL = """SELECT nct_id, as_of_date, risk_12m FROM (
      SELECT nct_id, as_of_date, risk_12m, row_number() OVER (
        PARTITION BY nct_id ORDER BY as_of_date DESC, history_id DESC) AS rn
      FROM serving.trial_score_history
      WHERE nct_id IN (SELECT nct_id FROM serving.trial_score_history WHERE as_of_date >= %s)
    ) h WHERE rn <= 2"""
MEMOS_SQL = "SELECT nct_id, payload_hash, prompt_version, valid_as_of FROM serving.trial_memos"
NAMES_SQL = "SELECT display_name FROM serving.sponsors WHERE display_name IS NOT NULL"


def read_inputs(con: Any, cfg: MemoConfig, today: dt.date) -> Inputs:
    """Everything the run reads, in one read-only snapshot."""
    from psycopg.rows import dict_row

    with con.transaction():
        con.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cur = con.cursor(row_factory=dict_row)
        scores = cur.execute(SCORES_SQL).fetchall()
        since = today - dt.timedelta(days=cfg.move_window_days)
        history = cur.execute(HISTORY_SQL, (since,)).fetchall()
        memos = {r["nct_id"]: r for r in cur.execute(MEMOS_SQL)}
        names = [r["display_name"] for r in cur.execute(NAMES_SQL)]
        inputs = Inputs([dict(s) for s in scores], [dict(h) for h in history],
                        {k: dict(v) for k, v in memos.items()}, names)  # fmt: skip
        chosen = pl.choose(inputs.scores, inputs.history, cfg.top_n, cfg.move_threshold,
                           cfg.move_window_days, today)  # fmt: skip
        ids = sorted(set(chosen) | set(memos))
        for r in cur.execute("SELECT * FROM live.trial_state WHERE nct_id = ANY(%s)", (ids,)):
            inputs.states[r["nct_id"]] = dict(r)
        for r in cur.execute("""SELECT nct_id, sponsor_class FROM serving.trial_display
                                WHERE nct_id = ANY(%s)""", (ids,)):  # fmt: skip
            inputs.displays[r["nct_id"]] = dict(r)
        for r in cur.execute("""SELECT nct_id, post_dates, kinds FROM serving.amendment_markers
                                WHERE nct_id = ANY(%s)""", (ids,)):  # fmt: skip
            inputs.markers[r["nct_id"]] = dict(r)
        for r in cur.execute("""SELECT * FROM live.study_versions WHERE nct_id = ANY(%s)
                                ORDER BY nct_id, effective_date""", (ids,)):  # fmt: skip
            inputs.versions.setdefault(r["nct_id"], []).append(dict(r))
    return inputs


# The run ----------------------------------------------------------------------------------

Complete = Callable[[dict[str, Any]], dict[str, Any]]


def plan(inputs: Inputs, cfg: MemoConfig, today: dt.date, version: str,
         features: Sequence[str]) -> tuple[dict[str, dict[str, Any]], list[str], list[str],
                                           dict[str, dt.date]]:  # fmt: skip
    """The payloads of the chosen trials, the trials to ask for (top 100 first, by risk), the
    stored memos to delete, and the stored memos that stay current with their new date.
    `features` are the names the stored driver indexes point to (the API's `model_columns`)."""
    chosen = pl.choose(inputs.scores, inputs.history, cfg.top_n, cfg.move_threshold,
                       cfg.move_window_days, today)  # fmt: skip
    scores = {str(s["nct_id"]): s for s in inputs.scores}
    payloads: dict[str, dict[str, Any]] = {}
    for nct, (reasons, move) in chosen.items():
        payloads[nct] = pl.build(scores[nct], inputs.states.get(nct, {}),
                                 inputs.displays.get(nct), inputs.markers.get(nct),
                                 inputs.versions.get(nct, []), reasons, move, features,
                                 cfg.top_n, cfg.max_record_changes)  # fmt: skip
    current: dict[str, dt.date] = {}
    delete: list[str] = []
    for nct, memo in inputs.memos.items():
        same = (nct in payloads and memo["prompt_version"] == version
                and bytes(memo["payload_hash"]) == pl.payload_hash(payloads[nct]))  # fmt: skip
        if same:
            current[nct] = scores[nct]["as_of_date"]
        elif nct not in payloads:
            delete.append(nct)
    ask = [n for n in payloads if n not in current]
    ask.sort(key=lambda n: (pl.TOP not in payloads[n]["chosen_as"],
                            -float(scores[n]["risk_12m"]), n))  # fmt: skip
    return payloads, ask, sorted(delete), current


def run_memos(
    dsn: str,
    cfg: MemoConfig,
    complete: Complete,
    today: dt.date,
    trigger: str,
    connect: Callable[[str], Any] | None = None,
) -> tuple[int, Tally]:
    """One memo run (see the module text): its run id and its counts."""
    import psycopg

    from trialpulse.nlp.llm_labeler import DailyLimitError, InvalidResponseError

    open_db = connect or (lambda d: psycopg.connect(d, autocommit=True, connect_timeout=30))
    version = prompt_version(cfg)
    system = load_prompt(cfg)
    with open_db(dsn) as con:
        row = con.execute(
            """INSERT INTO monitoring.memo_runs (status, trigger, model, prompt_version)
               VALUES ('running', %s, %s, %s) RETURNING run_id""",
            (trigger, cfg.model, version),
        ).fetchone()
        assert row is not None
        run_id = int(row[0])
    tally = Tally()
    try:
        with open_db(dsn) as con:
            inputs = read_inputs(con, cfg, today)
        payloads, ask, delete, current = plan(inputs, cfg, today, version, model_columns())
        tally.n_chosen, tally.n_current = len(payloads), len(current)
        with open_db(dsn) as con, con.transaction():
            for nct, day in current.items():
                con.execute("UPDATE serving.trial_memos SET valid_as_of = %s WHERE nct_id = %s",
                            (day, nct))  # fmt: skip
            if delete:
                con.execute("DELETE FROM serving.trial_memos WHERE nct_id = ANY(%s)", (delete,))
        tally.n_deleted = len(delete)
        tally.n_over_cap = max(0, len(ask) - cfg.cap_per_run)
        for nct in ask[: cfg.cap_per_run]:
            payload = payloads[nct]
            try:
                answer = complete(request_body(cfg, system, payload))
            except DailyLimitError as exc:
                tally.status, tally.stopped_reason = "stopped", str(exc)
                break
            except InvalidResponseError:
                tally.n_called += 1
                tally.reject(["invalid_json"], {"nct_id": nct, "paragraphs": None,
                             "reasons": ["invalid_json"], "details": [], "payload": payload},
                             cfg.rejected_samples)  # fmt: skip
                continue
            tally.n_called += 1
            usage = answer.get("usage") or {}
            tally.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            tally.completion_tokens += int(usage.get("completion_tokens") or 0)
            tally.total_tokens += int(usage.get("total_tokens") or 0)
            paragraphs = paragraphs_of(answer)
            if paragraphs is None:
                tally.reject(["invalid_json"], {"nct_id": nct, "paragraphs": None,
                             "reasons": ["invalid_json"], "details": [], "payload": payload},
                             cfg.rejected_samples)  # fmt: skip
                continue
            result = checks.check(paragraphs, payload, inputs.names)
            if not result.passed:
                tally.reject(result.reasons, {"nct_id": nct, "paragraphs": result.paragraphs,
                             "reasons": result.reasons, "details": result.details,
                             "payload": payload}, cfg.rejected_samples)  # fmt: skip
                continue
            with open_db(dsn) as con, con.transaction():
                con.execute(
                    """INSERT INTO serving.trial_memos (nct_id, payload_hash, payload_version,
                         payload, prompt_version, model, chosen_as, paragraphs, valid_as_of,
                         generated_at, run_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s)
                       ON CONFLICT (nct_id) DO UPDATE SET payload_hash = EXCLUDED.payload_hash,
                         payload_version = EXCLUDED.payload_version, payload = EXCLUDED.payload,
                         prompt_version = EXCLUDED.prompt_version, model = EXCLUDED.model,
                         chosen_as = EXCLUDED.chosen_as, paragraphs = EXCLUDED.paragraphs,
                         valid_as_of = EXCLUDED.valid_as_of, generated_at = now(),
                         run_id = EXCLUDED.run_id""",
                    (nct, pl.payload_hash(payload), pl.PAYLOAD_VERSION,
                     json.dumps(payload), version, cfg.model, payload["chosen_as"],
                     result.paragraphs, dt.date.fromisoformat(payload["score_date"]), run_id),
                )  # fmt: skip
            tally.n_stored += 1
    except Exception as exc:
        tally.status, tally.stopped_reason = "failed", type(exc).__name__
        _finish(open_db, dsn, run_id, tally, today)
        raise
    _finish(open_db, dsn, run_id, tally, today)
    return run_id, tally


def _finish(open_db: Callable[[str], Any], dsn: str, run_id: int, tally: Tally,
            today: dt.date) -> None:  # fmt: skip
    with open_db(dsn) as con:
        con.execute(
            """UPDATE monitoring.memo_runs SET finished_at = now(), status = %s,
                 score_date = %s, n_chosen = %s, n_current = %s, n_called = %s, n_stored = %s,
                 n_rejected = %s, n_deleted = %s, n_over_cap = %s, rejections = %s,
                 rejected_samples = %s, prompt_tokens = %s, completion_tokens = %s,
                 total_tokens = %s, stopped_reason = %s WHERE run_id = %s""",
            (tally.status, today, tally.n_chosen, tally.n_current, tally.n_called,
             tally.n_stored, tally.n_rejected, tally.n_deleted, tally.n_over_cap,
             json.dumps(tally.rejections), json.dumps(tally.samples), tally.prompt_tokens,
             tally.completion_tokens, tally.total_tokens, tally.stopped_reason, run_id),
        )  # fmt: skip


def groq_complete(cfg: MemoConfig) -> tuple[Complete, Callable[[], None]]:
    """A completion function on Step 6's Groq client (its limits and retries), and its close."""
    import httpx

    from trialpulse.config import Secrets
    from trialpulse.nlp.llm_labeler import GroqClient, InvalidKeyError, LLMSettings

    key = Secrets().groq_api_key
    if key is None:
        raise InvalidKeyError("GROQ_API_KEY is not set")
    settings = LLMSettings(model=cfg.model, reasoning_effort=cfg.reasoning_effort,
                           max_completion_tokens=cfg.max_completion_tokens)  # fmt: skip
    http = httpx.Client(timeout=180)
    client = GroqClient(http, key, settings)
    return (lambda body: client.complete(body, ESTIMATE_TOKENS)), http.close


def main(argv: Sequence[str] | None = None) -> int:
    from trialpulse.live.daily import _dsn

    parser = argparse.ArgumentParser(description="The memo run of ADR 0084.")
    parser.add_argument("--target", choices=("local", "neon"), required=True)
    parser.add_argument("--trigger", default="manual")
    args = parser.parse_args(argv)
    cfg = load_project_config().memo
    complete, close = groq_complete(cfg)
    try:
        run_id, tally = run_memos(_dsn(args.target), cfg, complete,
                                  dt.datetime.now(dt.UTC).date(), args.trigger)  # fmt: skip
    finally:
        close()
    alert = tally.alert(cfg.rejection_alert_share)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as fh:
            fh.write(f"memo_alert={'rejections' if alert else ''}\n")
    summary = {"run_id": run_id, "status": tally.status, "chosen": tally.n_chosen,
               "current": tally.n_current, "called": tally.n_called, "stored": tally.n_stored,
               "rejected": tally.n_rejected, "rejections": tally.rejections,
               "deleted": tally.n_deleted, "over_cap": tally.n_over_cap,
               "total_tokens": tally.total_tokens, "stopped": tally.stopped_reason,
               "rejection_alert": alert}  # fmt: skip
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
