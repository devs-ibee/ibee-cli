"""0.4.0 portal-parity VM, recovery and VM-volume commands (SDK mocked, no network)."""

from __future__ import annotations

import inspect
import json
import re
from types import SimpleNamespace

import pytest
from click import unstyle
from ibee import Ibee
from ibee.errors import OperationTimeoutError, RecoveryFailedError, error_from_response
from ibee.validation import IbeeValidationError
from typer.testing import CliRunner

from ibee_cli import helpers
from ibee_cli.commands import console, gpus, vm_lifecycle, vms
from ibee_cli.main import app
from ibee_cli.render import api_error_lines, validation_error_lines

runner = CliRunner()
BASE = ["--token", "test-token", "--workspace", "973318"]
VM_ID = "65f0c0ffee0000000000abcd"
SSH_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGx0ZXN0a2V5ZGF0YQ== me@laptop"
RESERVED_SKU = {"sku_id": 11, "sku_code": "PUBLIC-IP-RESERVED"}


def plain(result) -> str:
    return re.sub(r"\s+", " ", unstyle(result.output))


class Recorder:
    """A fake SDK resource: records calls, returns scripted values or raises."""

    def __init__(self, calls, returns=None):
        self.calls = calls
        self.returns = returns if returns is not None else {}

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            value = self.returns.get(name)
            if callable(value):
                return value(*args, **kwargs)
            if isinstance(value, BaseException):
                raise value
            if value is not None:
                return value
            if name.startswith("get_") and name.endswith("_vm"):
                return {"_id": kwargs.get("vm_id"), "name": "web", "status": "running"}
            if name == "get_compute_operation":
                return {"operation_id": kwargs["operation_id"], "status": "succeeded"}
            return SimpleNamespace(operation_id=f"op-{name}", restore_id="rst-1", run_id="run-1",
                                   snapshot_set_id="snap-1")

        return call


@pytest.fixture
def sdk(monkeypatch):
    state = SimpleNamespace(calls=[], returns={})
    resource = Recorder(state.calls, state.returns)
    client = SimpleNamespace(cloud_vms=resource, gpu_vms=resource, vm_console=resource)
    for module in (vms, gpus, vm_lifecycle, console):
        monkeypatch.setattr(module, "get_client", lambda _settings: client)
    return state


def invoke(args, **kwargs):
    return runner.invoke(app, [*BASE, *args], **kwargs)


def names(state):
    return [name for name, _args, _kwargs in state.calls]


def assert_sdk_transport(resource_name, method_name, positional, kwargs):
    client = Ibee(token="test-token", base_url="http://localhost")
    inspect.signature(getattr(getattr(client, resource_name), method_name)).bind(*positional, **kwargs)


CREATE = ["vms", "create", "web", "--site-id", "site-1", "--plan-id", "plan-1", "--template-id", "img-1"]
GPU_CREATE = ["gpus", "create", "train", "--site-id", "site-1", "--plan-id", "gplan", "--template-id", "gimg"]


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_forwards_portal_deploy_fields(sdk, tmp_path):
    key_file = tmp_path / "id.pub"
    key_file.write_text(SSH_KEY.replace("me@laptop", "other") + "\n")
    lic = tmp_path / "lic.json"
    lic.write_text(json.dumps({"sku_id": 5, "sku_code": "WIN-LIC"}))
    result = invoke(
        [
            *CREATE, "--billing-term", "MONTHLY", "--ssh-key", SSH_KEY, "--ssh-key-file", str(key_file),
            "--firewall-group-id", "fw-1", "--vpc-id", "vpc-1", "--subnet-id", "sub-1",
            "--network-connectivity", "nat", "--windows-license-file", str(lic), "--requested-by", "ci",
        ]
    )
    assert result.exit_code == 0, result.output
    name, args, kwargs = sdk.calls[0]
    assert name == "create_cloud_vm"
    assert kwargs["name"] == "web"
    assert kwargs["site_id"] == "site-1"
    assert kwargs["billing_term"] == "MONTHLY"
    assert kwargs["ssh_keys"] == [SSH_KEY, SSH_KEY.replace("me@laptop", "other")]
    assert kwargs["firewall_group_ids"] == ["fw-1"]
    assert (kwargs["vpc_id"], kwargs["subnet_id"], kwargs["network_connectivity"]) == ("vpc-1", "sub-1", "nat")
    assert kwargs["windows_license"] == {"sku_id": 5, "sku_code": "WIN-LIC"}
    assert kwargs["requested_by"] == "ci"
    # CPU, RAM, disk and OS come from the plan and image, not CLI defaults.
    for absent in ("cpu", "ram_mb", "disk_gb", "os_type", "os_distro", "preflight_billing"):
        assert absent not in kwargs
    assert kwargs["idempotency_key"].startswith("cli-vm-create-web-")
    assert_sdk_transport("cloud_vms", name, args, kwargs)


def test_gpu_create_takes_shape_from_plan(sdk):
    result = invoke([*GPU_CREATE, "--preflight-billing"])
    assert result.exit_code == 0, result.output
    name, args, kwargs = sdk.calls[0]
    assert name == "create_gpu_vm"
    assert "gpu_model" not in kwargs and "gpu_count" not in kwargs
    assert kwargs["preflight_billing"] is True
    assert kwargs["idempotency_key"].startswith("cli-gpu-create-train-")
    assert_sdk_transport("gpu_vms", name, args, kwargs)


@pytest.mark.parametrize(
    "extra",
    [
        ["--ssh-key", "ssh-rsa !!!notbase64"], ["--ssh-key", "ssh-dss AAAAB3NzaC1kc3M="],
        ["--ssh-key", "-----BEGIN OPENSSH PRIVATE KEY----- abc"],
        ["--firewall-group-id", "a", "--firewall-group-id", "b"],
        ["--subnet-id", "sub-1"],
        ["--network-connectivity", "nat"],
        ["--billing-term", "WEEKLY"],
        ["--count", "2", "--vpc-id", "v", "--subnet-id", "s", "--network-connectivity", "public_ip",
         "--reserved-public-ip-id", "rip-1"],
        ["--billing-catalog", "[1]"],
    ],
)
def test_create_rejects_invalid_input_before_any_request(sdk, extra):
    result = invoke([*CREATE, *extra])
    assert result.exit_code == 2, result.output
    assert sdk.calls == []


def test_create_requires_site_and_valid_hostname(sdk):
    result = invoke(["vms", "create", "web", "--plan-id", "p", "--template-id", "i"])
    assert result.exit_code == 2
    assert "--site-id is required" in plain(result)
    result = invoke(["vms", "create", "web server", "--site-id", "s", "--plan-id", "p", "--template-id", "i"])
    assert result.exit_code == 2
    assert sdk.calls == []


def test_batch_create_one_request_and_key_per_vm(sdk):
    result = invoke([*CREATE, "--count", "3", "--instance-name", "", "--instance-name", "db"])
    assert result.exit_code == 0, result.output
    creates = [kwargs for name, _a, kwargs in sdk.calls if name == "create_cloud_vm"]
    assert [kwargs["name"] for kwargs in creates] == ["web-1", "db", "web-3"]
    keys = [kwargs["idempotency_key"] for kwargs in creates]
    assert len(set(keys)) == 3
    assert "Create accepted for db" in plain(result)


def test_batch_create_with_explicit_key_suffixes_each_index(sdk):
    result = invoke([*CREATE, "--count", "2", "--idempotency-key", "deploy-7"])
    assert result.exit_code == 0, result.output
    assert [kwargs["idempotency_key"] for _n, _a, kwargs in sdk.calls] == ["deploy-7-1", "deploy-7-2"]


def test_batch_create_stops_at_first_error(sdk):
    responses = iter([SimpleNamespace(operation_id="op-1"), error_from_response(409, {"detail": "VM with this name already exists"})])

    def create(*_args, **_kwargs):
        value = next(responses)
        if isinstance(value, BaseException):
            raise value
        return value

    sdk.returns["create_cloud_vm"] = create
    result = invoke([*CREATE, "--count", "3"])
    assert result.exit_code == 1
    output = plain(result)
    assert "Created 1 of 3 cloud VMs; stopped at web-2." in output
    assert "Create accepted for web-1 (operation op-1)" in output
    assert "VM with this name already exists" in output
    assert names(sdk) == ["create_cloud_vm", "create_cloud_vm"]


def test_batch_create_json_prints_one_list_and_waits_each(sdk):
    result = runner.invoke(app, [*BASE, "--json", *CREATE, "--count", "2", "--wait"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [item["status"] for item in data] == ["succeeded", "succeeded"]
    assert names(sdk).count("get_compute_operation") == 2


def test_batch_wait_reports_failure_after_all(sdk):
    statuses = iter(["failed", "succeeded"])
    sdk.returns["get_compute_operation"] = lambda **kw: {
        "operation_id": kw["operation_id"], "status": next(statuses), "error_message": "no capacity"
    }
    result = invoke([*CREATE, "--count", "2", "--wait"])
    assert result.exit_code == 1
    output = plain(result)
    assert "Create failed for web-1: no capacity" in output
    assert "Create completed for web-2" in output


# ---------------------------------------------------------------------------
# Delete: public IP choice
# ---------------------------------------------------------------------------


def _vm_with_ip(**extra):
    return {"_id": VM_ID, "name": "web", "status": "running", "public_ip": "203.0.113.9", "site_id": "site-1", **extra}


def test_delete_asks_about_auto_assigned_ip_and_defaults_to_release(sdk):
    sdk.returns["get_cloud_vm"] = _vm_with_ip(data_volumes=[{"volume_id": "vol-9"}])
    result = invoke(["vms", "delete", VM_ID], input="y\n\n")
    assert result.exit_code == 0, result.output
    output = plain(result)
    assert "Delete cloud VM 'web' (" in output
    assert "Reserve public IP 203.0.113.9 as a Reserved IP (billing continues)?" in output
    assert "detached automatically and kept: vol-9" in output
    name, args, kwargs = sdk.calls[-1]
    assert name == "delete_cloud_vm"
    assert kwargs["public_ip_action"] == "release"
    assert kwargs["check_state"] is False
    assert_sdk_transport("cloud_vms", name, args, kwargs)


def test_delete_reserve_interactively_with_sku(sdk):
    sdk.returns["get_cloud_vm"] = _vm_with_ip()
    result = invoke(
        ["vms", "delete", VM_ID, "--reserved-ip-billing-catalog", json.dumps(RESERVED_SKU)], input="y\ny\n"
    )
    assert result.exit_code == 0, result.output
    kwargs = sdk.calls[-1][2]
    assert kwargs["public_ip_action"] == "reserve"
    assert kwargs["reserved_ip_label"] == "web"
    assert kwargs["reserved_ip_billing_catalog"]["sku_code"] == "PUBLIC-IP-RESERVED"


def test_delete_reserve_without_sku_deletes_nothing(sdk):
    sdk.returns["get_cloud_vm"] = _vm_with_ip()
    result = invoke(["vms", "delete", VM_ID], input="y\ny\n")
    assert result.exit_code == 2
    assert "--reserved-ip-billing-catalog" in plain(result)
    assert names(sdk) == ["get_cloud_vm"]
    result = invoke(["vms", "delete", VM_ID, "--reserve-public-ip", "--yes"])
    assert result.exit_code == 2
    assert "delete_cloud_vm" not in names(sdk)


def test_delete_with_yes_releases_without_asking(sdk):
    sdk.returns["get_gpu_vm"] = _vm_with_ip()
    result = invoke(["gpus", "delete", VM_ID, "--yes"])
    assert result.exit_code == 0, result.output
    assert "Reserve public IP" not in plain(result)
    name, args, kwargs = sdk.calls[-1]
    assert name == "delete_gpu_vm"
    assert kwargs["public_ip_action"] == "release"
    assert kwargs["idempotency_key"].startswith(f"cli-gpu-delete-{VM_ID}-")
    assert_sdk_transport("gpu_vms", name, args, kwargs)


def test_delete_reserve_flag_with_preflight(sdk):
    sdk.returns["get_cloud_vm"] = _vm_with_ip()
    result = runner.invoke(
        app,
        [*BASE, "--check-billing", "vms", "delete", VM_ID, "--reserve-public-ip", "--reserved-ip-label", "keep",
         "--reserved-ip-billing-catalog", json.dumps(RESERVED_SKU), "--yes"],
    )
    assert result.exit_code == 0, result.output
    kwargs = sdk.calls[-1][2]
    assert (kwargs["public_ip_action"], kwargs["reserved_ip_label"], kwargs["preflight_billing"]) == (
        "reserve", "keep", True
    )


def test_delete_vm_without_public_ip_sends_no_choice(sdk):
    result = invoke(["vms", "delete", VM_ID, "--yes"])
    assert result.exit_code == 0, result.output
    assert "public_ip_action" not in sdk.calls[-1][2]


@pytest.mark.parametrize(
    "args",
    [
        ["vms", "delete", "vm-1", "--yes"],
        ["vms", "delete", VM_ID, "--reserve-public-ip", "--release-public-ip", "--yes"],
        ["vms", "delete", VM_ID, "--release-public-ip", "--reserved-ip-label", "x", "--yes"],
    ],
)
def test_delete_usage_errors(sdk, args):
    result = invoke(args)
    assert result.exit_code == 2, result.output
    assert "delete_cloud_vm" not in names(sdk)


def test_delete_refused_while_deleting(sdk):
    sdk.returns["get_cloud_vm"] = {"_id": VM_ID, "status": "deleting"}
    result = invoke(["vms", "delete", VM_ID, "--yes"])
    assert result.exit_code == 2
    assert "status is 'deleting'" in plain(result)
    assert names(sdk) == ["get_cloud_vm"]


def test_delete_without_state_check_lets_sdk_decide(sdk):
    result = invoke(["vms", "delete", VM_ID, "--yes", "--no-check-state"])
    assert result.exit_code == 0, result.output
    assert names(sdk) == ["delete_cloud_vm"]
    assert sdk.calls[0][2]["check_state"] is False


# ---------------------------------------------------------------------------
# Power, access, resize, volumes, metrics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("group,fragment", [("vms", "cloud_vm"), ("gpus", "gpu_vm")])
@pytest.mark.parametrize("action", ["start", "stop", "reboot"])
def test_power_actions_check_state_by_default(sdk, group, fragment, action):
    result = invoke([group, action, VM_ID])
    assert result.exit_code == 0, result.output
    name, args, kwargs = sdk.calls[0]
    assert name == f"{action}_{fragment}"
    assert kwargs["check_state"] is True
    assert_sdk_transport(f"{fragment}s", name, args, kwargs)
    sdk.calls.clear()
    result = invoke([group, action, VM_ID, "--no-check-state"])
    assert result.exit_code == 0, result.output
    assert sdk.calls[0][2]["check_state"] is False


def test_access_update_reads_key_files_and_checks_state(sdk, tmp_path):
    key_file = tmp_path / "k.pub"
    key_file.write_text(SSH_KEY)
    result = invoke(["vms", "access-update", VM_ID, "--ssh-key-mode", "add", "--ssh-key-file", str(key_file)])
    assert result.exit_code == 0, result.output
    name, args, kwargs = sdk.calls[0]
    assert kwargs["ssh_keys"] == [SSH_KEY]
    assert kwargs["check_state"] is True
    assert_sdk_transport("cloud_vms", name, args, kwargs)


@pytest.mark.parametrize("group,fragment", [("vms", "cloud_vm"), ("gpus", "gpu_vm")])
@pytest.mark.parametrize(
    ("args", "method", "expected"),
    [
        (["resize-precheck", VM_ID, "--plan-id", "plan-2"], "precheck_{f}_resize", {"plan_id": "plan-2"}),
        (["resize", VM_ID, "--plan-id", "plan-2", "--billing-term", "YEARLY"], "resize_{f}",
         {"plan_id": "plan-2", "billing_term": "YEARLY", "check_state": True}),
        (["resize-plan", VM_ID, "--plan-id", "plan-2", "--confirm-downgrade"], "resize_{f}_plan",
         {"plan_id": "plan-2", "confirm_downgrade": True}),
        (["resize-root-disk", VM_ID, "--new-size-gb", "200", "--billing-catalog", '{"sku_id": 1, "sku_code": "X"}'],
         "resize_{f}_root_disk", {"new_size_gb": 200, "billing_catalog": {"sku_id": 1, "sku_code": "X"}}),
        (["volume-attach", VM_ID, "vol-1"], "attach_{f}_volume", {"volume_id": "vol-1", "check_state": True}),
        (["volume-detach", VM_ID, "vol-1", "--force", "--yes", "--no-check-state"], "detach_{f}_volume",
         {"force": True, "check_state": False}),
        (["bandwidth", VM_ID], "get_{f}_bandwidth", {}),
        (["events", VM_ID, "--limit", "500"], "list_{f}_events", {"limit": 500}),
    ],
)
def test_lifecycle_parity_options_forward(sdk, group, fragment, args, method, expected):
    result = invoke([group, *args])
    assert result.exit_code == 0, result.output
    name, positional, kwargs = sdk.calls[0]
    assert name == method.format(f=fragment)
    for key, value in expected.items():
        assert kwargs[key] == value
    if name.endswith("_bandwidth"):
        assert "month" not in kwargs  # the SDK defaults to the current UTC month
    assert_sdk_transport(f"{fragment}s", name, positional, kwargs)


@pytest.mark.parametrize(
    "args",
    [
        ["resize", VM_ID],
        ["resize-plan", VM_ID, "--cpu", "4"],
        ["resize-root-disk", VM_ID, "--new-size-gb", "10001"],
        ["events", VM_ID, "--limit", "501"],
        ["volume-attach", VM_ID, "vol-1", "--mode", "shared"],
        ["volume-detach", VM_ID, "vol-1", "--yes"],
        ["resize", VM_ID, "--plan-id", "p", "--billing-term", "DAILY"],
        ["snapshots", "list", VM_ID, "--limit", "201"],
        ["backup-policy", "update", VM_ID, "--frequency", "hourly"],
        ["backup-policy", "enable", VM_ID, "--window-minutes", "4", "--billing-catalog", "{}"],
    ],
)
def test_lifecycle_usage_errors_exit_2_before_requests(sdk, args):
    result = invoke(["vms", *args])
    assert result.exit_code == 2, result.output
    assert sdk.calls == []


def test_detach_without_confirmation_explains_the_rule(sdk):
    result = invoke(["vms", "volume-detach", VM_ID, "vol-1", "--yes"])
    assert "--confirm-unmounted" in plain(result)


def test_resize_not_in_place_prints_precheck_reasons(sdk):
    sdk.returns["resize_cloud_vm"] = IbeeValidationError(
        "This downgrade requires migration. In-place disk shrink is blocked.",
        code="resize_not_in_place",
        details={"decision": "migration_required", "reasons": ["disk shrink"], "warnings": [{"message": "stop"}]},
    )
    result = invoke(["vms", "resize", VM_ID, "--plan-id", "small"])
    assert result.exit_code == 2
    output = plain(result)
    assert "requires migration" in output
    assert "decision: migration_required" in output
    assert "reason: disk shrink" in output
    assert "warning: stop" in output


def test_precheck_prints_portal_message_for_blocked(sdk):
    sdk.returns["precheck_cloud_vm_resize"] = {"decision": "blocked", "reasons": []}
    result = invoke(["vms", "resize-precheck", VM_ID, "--cpu", "2"])
    assert result.exit_code == 0
    assert "Resize is currently blocked." in plain(result)


def test_resize_blocked_conflict_lines():
    exc = error_from_response(409, {"detail": {"decision": "blocked", "reasons": ["no capacity"], "message": "blocked"}})
    lines = api_error_lines(exc)
    assert lines[0].startswith("Resize not possible (409, decision blocked)")
    assert "  reason: no capacity" in lines


def test_validation_error_lines_without_details():
    assert validation_error_lines(IbeeValidationError("bad", code="x")) == ["bad"]


def test_vm_list_all_conflicts_with_limit(sdk):
    result = invoke(["vms", "list", "--all", "--limit", "5"])
    assert result.exit_code == 2
    result = invoke(["gpus", "list", "--all"])
    assert result.exit_code == 0, result.output


def test_console_is_cloud_only(sdk):
    result = invoke(["console", "create", VM_ID, "--vm-type", "gpu"])
    assert result.exit_code == 2
    assert "only for cloud VMs" in plain(result)
    assert sdk.calls == []
    result = invoke(["console", "create", VM_ID, "--no-check-state"])
    assert result.exit_code == 0, result.output
    assert sdk.calls[0][2]["check_state"] is False


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

SNAP_SKU = {"sku_id": 7, "sku_code": "SNAPSHOT-STD", "product_code": "snapshot_storage"}
BACKUP_SKU = {"sku_id": 8, "sku_code": "BACKUP-STD", "product_code": "backup_storage"}


@pytest.mark.parametrize(
    "args",
    [
        ["snapshots", "create", VM_ID, "nightly"],
        ["backup-policy", "enable", VM_ID],
        ["backups", "create", VM_ID],
    ],
)
def test_billed_recovery_writes_require_a_sku(sdk, args):
    result = invoke(["vms", *args])
    assert result.exit_code == 2
    output = plain(result)
    assert "--billing-catalog" in output
    assert "STD" in output
    assert sdk.calls == []


def test_snapshot_create_from_file_waits_for_success(sdk, tmp_path):
    sku = tmp_path / "sku.json"
    sku.write_text(json.dumps(SNAP_SKU))
    sdk.returns["wait_for_gpu_vm_snapshot"] = {"snapshot_set_id": "snap-1", "status": "succeeded"}
    result = runner.invoke(
        app,
        [*BASE, "--check-billing", "gpus", "snapshots", "create", VM_ID, "nightly",
         "--billing-catalog-file", str(sku), "--wait", "--timeout", "60"],
    )
    assert result.exit_code == 0, result.output
    (create, c_args, c_kwargs), (wait, w_args, w_kwargs) = sdk.calls
    assert create == "create_gpu_vm_snapshot"
    assert c_kwargs["billing_catalog"] == SNAP_SKU
    assert c_kwargs["preflight_billing"] is True
    assert_sdk_transport("gpu_vms", create, c_args, c_kwargs)
    assert wait == "wait_for_gpu_vm_snapshot"
    assert w_args == ("snap-1",)
    assert (w_kwargs["vm_id"], w_kwargs["timeout"]) == (VM_ID, 60)
    assert_sdk_transport("gpu_vms", wait, w_args, w_kwargs)
    assert json.loads(result.output)["status"] == "succeeded"


def test_snapshot_wait_failure_and_timeout(sdk):
    sdk.returns["wait_for_cloud_vm_snapshot"] = RecoveryFailedError(
        {"snapshot_set_id": "snap-1", "status": "failed", "error_message": "disk busy"},
        kind="snapshot", id_field="snapshot_set_id",
    )
    args = ["vms", "snapshots", "create", VM_ID, "n", "--billing-catalog", json.dumps(SNAP_SKU), "--wait"]
    result = invoke(args)
    assert result.exit_code == 1
    assert "disk busy" in plain(result)
    sdk.returns["wait_for_cloud_vm_snapshot"] = OperationTimeoutError({"status": "running"}, timeout=5, operation_id="snap-1")
    result = invoke(args)
    assert result.exit_code == 3
    assert "resume with: ibee vms snapshots get snap-1" in plain(result)
    # Recovery waits default to 30 minutes.
    assert sdk.calls[-1][2]["timeout"] == 1800


def test_snapshot_restore_new_vm_options(sdk):
    result = invoke(
        [
            "vms", "snapshots", "restore", VM_ID, "snap-1", "--target-mode", "new_vm", "--target-plan-id", "plan-2",
            "--target-volume-name", "vol-a=data-copy", "--vpc-id", "vpc-1", "--subnet-id", "sub-1",
            "--network-connectivity", "private", "--ssh-key-id", "key-1", "--yes", "--wait",
        ]
    )
    assert result.exit_code == 0, result.output
    (name, args, kwargs), (wait, w_args, _w) = sdk.calls
    assert name == "restore_cloud_vm_snapshot"
    assert kwargs["target_volume_names"] == {"vol-a": "data-copy"}
    assert (kwargs["vpc_id"], kwargs["subnet_id"], kwargs["network_connectivity"]) == ("vpc-1", "sub-1", "private")
    assert kwargs["ssh_key_ids"] == ["key-1"]
    assert kwargs["check_state"] is True
    assert_sdk_transport("cloud_vms", name, args, kwargs)
    assert (wait, w_args) == ("wait_for_cloud_vm_snapshot_restore", ("rst-1",))


@pytest.mark.parametrize(
    "args",
    [
        ["snapshots", "restore", VM_ID, "snap-1", "--target-mode", "volume_only", "--yes"],
        ["snapshots", "restore", VM_ID, "snap-1", "--target-vm-name", "x", "--yes"],
        ["snapshots", "restore", VM_ID, "snap-1", "--vpc-id", "v", "--yes"],
        ["snapshots", "restore", VM_ID, "snap-1", "--target-mode", "new_vm", "--target-volume-name", "novalue", "--yes"],
        ["backups", "restore", VM_ID, "rp-1", "--selected-volume-id", "vol-1", "--yes"],
        ["snapshots", "restore", VM_ID, "snap-1", "--target-mode", "clone", "--yes"],
    ],
)
def test_restore_mode_rules_exit_2_before_prompt(sdk, args):
    result = invoke(["vms", *args])
    assert result.exit_code == 2, result.output
    assert sdk.calls == []


def test_backup_restore_and_status_wait(sdk):
    result = invoke(
        ["gpus", "backups", "restore", VM_ID, "rp-1", "--target-mode", "new_vm", "--target-billing-catalog",
         json.dumps({"sku_id": 3, "sku_code": "GPU-A100"}), "--yes"]
    )
    assert result.exit_code == 0, result.output
    name, args, kwargs = sdk.calls[0]
    assert kwargs["target_billing_catalog"]["sku_code"] == "GPU-A100"
    assert_sdk_transport("gpu_vms", name, args, kwargs)
    sdk.calls.clear()
    sdk.returns["wait_for_gpu_vm_backup_restore"] = OperationTimeoutError({"status": "running"}, timeout=5, operation_id="r9")
    result = invoke(["gpus", "backups", "restore-status", "r9", "--wait"])
    assert result.exit_code == 3
    assert "resume with: ibee gpus backups restore-status r9 --wait" in plain(result)
    result = invoke(["gpus", "backups", "restore-status", "r9", "--timeout", "30"])
    assert result.exit_code == 2


def test_backup_policy_and_runs(sdk):
    result = invoke(
        ["vms", "backup-policy", "update", VM_ID, "--billing-catalog", json.dumps(BACKUP_SKU), "--no-check-state"]
    )
    assert result.exit_code == 0, result.output
    name, args, kwargs = sdk.calls[-1]
    assert kwargs["billing_catalog"] == BACKUP_SKU
    assert kwargs["check_state"] is False
    assert_sdk_transport("cloud_vms", name, args, kwargs)

    result = invoke(["vms", "backups", "create", VM_ID, "--billing-catalog", json.dumps(BACKUP_SKU), "--wait"])
    assert result.exit_code == 0, result.output
    assert names(sdk)[-2:] == ["create_cloud_vm_backup_run", "wait_for_cloud_vm_backup_run"]
    assert sdk.calls[-1][1] == ("run-1",)

    result = invoke(["vms", "backups", "list", VM_ID, "--restorable-only", "--limit", "200"])
    assert result.exit_code == 0, result.output
    assert sdk.calls[-1][2]["restorable_only"] is True


def test_backups_list_all_and_delete(sdk):
    result = invoke(["gpus", "backups", "list-all", "--vm-id", VM_ID, "--status", "succeeded", "--status", "failed"])
    assert result.exit_code == 0, result.output
    name, args, kwargs = sdk.calls[-1]
    assert name == "list_all_gpu_vm_backup_runs"
    assert kwargs["status"] == ["succeeded", "failed"]
    assert_sdk_transport("gpu_vms", name, args, kwargs)

    result = invoke(["gpus", "backups", "list-all", "--status", "done"])
    assert result.exit_code == 2

    result = invoke(["vms", "backups", "delete", "run-1"])
    assert result.exit_code == 1  # declined confirmation
    result = invoke(["vms", "backups", "delete", "run-1", "--yes"])
    assert result.exit_code == 0, result.output
    name, args, kwargs = sdk.calls[-1]
    assert (name, args, kwargs["check_state"]) == ("delete_cloud_vm_backup_run", ("run-1",), True)
    assert_sdk_transport("cloud_vms", name, args, kwargs)


def test_snapshot_delete_checks_restore_state(sdk):
    result = invoke(["vms", "snapshots", "delete", "snap-1", "--yes"])
    assert result.exit_code == 0, result.output
    name, args, kwargs = sdk.calls[-1]
    assert kwargs["check_state"] is True
    assert_sdk_transport("cloud_vms", name, args, kwargs)


def test_uncontracted_commands_say_so():
    for group in ("vms", "gpus"):
        for command in ("list-all", "delete"):
            result = runner.invoke(app, [group, "backups", command, "--help"])
            assert result.exit_code == 0
            assert "Not yet part of the published API contract" in plain(result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_parse_key_value_pairs_and_load_json_input(tmp_path):
    assert helpers.parse_key_value_pairs(["a=b=c", " k =v"], "--x") == {"a": "b=c", "k": "v"}
    assert helpers.parse_key_value_pairs(None, "--x") is None
    with pytest.raises(Exception):
        helpers.parse_key_value_pairs(["a=1", "a=2"], "--x")
    path = tmp_path / "c.json"
    path.write_text('{"sku_id": 1}')
    assert helpers.load_json_input(None, str(path), "billing-catalog") == {"sku_id": 1}
    with pytest.raises(Exception):
        helpers.load_json_input("{}", str(path), "billing-catalog")
    with pytest.raises(Exception):
        helpers.load_json_input(None, str(tmp_path / "missing.json"), "billing-catalog")


def test_resolve_wait_recovery_default_timeout():
    config = helpers.resolve_wait(True, None, None, default_timeout=1800)
    assert config.timeout == 1800
    assert helpers.resolve_wait(True).timeout == 1200
