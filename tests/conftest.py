"""Test-wide safety net: no test may reach a real network endpoint."""

from __future__ import annotations

import httpx
import pytest


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    def refuse(self, request):  # pragma: no cover - only hit by a test bug
        raise AssertionError(f"tests must not call the network: {request.method} {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture
def gw(monkeypatch):
    """A scripted public gateway behind the real Python SDK (see ``_net_fixtures``)."""

    from _net_fixtures import install

    return install(monkeypatch)
