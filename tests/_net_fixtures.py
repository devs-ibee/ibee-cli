"""A scripted public gateway for CLI tests.

Commands run against the real Python SDK (``Ibee``) whose HTTP client uses an
``httpx.MockTransport``, so every request the CLI makes goes through the SDK.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import httpx
from click import unstyle
from ibee import Ibee
from typer.testing import CliRunner

from ibee_cli import context
from ibee_cli.main import app

WS = "973318"
BASE_URL = "https://gateway.example/v1"
BASE = ["--token", "test-token", "--workspace", WS]
TS = "2026-09-01T10:00:00Z"

runner = CliRunner()


class Gateway:
    """Scripted responses keyed by (METHOD, path relative to /v1); unscripted requests get ``default``."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list] = {}
        self.calls: list[SimpleNamespace] = []
        self.base_url = BASE_URL
        self.default = None

    def on(self, method: str, path: str, *responses) -> "Gateway":
        self.routes.setdefault((method.upper(), path), []).extend(responses or [None])
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/v1/"):
            path = path[len("/v1/"):]
        body = json.loads(request.content) if request.content else None
        self.calls.append(
            SimpleNamespace(
                method=request.method,
                path=path,
                params={key: request.url.params.get_list(key) for key in request.url.params.keys()},
                json=body,
                headers=dict(request.headers),
            )
        )
        queue = self.routes.get((request.method, path))
        if not queue:
            if self.default is None:
                raise AssertionError(f"unexpected request {request.method} {path}")
            item = self.default
        else:
            item = queue.pop(0) if len(queue) > 1 else queue[0]
        headers = None
        if isinstance(item, tuple) and len(item) == 3:
            status, payload, headers = item
        else:
            status, payload = item if isinstance(item, tuple) else (200, item)
        if isinstance(payload, bytes):
            return httpx.Response(status, content=payload, headers=headers)
        if payload is None:
            return httpx.Response(204 if status == 200 else status, headers=headers)
        return httpx.Response(status, json=payload, headers=headers)

    def client(self) -> Ibee:
        return Ibee(
            token="test-token",
            workspace_id=WS,
            base_url=self.base_url,
            httpx_client=httpx.Client(transport=httpx.MockTransport(self.handler)),
            max_retries=0,
        )

    def requests(self, method: str | None = None) -> list[SimpleNamespace]:
        return [call for call in self.calls if method is None or call.method == method]

    def writes(self) -> list[SimpleNamespace]:
        return [call for call in self.calls if call.method != "GET"]

    def last(self, method: str, path: str) -> SimpleNamespace:
        matches = [c for c in self.calls if c.method == method and c.path == path]
        assert matches, f"no {method} {path} in {[(c.method, c.path) for c in self.calls]}"
        return matches[-1]


def install(monkeypatch) -> Gateway:
    """Route every command's SDK client to a new scripted gateway."""

    gateway = Gateway()
    monkeypatch.setattr(context, "get_client", lambda _settings: gateway.client())
    return gateway


def run(args, **kwargs):
    return runner.invoke(app, [*BASE, *args], **kwargs)


def plain(result) -> str:
    return re.sub(r"\s+", " ", unstyle(result.output))
