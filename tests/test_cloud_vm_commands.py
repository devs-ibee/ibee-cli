"""Cloud VM CLI lifecycle tests — no network required."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from ibee_cli.commands import gpus, ops, vms
from ibee_cli.main import app

runner = CliRunner()
BASE_ARGS = ["--token", "test-token", "--workspace", "workspace-123"]


class FakeBilling:
    def __init__(self, *, allowed: bool = True, reason: str = "ok", calls=None):
        self.allowed = allowed
        self.reason = reason
        self.calls = [] if calls is None else calls

    def check_resource_eligibility(self, **kwargs):
        self.calls.append(("billing", kwargs))
        return SimpleNamespace(allowed=self.allowed, reason=self.reason)


class FakeCatalog:
    def __init__(self, calls):
        self.calls = calls

    def list_compute_plans(self, **kwargs):
        self.calls.append(("catalog", kwargs))
        return SimpleNamespace(
            plans=[SimpleNamespace(plan_id="plan-1", code="STANDARD-2-4")]
        )


class FakeCloudVms:
    def __init__(self, calls, list_result=None):
        self.calls = calls
        self.list_result = [] if list_result is None else list_result

    def list_cloud_vms(self, **kwargs):
        self.calls.append(("list", kwargs))
        return self.list_result

    def get_cloud_vm(self, **kwargs):
        self.calls.append(("get", kwargs))
        return {"id": kwargs["vm_id"]}

    def create_cloud_vm(self, **kwargs):
        self.calls.append(("create", kwargs))
        return SimpleNamespace(operation_id="op-create")

    def delete_cloud_vm(self, **kwargs):
        self.calls.append(("delete", kwargs))
        return SimpleNamespace(operation_id="op-delete")

    def start_cloud_vm(self, **kwargs):
        self.calls.append(("start", kwargs))
        return SimpleNamespace(operation_id="op-start")

    def stop_cloud_vm(self, **kwargs):
        self.calls.append(("stop", kwargs))
        return SimpleNamespace(operation_id="op-stop")

    def reboot_cloud_vm(self, **kwargs):
        self.calls.append(("reboot", kwargs))
        return SimpleNamespace(operation_id="op-reboot")

    def get_cloud_vm_metrics_overview(self, **kwargs):
        self.calls.append(("metrics", kwargs))
        return {"cpu_percent": 12.5}

    def get_compute_operation(self, **kwargs):
        self.calls.append(("operation", kwargs))
        return SimpleNamespace(status="succeeded", operation_id=kwargs["operation_id"])


def _client(calls, *, list_result=None, allowed=True, include_billing=True):
    client = SimpleNamespace(
        cloud_vms=FakeCloudVms(calls, list_result=list_result),
        compute_catalog=FakeCatalog(calls),
    )
    if include_billing:
        client.billing = FakeBilling(allowed=allowed, reason="insufficient_balance", calls=calls)
    return client


def _invoke(args):
    return runner.invoke(app, [*BASE_ARGS, *args])


def test_list_renders_bare_sdk_list(monkeypatch):
    calls = []
    vm = SimpleNamespace(
        id="vm-1",
        name="web",
        status="running",
        cpu=2,
        ram_mb=4096,
        public_ip="203.0.113.5",
    )
    monkeypatch.setattr(vms, "get_client", lambda _settings: _client(calls, list_result=[vm]))

    result = _invoke(["vms", "list"])

    assert result.exit_code == 0, result.output
    assert "web" in result.output
    assert "203.0.113.5" in result.output
    assert calls == [("list", {"workspace_id": "workspace-123"})]


def test_list_json_serializes_bare_sdk_models(monkeypatch):
    calls = []

    class Model:
        def model_dump(self):
            return {"id": "vm-1", "name": "web"}

    monkeypatch.setattr(vms, "get_client", lambda _settings: _client(calls, list_result=[Model()]))
    result = runner.invoke(app, [*BASE_ARGS, "--json", "vms", "list"])
    assert result.exit_code == 0, result.output
    assert '"id": "vm-1"' in result.output
    assert '"name": "web"' in result.output


def test_get_forwards_workspace_and_vm_id(monkeypatch):
    calls = []
    monkeypatch.setattr(vms, "get_client", lambda _settings: _client(calls))
    result = _invoke(["vms", "get", "vm-1"])
    assert result.exit_code == 0, result.output
    assert calls == [("get", {"workspace_id": "workspace-123", "vm_id": "vm-1"})]


def test_create_resolves_plan_sku_preflights_then_submits(monkeypatch):
    calls = []
    monkeypatch.setattr(vms, "get_client", lambda _settings: _client(calls))
    result = _invoke(
        [
            "vms",
            "create",
            "web",
            "--site-id",
            "site-1",
            "--plan-id",
            "plan-1",
            "--template-id",
            "image-1",
            "--ssh-key-id",
            "key-1",
            "--tag",
            "production",
            "--wait",
        ]
    )
    assert result.exit_code == 0, result.output
    assert [name for name, _ in calls] == ["catalog", "billing", "create", "operation"]
    assert calls[0][1] == {
        "workspace_id": "workspace-123",
        "vm_type": "cloud",
        "site_id": "site-1",
    }
    assert calls[1][1] == {
        "workspace_id": "workspace-123",
        "sku_code": "STANDARD-2-4",
    }
    create = calls[2][1]
    assert create["workspace_id"] == "workspace-123"
    assert create["site_id"] == "site-1"
    assert create["plan_id"] == "plan-1"
    assert create["template_id"] == "image-1"
    assert create["ssh_key_ids"] == ["key-1"]
    assert create["tags"] == ["production"]
    assert create["idempotency_key"].startswith("cli-vm-create-web-")
    assert "Create completed" in result.output


def test_billing_denial_prevents_vm_create(monkeypatch):
    calls = []
    monkeypatch.setattr(
        vms, "get_client", lambda _settings: _client(calls, allowed=False)
    )
    result = _invoke(
        [
            "vms",
            "create",
            "web",
            "--site-id",
            "site-1",
            "--plan-id",
            "plan-1",
            "--template-id",
            "image-1",
        ]
    )
    assert result.exit_code == 1
    assert "insufficient_balance" in result.output
    assert [name for name, _ in calls] == ["catalog", "billing"]


def test_older_sdk_without_billing_resource_remains_compatible(monkeypatch):
    calls = []
    monkeypatch.setattr(
        vms,
        "get_client",
        lambda _settings: _client(calls, include_billing=False),
    )
    result = _invoke(
        [
            "vms",
            "create",
            "web",
            "--site-id",
            "site-1",
            "--plan-id",
            "plan-1",
            "--template-id",
            "image-1",
        ]
    )
    assert result.exit_code == 0, result.output
    assert [name for name, _ in calls] == ["catalog", "create"]


@pytest.mark.parametrize("missing", ["plan", "template"])
def test_create_requires_catalog_ids_before_any_sdk_call(monkeypatch, missing):
    calls = []
    monkeypatch.setattr(vms, "get_client", lambda _settings: _client(calls))
    args = ["vms", "create", "web"]
    args += ["--site-id", "site-1"]
    if missing != "plan":
        args += ["--plan-id", "plan-1"]
    if missing != "template":
        args += ["--template-id", "image-1"]
    result = _invoke(args)
    assert result.exit_code != 0
    assert calls == []


def test_delete_requires_confirmation(monkeypatch):
    calls = []
    monkeypatch.setattr(vms, "get_client", lambda _settings: _client(calls))
    result = _invoke(["vms", "delete", "vm-1"],)
    assert result.exit_code != 0
    assert calls == []


def test_delete_yes_and_wait(monkeypatch):
    calls = []
    monkeypatch.setattr(vms, "get_client", lambda _settings: _client(calls))
    result = _invoke(["vms", "delete", "vm-1", "--yes", "--wait"])
    assert result.exit_code == 0, result.output
    assert [name for name, _ in calls] == ["delete", "operation"]
    assert calls[0][1]["idempotency_key"].startswith("cli-vm-delete-vm-1-")
    assert "Delete completed" in result.output


@pytest.mark.parametrize("action", ["start", "stop", "reboot"])
def test_power_actions_are_not_wallet_blocked(monkeypatch, action):
    calls = []
    client = _client(calls, allowed=False)
    monkeypatch.setattr(vms, "get_client", lambda _settings: client)
    result = _invoke(["vms", action, "vm-1"])
    assert result.exit_code == 0, result.output
    assert [name for name, _ in calls] == [action]
    assert calls[0][1]["workspace_id"] == "workspace-123"
    assert calls[0][1]["vm_id"] == "vm-1"


def test_metrics_uses_current_sdk_method(monkeypatch):
    calls = []
    monkeypatch.setattr(vms, "get_client", lambda _settings: _client(calls))
    result = _invoke(["vms", "metrics", "vm-1"])
    assert result.exit_code == 0, result.output
    assert calls == [
        ("metrics", {"workspace_id": "workspace-123", "vm_id": "vm-1"})
    ]


def test_operation_status_get_and_wait(monkeypatch):
    calls = []
    client = _client(calls)
    monkeypatch.setattr(ops, "get_client", lambda _settings: client)
    result = _invoke(["ops", "get", "op-1"])
    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "operation",
            {"operation_id": "op-1", "workspace_id": "workspace-123"},
        )
    ]

    calls.clear()
    result = _invoke(["ops", "get", "op-1", "--wait"])
    assert result.exit_code == 0, result.output
    assert [name for name, _ in calls] == ["operation"]


def test_gpu_list_also_handles_bare_sdk_list(monkeypatch):
    calls = []
    vm = SimpleNamespace(
        id="gpu-1",
        name="trainer",
        status="running",
        gpu_model="A100",
        gpu_count=1,
        cpu=8,
        ram_mb=32768,
        public_ip=None,
    )

    class GpuVms:
        def list_gpu_vms(self, **kwargs):
            calls.append(("list", kwargs))
            return [vm]

    monkeypatch.setattr(
        gpus, "get_client", lambda _settings: SimpleNamespace(gpu_vms=GpuVms())
    )
    result = _invoke(["gpus", "list"])
    assert result.exit_code == 0, result.output
    assert "trainer" in result.output
    assert "A100" in result.output


def test_gpu_create_resolves_catalog_sku_and_preflights(monkeypatch):
    calls = []

    class Catalog:
        def list_compute_plans(self, **kwargs):
            calls.append(("catalog", kwargs))
            return SimpleNamespace(
                plans=[SimpleNamespace(plan_id="gpu-plan", code="GPU-A100-1")]
            )

    class GpuVms:
        def create_gpu_vm(self, **kwargs):
            calls.append(("create", kwargs))
            return SimpleNamespace(operation_id="op-gpu")

    client = SimpleNamespace(
        compute_catalog=Catalog(),
        billing=FakeBilling(calls=calls),
        gpu_vms=GpuVms(),
    )
    monkeypatch.setattr(gpus, "get_client", lambda _settings: client)
    result = _invoke(
        [
            "gpus",
            "create",
            "trainer",
            "--site-id",
            "site-1",
            "--gpu-model",
            "A100",
            "--plan-id",
            "gpu-plan",
            "--template-id",
            "gpu-image",
        ]
    )
    assert result.exit_code == 0, result.output
    assert [name for name, _ in calls] == ["catalog", "billing", "create"]
    assert calls[1][1]["sku_code"] == "GPU-A100-1"
