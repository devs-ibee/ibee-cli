"""Global options, configuration errors, error output and exit codes."""

from __future__ import annotations

import json

import httpx
import pytest
from _net_fixtures import BASE, WS, plain, run, runner

from ibee_cli import __version__, context
from ibee_cli.main import app
from ibee_cli.render import EXIT_CODES

EDGE_402 = {
    "error": "billing_denied",
    "required_scope": None,
    "billing_reason": "resource_limit_reached",
    "billing_sku_code": "VM-2C",
    "billing_evaluated_at": "2026-10-19T10:00:00Z",
    "admission_context_id": "adm-1",
}
REQ = {"x-request-id": "req-123"}
GROUPS = ("vms", "gpus", "ops", "compute", "block-storage", "buckets", "cdn", "secrets", "vpcs", "reserved-ips",
          "firewalls", "load-balancers", "registries")


def test_help_and_version() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for group in GROUPS:
        assert group in result.output
    for gone in ("billing", "console"):
        assert runner.invoke(app, [gone, "--help"]).exit_code == 2
    assert runner.invoke(app, ["--version"]).output.strip() == f"ibee-cli {__version__}" == "ibee-cli 0.6.0"


def test_missing_token_and_workspace_are_usage_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("IBEE_TOKEN", "IBEE_API_TOKEN", "IBEE_WORKSPACE_ID"):
        monkeypatch.delenv(name, raising=False)
    result = runner.invoke(app, ["compute", "sites"])
    assert result.exit_code == 2 and "No API token" in result.output
    result = runner.invoke(app, ["--token", "t", "compute", "sites"])
    assert result.exit_code == 2 and "No workspace" in result.output


def test_bad_endpoint_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    result = runner.invoke(app, [*BASE, "--base-url", "http://api.example.test/v1", "compute", "sites"])
    assert result.exit_code == 2 and "Invalid IBEE base URL" in result.output
    monkeypatch.setenv("IBEE_ENV", "staging")
    assert runner.invoke(app, [*BASE, "compute", "sites"]).exit_code == 2


def test_endpoint_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    def base(**kw) -> str:
        settings = context.Settings(token="t", workspace=WS, dev=kw.pop("dev", False), base_url=kw.pop("base_url", None), **kw)
        return context.get_client(settings)._client_wrapper.get_base_url()

    assert base() == "https://api.ibee.ai/v1"
    assert base(dev=True) == "https://api.ibee.co.in/v1"
    assert base(dev=True, base_url="https://override.example.test/v1") == "https://override.example.test/v1"
    assert base(endpoint="https://endpoint.example.test/v1") == "https://endpoint.example.test/v1"
    monkeypatch.setenv("IBEE_ENV", "dev")
    assert base() == "https://api.ibee.co.in/v1"


def test_requests_send_the_cli_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real client from ``get_client`` sends ``ibee-cli/<version>`` and keeps the SDK's other headers."""
    assert context.USER_AGENT == f"ibee-cli/{__version__}"
    client = context.get_client(context.Settings(token="t", workspace=WS, dev=False, base_url=None))
    assert client._client_wrapper.get_headers()["User-Agent"] == f"ibee-cli/{__version__}"

    sent: list[httpx.Request] = []

    def capture(self, request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"sites": [], "count": 0})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", capture)  # nothing leaves the process
    assert run(["compute", "sites"]).exit_code == 0
    assert len(sent) == 1
    headers = sent[0].headers
    assert headers["user-agent"] == f"ibee-cli/{__version__}"
    assert headers["x-fern-language"] == "Python"
    assert headers["authorization"] == "Bearer test-token"


def test_workspace_is_sent_as_workspace_id(gw) -> None:
    gw.on("GET", "compute/sites", {"sites": [], "count": 0})
    assert run(["compute", "sites"]).exit_code == 0
    assert gw.last("GET", "compute/sites").params == {"workspace_id": [WS]}
    result = runner.invoke(app, ["--token", "t", "-w", "42", "compute", "sites"])
    assert result.exit_code == 0 and gw.calls[-1].params == {"workspace_id": ["42"]}


def test_edge_denial_prints_every_field_and_exits_12(gw) -> None:
    gw.on("GET", "compute/sites", (402, EDGE_402, REQ))
    result = run(["compute", "sites"])
    assert result.exit_code == EXIT_CODES[402] == 12
    text = plain(result)
    for part in ("Error: HTTP 402", "code: billing_denied", "reason: resource_limit_reached", "message: -",
                 "request_id: req-123", "admission_context_id: adm-1"):
        assert part in text
    assert "Payment required" not in text  # no invented wording


def test_product_service_error_and_unknown_code(gw) -> None:
    gw.on("GET", "compute/sites", (400, {"error": "brand_new_code"}, REQ))
    result = run(["compute", "sites"])
    assert result.exit_code == 10
    assert "code: brand_new_code" in plain(result) and "request_id: req-123" in plain(result)
    gw.on("GET", "compute/plans", (409, {"detail": {"code": "RESOURCE_LIMIT_REACHED", "message": "Your plan allows 2."}}))
    result = run(["compute", "plans", "--vm-type", "cloud"])
    assert result.exit_code == 15
    assert "code: RESOURCE_LIMIT_REACHED" in plain(result) and "message: Your plan allows 2." in plain(result)
    gw.on("DELETE", "compute/cloud-vms/vm-1", (409, {"detail": {"code": "VM_COMMITTED", "message": "Committed until 2026-11-29."}}))
    result = run(["vms", "delete", "vm-1", "--yes"])
    assert result.exit_code == 15 and "code: VM_COMMITTED" in plain(result)


@pytest.mark.parametrize(("status", "code"), sorted(EXIT_CODES.items()) + [(500, 19), (503, 19), (418, 1)])
def test_exit_code_per_error_class(gw, status: int, code: int) -> None:
    gw.on("GET", "compute/sites", (status, {"error": "x"}))
    assert run(["compute", "sites"]).exit_code == code


def test_json_errors(gw) -> None:
    gw.on("GET", "compute/sites", (402, EDGE_402, REQ))
    result = run(["-o", "json", "compute", "sites"])
    assert result.exit_code == 12
    error = json.loads(result.output)["error"]
    assert error == {
        "status": 402,
        "code": "billing_denied",
        "reason": "resource_limit_reached",
        "message": None,
        "request_id": "req-123",
        "admission_context_id": "adm-1",
        "body": EDGE_402,
    }


def test_text_body_error(gw) -> None:
    gw.on("GET", "compute/sites", (502, "Bad gateway"))
    result = run(["compute", "sites"])
    assert result.exit_code == 19 and "message: Bad gateway" in plain(result)


def test_network_error_exits_4_with_a_retry_hint(gw) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    gw.handler = refuse  # type: ignore[method-assign]
    result = run(["vms", "start", "vm-1", "--idempotency-key", "k-1"])
    assert result.exit_code == 4
    assert "Connection error" in plain(result) and "--idempotency-key k-1" in plain(result)


def test_keyed_write_server_error_prints_the_retry_key(gw) -> None:
    gw.on("POST", "compute/cloud-vms/vm-1/actions/start", (502, {"detail": "upstream"}, REQ))
    result = run(["vms", "start", "vm-1", "--idempotency-key", "k-2"])
    assert result.exit_code == 19
    assert "Retry safely with: --idempotency-key k-2" in plain(result)


def test_response_that_does_not_match_the_contract_is_printed_as_received(gw) -> None:
    """The SDK cannot parse it: the body is shown as sent (CORS reads return S3-cased keys today)."""
    sent = {"bucket_name": "b", "rules": [{"AllowedOrigins": ["*"], "AllowedMethods": ["GET"]}]}
    gw.on("GET", "object-storage/buckets/b/cors", (200, sent, REQ))
    result = run(["-o", "json", "buckets", "cors", "get", "b"])
    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["body"] == sent and error["request_id"] == "req-123"
    assert error["message"].startswith("response not parsed")
