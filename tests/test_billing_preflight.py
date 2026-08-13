"""Billing ownership coverage for explicit preview and product writes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from ibee_cli.commands import billing, secrets
from ibee_cli.main import app

runner = CliRunner()
BASE_ARGS = ["--token", "test-token", "--workspace", "973318"]


class Billing:
    def __init__(self, calls, response=None):
        self.calls = calls
        self.response = response or SimpleNamespace(allowed=True, reason="ok")

    def check_resource_eligibility(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_manual_billing_eligibility_uses_typed_sdk(monkeypatch):
    calls = []
    client = SimpleNamespace(billing=Billing(calls))
    monkeypatch.setattr(billing, "get_client", lambda _settings: client)
    result = runner.invoke(
        app,
        [
            *BASE_ARGS,
            "billing",
            "eligibility",
            "--sku-code",
            "STANDARD-2-4",
            "--estimated-cost-minor",
            "12500",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "workspace_id": "973318",
            "sku_code": "STANDARD-2-4",
            "estimated_cost_minor": 12500,
        }
    ]


def test_manual_billing_command_reports_old_sdk(monkeypatch):
    monkeypatch.setattr(billing, "get_client", lambda _settings: SimpleNamespace())
    result = runner.invoke(app, [*BASE_ARGS, "billing", "eligibility"])
    assert result.exit_code == 1
    assert "Upgrade the SDK" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["vpcs", "nat", "create", "vpc-1"],
        ["reserved-ips", "reserve", "--site-id", "site-1"],
        [
                "load-balancers",
                "create-l4",
                "edge",
                "--backends",
                '[{"type":"ip","target":"10.0.0.5","port":443}]',
        ],
    ],
)
def test_billable_creates_send_one_product_request_for_edge_admission(
    monkeypatch, args
):
    events = []

    def request(method, url, **kwargs):
        events.append({"method": method, "url": url})
        return SimpleNamespace(status_code=200, content=b"{}", json=lambda: {})

    monkeypatch.setattr("ibee_cli.context.httpx.request", request)
    result = runner.invoke(app, [*BASE_ARGS, *args])
    assert result.exit_code == 0, result.output
    assert len(events) == 1
    assert events[0]["method"] == "POST"


def test_secret_store_and_secret_create_rely_on_edge_admission(monkeypatch):
    billing_calls = []
    resource_calls = []

    class SecretStore:
        def create_secret_store(self, **kwargs):
            resource_calls.append(("store", kwargs))
            return SimpleNamespace(id="store-1")

        def create_secret(self, **kwargs):
            resource_calls.append(("secret", kwargs))
            return SimpleNamespace(id="secret-1")

    client = SimpleNamespace(
        billing=Billing(billing_calls), secret_store=SecretStore()
    )
    monkeypatch.setattr(secrets, "get_client", lambda _settings: client)

    result = runner.invoke(
        app, [*BASE_ARGS, "secrets", "stores", "create", "production"]
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        [
            *BASE_ARGS,
            "secrets",
            "create",
            "--store-id",
            "store-1",
            "--name",
            "db-url",
            "--value",
            "url=postgres://db",
        ],
    )
    assert result.exit_code == 0, result.output
    assert billing_calls == []
    assert [name for name, _ in resource_calls] == ["store", "secret"]
