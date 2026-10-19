"""`ibee vms`, `ibee gpus`, `ibee ops`, `ibee compute`, `ibee console` and `ibee billing` send one SDK call each."""

from __future__ import annotations

import json

import pytest
from _net_fixtures import TS, plain, run

from ibee_cli import helpers

VM = "vm-1"
ACCEPTED = {"operation_id": "op-1", "vm_id": VM, "status": "accepted", "submitted_at": TS}
OPTIONS = [{"billing_interval": "HOURLY", "unit_price_minor": 100}, {"billing_interval": "MONTHLY", "unit_price_minor": 7000}]


def operation(status: str, **extra) -> dict:
    return {"operation_id": "op-1", "vm_id": VM, "action": "create", "status": status, "submitted_at": TS, "updated_at": TS, **extra}


def plan(**extra) -> dict:
    record = {"plan_id": "p1", "vm_type": "cloud", "name": "n", "code": "c", "cpu": 2, "ram_mb": 2048, "disk_gb": 40,
              "gpu_count": 0, "selectable": True, "pricing_status": "priced", "currency": "INR", "billing_interval": "HOURLY",
              "billing_catalog": {"sku_id": 11, "sku_code": "VM-2C", "billing_options": OPTIONS}}
    return {**record, **extra}


def catalog(gw, vm_type: str = "cloud", **plan_extra) -> None:
    gw.on("GET", "compute/plans", (200, {"plans": [plan(vm_type=vm_type, **plan_extra)], "count": 1, "vm_type": vm_type,
                                         "currency": "INR", "billing_interval": "HOURLY"}))
    gw.on("GET", "compute/images", (200, {"images": [{"template_id": "t1", "name": "u", "os_distro": "ubuntu", "os_type": "linux",
                                                      "architecture": "x86_64", "size_bytes": 1, "gpu_compatible": True,
                                                      "compatible_vm_types": [vm_type], "site_ids": ["s1"]}],
                                          "count": 1, "vm_type": vm_type}))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(helpers, "_sleep", lambda seconds: None)


def test_create_uses_the_held_sdk_flow_and_sends_one_create(gw) -> None:
    catalog(gw)
    gw.on("POST", "compute/cloud-vms", (202, ACCEPTED))
    result = run(["vms", "create", "web", "--site-id", "s1", "--plan-id", "p1", "--template-id", "t1", "--billing-term", "MONTHLY",
                  "--ssh-key", "ssh-ed25519 AAAA me", "--tag", "a"])
    assert result.exit_code == 0, result.output
    assert "Create accepted for web (operation op-1" in plain(result)
    create = gw.last("POST", "compute/cloud-vms")
    assert create.json["billing_catalog"]["billing_interval"] == "MONTHLY"
    assert (create.json["cpu"], create.json["os_type"], create.json["ssh_keys"], create.json["tags"]) == (2, "linux", ["ssh-ed25519 AAAA me"], ["a"])
    assert create.headers["x-idempotency-key"]
    assert len(gw.writes()) == 1


def test_create_name_and_shape_are_sent_as_typed(gw) -> None:
    """No client name rule, no plan selectable check: the server answers."""
    catalog(gw, selectable=False, pricing_status="unpriced")
    gw.on("POST", "compute/cloud-vms", (422, {"detail": [{"loc": ["body", "name"], "msg": "invalid hostname"}]}))
    result = run(["vms", "create", "Web_01!", "--site-id", "s1", "--plan-id", "p1", "--template-id", "t1"])
    assert result.exit_code == 16
    assert gw.last("POST", "compute/cloud-vms").json["name"] == "Web_01!"
    assert "Error: HTTP 422" in plain(result)


def test_create_requires_a_site(gw) -> None:
    """Contract 0.4.1: site_id is required on VM create; nothing is sent without it."""
    for group in ("vms", "gpus"):
        result = run([group, "create", "web", "--plan-id", "p1", "--template-id", "t1"])
        assert result.exit_code == 2
        assert "--site-id" in plain(result)
    assert gw.requests("POST") == []


def test_count_creates_numbered_vms_with_numbered_keys(gw) -> None:
    catalog(gw)
    gw.on("POST", "compute/cloud-vms", (202, ACCEPTED))
    result = run(["vms", "create", "web", "--site-id", "s1", "--plan-id", "p1", "--template-id", "t1", "--count", "3", "--idempotency-key", "k"])
    assert result.exit_code == 0, result.output
    creates = gw.requests("POST")
    assert [c.json["name"] for c in creates] == ["web-1", "web-2", "web-3"]
    assert [c.headers["x-idempotency-key"] for c in creates] == ["k-1", "k-2", "k-3"]


def test_gpu_create_takes_the_gpu_shape_from_the_plan(gw) -> None:
    catalog(gw, vm_type="gpu", gpu_count=1, gpu_model="L4")
    gw.on("POST", "compute/gpu-vms", (202, ACCEPTED))
    assert run(["gpus", "create", "g", "--site-id", "s1", "--plan-id", "p1", "--template-id", "t1"]).exit_code == 0
    body = gw.last("POST", "compute/gpu-vms").json
    assert (body["gpu_count"], body["gpu_model"]) == (1, "L4")


def test_create_with_wait_polls_the_operation(gw) -> None:
    catalog(gw)
    gw.on("POST", "compute/cloud-vms", (202, ACCEPTED))
    gw.on("GET", "compute/operations/op-1", (200, operation("running")), (200, operation("succeeded")))
    result = run(["vms", "create", "web", "--site-id", "s1", "--plan-id", "p1", "--template-id", "t1", "--wait"])
    assert result.exit_code == 0, result.output
    assert "Create succeeded for web" in plain(result)


def test_failed_and_timed_out_waits(gw, monkeypatch: pytest.MonkeyPatch) -> None:
    gw.on("POST", "compute/cloud-vms/vm-1/actions/stop", (202, ACCEPTED))
    gw.on("GET", "compute/operations/op-1", (200, operation("failed", error_code="E1", error_message="boom")))
    result = run(["vms", "stop", VM, "--wait"])
    assert result.exit_code == 1 and "E1: boom" in plain(result)

    gw.routes[("GET", "compute/operations/op-1")] = [(200, operation("running"))]
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(helpers, "_clock", lambda: next(clock))
    result = run(["vms", "stop", VM, "--wait", "--timeout", "150"])
    assert result.exit_code == 3 and "ibee ops wait op-1" in plain(result)


def test_list_table_json_and_all_pages(gw) -> None:
    vms = [{"id": f"vm{i}", "name": f"n{i}", "status": "running", "cpu": 2, "ram_mb": 2048, "public_ip": None} for i in range(3)]
    gw.on("GET", "compute/cloud-vms", (200, vms))
    result = run(["vms", "list"])
    assert result.exit_code == 0 and "vm2" in result.output
    assert gw.calls[-1].params == {"workspace_id": "973318", "limit": "100", "offset": "0"}
    result = run(["-o", "json", "vms", "list", "--limit", "2"])
    assert json.loads(result.output)[0]["id"] == "vm0"
    assert gw.calls[-1].params["limit"] == "2"


def test_delete_confirms_and_defaults_to_releasing_the_public_ip(gw) -> None:
    gw.on("GET", "compute/cloud-vms/vm-1", (200, {"id": VM, "name": "web", "public_ip": "203.0.113.5"}))
    gw.on("DELETE", "compute/cloud-vms/vm-1", (202, ACCEPTED))
    result = run(["vms", "delete", VM], input="n\n")
    assert result.exit_code == 1 and gw.writes() == []
    assert run(["vms", "delete", VM, "--yes"]).exit_code == 0
    assert gw.last("DELETE", "compute/cloud-vms/vm-1").json == {"public_ip_action": "release"}


def test_delete_reserve_passes_the_sku_through(gw) -> None:
    gw.on("DELETE", "compute/gpu-vms/vm-1", (202, ACCEPTED))
    sku = {"sku_id": 41, "sku_code": "RESERVED-IP"}
    result = run(["gpus", "delete", VM, "--yes", "--reserve-public-ip", "--reserved-ip-label", "keep",
                  "--reserved-ip-billing-catalog", json.dumps(sku)])
    assert result.exit_code == 0, result.output
    assert gw.last("DELETE", "compute/gpu-vms/vm-1").json == {
        "public_ip_action": "reserve", "reserved_ip_label": "keep", "reserved_ip_billing_catalog": sku,
    }


def test_power_and_volume_actions_are_single_calls(gw) -> None:
    gw.on("POST", "compute/cloud-vms/vm-1/actions/reboot", (202, ACCEPTED))
    gw.on("POST", "compute/cloud-vms/vm-1/actions/detach-volume", (202, ACCEPTED))
    assert run(["vms", "reboot", VM, "--force"]).exit_code == 0
    assert gw.last("POST", "compute/cloud-vms/vm-1/actions/reboot").json == {"force": True}
    # No client rule asks for --confirm-unmounted or --force: the server decides.
    assert run(["vms", "volume-detach", VM, "vol-1", "--yes"]).exit_code == 0
    assert gw.last("POST", "compute/cloud-vms/vm-1/actions/detach-volume").json == {"volume_id": "vol-1"}


def test_resize_with_a_plan_sends_the_plan_shape_and_sku(gw) -> None:
    gw.on("GET", "compute/cloud-vms/vm-1", (200, {"id": VM, "site_id": "s1", "os_type": "linux"}))
    catalog(gw, cpu=4, ram_mb=8192, disk_gb=80)
    gw.on("POST", "compute/cloud-vms/vm-1/actions/resize", (202, ACCEPTED))
    assert run(["vms", "resize", VM, "--plan-id", "p1"]).exit_code == 0
    body = gw.last("POST", "compute/cloud-vms/vm-1/actions/resize").json
    assert (body["cpu"], body["ram_mb"], body["disk_gb"], body["billing_catalog"]["billing_interval"]) == (4, 8192, 80, "HOURLY")
    assert not [c for c in gw.calls if c.path.endswith("precheck")]


def test_snapshot_create_sends_the_catalog_as_given(gw) -> None:
    sku = {"sku_id": 5, "sku_code": "SNAPSHOT-STD"}
    snapshot = {"snapshot_set_id": "ss-1", "organization_id": "o", "workspace_id": "973318", "vm_id": VM, "name": "s",
                "recovery_point_id": "rp-1", "status": "creating", "created_at": TS, "updated_at": TS}
    gw.on("POST", "compute/cloud-vms/vm-1/snapshots", (201, snapshot))
    result = run(["vms", "snapshots", "create", VM, "s", "--billing-catalog", json.dumps(sku)])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", "compute/cloud-vms/vm-1/snapshots").json == {"name": "s", "billing_catalog": sku}


def test_ops_get_and_wait(gw) -> None:
    gw.on("GET", "compute/operations/op-1", (200, operation("succeeded")))
    result = run(["ops", "get", "op-1"])
    assert result.exit_code == 0 and json.loads(result.output)["status"] == "succeeded"
    assert run(["ops", "wait", "op-1"]).exit_code == 0


def test_console_url_is_hidden_unless_asked(gw) -> None:
    session = {"session_id": "cs-1", "vm_id": VM, "vm_type": "cloud", "console_type": "graphical", "state": "ready",
               "connect_url": "https://console/secret-token", "token_expires_in_seconds": 60, "idle_timeout_seconds": 600,
               "max_duration_seconds": 3600}
    gw.on("POST", "compute/console/sessions", (201, session))
    result = run(["console", "create", VM])
    assert result.exit_code == 0, result.output
    assert "secret-token" not in result.output
    assert "secret-token" in run(["console", "create", VM, "--show-url"]).output


def test_billing_eligibility_passes_new_operations_through(gw) -> None:
    decision = {"allowed": False, "reason": "resource_limit_reached", "billing_state": "ACTIVE", "billing_mode": "PREPAID",
                "currency": "INR", "organization_id": "o", "evaluated_at": TS}
    gw.on("POST", "billing/resource-eligibility", (200, decision))
    result = run(["billing", "eligibility", "--operation", "SOME_FUTURE_OPERATION", "--resource-type", "cpu_vm"])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", "billing/resource-eligibility").json == {"operation": "SOME_FUTURE_OPERATION", "resource_type": "cpu_vm"}
    assert run(["billing", "eligibility", "--require"]).exit_code == 1
