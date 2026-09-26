"""The LLM labeler with a mocked provider: prompt loading and the leak check, the request,
answer validation, rate limits, caching and resume, the committed-prompt rule, and the
10,000-text sample that excludes every gold text."""

import json
import subprocess
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import SecretStr

from trialpulse.nlp.gold import (
    LABELS_PATH,
    SAMPLE_PATH,
    EarlyStop,
    load_sample,
    normalize_text,
    text_sha256,
)
from trialpulse.nlp.holdout import heldout_hashes
from trialpulse.nlp.llm_labeler import (
    FINAL_PROMPT,
    LLM_SAMPLE_PATH,
    RESPONSE_SCHEMA,
    DailyLimitError,
    GroqClient,
    InvalidResponseError,
    Labeler,
    LabelItem,
    LLMSettings,
    PromptLeakError,
    RunStats,
    build_llm_sample,
    check_rendered_prompt,
    committed_prompt,
    days_needed,
    gold_hashes,
    load_prompt,
    parse_duration,
    parse_labels,
    render_messages,
    request_body,
)

SETTINGS = LLMSettings(batch_size=2)
URL = f"{SETTINGS.base_url}/chat/completions"
KEY = SecretStr("gsk_synthetic_test_key_value")
HEADERS = {
    "x-ratelimit-remaining-tokens": "7000",
    "x-ratelimit-reset-tokens": "1.5s",
    "x-ratelimit-remaining-requests": "900",
}


def _answer(labels: list[tuple[str, str]], tokens: int = 100) -> httpx.Response:
    content = json.dumps({"labels": [{"id": i, "label": lab} for i, lab in labels]})
    body = {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"total_tokens": tokens, "completion_tokens": tokens // 4},
    }
    return httpx.Response(200, json=body, headers=HEADERS)


def _items(n: int) -> list[LabelItem]:
    return [LabelItem(f"NCT{i:08d}", f"{i:064x}", f"Synthetic reason {i}") for i in range(n)]


def _labeler(tmp_path: Path, http: httpx.Client | None, waits: list[float]) -> Labeler:
    client = GroqClient(http, KEY, SETTINGS, sleep=waits.append) if http else None
    return Labeler("Label these.", "reason_test.md", SETTINGS, tmp_path / "cache", client)


def test_prompts_load_only_from_the_prompts_folder(tmp_path: Path) -> None:
    (tmp_path / "reason_v1.md").write_text("Label reasons.", encoding="utf-8")
    (tmp_path / "README.md").write_text("notes", encoding="utf-8")
    (tmp_path.parent / "outside.md").write_text("outside", encoding="utf-8")
    assert load_prompt("reason_v1.md", tmp_path) == "Label reasons."
    for name in ("README.md", "../outside.md", "missing.md"):
        with pytest.raises(ValueError, match="not a prompt file"):
            load_prompt(name, tmp_path)


def test_a_rendered_prompt_with_a_test_text_is_refused() -> None:
    secret = "Synthetic: the pandemic halted every study visit"
    hashes = {text_sha256(normalize_text(secret))}
    check_rendered_prompt("Label each reason.", hashes, [secret])
    with pytest.raises(PromptLeakError, match="at lines"):
        check_rendered_prompt(f"Rules.\nExample: `{secret}` is covid19.", hashes, [])
    assert secret not in str(render_messages("x", []))


def test_the_final_prompt_passes_the_guard() -> None:
    texts = (
        [i.why_stopped for i in load_sample(SAMPLE_PATH) if i.split == "test"]
        if SAMPLE_PATH.is_file()
        else []
    )
    check_rendered_prompt(load_prompt(FINAL_PROMPT), heldout_hashes(LABELS_PATH), texts)


def test_the_request_is_temperature_zero_with_a_strict_schema() -> None:
    body = request_body(SETTINGS, render_messages("Label.", [("1", "Slow accrual")]))
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["temperature"] == 0.0
    schema = body["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"] == RESPONSE_SCHEMA
    assert json.loads(body["messages"][1]["content"]) == {
        "items": [{"id": "1", "text": "Slow accrual"}]
    }


def test_answers_are_validated() -> None:
    ids = ["1", "2"]
    good = json.dumps({"labels": [{"id": "2", "label": "funding"}, {"id": "1", "label": "other"}]})
    assert parse_labels(good, ids) == {"1": "other", "2": "funding"}
    bad = [
        "not json",
        json.dumps({"items": []}),
        json.dumps({"labels": [{"id": "1", "label": "other"}]}),  # a missing id
        json.dumps({"labels": [{"id": "1", "label": "other"}] * 2}),  # a repeated id
        json.dumps({"labels": [{"id": "1", "label": "other"}, {"id": "3", "label": "other"}]}),
        json.dumps({"labels": [{"id": "1", "label": "other"}, {"id": "2", "label": "harm"}]}),
    ]
    for content in bad:
        with pytest.raises(InvalidResponseError):
            parse_labels(content, ids)


def test_parse_duration() -> None:
    assert parse_duration("7.66s") == pytest.approx(7.66)
    assert parse_duration("2m59.56s") == pytest.approx(179.56)
    assert parse_duration("1h2m3s") == pytest.approx(3723)
    assert parse_duration("120ms") == pytest.approx(0.12)
    assert parse_duration(None) == 0.0


@respx.mock
def test_labels_are_cached_and_a_rerun_makes_no_request(tmp_path: Path) -> None:
    route = respx.post(URL).mock(
        side_effect=[_answer([("1", "accrual"), ("2", "funding")]), _answer([("1", "safety")])]
    )
    items = _items(3)
    with httpx.Client() as http:
        first = _labeler(tmp_path, http, []).label(items)
    assert first == {items[0].key: "accrual", items[1].key: "funding", items[2].key: "safety"}
    assert route.call_count == 2
    sent = json.loads(route.calls[0].request.content)
    assert sent["temperature"] == 0.0
    assert route.calls[0].request.headers["authorization"] == f"Bearer {KEY.get_secret_value()}"

    with httpx.Client() as http:
        rerun = _labeler(tmp_path, http, [])
        assert rerun.label(items) == first
    assert route.call_count == 2  # everything came from the cache
    assert rerun.stats.from_cache == 3
    cache_text = "".join(p.read_text("utf-8") for p in (tmp_path / "cache").rglob("*.json"))
    assert KEY.get_secret_value() not in cache_text  # the key is never cached


@respx.mock
def test_a_per_minute_429_waits_and_retries(tmp_path: Path) -> None:
    limited = httpx.Response(
        429,
        json={"error": {"message": "Rate limit reached on tokens per minute (TPM)"}},
        headers={"retry-after": "3"},
    )
    respx.post(URL).mock(side_effect=[limited, _answer([("1", "other")])])
    waits: list[float] = []
    with httpx.Client() as http:
        labels = _labeler(tmp_path, http, waits).label(_items(1))
    assert len(labels) == 1
    assert any(w >= 3 for w in waits)


@respx.mock
def test_a_daily_limit_stops_calls_but_keeps_reading_the_cache(tmp_path: Path) -> None:
    items = _items(4)
    respx.post(URL).mock(side_effect=[_answer([("1", "accrual"), ("2", "business")])])
    with httpx.Client() as http:
        _labeler(tmp_path, http, []).label(items[2:])  # caches the second batch
    daily = httpx.Response(
        429,
        json={"error": {"message": "Rate limit reached on tokens per day (TPD): Limit 200000"}},
        headers={"retry-after": "3600"},
    )
    route = respx.post(URL).mock(side_effect=[daily])
    before = route.call_count
    with httpx.Client() as http:
        labeler = _labeler(tmp_path, http, [])
        labels = labeler.label(items)
    assert route.call_count - before == 1  # one refused call, then no more calls
    assert labels == {items[2].key: "accrual", items[3].key: "business"}
    assert "daily limit" in labeler.stats.stopped
    assert KEY.get_secret_value() not in labeler.stats.stopped


@respx.mock
def test_server_errors_are_retried(tmp_path: Path) -> None:
    route = respx.post(URL).mock(side_effect=[httpx.Response(503), _answer([("1", "efficacy")])])
    with httpx.Client() as http:
        labels = _labeler(tmp_path, http, []).label(_items(1))
    assert list(labels.values()) == ["efficacy"]
    assert route.call_count == 2


@respx.mock
def test_invalid_answers_are_retried_then_the_batch_is_split(tmp_path: Path) -> None:
    missing = _answer([("1", "accrual")])  # the batch had two ids
    route = respx.post(URL).mock(
        side_effect=[missing, missing, _answer([("1", "accrual")]), _answer([("1", "covid19")])]
    )
    items = _items(2)
    with httpx.Client() as http:
        labels = _labeler(tmp_path, http, []).label(items)
    assert labels == {items[0].key: "accrual", items[1].key: "covid19"}
    assert route.call_count == 4


@respx.mock
def test_an_item_that_never_validates_is_counted_as_failed(tmp_path: Path) -> None:
    respx.post(URL).mock(return_value=_answer([("9", "other")]))
    with httpx.Client() as http:
        labeler = _labeler(tmp_path, http, [])
        assert labeler.label(_items(1)) == {}
    assert labeler.stats.failed == 1
    assert not list((tmp_path / "cache").rglob("*.json"))  # invalid answers are not cached


def test_without_a_client_only_the_cache_is_read(tmp_path: Path) -> None:
    labeler = _labeler(tmp_path, None, [])
    assert labeler.label(_items(3)) == {}
    assert labeler.stats.items == 3


def test_the_client_refuses_the_panel_model_family() -> None:
    with httpx.Client() as http, pytest.raises(ValueError, match="different family"):
        GroqClient(http, KEY, LLMSettings(model="anthropic/claude-sonnet-5"))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_test_items_need_a_committed_unchanged_prompt(tmp_path: Path) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    prompt = prompts / "reason_v1.md"
    prompt.write_text("Label reasons.\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    with pytest.raises(ValueError, match="failed"):
        committed_prompt("reason_v1.md", tmp_path, prompts)  # untracked
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "prompt")
    commit = committed_prompt("reason_v1.md", tmp_path, prompts)
    assert len(commit) == 40
    prompt.write_text("Label reasons differently.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="uncommitted changes"):
        committed_prompt("reason_v1.md", tmp_path, prompts)


def _record(n: int, text: str, status: str = "TERMINATED", year: int = 2015) -> EarlyStop:
    return EarlyStop(f"NCT{n:08d}", status, text, f"{year}-06-01", "ACTUAL", f"{year}-07-01")


def test_the_llm_sample_excludes_gold_texts_after_normalization() -> None:
    records = [
        _record(n, f"Synthetic reason {n}", "WITHDRAWN" if n % 3 else "TERMINATED", 2010 + n % 5)
        for n in range(200)
    ]
    records += [
        _record(900, "  GOLD reason: slow accrual!! "),
        _record(901, "Gold reason, funding"),
    ]
    gold = {
        text_sha256(normalize_text(t))
        for t in ("gold reason: slow accrual", "gold reason, funding.")
    }
    sample = build_llm_sample(records, ("TERMINATED", "WITHDRAWN"), "src", gold, 150, seed=42)
    assert len(sample) == 150
    assert len({i.text_sha256 for i in sample}) == 150
    assert not {i.text_sha256 for i in sample} & gold
    assert sample == build_llm_sample(records, ("TERMINATED", "WITHDRAWN"), "src", gold, 150, 42)
    assert sample != sorted(sample, key=lambda i: i.key)  # a seeded random order


def test_the_real_llm_sample_holds_no_gold_text() -> None:
    """Runs where the gitignored sample exists: 10,000 distinct texts, none of them gold."""
    if not LLM_SAMPLE_PATH.is_file():
        pytest.skip("no LLM sample on this machine")
    sample = load_sample(LLM_SAMPLE_PATH)
    hashes = [i.text_sha256 for i in sample]
    assert len(hashes) == len(set(hashes)) == 10_000
    assert all(text_sha256(normalize_text(i.why_stopped)) == i.text_sha256 for i in sample)
    assert not set(hashes) & gold_hashes()


def test_days_needed_uses_this_runs_tokens_per_text() -> None:
    stats = RunStats(items=1000, labeled=250, from_cache=50, tokens=20_000, cached_tokens=5_000)
    assert days_needed(750, stats, LLMSettings()) == round(750 * 100 / 200_000, 1)
    assert days_needed(0, stats, LLMSettings()) == 0.0
    assert days_needed(10, RunStats(), LLMSettings()) is None


@respx.mock
def test_the_client_raises_on_a_daily_limit_or_no_requests_left() -> None:
    daily = httpx.Response(
        429, json={"error": {"message": "Rate limit reached on requests per day (RPD)"}}
    )
    respx.post(URL).mock(return_value=daily)
    with httpx.Client() as http, pytest.raises(DailyLimitError, match="daily limit"):
        GroqClient(http, KEY, SETTINGS, sleep=lambda _: None).complete({}, 10)

    last = _answer([("1", "other")])
    last.headers["x-ratelimit-remaining-requests"] = "0"
    respx.post(URL).mock(return_value=last)
    with httpx.Client() as http:
        client = GroqClient(http, KEY, SETTINGS, sleep=lambda _: None)
        client.complete({}, 10)  # the day's last request succeeds
        with pytest.raises(DailyLimitError, match="no requests left"):
            client.complete({}, 10)


@respx.mock
def test_a_long_per_minute_wait_is_waited_out_not_treated_as_daily(tmp_path: Path) -> None:
    long_minute = httpx.Response(
        429,
        json={"error": {"message": "Rate limit reached on tokens per minute (TPM): Limit 8000"}},
        headers={"retry-after": "293"},
    )
    respx.post(URL).mock(side_effect=[long_minute, _answer([("1", "other")])])
    waits: list[float] = []
    with httpx.Client() as http:
        labeler = _labeler(tmp_path, http, waits)
        assert len(labeler.label(_items(1))) == 1
    assert labeler.stats.stopped == ""
    assert any(w >= 293 for w in waits)


@respx.mock
def test_a_split_batch_is_found_again_on_a_rerun(tmp_path: Path) -> None:
    missing = _answer([("1", "accrual")])  # the batch had two ids
    route = respx.post(URL).mock(
        side_effect=[missing, missing, _answer([("1", "accrual")]), _answer([("1", "funding")])]
    )
    items = _items(2)
    with httpx.Client() as http:
        first = _labeler(tmp_path, http, []).label(items)
    calls = route.call_count
    reader = _labeler(tmp_path, None, [])  # the status path: cache only
    assert reader.label(items) == first
    assert reader.stats.from_cache == 2
    with httpx.Client() as http:
        assert _labeler(tmp_path, http, []).label(items) == first
    assert route.call_count == calls  # the rerun makes no call


@respx.mock
def test_a_provider_validation_failure_is_retried_then_split(tmp_path: Path) -> None:
    refused = httpx.Response(
        400, json={"error": {"code": "json_validate_failed", "message": "max completion tokens"}}
    )
    respx.post(URL).mock(
        side_effect=[refused, refused, _answer([("1", "other")]), _answer([("1", "safety")])]
    )
    items = _items(2)
    with httpx.Client() as http:
        labels = _labeler(tmp_path, http, []).label(items)
    assert labels == {items[0].key: "other", items[1].key: "safety"}


@respx.mock
def test_other_provider_errors_stop_the_run_but_keep_the_labels(tmp_path: Path) -> None:
    items = _items(4)
    respx.post(URL).mock(side_effect=[_answer([("1", "accrual"), ("2", "business")])])
    with httpx.Client() as http:
        _labeler(tmp_path, http, []).label(items[2:])
    respx.post(URL).mock(
        return_value=httpx.Response(401, json={"error": {"code": "invalid_api_key"}})
    )
    with httpx.Client() as http:
        labeler = _labeler(tmp_path, http, [])
        labels = labeler.label(items)
    assert labels == {items[2].key: "accrual", items[3].key: "business"}
    assert labeler.stats.stopped == "HTTP 401 (invalid_api_key)"


@respx.mock
def test_network_failures_never_quote_the_key(tmp_path: Path) -> None:
    respx.post(URL).mock(
        side_effect=httpx.ConnectError(f"refused, header {KEY.get_secret_value()}")
    )
    with httpx.Client() as http:
        labeler = _labeler(tmp_path, http, [])
        assert labeler.label(_items(1)) == {}
    assert "provider unavailable after retries (ConnectError)" in labeler.stats.stopped
    assert KEY.get_secret_value() not in labeler.stats.stopped


def test_the_key_is_stripped_and_checked() -> None:
    with httpx.Client() as http:
        GroqClient(http, SecretStr("  gsk_padded_key \n"), SETTINGS)  # surrounding space is fine
        for bad in ("gsk_with space", "gsk_ctrl\x07", "   "):
            with pytest.raises(ValueError, match="spaces or control characters") as info:
                GroqClient(http, SecretStr(bad), SETTINGS)
            if bad.strip():
                assert bad.strip() not in str(info.value)


def test_sample_runs_need_the_final_prompt_and_a_valid_size() -> None:
    from trialpulse.nlp.llm_labeler import main

    for argv, message in (
        (["--sample", "0"], "1 to 10000"),
        (["--sample", "10001"], "1 to 10000"),
        (["--sample", "5", "--prompt", "reason_v1.md"], "final prompt only"),
        (["--status", "--prompt", "reason_v1.md"], "final prompt only"),
        (["--split", "test", "--prompt", "reason_v1.md"], "final prompt only"),
    ):
        with pytest.raises(SystemExit, match=message):
            main(argv)
