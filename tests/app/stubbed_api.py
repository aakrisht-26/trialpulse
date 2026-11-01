"""The stubbed API of the dashboard's tests (ADR 0056): the recorded answers of
tests/app/stubs.json, served to the stub address, with failures queued on demand."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import httpx

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"

BASE = "http://api.test"
STUBS = json.loads((Path(__file__).parent / "stubs.json").read_text(encoding="utf-8"))


def _key(path: str, params: dict[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
    return path, tuple(sorted(params.items()))


ANSWERS = {_key(e["path"], e["params"]): e for e in STUBS if e["name"] is None}
NAMED = {e["name"]: e for e in STUBS if e["name"]}


def response(entry: dict[str, Any]) -> httpx.Response:
    headers = {"Retry-After": entry["retry_after"]} if entry["retry_after"] else {}
    return httpx.Response(entry["status"], json=entry["body"], headers=headers)


@dataclass
class StubApi:
    """What the dashboard asked and how the stub answered. `fail` queues failures that come
    before the recorded answers, for every path or for paths that start with `on`: "connect"
    (refused), "timeout", "proxy" (the platform's 502 without the API's notice), "platform"
    (a 200 page that is not the API's JSON), "waking" (the
    API's recorded 503), "busy" (its recorded 429) or "error" (a 500). `always` fails every
    request; `override` replaces the body of one recorded answer."""

    calls: list[str] = field(default_factory=list)
    queued: list[tuple[str, str | None]] = field(default_factory=list)
    always: str | None = None
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    slept: list[float] = field(default_factory=list)
    waits: list[str] = field(default_factory=list)
    now: float = 0.0

    def fail(self, *kinds: str, on: str | None = None) -> None:
        self.queued.extend((k, on) for k in kinds)

    def override(self, path: str, **fields: Any) -> None:
        self.overrides[path] = fields

    def _queued(self, path: str) -> str | None:
        for i, (kind, on) in enumerate(self.queued):
            if on is None or path.startswith(on):
                del self.queued[i]
                return kind
        return None

    def data_calls(self) -> list[str]:
        return [c for c in self.calls if c != "/health"]

    def answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(parse_qsl(request.url.query.decode()))
        self.calls.append(path + (f"?{request.url.query.decode()}" if params else ""))
        kind = self.always or self._queued(path)
        if kind == "connect":
            raise httpx.ConnectError("refused", request=request)
        if kind == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if kind == "error":
            return httpx.Response(500, json={"detail": "Internal Server Error"})
        if kind == "proxy":
            return httpx.Response(502, text="<html>Bad gateway</html>")
        if kind == "platform":  # a 200 page of the platform, not the API's JSON
            return httpx.Response(200, text="<html>This Space is starting</html>")
        if kind == "waking":
            return response(NAMED["database waking"])
        if kind == "busy":
            return response(NAMED["rate limited"])
        entry = ANSWERS.get(_key(path, params))
        if entry is None:
            raise AssertionError(f"no stub holds this request: {path} {params}")
        if path in self.overrides:
            entry = {**entry, "body": {**entry["body"], **self.overrides[path]}}
        return response(entry)
