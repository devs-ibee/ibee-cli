"""Focused tests for the billing eligibility CLI command."""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from ibee_cli.commands import billing
from ibee_cli.main import app

runner = CliRunner()


class FakeBilling:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[dict[str, object]] = []

    def check_resource_eligibility(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            organization_id="250612",
            allowed=self.allowed,
            reason="sufficient_balance" if self.allowed else "insufficient_balance",
            billing_mode="PREPAID",
            billing_state="CURRENT",
            currency="INR",
            sku_code=kwargs["sku_code"],
            estimated_cost_minor=kwargs["estimated_cost_minor"],
            effective_balance_minor=500000,
            credit_headroom_minor=None,
            evaluated_at="2026-08-04T10:30:00Z",
        )


def _fake_client(monkeypatch, *, allowed: bool = True) -> FakeBilling:
    fake_billing = FakeBilling(allowed=allowed)
    monkeypatch.setattr(
        billing,
        "get_client",
        lambda settings: SimpleNamespace(billing=fake_billing),
    )
    return fake_billing


def test_billing_eligibility_forwards_workspace_and_optional_estimate(monkeypatch) -> None:
    fake_billing = _fake_client(monkeypatch)

    result = runner.invoke(
        app,
        [
            "--token",
            "test-token",
            "--workspace",
            "710995",
            "billing",
            "eligibility",
            "--sku-code",
            "STANDARD-2-8-50",
            "--estimated-cost-minor",
            "120000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Billing eligibility: ALLOWED" in result.output
    assert fake_billing.calls == [
        {
            "workspace_id": "710995",
            "sku_code": "STANDARD-2-8-50",
            "estimated_cost_minor": 120000,
        }
    ]


def test_billing_eligibility_allows_omitting_optional_fields(monkeypatch) -> None:
    fake_billing = _fake_client(monkeypatch, allowed=False)

    result = runner.invoke(
        app,
        [
            "--token",
            "test-token",
            "--workspace",
            "710995",
            "billing",
            "eligibility",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Billing eligibility: DENIED" in result.output
    assert fake_billing.calls == [
        {
            "workspace_id": "710995",
            "sku_code": None,
            "estimated_cost_minor": None,
        }
    ]


def test_billing_eligibility_honors_json_output(monkeypatch) -> None:
    fake_billing = _fake_client(monkeypatch)
    rendered: list[object] = []
    monkeypatch.setattr(billing, "print_json", rendered.append)

    result = runner.invoke(
        app,
        [
            "--json",
            "--token",
            "test-token",
            "--workspace",
            "710995",
            "billing",
            "eligibility",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(rendered) == 1
    assert rendered[0].allowed is True
    assert "Billing eligibility: ALLOWED" not in result.output
    assert len(fake_billing.calls) == 1


def test_billing_eligibility_rejects_negative_estimated_cost(monkeypatch) -> None:
    fake_billing = _fake_client(monkeypatch)

    result = runner.invoke(
        app,
        [
            "--token",
            "test-token",
            "--workspace",
            "710995",
            "billing",
            "eligibility",
            "--estimated-cost-minor",
            "-1",
        ],
    )

    assert result.exit_code == 2
    assert "x>=0" in result.output
    assert fake_billing.calls == []


def test_help_lists_billing_eligibility_command() -> None:
    root_help = runner.invoke(app, ["--help"])
    billing_help = runner.invoke(app, ["billing", "--help"])

    assert root_help.exit_code == 0
    assert "billing" in root_help.output
    assert billing_help.exit_code == 0
    assert "eligibility" in billing_help.output
