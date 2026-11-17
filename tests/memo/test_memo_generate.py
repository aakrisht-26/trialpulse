"""The memo run (ADR 0084) on a seeded scratch database: answers in Groq's format through
Step 6's client (respx, no network), the payload-hash skip, rejections, the cap, the daily
limit, deletions and the step output."""

import datetime as dt
import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
import respx

from trialpulse.config import REPO_ROOT, load_project_config
from trialpulse.memo import generate as g

URL = "https://api.groq.com/openai/v1/chat/completions"
spec = importlib.util.spec_from_file_location("api_seed", REPO_ROOT / "tests/api/seed.py")
assert spec is not None
assert spec.loader is not None
seeding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seeding)
DAY = seeding.DAY


def _plain(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def cfg(**over: Any) -> Any:
    return load_project_config().memo.model_copy(update={"top_n": 3, "cap_per_run": 10, **over})


def faithful(payload: dict[str, Any]) -> list[str]:
    """A memo that keeps to the payload, as the prompt asks."""
    r12, r24 = payload["risk_percent"]["12"], payload["risk_percent"]["24"]
    first = (f"{payload['nct_id']} has elevated early-stop risk: {r12}% within 12 months and "
             f"{r24}% within 24 months, scored on {payload['score_date']}. It is among the "
             f"{payload['top_n']} unflagged trials with the highest 12-month risk.")  # fmt: skip
    d = payload["drivers"]["12"][0]
    second = (f"The strongest driver at 12 months is `{d['feature']}` ({d['value']}), which "
              f"{d['direction']} the risk. A driver describes what moved the model's score, "
              "not why a trial stops.")  # fmt: skip
    third = "No record change is held since the trial's first live version."
    return [first, second, third]


def envelope(paragraphs: list[str] | None, content: str | None = None) -> dict[str, Any]:
    """A chat completion in Groq's response format."""
    text = content if content is not None else json.dumps({"paragraphs": paragraphs})
    return {
        "id": "chatcmpl-test", "object": "chat.completion", "created": 1794900000,
        "model": "openai/gpt-oss-120b",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "logprobs": None, "finish_reason": "stop"}],
        "usage": {"queue_time": 0.1, "prompt_tokens": 1200, "prompt_time": 0.01,
                  "completion_tokens": 300, "completion_time": 0.2, "total_tokens": 1500,
                  "total_time": 0.21, "completion_tokens_details": {"reasoning_tokens": 90}},
        "system_fingerprint": "fp_test", "x_groq": {"id": "req_test"},
    }  # fmt: skip


HEADERS = {"x-ratelimit-remaining-tokens": "7000", "x-ratelimit-reset-tokens": "1s",
           "x-ratelimit-remaining-requests": "900"}  # fmt: skip


def answering(write: Any) -> Any:
    """A respx side effect: the memo `write` makes of the payload the request carries."""

    def reply(request: httpx.Request) -> httpx.Response:
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        return httpx.Response(200, json=envelope(write(payload)), headers=HEADERS)

    return reply


@pytest.fixture
def seeded(scratch: str, monkeypatch: pytest.MonkeyPatch) -> str:
    seeding.seed(_plain(scratch))
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key_not_real")
    return _plain(scratch)


def run(dsn: str, c: Any) -> tuple[int, g.Tally]:
    complete, close = g.groq_complete(c)
    try:
        return g.run_memos(dsn, c, complete, DAY, "test")
    finally:
        close()


def memos(dsn: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn) as con:
        return con.execute(
            """SELECT nct_id, valid_as_of, chosen_as, cardinality(paragraphs),
               payload ? 'nct_id' FROM serving.trial_memos ORDER BY nct_id"""
        ).fetchall()


@respx.mock
def test_a_run_stores_checked_memos_and_a_second_run_asks_nothing(seeded: str) -> None:
    route = respx.post(URL).mock(side_effect=answering(faithful))
    run_id, tally = run(seeded, cfg())
    assert (tally.status, tally.n_chosen, tally.n_called, tally.n_stored) == ("succeeded", 3, 3, 3)
    assert (tally.prompt_tokens, tally.completion_tokens, tally.total_tokens) == (3600, 900, 4500)
    stored = memos(seeded)
    assert [(m[1], m[2], m[3], m[4]) for m in stored] == [(DAY, ["top_100"], 3, True)] * 3
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["temperature"] == 0.0
    assert body["response_format"]["json_schema"]["schema"] == g.RESPONSE_SCHEMA
    assert body["messages"][0]["content"] == g.load_prompt(cfg())
    with psycopg.connect(seeded) as con:
        row = con.execute(
            """SELECT status, n_called, n_stored, total_tokens, trigger
               FROM monitoring.memo_runs WHERE run_id = %s""",
            (run_id,),
        ).fetchone()
    assert row == ("succeeded", 3, 3, 4500, "test")

    _, again = run(seeded, cfg())  # the same day: every payload hash unchanged
    assert (again.n_called, again.n_current, again.n_stored) == (0, 3, 0)
    assert route.call_count == 3


@respx.mock
def test_fabricated_memos_are_never_stored_and_counted_by_reason(seeded: str) -> None:
    def invented(payload: dict[str, Any]) -> list[str]:
        good = faithful(payload)
        return [good[0].replace("% within 12", "7% within 12", 1), good[1],
                "The trial will fail; its risk doubled over twelve months."]  # fmt: skip

    respx.post(URL).mock(side_effect=answering(invented))
    run_id, tally = run(seeded, cfg(rejected_samples=2))
    assert (tally.n_called, tally.n_stored, tally.n_rejected) == (3, 0, 3)
    assert memos(seeded) == []
    for reason in ("number", "banned_language", "comparison", "number_word"):
        assert tally.rejections[reason] == 3, reason
    assert tally.alert(0.2)
    with psycopg.connect(seeded) as con:
        samples, rejections = con.execute(
            "SELECT rejected_samples, rejections FROM monitoring.memo_runs WHERE run_id = %s",
            (run_id,)).fetchone()  # fmt: skip
    assert len(samples) == 2  # at most rejected_samples, for the review
    assert set(samples[0]) == {"nct_id", "paragraphs", "reasons", "details", "payload"}
    assert rejections["number"] == 3


@respx.mock
def test_an_answer_that_is_not_the_expected_json_is_a_rejection(seeded: str) -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json=envelope(None, "not json"),
                                                     headers=HEADERS))  # fmt: skip
    _, tally = run(seeded, cfg())
    assert tally.rejections == {"invalid_json": 3}
    assert tally.n_stored == 0


@respx.mock
def test_the_cap_limits_the_calls_of_a_run(seeded: str) -> None:
    respx.post(URL).mock(side_effect=answering(faithful))
    _, tally = run(seeded, cfg(cap_per_run=2))
    assert (tally.n_called, tally.n_stored, tally.n_over_cap) == (2, 2, 1)


@respx.mock
def test_a_daily_limit_stops_the_run_cleanly(seeded: str) -> None:
    limited = httpx.Response(429, json={"error": {
        "message": "Rate limit reached for model `openai/gpt-oss-120b` on tokens per day (TPD): "
                   "Limit 200000, Used 199000, Requested 2000. Please try again in 7m12s.",
        "type": "tokens", "code": "rate_limit_exceeded"}})  # fmt: skip
    respx.post(URL).mock(side_effect=[httpx.Response(200, json=envelope(None, "{}"),
                                                     headers=HEADERS), limited])  # fmt: skip
    run_id, tally = run(seeded, cfg())
    assert tally.status == "stopped"
    assert tally.stopped_reason is not None
    assert "TPD" in tally.stopped_reason
    assert tally.n_called == 1
    with psycopg.connect(seeded) as con:
        assert con.execute("SELECT status FROM monitoring.memo_runs WHERE run_id = %s",
                           (run_id,)).fetchone() == ("stopped",)  # fmt: skip


@respx.mock
def test_a_changed_payload_regenerates_and_a_trial_no_longer_chosen_is_deleted(
    seeded: str,
) -> None:
    respx.post(URL).mock(side_effect=answering(faithful))
    run(seeded, cfg())
    before = {m[0] for m in memos(seeded)}
    with psycopg.connect(seeded) as con:
        top, other = con.execute(
            """SELECT nct_id FROM serving.trial_scores_latest WHERE NOT flagged
               ORDER BY risk_12m DESC, nct_id LIMIT 2""").fetchall()  # fmt: skip
        con.execute("UPDATE serving.trial_scores_latest SET risk_12m = 0.001 WHERE nct_id = %s",
                    top)  # it leaves the top 3  # fmt: skip
        con.execute("""UPDATE serving.trial_scores_latest SET risk_24m = risk_24m + 0.05
                       WHERE nct_id = %s""", other)  # its payload changes  # fmt: skip
        con.commit()
    _, tally = run(seeded, cfg())
    after = {m[0] for m in memos(seeded)}
    assert top[0] in before
    assert top[0] not in after
    assert tally.n_deleted == 1
    assert tally.n_called == 2  # the changed one and the newcomer to the top 3
    assert tally.n_current == 1


def test_a_provider_failure_ends_the_run_as_failed(seeded: str) -> None:
    from trialpulse.nlp.llm_labeler import ProviderError

    def broken(body: dict[str, Any]) -> dict[str, Any]:
        raise ProviderError("HTTP 401 (invalid_api_key)", retry_later=False)

    with pytest.raises(ProviderError):
        g.run_memos(seeded, cfg(), broken, DAY, "test")
    with psycopg.connect(seeded) as con:
        row = con.execute("SELECT status, stopped_reason FROM monitoring.memo_runs").fetchone()
    assert row == ("failed", "ProviderError")


@respx.mock
def test_the_command_writes_the_rejection_alert_for_the_workflow(
    seeded: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.post(URL).mock(side_effect=answering(lambda p: ["x", "y"]))  # all refused
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    small = load_project_config().model_copy(update={"memo": cfg()})
    monkeypatch.setattr(g, "load_project_config", lambda: small)
    import trialpulse.memo.generate as module

    real_now = dt.datetime

    class Frozen(real_now):  # the seeded scores are dated DAY
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return real_now.combine(DAY, dt.time(12), tzinfo=tz)

    monkeypatch.setattr(module.dt, "datetime", Frozen)
    assert g.main(["--target", "local", "--trigger", "workflow_dispatch"]) == 0
    assert output.read_text(encoding="utf-8").strip() == "memo_alert=rejections"


@respx.mock
def test_the_review_file_holds_the_stored_memos_and_the_rejected_ones(
    seeded: str, tmp_path: Path
) -> None:
    from trialpulse.memo import review

    respx.post(URL).mock(side_effect=answering(lambda p: [*faithful(p)[:2], "It will fail."]))
    run(seeded, cfg())  # every memo refused
    respx.post(URL).mock(side_effect=answering(faithful))
    run(seeded, cfg())  # then stored
    assert review.main(["--target", "local", "--out-dir", str(tmp_path)]) == 0
    (path,) = tmp_path.glob("*.md")
    text = path.read_text(encoding="utf-8")
    assert text.count("Check run again: passed.") == 3
    assert text.count("### R") == 3
    assert "Reasons: banned_language" in text
    assert "```json" in text
    for name in ("Acme Pharma", "Northern University Hospital", "Hand-placed trial"):
        assert name not in text
