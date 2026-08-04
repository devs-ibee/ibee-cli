"""Billing preflight coverage for billable and non-billable CLI writes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from ibee_cli.commands import billing, load_balancers, networking, reserved_ips, secrets
from ibee_cli.helpers import require_billing_eligibility
from ibee_cli.main import app

runner = CliRunner()
BASE_ARGS = ["--token", "test-token", "--workspace", "workspace-123"]


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
            "workspace_id": "workspace-123",
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
    ("module", "args", "sku"),
    [
        (
            networking,
            ["vpcs", "nat", "create", "vpc-1"],
            None,
        ),
        (
            reserved_ips,
            ["reserved-ips", "reserve", "--site-id", "site-1"],
            None,
        ),
        (
            load_balancers,
            [
                "load-balancers",
                "create-l4",
                "edge",
                "--backends",
                '[{"type":"ip","target":"10.0.0.5","port":443}]',
            ],
            "LOADBALA-STD",
        ),
    ],
)
def test_direct_api_billable_creates_preflight_before_post(
    monkeypatch, module, args, sku
):
    events = []
    client = SimpleNamespace(billing=Billing(events))
    monkeypatch.setattr(module, "get_client", lambda _settings: client)

    def request(method, url, **kwargs):
        events.append({"method": method, "url": url})
        return SimpleNamespace(status_code=200, content=b"{}", json=lambda: {})

    monkeypatch.setattr("ibee_cli.context.httpx.request", request)
    result = runner.invoke(app, [*BASE_ARGS, *args])
    assert result.exit_code == 0, result.output
    expected = {"workspace_id": "workspace-123"}
    if sku is not None:
        expected["sku_code"] = sku
    assert events[0] == expected
    assert events[1]["method"] == "POST"


def test_direct_api_billing_denial_prevents_post(monkeypatch):
    events = []
    client = SimpleNamespace(
        billing=Billing(
            events,
            response=SimpleNamespace(
                allowed=False, reason="insufficient_balance"
            ),
        )
    )
    monkeypatch.setattr(networking, "get_client", lambda _settings: client)

    def must_not_post(*_args, **_kwargs):
        raise AssertionError("resource POST ran after a billing denial")

    monkeypatch.setattr("ibee_cli.context.httpx.request", must_not_post)
    result = runner.invoke(
        app, [*BASE_ARGS, "vpcs", "nat", "create", "vpc-1"]
    )
    assert result.exit_code == 1
    assert "insufficient_balance" in result.output
    assert events == [{"workspace_id": "workspace-123"}]


@pytest.mark.parametrize(
    ("module", "args"),
    [
        (networking, ["vpcs", "create", "private", "--site-id", "site-1"]),
        (
            networking,
            ["vpcs", "nodes", "attach", "vpc-1", "vm-1", "--subnet-id", "subnet-1"],
        ),
        (
            reserved_ips,
            ["reserved-ips", "attach", "ip-1", "vm-1"],
        ),
    ],
)
def test_nonbillable_posts_do_not_call_billing(monkeypatch, module, args):
    class MustNotRun:
        def check_resource_eligibility(self, **_kwargs):
            raise AssertionError("non-billable POST was wallet-blocked")

    monkeypatch.setattr(
        module,
        "get_client",
        lambda _settings: SimpleNamespace(billing=MustNotRun()),
        raising=False,
    )
    monkeypatch.setattr(
        "ibee_cli.context.httpx.request",
        lambda *_args, **_kwargs: SimpleNamespace(
            status_code=200, content=b"{}", json=lambda: {}
        ),
    )
    result = runner.invoke(app, [*BASE_ARGS, *args])
    assert result.exit_code == 0, result.output


def test_secret_store_and_secret_create_use_stable_sku(monkeypatch):
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
    assert billing_calls == [
        {"workspace_id": "workspace-123", "sku_code": "SECRETMA-STD"},
        {"workspace_id": "workspace-123", "sku_code": "SECRETMA-STD"},
    ]
    assert [name for name, _ in resource_calls] == ["store", "secret"]


def test_malformed_typed_billing_response_fails_closed():
    client = SimpleNamespace(
        billing=Billing([], response=SimpleNamespace(reason="missing allowed"))
    )
    with pytest.raises(Exception) as exc_info:
        require_billing_eligibility(
            client, "workspace-123", sku_code="STANDARD-2-4"
        )
    assert getattr(exc_info.value, "exit_code", None) == 1
