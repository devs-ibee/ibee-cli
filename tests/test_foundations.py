"""0.4.0 cross-cutting CLI behaviour: global options, output modes, environments,
typed errors and exit codes, retries, idempotency keys, waiting and billing preflight."""

from __future__ import annotations

import itertools
import json
import re
from types import SimpleNamespace

import httpx
import pytest
from click import unstyle
from typing import List, Optional

import typer
from ibee.errors import error_from_response
from typer.models import TyperInfo
from typer.testing import CliRunner

from ibee_cli import context, helpers
from ibee_cli.commands import billing, firewalls, gpus, ops, vms
from ibee_cli.main import app
from ibee_cli.render import handle_api_errors, identifiers, print_json, to_yaml

runner = CliRunner()
BASE_ARGS = ["--token", "test-token", "--workspace", "973318"]
GATEWAY = ["--base-url", "https://gateway.example/v1"]


def run(args, **kwargs):
    return runner.invoke(app, [*BASE_ARGS, *args], **kwargs)


def plain(result):
    return re.sub(r"\s+", " ", unstyle(result.output))


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, payload=None, status_code=200, headers=None):
        self.payload = {} if payload is None else payload
        self.status_code = status_code
        self.headers = headers or {}
        self.content = b"" if status_code == 204 else json.dumps(self.payload).encode()
        self.text = self.content.decode()

    def json(self):
        return self.payload


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    delays = []
    monkeypatch.setattr(context, "_sleep", delays.append)
    monkeypatch.setattr(helpers, "_sleep", lambda _seconds: None)
    for var in ("IBEE_ENV", "IBEE_ENDPOINT", "IBEE_BASE_URL", "IBEE_OUTPUT", "IBEE_ASSUME_YES",
                "IBEE_CHECK_BILLING"):
        monkeypatch.delenv(var, raising=False)
    return delays


@pytest.fixture
def http(monkeypatch):
    """Scripted responses for direct gateway requests."""

    state = SimpleNamespace(calls=[], responses=[])

    def fake_request(method, url, **kwargs):
        state.calls.append({"method": method, "url": url, **kwargs})
        item = state.responses.pop(0) if state.responses else FakeResponse()
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr("ibee_cli.context.httpx.request", fake_request)
    return state


# Every product command now calls the SDK, so direct gateway requests
# (``context.api_request``) are exercised through a test-only ``probe`` command.
probe_app = typer.Typer()


@probe_app.command("call")
@handle_api_errors
def _probe_call(
    ctx: typer.Context,
    method: str,
    path: str,
    body: Optional[str] = typer.Option(None, "--body"),
    param: Optional[List[str]] = typer.Option(None, "--param"),
) -> None:
    params = dict(item.split("=", 1) for item in param or []) or None
    result = context.api_request(
        context.get_settings(ctx), method, path, params=params, json_body=json.loads(body) if body else None
    )
    print_json(result)


@pytest.fixture
def probe(monkeypatch, http):
    monkeypatch.setattr(app, "registered_groups", [*app.registered_groups, TyperInfo(probe_app, name="probe")])
    return http


def call(method, path, body=None, *params):
    args = ["probe", "call", method, path]
    if body is not None:
        args += ["--body", json.dumps(body)]
    for item in params:
        args += ["--param", item]
    return args


CDN_BODY = {"name": "site-cdn", "origin_id": "bucket-1"}


class FakeVms:
    def __init__(self, statuses=("succeeded",), fail_polls=0):
        self.calls = []
        self.statuses = list(statuses)
        self.fail_polls = fail_polls

    def list_cloud_vms(self, **kwargs):
        self.calls.append(("list", kwargs))
        return [{"id": "vm-1", "name": "web", "status": "running"}, {"id": "vm-2", "name": "db"}]

    def create_cloud_vm(self, **kwargs):
        self.calls.append(("create", kwargs))
        return SimpleNamespace(operation_id="op-1", vm_id="vm-1", status="accepted")

    def get_cloud_vm(self, **kwargs):
        self.calls.append(("get", kwargs))
        return {"_id": kwargs["vm_id"], "name": "web", "status": "running"}

    def delete_cloud_vm(self, **kwargs):
        self.calls.append(("delete", kwargs))
        return SimpleNamespace(operation_id="op-del")

    def start_cloud_vm(self, **kwargs):
        self.calls.append(("start", kwargs))
        return SimpleNamespace(operation_id="op-start")

    def get_compute_operation(self, **kwargs):
        self.calls.append(("operation", kwargs))
        if self.fail_polls:
            self.fail_polls -= 1
            raise error_from_response(503, {"detail": "busy"})
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return {
            "operation_id": kwargs["operation_id"],
            "vm_id": "vm-1",
            "action": "create",
            "status": status,
            "error_code": "E_CAPACITY" if status == "failed" else None,
            "error_message": "no capacity" if status == "failed" else None,
        }


class FakeBilling:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    def check_resource_eligibility(self, **kwargs):
        self.calls.append(("check", kwargs))
        return self.decision


def fake_client(module, monkeypatch, **resources):
    client = SimpleNamespace(**resources)
    monkeypatch.setattr(module, "get_client", lambda _settings: client)
    return client


CREATE_ARGS = ["vms", "create", "web", "--site-id", "site-1", "--plan-id", "plan-1", "--template-id", "img-1"]
VM_ID = "65f0c0ffee0000000000abcd"


# ---------------------------------------------------------------------------
# Global options and output modes
# ---------------------------------------------------------------------------


def test_version_and_help_list_global_options():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for option in ("--output", "--json", "--yes", "--check-billing", "--base-url", "--dev"):
        assert option in unstyle(result.output)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["-o", "json"], '"name": "web"'),
        (["-o", "yaml"], "name: web"),
        (["--json"], '"id": "vm-1"'),
    ],
)
def test_output_modes_for_table_commands(monkeypatch, args, expected):
    fake_client(vms, monkeypatch, cloud_vms=FakeVms())
    result = runner.invoke(app, [*BASE_ARGS, *args, "vms", "list"])
    assert result.exit_code == 0, result.output
    assert expected in result.output
    assert "Cloud VMs" not in result.output


def test_output_id_prints_one_identifier_per_line(monkeypatch):
    fake_client(vms, monkeypatch, cloud_vms=FakeVms())
    result = runner.invoke(app, [*BASE_ARGS, "-o", "id", "vms", "list"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["vm-1", "vm-2"]


def test_output_env_and_json_flag_precedence(monkeypatch):
    fake_client(vms, monkeypatch, cloud_vms=FakeVms())
    result = runner.invoke(app, [*BASE_ARGS, "vms", "list"], env={"IBEE_OUTPUT": "id"})
    assert result.output.splitlines() == ["vm-1", "vm-2"]
    result = runner.invoke(app, [*BASE_ARGS, "--json", "vms", "list"], env={"IBEE_OUTPUT": "id"})
    assert result.exit_code == 0, result.output
    assert '"id": "vm-1"' in result.output


@pytest.mark.parametrize("args", [["--json", "-o", "yaml"], ["-o", "xml"]])
def test_conflicting_or_unknown_output_exits_2(monkeypatch, args):
    fake_client(vms, monkeypatch, cloud_vms=FakeVms())
    result = runner.invoke(app, [*BASE_ARGS, *args, "vms", "list"])
    assert result.exit_code == 2


def test_accepted_operation_with_output_id_prints_operation_id(monkeypatch):
    fake_client(vms, monkeypatch, cloud_vms=FakeVms())
    result = runner.invoke(app, [*BASE_ARGS, "-o", "id", *CREATE_ARGS])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "op-1"


def test_builtin_yaml_emitter_and_identifiers():
    data = {"id": "vm-1", "tags": ["a", "b"], "nested": {"on": True, "n": None, "v": "1.0"}, "empty": []}
    text = to_yaml(data)
    assert "id: vm-1" in text
    assert "- a" in text
    assert "  on: true" in text or '  "on": true' in text
    assert '  v: "1.0"' in text
    assert "empty: []" in text
    assert identifiers({"items": [{"volume_id": "v1"}, {"name": "x"}]}) == ["v1", "x"]


# ---------------------------------------------------------------------------
# Environments and tokens
# ---------------------------------------------------------------------------


def test_invalid_ibee_env_exits_2(http):
    result = run(["buckets", "list"], env={"IBEE_ENV": "staging"})
    assert result.exit_code == 2
    assert "Invalid IBEE_ENV" in result.output
    assert http.calls == []


@pytest.mark.parametrize(
    ("env", "args", "expected"),
    [
        ({"IBEE_ENV": " Development "}, [], "https://api.ibee.co.in/v1/"),
        ({"IBEE_ENV": "prod"}, [], "https://api.ibee.ai/v1/"),
        ({"IBEE_ENV": "prod"}, ["--dev"], "https://api.ibee.co.in/v1/"),
        ({"IBEE_ENDPOINT": "https://endpoint.example/v1/", "IBEE_ENV": "dev"}, [], "https://endpoint.example/v1/"),
        (
            {"IBEE_BASE_URL": "https://base.example/v1", "IBEE_ENDPOINT": "https://endpoint.example/v1"},
            [],
            "https://base.example/v1/",
        ),
        ({"IBEE_BASE_URL": "https://base.example/v1"}, ["--base-url", "http://localhost:8080/v1"],
         "http://localhost:8080/v1/"),
    ],
)
def test_endpoint_precedence(probe, env, args, expected):
    http = probe
    result = runner.invoke(app, [*BASE_ARGS, *args, *call("GET", "object-storage/buckets")], env=env)
    assert result.exit_code == 0, result.output
    assert http.calls[0]["url"].startswith(expected)


@pytest.mark.parametrize(
    "url",
    ["http://gateway.example/v1", "https://user:pw@gateway.example/v1", "https://gateway.example/v1?x=1"],
)
def test_unsafe_base_url_exits_2(http, url):
    result = run(["--base-url", url, "buckets", "list"])
    assert result.exit_code == 2
    assert "Invalid IBEE base URL" in plain(result)
    assert http.calls == []


@pytest.mark.parametrize(
    ("token", "args"),
    [("ibee_dev_key_abc.secret", []), ("ibee_prod_key_abc.secret", ["--dev"])],
)
def test_token_environment_mismatch_exits_2(http, monkeypatch, token, args):
    result = runner.invoke(app, ["--token", token, "--workspace", "973318", *args, "buckets", "list"])
    assert result.exit_code == 2
    assert "token environment does not match" in result.output
    assert http.calls == []
    # The SDK client path applies the same check.
    result = runner.invoke(app, ["--token", token, "--workspace", "973318", *args, "vms", "list"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Direct gateway requests: retries and typed errors
# ---------------------------------------------------------------------------


def test_get_is_retried_on_503_with_retry_after(probe, no_sleep):
    http = probe
    http.responses = [
        FakeResponse({"detail": "busy"}, 503, {"retry-after": "7"}),
        FakeResponse({"buckets": []}),
    ]
    result = run([*GATEWAY, *call("GET", "object-storage/buckets")])
    assert result.exit_code == 0, result.output
    assert len(http.calls) == 2
    assert no_sleep == [7.0]


def test_retry_after_is_capped_at_30_seconds(probe, no_sleep):
    http = probe
    http.responses = [FakeResponse({}, 429, {"Retry-After": "600"}), FakeResponse({"buckets": []})]
    result = run([*GATEWAY, *call("GET", "object-storage/buckets")])
    assert result.exit_code == 0, result.output
    assert no_sleep == [30.0]


def test_unkeyed_create_is_not_retried(probe):
    http = probe
    http.responses = [FakeResponse({"detail": "upstream"}, 503)]
    result = run([*GATEWAY, *call("POST", "cdn/distributions", CDN_BODY)])
    assert result.exit_code == 1
    assert len(http.calls) == 1
    assert "Service error (503" in plain(result)


def test_keyed_block_storage_write_is_retried_with_the_same_key(probe):
    http = probe
    http.responses = [FakeResponse({}, 502), FakeResponse({"volume_id": "vol-1"})]
    body = {"new_size_gb": 20, "idempotency_key": helpers.new_idempotency_key("block-resize", "vol-1")}
    result = run([*GATEWAY, *call("POST", "block-storage/volumes/vol-1/resize", body)])
    assert result.exit_code == 0, result.output
    assert len(http.calls) == 2
    first, second = (item["json"]["idempotency_key"] for item in http.calls)
    assert first == second and first.startswith("cli-block-resize-vol-1-")


def test_keyed_write_failure_prints_retry_hint(probe):
    http = probe
    http.responses = [FakeResponse({}, 504)] * 3
    body = {"new_size_gb": 20, "idempotency_key": "my-key-1"}
    result = run([*GATEWAY, *call("POST", "block-storage/volumes/vol-1/resize", body)])
    assert result.exit_code == 1
    assert len(http.calls) == 3
    assert "Retry safely with: --idempotency-key my-key-1" in plain(result)


@pytest.mark.parametrize("status", [409, 500, 408])
def test_non_retryable_statuses_are_not_retried(probe, status):
    http = probe
    http.responses = [FakeResponse({"detail": "nope"}, status)]
    result = run([*GATEWAY, *call("GET", "object-storage/buckets")])
    assert result.exit_code == 1
    assert len(http.calls) == 1


def test_connect_error_is_retried_but_read_timeout_on_unkeyed_post_is_not(probe):
    http = probe
    request = httpx.Request("POST", "https://gateway.example/v1/cdn/distributions")
    http.responses = [httpx.ConnectError("refused", request=request), FakeResponse({"id": "rip-1"})]
    result = run([*GATEWAY, *call("POST", "cdn/distributions", CDN_BODY)])
    assert result.exit_code == 0, result.output
    assert len(http.calls) == 2

    http.calls.clear()
    http.responses = [httpx.ReadTimeout("slow", request=request)]
    result = run([*GATEWAY, *call("POST", "cdn/distributions", CDN_BODY)])
    assert result.exit_code == 1
    assert len(http.calls) == 1
    assert "Connection error" in plain(result)


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"error": "invalid_api_key"}, "for the other environment"),
        (403, {"error": "insufficient_scope", "required_scope": "storage.write"}, "missing scope storage.write"),
        (404, {"detail": "Bucket not found"}, "does not exist in this workspace"),
        (
            422,
            {"detail": [{"loc": ["body", "name"], "msg": "too long"}]},
            "Validation failed (422): name: too long",
        ),
        (413, {"error": "request_body_too_large"}, "64 KiB"),
        (423, {"detail": {"code": "ORG_BILLING_SUSPENDED", "message": "suspended"}}, "Organization suspended"),
        (429, {"detail": "slow down"}, "Rate limited (429)"),
    ],
)
def test_typed_error_messages(probe, status, body, expected):
    http = probe
    http.responses = [FakeResponse(body, status)] * 3
    result = run([*GATEWAY, *call("POST", "object-storage/buckets", {"name": "b1", "region": "r1"})])
    assert result.exit_code == 1
    assert expected in plain(result)


def test_edge_billing_denial_prints_portal_copy_and_topup(probe):
    http = probe
    http.responses = [
        FakeResponse(
            {
                "error": "billing_denied",
                "billing_reason": "insufficient_balance",
                "billing_sku_code": "CDN-1",
                "admission_context_id": "adm-9",
            },
            402,
        )
    ]
    result = run([*GATEWAY, *call("POST", "cdn/distributions", CDN_BODY)])
    assert result.exit_code == 1
    output = plain(result)
    assert "Your available wallet balance does not cover this CDN distribution" in output
    assert "reason=insufficient_balance" in output
    assert "admission_context_id=adm-9" in output
    assert "Add credits in the IBEE portal" in output


def test_service_error_shows_request_id(probe):
    http = probe
    http.responses = [FakeResponse({"detail": "boom"}, 502, {"x-request-id": "req-42"})]
    result = run([*GATEWAY, *call("POST", "cdn/distributions", CDN_BODY)])
    assert result.exit_code == 1
    assert "request_id=req-42" in plain(result)


def test_oversized_billable_create_body_is_rejected_before_sending(probe):
    http = probe
    result = run([*GATEWAY, *call("POST", "cdn/distributions", {**CDN_BODY, "cache_policy": "x" * 70000})])
    assert result.exit_code == 2
    assert "65536-byte limit" in plain(result)
    assert http.calls == []


def test_cli_api_error_alias_is_the_sdk_api_error():
    from ibee.core.api_error import ApiError

    assert issubclass(context.CliApiError, ApiError)


# ---------------------------------------------------------------------------
# Idempotency keys
# ---------------------------------------------------------------------------


def test_explicit_idempotency_key_is_used(monkeypatch):
    fake = FakeVms()
    fake_client(vms, monkeypatch, cloud_vms=fake)
    result = run(["vms", "start", "vm-1", "--idempotency-key", "retry-7"])
    assert result.exit_code == 0, result.output
    assert fake.calls[0][1]["idempotency_key"] == "retry-7"


@pytest.mark.parametrize("key", ["has space", "x" * 129, "tab\tkey"])
def test_invalid_idempotency_key_exits_2_before_any_call(monkeypatch, key):
    fake = FakeVms()
    fake_client(vms, monkeypatch, cloud_vms=fake)
    result = run([*CREATE_ARGS, "--idempotency-key", key])
    assert result.exit_code == 2
    assert "idempotency_key must be 1-128 printable ASCII" in plain(result)
    assert fake.calls == []


def test_generated_keys_use_the_portal_format():
    key = helpers.new_idempotency_key("vm-create", "my web/01")
    assert key.startswith("cli-vm-create-myweb01-")
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key)
    assert helpers.new_idempotency_key("x", "a") != helpers.new_idempotency_key("x", "a")


# ---------------------------------------------------------------------------
# Waiting for operations
# ---------------------------------------------------------------------------


def test_ops_wait_success(monkeypatch):
    fake = FakeVms(statuses=["running", "succeeded"])
    fake_client(ops, monkeypatch, cloud_vms=fake)
    result = run(["ops", "wait", "op-1", "--poll-interval", "1"])
    assert result.exit_code == 0, result.output
    assert [name for name, _ in fake.calls] == ["operation", "operation"]
    assert '"status": "succeeded"' in result.output


@pytest.mark.parametrize("status", ["failed", "cancelled", "timed_out"])
def test_ops_wait_failure_exits_1(monkeypatch, status):
    fake_client(ops, monkeypatch, cloud_vms=FakeVms(statuses=[status]))
    result = run(["ops", "wait", "op-1"])
    assert result.exit_code == 1
    assert f"{status} for op-1" in plain(result)


def test_ops_wait_timeout_exits_3_with_resume_hint(monkeypatch):
    monkeypatch.setattr(helpers, "_clock", itertools.count(0, 50).__next__)
    fake_client(ops, monkeypatch, cloud_vms=FakeVms(statuses=["running"]))
    result = run(["ops", "wait", "op-1", "--timeout", "60", "--poll-interval", "10"])
    assert result.exit_code == 3
    output = plain(result)
    assert "still running" in output
    assert "ibee ops wait op-1" in output


def test_ops_wait_tolerates_two_transient_poll_failures(monkeypatch):
    fake = FakeVms(statuses=["succeeded"], fail_polls=2)
    fake_client(ops, monkeypatch, cloud_vms=fake)
    result = run(["ops", "wait", "op-1"])
    assert result.exit_code == 0, result.output
    assert len(fake.calls) == 3

    fake = FakeVms(statuses=["succeeded"], fail_polls=3)
    fake_client(ops, monkeypatch, cloud_vms=fake)
    result = run(["ops", "wait", "op-1"])
    assert result.exit_code == 1
    assert len(fake.calls) == 3


def test_ops_wait_404_aborts_immediately(monkeypatch):
    class Missing:
        calls = 0

        def get_compute_operation(self, **kwargs):
            Missing.calls += 1
            raise error_from_response(404, {"detail": "Operation not found"})

    fake_client(ops, monkeypatch, cloud_vms=Missing())
    result = run(["ops", "wait", "op-1"])
    assert result.exit_code == 1
    assert Missing.calls == 1


@pytest.mark.parametrize(
    "args",
    [
        ["ops", "get", "op-1", "--timeout", "30"],
        ["ops", "wait", "op-1", "--timeout", "0"],
        ["ops", "wait", "op-1", "--timeout", "8000"],
        ["ops", "wait", "op-1", "--poll-interval", "61"],
        ["ops", "wait", "op-1", "--timeout", "10", "--poll-interval", "20"],
        ["ops", "wait", "   "],
    ],
)
def test_wait_bounds_exit_2(monkeypatch, args):
    fake = FakeVms()
    fake_client(ops, monkeypatch, cloud_vms=fake)
    result = run(args)
    assert result.exit_code == 2, result.output
    assert fake.calls == []


def test_timeout_without_wait_exits_2_on_async_verbs(monkeypatch):
    fake = FakeVms()
    fake_client(vms, monkeypatch, cloud_vms=fake)
    result = run(["vms", "start", "vm-1", "--timeout", "30"])
    assert result.exit_code == 2
    assert "require --wait" in plain(result)
    assert fake.calls == []


def test_create_wait_failure_and_timeout_exit_codes(monkeypatch):
    fake = FakeVms(statuses=["failed"])
    fake_client(vms, monkeypatch, cloud_vms=fake)
    result = run([*CREATE_ARGS, "--wait"])
    assert result.exit_code == 1
    assert "Create failed for web: E_CAPACITY: no capacity (operation op-1)" in plain(result)

    monkeypatch.setattr(helpers, "_clock", itertools.count(0, 100).__next__)
    fake = FakeVms(statuses=["running"])
    fake_client(vms, monkeypatch, cloud_vms=fake)
    result = run([*CREATE_ARGS, "--wait", "--timeout", "30", "--idempotency-key", "k-1"])
    assert result.exit_code == 3
    output = plain(result)
    assert "resume with: ibee ops wait op-1" in output
    assert "Retry safely with: --idempotency-key k-1" in output


def test_create_wait_json_prints_final_operation(monkeypatch):
    fake_client(vms, monkeypatch, cloud_vms=FakeVms())
    result = runner.invoke(app, [*BASE_ARGS, "--json", *CREATE_ARGS, "--wait"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "succeeded"


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


def test_global_yes_and_env_skip_confirmation(monkeypatch):
    fake = FakeVms()
    fake_client(vms, monkeypatch, cloud_vms=fake)
    result = runner.invoke(app, [*BASE_ARGS, "--yes", "vms", "delete", VM_ID])
    assert result.exit_code == 0, result.output
    result = run(["vms", "delete", VM_ID], env={"IBEE_ASSUME_YES": "true"})
    assert result.exit_code == 0, result.output
    assert [name for name, _ in fake.calls] == ["get", "delete", "get", "delete"]


def test_declined_confirmation_exits_1(monkeypatch):
    fake = FakeVms()
    fake_client(vms, monkeypatch, cloud_vms=fake)
    result = run(["vms", "delete", VM_ID], input="n\n")
    assert result.exit_code == 1
    assert [name for name, _ in fake.calls] == ["get"]


# ---------------------------------------------------------------------------
# Billing preflight and eligibility
# ---------------------------------------------------------------------------


class RequireBilling:
    def __init__(self, calls, allowed=True, reason="ok"):
        self.calls = calls
        self.allowed = allowed
        self.reason = reason

    def require_resource_eligibility(self, **kwargs):
        from ibee.errors import BillingDeniedError

        self.calls.append(("require", kwargs))
        decision = {"allowed": self.allowed, "reason": self.reason, "currency": "INR"}
        if self.allowed is not True:
            raise BillingDeniedError(decision=decision, create_type=kwargs.get("resource_type"))
        return decision


def test_check_billing_asks_the_sdk_to_preflight_the_plan_sku(monkeypatch):
    calls = []
    vms_fake = FakeVms()
    fake_client(vms, monkeypatch, cloud_vms=vms_fake, billing=RequireBilling(calls))
    result = runner.invoke(app, [*BASE_ARGS, "--check-billing", *CREATE_ARGS])
    assert result.exit_code == 0, result.output
    # The SDK checks the plan's SKU and cost itself, so no SKU-less CLI preflight is sent.
    assert calls == []
    assert [name for name, _ in vms_fake.calls] == ["create"]
    assert vms_fake.calls[0][1]["preflight_billing"] is True


def test_check_billing_denial_blocks_create(monkeypatch):
    from ibee.errors import BillingDeniedError

    class DenyingVms(FakeVms):
        def create_cloud_vm(self, **kwargs):
            self.calls.append(("create", kwargs))
            assert kwargs["preflight_billing"] is True
            raise BillingDeniedError(
                decision={"allowed": False, "reason": "initial_topup_required"}, create_type="vm"
            )

    vms_fake = DenyingVms()
    fake_client(vms, monkeypatch, cloud_vms=vms_fake)
    result = run(CREATE_ARGS, env={"IBEE_CHECK_BILLING": "1"})
    assert result.exit_code == 1
    output = plain(result)
    assert "Add at least ₹2,000 to your wallet before creating your first cloud VM." in output
    assert "Add credits in the IBEE portal" in output


def test_check_billing_falls_back_to_check_with_exact_true_gate():
    from ibee.errors import BillingDeniedError

    billing_fake = FakeBilling({"allowed": "true", "reason": "ok"})
    settings = SimpleNamespace(check_billing=True)
    client = SimpleNamespace(billing=billing_fake)
    with pytest.raises(BillingDeniedError):
        helpers.preflight_billing(settings, client, "973318", resource_type="gpu_vm")
    assert [name for name, _ in billing_fake.calls] == ["check"]


def test_preflight_create_uses_require_eligibility():
    from ibee.errors import BillingDeniedError

    calls = []
    client = SimpleNamespace(billing=RequireBilling(calls, allowed=False, reason="unknown_sku"))
    settings = SimpleNamespace(check_billing=True, workspace="973318")
    with pytest.raises(BillingDeniedError) as info:
        helpers.preflight_create(settings, "cdn", client=client)
    assert "could not be verified" in str(info.value)
    assert calls[0][1]["resource_type"] == "cdn"
    assert helpers.preflight_create(SimpleNamespace(check_billing=False), "cdn") is None


def test_billing_eligibility_operation_and_table(monkeypatch):
    billing_fake = FakeBilling(
        {"allowed": False, "reason": "insufficient_balance", "billing_state": "CURRENT",
         "billing_mode": "PREPAID", "currency": "INR", "organization_id": "org-1"}
    )
    fake_client(billing, monkeypatch, billing=billing_fake)
    result = run(["-o", "table", "billing", "eligibility", "--operation", " mutate_resource ",
                  "--sku-code", "  VM-1  "])
    assert result.exit_code == 0, result.output
    assert billing_fake.calls == [
        ("check", {"workspace_id": "973318", "sku_code": "VM-1", "operation": "MUTATE_RESOURCE"})
    ]
    output = plain(result)
    assert "insufficient_balance" in output
    assert "wallet balance does not cover" in output


def test_billing_eligibility_require_exit_codes(monkeypatch):
    billing_fake = FakeBilling({"allowed": False, "reason": "insufficient_balance"})
    fake_client(billing, monkeypatch, billing=billing_fake)
    assert run(["billing", "eligibility"]).exit_code == 0
    result = run(["billing", "eligibility", "--require"])
    assert result.exit_code == 1
    assert "Add credits in the IBEE portal" in plain(result)

    billing_fake.decision = {"allowed": True, "reason": "ok"}
    assert run(["billing", "eligibility", "--require"]).exit_code == 0


@pytest.mark.parametrize(
    "args",
    [["--operation", "launch"], ["--sku-code", "S" * 65], ["--estimated-cost-minor", "-1"]],
)
def test_billing_eligibility_validation_exits_2(monkeypatch, args):
    billing_fake = FakeBilling({"allowed": True})
    fake_client(billing, monkeypatch, billing=billing_fake)
    result = run(["billing", "eligibility", *args])
    assert result.exit_code == 2
    assert billing_fake.calls == []


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_vm_list_forwards_paging_options(monkeypatch):
    fake = FakeVms()
    fake_client(vms, monkeypatch, cloud_vms=fake)
    result = run(["vms", "list", "--limit", "20", "--offset", "40", "--search", " web ",
                  "--sort-by", "name", "--sort-direction", "asc"])
    assert result.exit_code == 0, result.output
    assert fake.calls == [
        ("list", {"workspace_id": "973318", "limit": 20, "offset": 40, "search": "web",
                  "sort_by": "name", "sort_direction": "asc"})
    ]


@pytest.mark.parametrize(
    "args", [["--limit", "101"], ["--offset", "-1"], ["--sort-by", "size"], ["--search", "x" * 121]]
)
def test_vm_list_paging_validation_exits_2(monkeypatch, args):
    fake = FakeVms()
    fake_client(vms, monkeypatch, cloud_vms=fake)
    result = run(["vms", "list", *args])
    assert result.exit_code == 2
    assert fake.calls == []


def test_gpu_list_paging_and_firewall_list_use_the_sdk(monkeypatch):
    calls = []

    class GpuVms:
        def list_gpu_vms(self, **kwargs):
            calls.append(("gpu", kwargs))
            return []

    class Firewalls:
        def list_firewall_groups(self, **kwargs):
            calls.append(("fw", kwargs))
            return [{"id": "fw-1"}]

    fake_client(gpus, monkeypatch, gpu_vms=GpuVms())
    fake_client(firewalls, monkeypatch, firewalls=Firewalls())
    assert run(["gpus", "list", "--limit", "5"]).exit_code == 0
    assert run(["firewalls", "list"]).exit_code == 0
    result = run(["-o", "id", "firewalls", "list", "--limit", "10", "--offset", "10"])
    assert result.output.strip() == "fw-1"
    assert calls == [
        ("gpu", {"workspace_id": "973318", "limit": 5}),
        ("fw", {"workspace_id": "973318"}),
        ("fw", {"workspace_id": "973318", "limit": 10, "offset": 10}),
    ]
