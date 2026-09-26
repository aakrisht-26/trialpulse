"""LLM labeling of why_stopped texts (CLAUDE.md Section 11, ADR 0010).

The prompt is a file in src/trialpulse/nlp/prompts/, sent verbatim as the system message.
The user message holds only the batch ({"id", "text"} items). Before any call:

- the rendered prompt (system message and an empty batch) is checked with
  holdout.prompt_leaks against the gold-test texts;
- the model id is checked with panel.check_graded_model (not the panel's model family);
- test items are labeled only with the final prompt, committed and unchanged in git.

Calls go to Groq's OpenAI-compatible API through httpx (provider and model configurable),
at temperature 0 with strict JSON-schema output, and every answer is validated again here.
Texts go in batches of 25. Each valid answer is cached under a hash of the full request, so a
rerun never repeats a call and an interrupted run resumes. The per-minute budgets come from
the response headers; a daily limit stops the run cleanly, keeping all labels so far. The API
key comes from GROQ_API_KEY and is never printed, logged or cached.

    uv run python -m trialpulse.nlp.llm_labeler --split dev --prompt reason_v1.md
    uv run python -m trialpulse.nlp.llm_labeler --split test
    uv run python -m trialpulse.nlp.llm_labeler --sample 10000
    uv run python -m trialpulse.nlp.llm_labeler --status

Expected refusals (a non-final or uncommitted prompt, a missing input, a bad --sample size)
print one "refused:" line and exit with code 2. A run stopped by a provider limit saves its
labels, prints its summary and one "stopped:" line, and exits with code 4.
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import random
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from trialpulse.cli import RefusedError, StoppedEarlyError, run
from trialpulse.config import REPO_ROOT, ProjectConfig, Secrets, load_project_config
from trialpulse.ingest.ctgov_api import ApiClient
from trialpulse.nlp.evaluate import write_predictions
from trialpulse.nlp.gold import (
    LABELS_PATH,
    NLP_DIR,
    PULL_CACHE,
    SAMPLE_PATH,
    EarlyStop,
    GoldItem,
    distinct_items,
    load_sample,
    pull_early_stops,
    source_label,
    stratified_sample,
    write_sample,
)
from trialpulse.nlp.holdout import PROMPTS_DIR, heldout_hashes, prompt_leaks
from trialpulse.nlp.panel import check_graded_model
from trialpulse.nlp.taxonomy import LABELS

FINAL_PROMPT = "reason_v2.md"  # chosen on dev (ties go to the earlier version)
SAMPLE_SIZE = 10_000
LLM_DIR = NLP_DIR / "llm"
CACHE_DIR = LLM_DIR / "cache"
LABELS_DIR = LLM_DIR / "labels"
LLM_SAMPLE_PATH = LLM_DIR / "sample.csv"
VALIDATION_ATTEMPTS = 2  # calls per batch before it is split in half
MAX_RATE_WAIT_S = 900.0  # longest wait for a per-minute limit before the run stops
Key = tuple[str, str]

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string", "enum": list(LABELS)},
                },
                "required": ["id", "label"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["labels"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class LLMSettings:
    model: str = "openai/gpt-oss-120b"
    base_url: str = "https://api.groq.com/openai/v1"
    temperature: float = 0.0
    reasoning_effort: str = "medium"
    max_completion_tokens: int = 4096
    batch_size: int = 25
    requests_per_minute: int = 30
    tokens_per_day: int = 200_000  # Groq free tier for this model, for the days estimate


@dataclass(frozen=True)
class LabelItem:
    nct_id: str
    text_sha256: str
    text: str

    @property
    def key(self) -> Key:
        return (self.nct_id, self.text_sha256)


@dataclass
class RunStats:
    items: int = 0
    labeled: int = 0
    from_cache: int = 0
    failed: int = 0
    requests: int = 0
    tokens: int = 0
    cached_tokens: int = 0
    stopped: str = ""


class PromptLeakError(RefusedError, ValueError):
    pass


class UncommittedPromptError(RefusedError, ValueError):
    pass


class InvalidKeyError(RefusedError, ValueError):
    pass


class InvalidResponseError(ValueError):
    pass


class DailyLimitError(RuntimeError):
    pass


class RetryableError(RuntimeError):
    pass


class ProviderError(RuntimeError):
    """A provider failure that stops the run: an HTTP error that is neither a rate limit nor
    an invalid answer, or a network or server error that outlasted the retries."""


def prompt_stem(name: str) -> str:
    return Path(name).stem


def load_prompt(name: str, prompt_dir: Path = PROMPTS_DIR) -> str:
    """A prompt file from the prompts folder, and only from there."""
    path = (prompt_dir / name).resolve()
    if path.parent != prompt_dir.resolve() or not path.is_file() or path.name == "README.md":
        raise ValueError(f"{name!r} is not a prompt file in {prompt_dir}")
    return path.read_text(encoding="utf-8")


def render_messages(system_prompt: str, items: Sequence[tuple[str, str]]) -> list[dict[str, str]]:
    user = json.dumps({"items": [{"id": i, "text": t} for i, t in items]}, ensure_ascii=False)
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user}]


def check_rendered_prompt(system_prompt: str, hashes: set[str], texts: Iterable[str]) -> None:
    """The prompt as sent, without the items being labeled, must hold no gold-test text."""
    rendered = "\n".join(m["content"] for m in render_messages(system_prompt, []))
    leaks = prompt_leaks(rendered, hashes, texts)
    if leaks:
        raise PromptLeakError(
            f"the rendered prompt contains {len(leaks)} gold-test text(s) at lines "
            f"{sorted(set(leaks.values()))}; use dev items only"
        )


def request_body(settings: LLMSettings, messages: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "model": settings.model,
        "messages": messages,
        "temperature": settings.temperature,
        "reasoning_effort": settings.reasoning_effort,
        "include_reasoning": False,
        "max_completion_tokens": settings.max_completion_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "reason_labels", "strict": True, "schema": RESPONSE_SCHEMA},
        },
    }


def request_key(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def parse_labels(content: str, ids: Sequence[str]) -> dict[str, str]:
    """The label for every id, from a JSON answer. Anything else is invalid: bad JSON, a
    missing, repeated or unknown id, or a label outside the taxonomy."""
    try:
        data = json.loads(content)
        rows = data["labels"]
        pairs = [(str(r["id"]), str(r["label"])) for r in rows]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise InvalidResponseError(f"not the expected JSON: {exc}") from exc
    got = [i for i, _ in pairs]
    if sorted(got) != sorted(ids) or len(set(got)) != len(got):
        raise InvalidResponseError("the answer does not give each id exactly once")
    labels = dict(pairs)
    for label in labels.values():
        if label not in LABELS:
            raise InvalidResponseError(f"unknown label {label!r}")
    return labels


def parse_duration(text: str | None) -> float:
    """Groq's reset values, such as '7.66s', '2m59.56s', '1h2m3s' or '120ms', in seconds."""
    if not text:
        return 0.0
    scale = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    return sum(float(v) * scale[u] for v, u in re.findall(r"([\d.]+)(ms|h|m|s)", text))


def _rate_limit(response: httpx.Response) -> tuple[float, str]:
    """How long a 429 asks us to wait, and which limit it names: TPD, RPD, TPM, RPM, or an
    empty string when the message names none. The raw message is never returned, since it
    holds the organization id."""
    try:
        message = str(response.json().get("error", {}).get("message", ""))
    except ValueError:
        message = ""
    named = re.search(r"\((TPD|RPD|TPM|RPM)\)", message)
    kind = named.group(1) if named else ("TPD" if "per day" in message.lower() else "")
    wait = float(response.headers.get("retry-after", "0") or 0)
    if not wait:
        found = re.search(r"try again in ([\dhms.]+)", message)
        wait = parse_duration(found.group(1)) if found else 0.0
    return wait, kind


class GroqClient:
    """A minimal chat-completions client within the provider's rate limits."""

    def __init__(
        self,
        http: httpx.Client,
        api_key: SecretStr,
        settings: LLMSettings,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        check_graded_model(settings.model)
        self._http = http
        self._settings = settings
        self._auth = {"Authorization": f"Bearer {_api_token(api_key)}"}
        self._sleep = sleep
        self._clock = clock
        self._last_request = float("-inf")
        self._remaining_tokens: int | None = None
        self._tokens_reset_at = 0.0
        self._daily_exhausted = False
        self.requests_made = 0

    def _throttle(self, estimate: int) -> None:
        now = self._clock()
        wait = self._last_request + 60.0 / self._settings.requests_per_minute - now
        if self._remaining_tokens is not None and self._remaining_tokens < estimate:
            wait = max(wait, self._tokens_reset_at - now)
        if wait > 0:
            self._sleep(wait)

    def _record(self, headers: httpx.Headers) -> None:
        remaining = headers.get("x-ratelimit-remaining-tokens")
        self._remaining_tokens = int(float(remaining)) if remaining else None
        reset = parse_duration(headers.get("x-ratelimit-reset-tokens"))
        self._tokens_reset_at = self._clock() + reset + 0.5
        self._daily_exhausted = headers.get("x-ratelimit-remaining-requests") == "0"

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        try:
            for attempt in Retrying(
                retry=retry_if_exception_type((httpx.TransportError, RetryableError)),
                wait=wait_random_exponential(multiplier=2, max=60),
                stop=stop_after_attempt(6),
                sleep=self._sleep,
                reraise=True,
            ):
                with attempt:
                    self._last_request = self._clock()
                    self.requests_made += 1
                    response = self._http.post(
                        f"{self._settings.base_url}/chat/completions",
                        json=body,
                        headers=self._auth,
                    )
                    if response.status_code >= 500:
                        raise RetryableError(f"HTTP {response.status_code} from the provider")
                    return response
        except (httpx.TransportError, RetryableError) as exc:
            # "from None" drops the original exception, whose text may quote request details.
            raise ProviderError(
                f"provider unavailable after retries ({type(exc).__name__})"
            ) from None
        raise AssertionError("unreachable")  # pragma: no cover

    def complete(self, body: dict[str, Any], estimate: int) -> dict[str, Any]:
        if self._daily_exhausted:
            raise DailyLimitError("no requests left today")
        for _ in range(10):
            self._throttle(estimate)
            response = self._post(body)
            if response.status_code == 429:
                wait, kind = _rate_limit(response)
                if kind in ("TPD", "RPD"):
                    raise DailyLimitError(
                        f"daily limit ({kind}) reached; retry in about {wait:.0f} s"
                    )
                if wait > MAX_RATE_WAIT_S:
                    raise DailyLimitError(f"rate limit ({kind or 'unnamed'}) asks for {wait:.0f} s")
                self._sleep(wait + random.uniform(0.5, 2.0))
                continue
            code = _error_code(response) if response.status_code >= 400 else ""
            if response.status_code == 400 and code == "json_validate_failed":
                raise InvalidResponseError("the provider could not produce a valid answer")
            if response.status_code >= 400:
                raise ProviderError(f"HTTP {response.status_code} ({code or 'no error code'})")
            self._record(response.headers)
            data: dict[str, Any] = response.json()
            return data
        raise ProviderError("still rate limited after 10 waits")


def _api_token(api_key: SecretStr) -> str:
    """The key, stripped, and refused if it holds spaces or control characters (an HTTP
    library would quote such a header value in its error message)."""
    token = api_key.get_secret_value().strip()
    if not token or any(not ("!" <= c <= "~") for c in token):
        raise InvalidKeyError("GROQ_API_KEY is empty or contains spaces or control characters")
    return token


def _error_code(response: httpx.Response) -> str:
    """The provider's error code or type. The message itself is never used: it can quote the
    organization id."""
    try:
        error = response.json().get("error", {})
    except ValueError:
        return ""
    return str(error.get("code") or error.get("type") or "") if isinstance(error, dict) else ""


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / key[:2] / f"{key}.json"


def read_cache(cache_dir: Path, key: str) -> dict[str, Any] | None:
    path = _cache_path(cache_dir, key)
    if not path.is_file():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def write_cache(cache_dir: Path, key: str, record: dict[str, Any]) -> None:
    path = _cache_path(cache_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


@dataclass
class Labeler:
    """Labels items in batches, reading the cache first. With no client it only reads the
    cache (for status reports). A batch that was split leaves a marker under its own key,
    so a rerun goes straight to its cached halves."""

    system_prompt: str
    prompt_name: str
    settings: LLMSettings
    cache_dir: Path
    client: GroqClient | None
    stats: RunStats = field(default_factory=RunStats)
    _completion_estimate: int = 1500

    def label(self, items: Sequence[LabelItem]) -> dict[Key, str]:
        labels: dict[Key, str] = {}
        self.stats.items += len(items)
        size = self.settings.batch_size
        for start in range(0, len(items), size):
            try:
                labels |= self._batch(items[start : start + size])
            except (DailyLimitError, ProviderError) as exc:  # keep reading the cache
                self.stats.stopped = str(exc)
        self.stats.labeled = len(labels)
        return labels

    def _split(self, batch: Sequence[LabelItem]) -> dict[Key, str]:
        half = len(batch) // 2
        return self._batch(batch[:half]) | self._batch(batch[half:])

    def _batch(self, batch: Sequence[LabelItem]) -> dict[Key, str]:
        ids = [str(n) for n in range(1, len(batch) + 1)]
        messages = render_messages(
            self.system_prompt, [(i, b.text) for i, b in zip(ids, batch, strict=True)]
        )
        body = request_body(self.settings, messages)
        key = request_key(body)
        cached = read_cache(self.cache_dir, key)
        if cached is not None and cached.get("split"):
            return self._split(batch)
        if cached is not None:
            self.stats.from_cache += len(batch)
            self.stats.cached_tokens += int((cached.get("usage") or {}).get("total_tokens") or 0)
            by_id = parse_labels(cached["content"], ids)
            return {b.key: by_id[i] for i, b in zip(ids, batch, strict=True)}
        if self.client is None or self.stats.stopped:
            return {}
        estimate = len(json.dumps(messages)) // 3 + self._completion_estimate
        for _ in range(VALIDATION_ATTEMPTS):
            try:
                data = self.client.complete(body, estimate)
            except InvalidResponseError:  # the provider refused to produce a valid answer
                self.stats.requests += 1
                continue
            self.stats.requests += 1
            usage = data.get("usage") or {}
            self.stats.tokens += int(usage.get("total_tokens") or 0)
            completion = int(usage.get("completion_tokens") or 0)
            if completion:
                self._completion_estimate = max(self._completion_estimate, completion)
            choice = data["choices"][0]
            try:
                by_id = parse_labels(choice["message"]["content"] or "", ids)
            except InvalidResponseError:
                continue
            write_cache(self.cache_dir, key, {
                "key": key, "model": self.settings.model, "prompt": self.prompt_name,
                "created": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                "content": choice["message"]["content"], "usage": usage,
                "finish_reason": choice.get("finish_reason"),
                "items": [list(b.key) for b in batch],
            })  # fmt: skip
            return {b.key: by_id[i] for i, b in zip(ids, batch, strict=True)}
        if len(batch) == 1:
            self.stats.failed += 1
            return {}
        write_cache(self.cache_dir, key, {
            "key": key, "split": True, "prompt": self.prompt_name,
            "created": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "items": [list(b.key) for b in batch],
        })  # fmt: skip
        return self._split(batch)


def committed_prompt(name: str, repo: Path = REPO_ROOT, prompt_dir: Path = PROMPTS_DIR) -> str:
    """The commit of a prompt file that is tracked and unchanged since that commit. Test items
    are labeled only with such a prompt."""
    path = (prompt_dir / name).resolve()

    def git(*args: str) -> tuple[int, str]:
        done = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
        )
        return done.returncode, done.stdout.strip()

    if git("ls-files", "--error-unmatch", str(path))[0] != 0:
        raise UncommittedPromptError(f"{name} is not committed; commit the prompt first")
    code, changes = git("status", "--porcelain", "--", str(path))
    if code != 0 or changes:
        raise UncommittedPromptError(f"{name} has uncommitted changes; commit the prompt first")
    code, commit = git("log", "-1", "--format=%H", "--", str(path))
    if code != 0 or not commit:
        raise UncommittedPromptError(f"{name} has no commit; commit the prompt first")
    return commit


def gold_hashes(labels_path: Path = LABELS_PATH) -> set[str]:
    """The text hashes of all 400 gold texts, dev and test."""
    with labels_path.open(newline="", encoding="utf-8") as fh:
        return {r["text_sha256"] for r in csv.DictReader(fh)}


def build_llm_sample(
    records: Iterable[EarlyStop],
    early_stop: Sequence[str],
    source: str,
    exclude: set[str],
    size: int,
    seed: int,
) -> list[GoldItem]:
    """A seeded sample of distinct texts, stratified by status and stop year, with every gold
    text excluded after normalization. It is returned in a seeded random order, so any prefix
    (a partial day of labeling) is itself a random subset."""
    items, _ = distinct_items(records, early_stop, source)
    pool = [i for i in items if i.text_sha256 not in exclude]
    sample = stratified_sample(pool, size, seed)
    if {i.text_sha256 for i in sample} & exclude:
        raise AssertionError("a gold text entered the LLM sample")
    random.Random(f"{seed}:llm-sample-order").shuffle(sample)
    return sample


def cached_early_stops(cfg: ProjectConfig) -> list[EarlyStop]:
    """The early stops of the gold pull, read from its cache (no request when complete)."""
    with httpx.Client(timeout=120, follow_redirects=True) as http:
        records, _ = pull_early_stops(ApiClient(http), cfg, PULL_CACHE, dt.date.today())
    return records


def _as_label_items(items: Iterable[GoldItem]) -> list[LabelItem]:
    return [LabelItem(i.nct_id, i.text_sha256, i.why_stopped) for i in items]


def _heldout(labels_path: Path, sample_path: Path) -> tuple[set[str], list[str]]:
    hashes = heldout_hashes(labels_path)
    texts = (
        [i.why_stopped for i in load_sample(sample_path) if i.split == "test"]
        if sample_path.is_file()
        else []
    )
    return hashes, texts


def days_needed(remaining: int, stats: RunStats, settings: LLMSettings) -> float | None:
    """Days of the provider's daily token budget that the remaining texts need, at this run's
    tokens per labeled text."""
    if remaining <= 0:
        return 0.0
    tokens = stats.tokens + stats.cached_tokens
    if stats.labeled <= 0 or tokens <= 0:
        return None
    return round(remaining * tokens / stats.labeled / settings.tokens_per_day, 1)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Label why_stopped texts with an LLM")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--split", choices=("dev", "test"), help="label a gold split")
    group.add_argument(
        "--sample", type=int, metavar="N", help="label the first N texts of the LLM sample"
    )
    group.add_argument("--status", action="store_true", help="report progress from the cache")
    parser.add_argument("--prompt", default=FINAL_PROMPT, help="prompt file (dev only)")
    args = parser.parse_args(argv)
    cfg = load_project_config()
    settings = LLMSettings()
    check_graded_model(settings.model)

    sample_run = args.sample is not None or args.status
    if args.sample is not None and not 1 <= args.sample <= SAMPLE_SIZE:
        raise RefusedError(f"--sample takes 1 to {SAMPLE_SIZE}, not {args.sample}")
    prompt_name = args.prompt
    if (args.split == "test" or sample_run) and prompt_name != FINAL_PROMPT:
        raise RefusedError(f"test and sample labels use the final prompt ({FINAL_PROMPT}) only")
    commit = committed_prompt(prompt_name) if args.split == "test" or sample_run else ""
    try:
        system_prompt = load_prompt(prompt_name)
    except ValueError as exc:
        raise RefusedError(str(exc)) from None
    if args.split and not SAMPLE_PATH.is_file():
        raise RefusedError(
            f"no gold sample at {SAMPLE_PATH}; build it with trialpulse.nlp.gold --build"
        )
    check_rendered_prompt(system_prompt, *_heldout(LABELS_PATH, SAMPLE_PATH))
    stem = prompt_stem(prompt_name)

    everything: list[LabelItem] = []
    if args.split:
        items = _as_label_items(i for i in load_sample(SAMPLE_PATH) if i.split == args.split)
        items.sort(key=lambda i: i.key)
        out = LABELS_DIR / f"{args.split}_{stem}.csv"
    else:
        if not LLM_SAMPLE_PATH.is_file():  # always the full sample; --sample N labels a prefix
            records = cached_early_stops(cfg)
            meta = json.loads(next(PULL_CACHE.rglob("pull.json")).read_text(encoding="utf-8"))
            sample = build_llm_sample(
                records, cfg.statuses.early_stop, source_label(meta), gold_hashes(),
                SAMPLE_SIZE, cfg.seeds.default,
            )  # fmt: skip
            write_sample(sample, LLM_SAMPLE_PATH)
        loaded = load_sample(LLM_SAMPLE_PATH)
        if len(loaded) != SAMPLE_SIZE or {i.text_sha256 for i in loaded} & gold_hashes():
            raise AssertionError(f"{LLM_SAMPLE_PATH} is not the {SAMPLE_SIZE}-text sample")
        everything = _as_label_items(loaded)
        items = everything[: args.sample] if args.sample is not None else []
        out = LABELS_DIR / f"sample_{stem}.csv"

    client: GroqClient | None = None
    http: httpx.Client | None = None
    if not args.status:
        key = Secrets().groq_api_key
        if key is None:
            raise RefusedError("GROQ_API_KEY is not set")
        http = httpx.Client(timeout=180)
        client = GroqClient(http, key, settings)
    try:
        labeler = Labeler(system_prompt, prompt_name, settings, CACHE_DIR, client)
        labels = labeler.label(items)
    finally:
        if http is not None:
            http.close()
    this_run = labeler.stats
    totals = this_run
    if sample_run:  # the labels file always holds every sample text labeled so far
        reader = Labeler(system_prompt, prompt_name, settings, CACHE_DIR, None)
        labels = reader.label(everything)
        totals = reader.stats
    if not args.status:
        write_predictions(out, labels)
    remaining = totals.items - totals.labeled
    print(
        json.dumps(
            {
                "prompt": prompt_name,
                "prompt_commit": commit,
                "items": totals.items,
                "labeled": totals.labeled,
                "remaining": remaining,
                "failed_this_run": this_run.failed,
                "requests_this_run": this_run.requests,
                "tokens_this_run": this_run.tokens,
                "days_needed_for_the_rest": days_needed(remaining, totals, settings),
                "stopped": this_run.stopped or None,
                "labels_file": str(out) if not args.status else None,
            },
            indent=2,
        )
    )
    if this_run.stopped:
        raise StoppedEarlyError(
            f"{this_run.stopped}; {totals.labeled} of {totals.items} labeled and saved; "
            "run the same command again later to continue"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(run(main))
