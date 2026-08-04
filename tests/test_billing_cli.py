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


def test_billing_eligibility_falls_back_with_published_sdk(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "organization_id": "250612",
                "allowed": True,
                "reason": "sufficient_balance",
                "billing_mode": "PREPAID",
                "billing_state": "CURRENT",
                "currency": "INR",
                "sku_code": "STANDARD-2-8-50",
                "estimated_cost_minor": 120000,
                "effective_balance_minor": 500000,
                "credit_headroom_minor": None,
                "evaluated_at": "2026-08-04T10:30:00Z",
            }

    monkeypatch.setattr(billing, "get_client", lambda settings: SimpleNamespace())

    def fake_post(url: str, **kwargs: object) -> FakeResponse:
        calls.append({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(billing.httpx, "post", fake_post)

    result = runner.invoke(
        app,
        [
            "--dev",
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
    assert calls == [
        {
            "url": "https://api.ibee.co.in/v1/billing/resource-eligibility",
            "params": {"workspace_id": "710995"},
            "json": {
                "sku_code": "STANDARD-2-8-50",
                "estimated_cost_minor": 120000,
            },
            "headers": {
                "Authorization": "Bearer test-token",
                "accept": "application/json",
                "content-type": "application/json",
            },
            "timeout": 30,
        }
    ]


def test_billing_fallback_omits_optional_fields_and_honors_base_url(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "organization_id": "250612",
                "allowed": False,
                "reason": "insufficient_balance",
                "billing_mode": "PREPAID",
                "billing_state": "PAYMENT_DUE",
                "currency": "INR",
                "sku_code": None,
                "estimated_cost_minor": None,
                "effective_balance_minor": 0,
                "credit_headroom_minor": None,
                "evaluated_at": "2026-08-04T10:30:00Z",
            }

    monkeypatch.setattr(billing, "get_client", lambda settings: SimpleNamespace())

    def fake_post(url: str, **kwargs: object) -> FakeResponse:
        calls.append({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(billing.httpx, "post", fake_post)

    result = runner.invoke(
        app,
        [
            "--base-url",
            "https://gateway.example.test/v1/",
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
    assert calls[0]["url"] == "https://gateway.example.test/v1/billing/resource-eligibility"
    assert calls[0]["json"] == {}


def test_billing_fallback_supports_json_output(monkeypatch) -> None:
    payload = {
        "organization_id": "250612",
        "allowed": True,
        "reason": "sufficient_balance",
        "billing_mode": "PREPAID",
        "billing_state": "CURRENT",
        "currency": "INR",
        "sku_code": None,
        "estimated_cost_minor": None,
        "effective_balance_minor": 500000,
        "credit_headroom_minor": None,
        "evaluated_at": "2026-08-04T10:30:00Z",
    }

    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> dict[str, object]:
            return payload

    rendered: list[object] = []
    monkeypatch.setattr(billing, "get_client", lambda settings: SimpleNamespace())
    monkeypatch.setattr(billing.httpx, "post", lambda *args, **kwargs: FakeResponse())
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
    assert rendered == [payload]


def test_billing_fallback_reports_http_errors(monkeypatch) -> None:
    class FakeResponse:
        status_code = 403
        text = '{"error":{"code":"FORBIDDEN"}}'

    monkeypatch.setattr(billing, "get_client", lambda settings: SimpleNamespace())
    monkeypatch.setattr(billing.httpx, "post", lambda *args, **kwargs: FakeResponse())

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

    assert result.exit_code == 1
    assert "API error 403" in result.output


def test_help_lists_billing_eligibility_command() -> None:
    root_help = runner.invoke(app, ["--help"])
    billing_help = runner.invoke(app, ["billing", "--help"])

    assert root_help.exit_code == 0
    assert "billing" in root_help.output
    assert billing_help.exit_code == 0
    assert "eligibility" in billing_help.output
