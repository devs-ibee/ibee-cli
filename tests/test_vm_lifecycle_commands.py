"""Complete cloud/GPU lifecycle command-to-SDK transport tests."""

from __future__ import annotations

import datetime as dt
import inspect
import re
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner
from ibee import Ibee

from ibee_cli.commands import console, vm_lifecycle
from ibee_cli.main import app

runner = CliRunner()
BASE = ["--token", "test-token", "--workspace", "973318"]
SNAP_SKU = '{"sku_id": 7, "sku_code": "SNAPSHOT-STD", "product_code": "snapshot_storage"}'
BACKUP_SKU = '{"sku_id": 8, "sku_code": "BACKUP-STD", "product_code": "backup_storage"}'


class RecordingResource:
    def __init__(self, calls):
        self.calls = calls

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name.startswith(("update_", "resize_", "attach_", "detach_")):
                return SimpleNamespace(operation_id=f"op-{name}")
            return {"method": name, "ok": True}

        return call


def fake_client(calls):
    return SimpleNamespace(
        cloud_vms=RecordingResource(calls),
        gpu_vms=RecordingResource(calls),
        vm_console=RecordingResource(calls),
    )


def assert_sdk_transport(resource_name, method_name, positional, kwargs):
    """Keep command forwarding aligned with the installed typed SDK signature."""

    client = Ibee(token="test-token", base_url="http://localhost")
    method = getattr(getattr(client, resource_name), method_name)
    inspect.signature(method).bind(*positional, **kwargs)


@pytest.fixture
def calls(monkeypatch):
    recorded = []
    client = fake_client(recorded)
    monkeypatch.setattr(vm_lifecycle, "get_client", lambda _settings: client)
    monkeypatch.setattr(console, "get_client", lambda _settings: client)
    return recorded


def invoke(args, *, input=None):
    return runner.invoke(app, [*BASE, *args], input=input)


@pytest.mark.parametrize(
    ("group", "fragment"),
    [("vms", "cloud_vm"), ("gpus", "gpu_vm")],
)
def test_access_password_is_stdin_only_and_never_echoed(calls, group, fragment):
    result = invoke(
        [group, "access-update", "vm-1", "--password-stdin", "--enable-password-auth"],
        input="super-secret-value\n",
    )
    assert result.exit_code == 0, result.output
    assert "super-secret-value" not in result.output
    name, args, kwargs = calls[0]
    assert name == f"update_{fragment}_access"
    assert args == ("vm-1",)
    assert kwargs["workspace_id"] == "973318"
    assert kwargs["new_password"] == "super-secret-value"
    assert kwargs["password_auth_enabled"] is True
    kind = "cloud" if group == "vms" else "gpu"
    assert kwargs["idempotency_key"].startswith(f"cli-{kind}-access-vm-1-")
    assert_sdk_transport(f"{kind}_vms", name, args, kwargs)


def test_access_help_has_no_password_value_option():
    for group in ("vms", "gpus"):
        result = invoke([group, "access-update", "--help"])
        assert result.exit_code == 0, result.output
        plain_output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "--password-stdin" in plain_output
        assert "--prompt-password" in plain_output
        assert "--new-password" not in plain_output
        assert "--password " not in plain_output


def test_interactive_password_prompt_is_hidden(calls):
    result = invoke(
        ["vms", "access-update", "vm-1", "--prompt-password"],
        input="another-secret\nanother-secret\n",
    )
    assert result.exit_code == 0, result.output
    assert "another-secret" not in result.output
    assert calls[0][2]["new_password"] == "another-secret"


@pytest.mark.parametrize(
    ("group", "fragment"),
    [("vms", "cloud_vm"), ("gpus", "gpu_vm")],
)
@pytest.mark.parametrize(
    ("args", "method", "expected"),
    [
        (["resize-precheck", "vm-1", "--cpu", "4", "--ram-mb", "8192"], "precheck_{f}_resize", {"cpu": 4, "ram_mb": 8192}),
        (["resize", "vm-1", "--cpu", "4", "--disk-gb", "80"], "resize_{f}", {"cpu": 4, "disk_gb": 80}),
        (["resize-plan", "vm-1", "--cpu", "8", "--ram-mb", "16384", "--allow-online"], "resize_{f}_plan", {"cpu": 8, "ram_mb": 16384, "allow_online": True}),
        (["resize-root-disk", "vm-1", "--new-size-gb", "120"], "resize_{f}_root_disk", {"new_size_gb": 120}),
        (["volume-attach", "vm-1", "vol-1", "--mode", "single-writer"], "attach_{f}_volume", {"volume_id": "vol-1", "mode": "single-writer"}),
        (["volume-detach", "vm-1", "vol-1", "--confirm-unmounted", "--yes"], "detach_{f}_volume", {"volume_id": "vol-1", "confirm_unmounted": True}),
        (["mount-guidance-acknowledge", "vm-1", "vol-1"], "acknowledge_{f}_mount_guidance", {"volume_id": "vol-1"}),
        (["events", "vm-1", "--limit", "25"], "list_{f}_events", {"limit": 25}),
        (["metrics-timeseries", "vm-1", "--range", "24h"], "get_{f}_metrics_timeseries", {"range": "24h"}),
        (["bandwidth", "vm-1", "--month", "2026-08"], "get_{f}_bandwidth", {"month": "2026-08"}),
    ],
)
def test_core_lifecycle_commands_forward_to_typed_sdk(calls, group, fragment, args, method, expected):
    result = invoke([group, *args])
    assert result.exit_code == 0, result.output
    name, positional, kwargs = calls[0]
    assert name == method.format(f=fragment)
    assert positional == ("vm-1",)
    assert kwargs["workspace_id"] == "973318"
    for key, value in expected.items():
        assert kwargs[key] == value
    if name.startswith(("resize_", "attach_", "detach_")):
        assert kwargs["idempotency_key"].startswith("cli-")
    resource_name = "cloud_vms" if group == "vms" else "gpu_vms"
    assert_sdk_transport(resource_name, name, positional, kwargs)


@pytest.mark.parametrize(
    ("group", "fragment"),
    [("vms", "cloud_vm"), ("gpus", "gpu_vm")],
)
@pytest.mark.parametrize(
    ("args", "method", "positionals", "expected"),
    [
        (["snapshots", "list", "vm-1", "--limit", "20", "--offset", "5", "--search", "nightly"], "list_{f}_snapshots", ("vm-1",), {"limit": 20, "offset": 5, "search": "nightly"}),
        (["snapshots", "create", "vm-1", "before-upgrade", "--mode", "selective", "--selected-data-volume-id", "vol-1", "--billing-catalog", SNAP_SKU], "create_{f}_snapshot", ("vm-1",), {"name": "before-upgrade", "mode": "selective", "selected_data_volume_ids": ["vol-1"], "billing_catalog": {"sku_id": 7, "sku_code": "SNAPSHOT-STD", "product_code": "snapshot_storage"}, "check_state": True}),
        (["snapshots", "get", "snap-1"], "get_{f}_snapshot", ("snap-1",), {}),
        (["snapshots", "delete", "snap-1", "--yes"], "delete_{f}_snapshot", ("snap-1",), {}),
        (["snapshots", "restore", "vm-1", "snap-1", "--target-mode", "new_vm", "--target-vm-name", "restored", "--target-site-id", "site-1", "--auto-start", "--yes"], "restore_{f}_snapshot", ("snap-1",), {"vm_id": "vm-1", "target_mode": "new_vm", "target_vm_name": "restored", "target_site_id": "site-1", "auto_start": True}),
        (["snapshots", "restore-status", "restore-1"], "get_{f}_snapshot_restore", ("restore-1",), {}),
    ],
)
def test_snapshot_lifecycle_forwards_to_typed_sdk(calls, group, fragment, args, method, positionals, expected):
    result = invoke([group, *args])
    assert result.exit_code == 0, result.output
    name, positional, kwargs = calls[0]
    assert name == method.format(f=fragment)
    assert positional == positionals
    assert kwargs["workspace_id"] == "973318"
    for key, value in expected.items():
        assert kwargs[key] == value
    resource_name = "cloud_vms" if group == "vms" else "gpu_vms"
    assert_sdk_transport(resource_name, name, positional, kwargs)


@pytest.mark.parametrize(
    ("group", "fragment"),
    [("vms", "cloud_vm"), ("gpus", "gpu_vm")],
)
@pytest.mark.parametrize(
    ("args", "method", "positionals", "expected"),
    [
        (["backup-policy", "get", "vm-1"], "get_{f}_backup_policy", ("vm-1",), {}),
        (["backup-policy", "update", "vm-1", "--frequency", "weekly", "--timezone", "Asia/Kolkata", "--day-of-week", "6", "--retention-days", "30", "--incremental"], "update_{f}_backup_policy", ("vm-1",), {"schedule": {"frequency": "weekly", "timezone": "Asia/Kolkata", "day_of_week": 6}, "retention_days": 30, "incremental_enabled": True}),
        (["backup-policy", "enable", "vm-1", "--frequency", "daily", "--hour", "2", "--billing-catalog", BACKUP_SKU], "enable_{f}_backups", ("vm-1",), {"schedule": {"frequency": "daily", "hour": 2}, "billing_catalog": {"sku_id": 8, "sku_code": "BACKUP-STD", "product_code": "backup_storage"}}),
        (["backup-policy", "disable", "vm-1"], "disable_{f}_backups", ("vm-1",), {}),
        (["backup-policy", "reschedule", "vm-1", "--next-run-at", "2026-08-10T02:00:00Z"], "reschedule_{f}_backup", ("vm-1",), {"next_run_at": dt.datetime(2026, 8, 10, 2, tzinfo=dt.timezone.utc)}),
    ],
)
def test_backup_policy_lifecycle_forwards_to_typed_sdk(calls, group, fragment, args, method, positionals, expected):
    result = invoke([group, *args])
    assert result.exit_code == 0, result.output
    name, positional, kwargs = calls[0]
    assert name == method.format(f=fragment)
    assert positional == positionals
    assert kwargs["workspace_id"] == "973318"
    for key, value in expected.items():
        assert kwargs[key] == value
    resource_name = "cloud_vms" if group == "vms" else "gpu_vms"
    assert_sdk_transport(resource_name, name, positional, kwargs)


@pytest.mark.parametrize(
    ("group", "fragment"),
    [("vms", "cloud_vm"), ("gpus", "gpu_vm")],
)
@pytest.mark.parametrize(
    ("args", "method", "positionals", "expected"),
    [
        (["backups", "list", "vm-1", "--limit", "10"], "list_{f}_backup_runs", ("vm-1",), {"limit": 10}),
        (["backups", "create", "vm-1", "--reason", "release", "--billing-catalog", BACKUP_SKU], "create_{f}_backup_run", ("vm-1",), {"reason": "release", "billing_catalog": {"sku_id": 8, "sku_code": "BACKUP-STD", "product_code": "backup_storage"}}),
        (["backups", "get", "run-1"], "get_{f}_backup_run", ("run-1",), {}),
        (["backups", "restore", "vm-1", "rp-1", "--target-mode", "volume_only", "--selected-volume-id", "vol-1", "--yes"], "restore_{f}_backup", ("vm-1",), {"recovery_point_id": "rp-1", "target_mode": "volume_only", "selected_volume_id": "vol-1"}),
        (["backups", "restore-status", "restore-1"], "get_{f}_backup_restore", ("restore-1",), {}),
    ],
)
def test_backup_run_lifecycle_forwards_to_typed_sdk(calls, group, fragment, args, method, positionals, expected):
    result = invoke([group, *args])
    assert result.exit_code == 0, result.output
    name, positional, kwargs = calls[0]
    assert name == method.format(f=fragment)
    assert positional == positionals
    assert kwargs["workspace_id"] == "973318"
    for key, value in expected.items():
        assert kwargs[key] == value
    resource_name = "cloud_vms" if group == "vms" else "gpu_vms"
    assert_sdk_transport(resource_name, name, positional, kwargs)


def test_destructive_vm_commands_require_confirmation(calls):
    for args in (
        ["vms", "volume-detach", "vm-1", "vol-1"],
        ["vms", "snapshots", "delete", "snap-1"],
        ["vms", "snapshots", "restore", "vm-1", "snap-1"],
        ["vms", "backups", "restore", "vm-1", "rp-1"],
    ):
        result = invoke(args)
        assert result.exit_code != 0
    assert calls == []


@pytest.mark.parametrize(
    ("args", "method", "positionals", "expected"),
    [
        (["console", "create", "vm-1", "--vm-type", "cloud", "--console-type", "graphical"], "create_vm_console_session", (), {"vm_id": "vm-1", "vm_type": "cloud", "console_type": "graphical", "check_state": True}),
        (["console", "get", "session-1"], "get_vm_console_session", ("session-1",), {}),
        (["console", "close", "session-1", "--reason", "finished", "--yes"], "close_vm_console_session", ("session-1",), {"reason": "finished"}),
    ],
)
def test_console_session_lifecycle(calls, args, method, positionals, expected):
    result = invoke(args)
    assert result.exit_code == 0, result.output
    name, positional, kwargs = calls[0]
    assert name == method
    assert positional == positionals
    assert kwargs["workspace_id"] == "973318"
    for key, value in expected.items():
        assert kwargs[key] == value
    assert_sdk_transport("vm_console", name, positional, kwargs)


def test_console_close_requires_confirmation(calls):
    result = invoke(["console", "close", "session-1"])
    assert result.exit_code != 0
    assert calls == []
