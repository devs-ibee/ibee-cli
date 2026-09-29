"""Block Storage and CDN commands against the real Python SDK (scripted gateway).

The SDK's portal rules run for real: names, sizes, IDs, delete and detach guards,
VM attach pre-steps, purge selectors and the purge ``success: false`` check.
"""

from __future__ import annotations

import pytest
from ibee_cli import helpers

import _net_fixtures as net

WS = net.WS
VOL = "64f1c2a9b8e7d6c5b4a39281"
V = f"block-storage/volumes/{VOL}"
CATALOG = {"sku_id": 11, "sku_code": "BLOCKSTO-STD", "unit_price_minor": 700}
TS = "2026-09-01T10:00:00Z"


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """SDK flows sleep through ``time.sleep``; record instead of sleeping."""
    import time

    calls = []
    monkeypatch.setattr(helpers, "_sleep", lambda _s: None)
    monkeypatch.setattr(time, "sleep", calls.append)
    return calls


def volume(**overrides):
    record = {
        "id": VOL,
        "name": "data",
        "size_gb": 100,
        "state": "ready",
        "site_id": "site-1",
        "site_name": "Bengaluru",
        "vm_type": "cloud",
        "attachments": [],
        "metadata": {"billing_catalog": CATALOG},
    }
    record.update(overrides)
    return record


def attached(**overrides):
    return volume(attachments=[{"vm_id": "7a1b2c3d4e5f60718293a4b5", "vm_name": "web", "node_name": "node-a", "vm_type": "cloud"}],
                  **overrides)


def accepted(op="op-1", vm="7a1b2c3d4e5f60718293a4b5"):
    return {"operation_id": op, "vm_id": vm, "status": "queued", "submitted_at": TS}


def op_status(status, op="op-1"):
    return {"operation_id": op, "vm_id": "7a1b2c3d4e5f60718293a4b5", "action": "attach_volume", "status": status,
            "submitted_at": TS, "updated_at": TS, "error_message": "disk busy" if status == "failed" else None}


run, plain = net.run, net.plain


# ---------------------------------------------------------------------------
# Block Storage: list, create, get, operations
# ---------------------------------------------------------------------------


def test_list_sends_filters_and_renders_table(gw):
    gw.on("GET", "block-storage/volumes", [volume(), attached(id="64f1c2a9b8e7d6c5b4a39282", name="logs")])
    result = run(["-o", "table", "block-storage", "list", "--site-id", "site-1", "--vm-type", "cloud",
                  "--limit", "50", "--offset", "10"])
    assert result.exit_code == 0, result.output
    call = gw.last("GET", "block-storage/volumes")
    assert call.params == {"workspace_id": WS, "site_id": "site-1", "vm_type": "cloud", "limit": "50", "offset": "10"}
    output = plain(result)
    assert "Block Storage volumes" in output and "logs" in output and "web" in output


def test_list_all_reads_every_page(gw):
    gw.on("GET", "block-storage/volumes", [volume(), volume(id="64f1c2a9b8e7d6c5b4a39282")],
          [volume(id="64f1c2a9b8e7d6c5b4a39283")])
    result = run(["-o", "id", "block-storage", "list", "--all", "--limit", "2"])
    assert result.exit_code == 0, result.output
    assert [c.params["offset"] for c in gw.requests("GET")] == ["0", "2"]
    assert result.output.split() == [VOL, "64f1c2a9b8e7d6c5b4a39282", "64f1c2a9b8e7d6c5b4a39283"]


def test_list_full_page_hints_at_more(gw):
    gw.on("GET", "block-storage/volumes", [volume(), volume()])
    result = run(["block-storage", "list", "--limit", "2"])
    assert result.exit_code == 0, result.output
    assert "use --all, or --offset 2" in plain(result)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--vm-type", "bare"], "vm_type"),
        (["--limit", "1001"], "limit"),
        (["--all", "--offset", "5"], "--offset cannot be used with --all"),
    ],
)
def test_list_rejects_bad_filters(gw, args, expected):
    result = run(["block-storage", "list", *args])
    assert result.exit_code == 2
    assert expected in plain(result)
    assert gw.calls == []


def test_create_resolves_site_name_and_sends_key_in_body_and_header(gw):
    gw.on("GET", "compute/sites", {"sites": [{"site_id": "site-1", "name": "Bengaluru"}]})
    gw.on("POST", "block-storage/volumes", {"volume": volume(), "operation": {"status": "succeeded"}})
    result = run(["block-storage", "create", "data", "--size-gb", "100", "--site-id", "site-1",
                  "--class", "performance", "--replicas", "3", "--no-backup", "--vm-type", "gpu",
                  "--delete-on-termination", "--sku-code", " blocksto-std "])
    assert result.exit_code == 0, result.output
    call = gw.last("POST", "block-storage/volumes")
    body = call.json
    assert body["name"] == "data" and body["size_gb"] == 100 and body["site_id"] == "site-1"
    assert body["site_name"] == "Bengaluru"
    assert body["volume_class"] == "performance" and body["replica_count"] == 3
    assert body["backup_enabled"] is False
    assert body["vm_type"] == "gpu" and body["delete_on_termination"] is True
    assert body["sku_code"] == "BLOCKSTO-STD"
    assert body["idempotency_key"].startswith("cli-block-create-data-")
    assert call.headers["x-idempotency-key"] == body["idempotency_key"]
    assert not any(k in body for k in ("volume_kind", "billing_catalog", "attach_to_node"))
    assert f"Volume {VOL} is ready." in plain(result)


def test_create_with_site_name_or_no_check_site_skips_site_lookup(gw):
    gw.on("POST", "block-storage/volumes", {"volume": volume(), "operation": {}})
    result = run(["block-storage", "create", "data", "--size-gb", "10", "--site-id", "site-1", "--no-check-site",
                  "--idempotency-key", "create-1"])
    assert result.exit_code == 0, result.output
    assert [c.path for c in gw.calls] == ["block-storage/volumes"]
    assert gw.calls[0].json["idempotency_key"] == "create-1"


def test_create_id_output_prints_the_volume_id(gw):
    gw.on("POST", "block-storage/volumes", {"volume": volume(), "operation": {}})
    result = run(["-o", "id", "block-storage", "create", "data", "--size-gb", "10", "--site-id", "s",
                  "--site-name", "S"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == VOL


def test_create_rejects_unknown_site(gw):
    gw.on("GET", "compute/sites", {"sites": [{"site_id": "site-2", "name": "Mumbai"}]})
    result = run(["block-storage", "create", "data", "--size-gb", "10", "--site-id", "site-1"])
    assert result.exit_code == 2
    assert "Unknown site_id" in plain(result)
    assert gw.writes() == []


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["My Data", "--size-gb", "10"], "try 'my-data'"),
        (["ab", "--size-gb", "10"], "Volume name must be at least 3 characters"),
        (["data", "--size-gb", "5"], "size_gb"),
        (["data", "--size-gb", "10001"], "size_gb"),
        (["data", "--size-gb", "10", "--class", "fast"], "volume_class"),
        (["data", "--size-gb", "10", "--replicas", "6"], "replica_count"),
        (["data", "--size-gb", "10", "--sku-code", "ROOTDISK-STD"], "root disk"),
    ],
)
def test_create_validation_runs_before_any_request(gw, args, expected):
    result = run(["block-storage", "create", *args, "--site-id", "site-1"])
    assert result.exit_code == 2, result.output
    assert expected in plain(result)
    assert gw.calls == []


def test_create_requires_site_before_request(gw):
    result = run(["block-storage", "create", "data", "--size-gb", "100"])
    assert result.exit_code == 2
    assert "--site-id" in plain(result)
    assert gw.calls == []


def test_get_and_operations(gw):
    gw.on("GET", V, volume())
    gw.on("GET", f"{V}/operations", [{"id": "o1", "action": "create", "debug_reason": "internal"}])
    assert run(["block-storage", "get", VOL]).exit_code == 0
    result = run(["block-storage", "operations", VOL, "--limit", "5"])
    assert result.exit_code == 0, result.output
    assert gw.last("GET", f"{V}/operations").params["limit"] == "5"
    assert "debug_reason" not in result.output


@pytest.mark.parametrize(
    "args",
    [["get", "vol/one"], ["operations", "not-hex"], ["operations", VOL, "--limit", "201"],
     ["resize", "vol-1", "--new-size-gb", "20"], ["delete", "vol-1", "--yes"]],
)
def test_volume_ids_and_limits_are_validated(gw, args):
    result = run(["block-storage", *args])
    assert result.exit_code == 2
    assert gw.calls == []


# ---------------------------------------------------------------------------
# Delete and resize
# ---------------------------------------------------------------------------


def test_delete_refuses_attached_volume_before_prompt(gw):
    gw.on("GET", V, attached())
    result = run(["block-storage", "delete", VOL])
    assert result.exit_code == 2
    assert "Detach this volume from all servers before deleting." in plain(result)
    assert gw.writes() == []


def test_delete_refuses_busy_volume(gw):
    gw.on("GET", V, volume(state="resizing"))
    result = run(["block-storage", "delete", VOL, "--yes"])
    assert result.exit_code == 2
    assert "Volume is currently 'resizing'" in plain(result)


def test_delete_sends_query_key_after_check(gw):
    gw.on("GET", V, volume())
    gw.on("DELETE", V, {"status": "deleted", "id": VOL, "operation_id": "o"})
    result = run(["block-storage", "delete", VOL, "--yes", "--idempotency-key", "del-1"])
    assert result.exit_code == 0, result.output
    call = gw.last("DELETE", V)
    assert call.params["idempotency_key"] == "del-1"
    assert call.params["force"] == "false"
    assert len(gw.requests("GET")) == 1


def test_delete_without_read_scope_lets_the_api_decide(gw):
    gw.on("GET", V, (403, {"error": "insufficient_scope", "required_scope": "block-storage.read"}))
    gw.on("DELETE", V, {"status": "deleted", "id": VOL})
    result = run(["block-storage", "delete", VOL, "--yes"])
    assert result.exit_code == 0, result.output
    assert gw.last("DELETE", V).params["idempotency_key"].startswith("cli-block-delete-")


def test_force_delete_asks_twice_and_skips_the_check(gw):
    gw.on("DELETE", V, {"status": "deleted", "id": VOL})
    result = run(["block-storage", "delete", VOL, "--force"], input="y\nn\n")
    assert result.exit_code == 1
    assert "erases all data" in plain(result)
    assert gw.calls == []
    result = run(["block-storage", "delete", VOL, "--force"], input="y\ny\n")
    assert result.exit_code == 0, result.output
    assert gw.last("DELETE", V).params["force"] == "true"
    assert gw.requests("GET") == []


def test_declined_delete_sends_nothing(gw):
    gw.on("GET", V, volume())
    result = run(["block-storage", "delete", VOL], input="n\n")
    assert result.exit_code == 1
    assert gw.writes() == []


@pytest.mark.parametrize(
    ("record", "args", "expected"),
    [
        (volume(size_gb=200), [], "Shrink is not supported"),
        (attached(), [], "Attached volume resize requires"),
        (volume(state="attaching"), [], "Retry resize once workflow completes"),
    ],
)
def test_resize_guards(gw, record, args, expected):
    gw.on("GET", V, record)
    result = run(["block-storage", "resize", VOL, "--new-size-gb", "150", *args])
    assert result.exit_code == 2
    assert expected in plain(result)
    assert gw.writes() == []


def test_resize_attached_offline_and_reminds_to_grow_filesystem(gw):
    gw.on("GET", V, attached())
    gw.on("POST", f"{V}/resize", {"volume": volume(size_gb=150), "operation": {}})
    result = run(["block-storage", "resize", VOL, "--new-size-gb", "150", "--vm-state", "stopped"])
    assert result.exit_code == 0, result.output
    body = gw.last("POST", f"{V}/resize").json
    assert body["new_size_gb"] == 150 and body["vm_state"] == "stopped" and body["allow_online"] is False
    assert body["idempotency_key"].startswith("cli-block-resize-")
    assert "Extend the filesystem" in plain(result)


def test_resize_no_check_state_skips_the_read(gw):
    gw.on("POST", f"{V}/resize", {"volume": volume(), "operation": {}})
    result = run(["block-storage", "resize", VOL, "--new-size-gb", "150", "--allow-online", "--no-check-state"])
    assert result.exit_code == 0, result.output
    assert gw.requests("GET") == []


# ---------------------------------------------------------------------------
# VM attach / detach (portal flow)
# ---------------------------------------------------------------------------


def test_attach_vm_resolves_sku_and_endpoint(gw):
    gw.on("GET", V, volume())
    gw.on("GET", "compute/cloud-vms/7a1b2c3d4e5f60718293a4b5", {"_id": "7a1b2c3d4e5f60718293a4b5", "status": "running", "site_id": "site-1"})
    gw.on("POST", "compute/cloud-vms/7a1b2c3d4e5f60718293a4b5/actions/attach-volume", accepted())
    result = run(["block-storage", "attach-vm", VOL, "7a1b2c3d4e5f60718293a4b5"])
    assert result.exit_code == 0, result.output
    call = gw.last("POST", "compute/cloud-vms/7a1b2c3d4e5f60718293a4b5/actions/attach-volume")
    assert call.json["volume_id"] == VOL
    assert call.json["billing_catalog"]["sku_code"] == "BLOCKSTO-STD"
    assert call.headers["x-idempotency-key"].startswith("cli-attach-volume-")
    assert "ibee ops wait op-1" in plain(result)


def test_attach_vm_uses_gpu_endpoint_for_gpu_volumes(gw):
    gw.on("GET", V, volume(vm_type="gpu"))
    gw.on("GET", "compute/gpu-vms/8a1b2c3d4e5f60718293a4b6", {"_id": "8a1b2c3d4e5f60718293a4b6", "status": "stopped", "site_id": "site-1"})
    gw.on("POST", "compute/gpu-vms/8a1b2c3d4e5f60718293a4b6/actions/attach-volume", accepted(vm="8a1b2c3d4e5f60718293a4b6"))
    result = run(["block-storage", "attach-vm", VOL, "8a1b2c3d4e5f60718293a4b6"])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", "compute/gpu-vms/8a1b2c3d4e5f60718293a4b6/actions/attach-volume")


@pytest.mark.parametrize(
    ("record", "vm", "extra", "expected"),
    [
        (attached(), None, [], "already attached"),
        (volume(), None, ["--vm-type", "gpu"], "created for cloud VMs and cannot attach to a gpu VM"),
        (volume(), {"_id": "7a1b2c3d4e5f60718293a4b5", "status": "running", "site_id": "site-9"}, [], "Select a server in Bengaluru"),
    ],
)
def test_attach_vm_guards(gw, record, vm, extra, expected):
    gw.on("GET", V, record)
    gw.on("GET", "compute/cloud-vms/7a1b2c3d4e5f60718293a4b5", vm or {"_id": "7a1b2c3d4e5f60718293a4b5", "status": "running", "site_id": "site-1"})
    result = run(["block-storage", "attach-vm", VOL, "7a1b2c3d4e5f60718293a4b5", *extra])
    assert result.exit_code == 2, result.output
    assert expected in plain(result)
    assert gw.writes() == []


def test_attach_vm_without_read_scope_needs_catalog(gw):
    gw.on("GET", V, (403, {"error": "insufficient_scope", "required_scope": "block-storage.read"}))
    result = run(["block-storage", "attach-vm", VOL, "7a1b2c3d4e5f60718293a4b5"])
    assert result.exit_code == 2
    assert "block-storage.read" in plain(result)
    assert gw.writes() == []


def test_attach_vm_wait_polls_every_two_seconds_and_rereads_volume(gw, sleeps):
    gw.on("GET", V, volume(), attached())
    gw.on("GET", "compute/cloud-vms/7a1b2c3d4e5f60718293a4b5", {"_id": "7a1b2c3d4e5f60718293a4b5", "status": "running", "site_id": "site-1"})
    gw.on("POST", "compute/cloud-vms/7a1b2c3d4e5f60718293a4b5/actions/attach-volume", accepted())
    gw.on("GET", "compute/operations/op-1", op_status("running"), op_status("succeeded"))
    result = run(["block-storage", "attach-vm", VOL, "7a1b2c3d4e5f60718293a4b5", "--wait"])
    assert result.exit_code == 0, result.output
    assert len(gw.requests("GET")) == 5
    assert sleeps == [2.0]
    output = plain(result)
    assert "Volume attach completed for volume" in output and "op-1" in output


def test_attach_vm_wait_failure_exits_1(gw):
    gw.on("GET", V, volume())
    gw.on("GET", "compute/cloud-vms/7a1b2c3d4e5f60718293a4b5", {"_id": "7a1b2c3d4e5f60718293a4b5", "status": "running", "site_id": "site-1"})
    gw.on("POST", "compute/cloud-vms/7a1b2c3d4e5f60718293a4b5/actions/attach-volume", accepted())
    gw.on("GET", "compute/operations/op-1", op_status("failed"))
    result = run(["block-storage", "attach-vm", VOL, "7a1b2c3d4e5f60718293a4b5", "--wait"])
    assert result.exit_code == 1
    assert "disk busy" in plain(result)


def test_attach_vm_timeout_without_wait_exits_2(gw):
    result = run(["block-storage", "attach-vm", VOL, "7a1b2c3d4e5f60718293a4b5", "--timeout", "30"])
    assert result.exit_code == 2
    assert "--timeout and --poll-interval require --wait" in plain(result)


def test_detach_vm_requires_unmount_confirmation_before_anything(gw):
    result = run(["block-storage", "detach-vm", VOL, "--yes"])
    assert result.exit_code == 2
    assert "Unmount the volume inside the server" in plain(result)
    assert gw.calls == []


def test_detach_vm_resolves_vm_from_attachment(gw):
    gw.on("GET", V, attached())
    gw.on("POST", "compute/cloud-vms/7a1b2c3d4e5f60718293a4b5/actions/detach-volume", accepted())
    result = run(["block-storage", "detach-vm", VOL, "--confirm-unmounted", "--yes"])
    assert result.exit_code == 0, result.output
    call = gw.last("POST", "compute/cloud-vms/7a1b2c3d4e5f60718293a4b5/actions/detach-volume")
    assert call.json["volume_id"] == VOL
    assert call.json["confirm_unmounted"] is True and call.json["force"] is False
    assert call.headers["x-idempotency-key"].startswith("cli-detach-volume-")


def test_detach_vm_errors_when_not_attached(gw):
    gw.on("GET", V, volume())
    result = run(["block-storage", "detach-vm", VOL, "--confirm-unmounted", "--yes"])
    assert result.exit_code == 2
    assert "Volume is not attached to any server" in plain(result)


def test_detach_vm_force_asks_again(gw):
    gw.on("GET", V, attached())
    result = run(["block-storage", "detach-vm", VOL, "7a1b2c3d4e5f60718293a4b5", "--force"], input="y\nn\n")
    assert result.exit_code == 1
    assert "skips the unmount safety check" in plain(result)
    assert "from VM 'web' (7a1b2c3d4e5f60718293a4b5)" in plain(result)
    assert gw.writes() == []


def test_detach_vm_wait_json_prints_operation_and_volume(gw):
    # One read resolves the attachment before the prompt, one inside the SDK, then the poll.
    gw.on("GET", V, attached(), attached(), volume())
    gw.on("POST", "compute/cloud-vms/7a1b2c3d4e5f60718293a4b5/actions/detach-volume", accepted())
    gw.on("GET", "compute/operations/op-1", op_status("succeeded"))
    result = run(["--json", "block-storage", "detach-vm", VOL, "7a1b2c3d4e5f60718293a4b5", "--confirm-unmounted", "--yes", "--wait"])
    assert result.exit_code == 0, result.output
    assert '"operation"' in result.output and '"volume"' in result.output


# ---------------------------------------------------------------------------
# Node-level attach / detach (advanced)
# ---------------------------------------------------------------------------


def test_node_attach_is_documented_as_advanced_and_validated(gw):
    help_text = plain(net.runner.invoke(net.app, ["block-storage", "attach", "--help"]))
    assert "does not attach the disk to a VM" in help_text
    result = run(["block-storage", "attach", VOL, "--node-name", "node-a", "--mode", "shared"])
    assert result.exit_code == 2
    assert gw.calls == []
    gw.on("GET", V, volume())
    result = run(["block-storage", "attach", VOL, "--node-name", "node-a", "--vm-site-id", "site-2"])
    assert result.exit_code == 2
    assert "Select a server in Bengaluru" in plain(result)


def test_node_attach_request(gw):
    gw.on("POST", f"{V}/attachments", {"volume": volume(), "operation": {}})
    result = run(["block-storage", "attach", VOL, "--node-name", "node-a", "--vm-id", "7a1b2c3d4e5f60718293a4b5", "--vm-state", "running"])
    assert result.exit_code == 0, result.output
    body = gw.last("POST", f"{V}/attachments").json
    assert body["node_name"] == "node-a" and body["mode"] == "single-writer" and body["vm_type"] == "cloud"
    assert body["idempotency_key"].startswith("cli-block-attach-")


def test_node_detach_requires_safe_detach(gw):
    result = run(["block-storage", "detach", VOL, "--node-name", "node-a", "--yes"])
    assert result.exit_code == 2
    assert "Safe detach requires VM state or explicit unmount confirmation." in plain(result)
    assert gw.calls == []


def test_node_detach_resolves_node_and_vm_type(gw):
    gw.on("GET", V, attached(vm_type="gpu"))
    gw.on("POST", f"{V}/detach", {"volume": volume(), "operation": {}})
    result = run(["block-storage", "detach", VOL, "--vm-state", "stopped", "--yes"])
    assert result.exit_code == 0, result.output
    body = gw.last("POST", f"{V}/detach").json
    assert body["node_name"] == "node-a" and body["vm_type"] == "gpu" and body["vm_state"] == "stopped"


# ---------------------------------------------------------------------------
# CDN
# ---------------------------------------------------------------------------

D = "cdn/distributions/dist-1"


def distribution(**overrides):
    record = {"id": "dist-1", "name": "assets", "origin_type": "bucket", "origin_id": "0123456789abcdef0123456789abcdef",
              "bucket_name": "site-assets", "cache_policy": "static-assets", "status": "active",
              "default_url": "https://dl-x.cdn.example"}
    record.update(overrides)
    return record


def test_cdn_list_table_uses_bucket_name_or_short_origin(gw):
    gw.on("GET", "cdn/distributions", {"distributions": [distribution(), distribution(id="d2", bucket_name=None)],
                                       "count": 2})
    result = run(["-o", "table", "cdn", "list"])
    assert result.exit_code == 0, result.output
    output = plain(result)
    assert "site-assets" in output and "012345…cdef" in output


def test_cdn_create_checks_origin_bucket_is_public(gw):
    gw.on("GET", "object-storage/buckets/site-assets",
          {"name": "site-assets", "region": "r", "is_public": False, "bucket_lock_enabled": False})
    result = run(["cdn", "create", "assets", "--origin-id", "site-assets"])
    assert result.exit_code == 2
    assert "Only public buckets can be used as CDN origins" in plain(result)
    assert gw.writes() == []


def test_cdn_create_request(gw):
    gw.on("GET", "object-storage/buckets/site-assets",
          {"name": "site-assets", "region": "r", "is_public": True, "bucket_lock_enabled": False})
    gw.on("POST", "cdn/distributions", distribution())
    result = run(["cdn", "create", " assets ", "--origin-id", "site-assets", "--cache-policy", "media"])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", "cdn/distributions").json == {
        "name": "assets", "origin_type": "bucket", "origin_id": "site-assets", "cache_policy": "media"}


def test_cdn_create_no_check_origin_and_unknown_bucket_id(gw):
    gw.on("POST", "cdn/distributions", distribution())
    assert run(["cdn", "create", "assets", "--origin-id", "b1", "--no-check-origin"]).exit_code == 0
    assert gw.requests("GET") == []
    gw.on("GET", "object-storage/buckets/0123", (404, {"detail": "Bucket not found"}))
    assert run(["cdn", "create", "assets", "--origin-id", "0123"]).exit_code == 0


@pytest.mark.parametrize(
    "args",
    [
        ["create", "assets", "--origin-id", "b", "--cache-policy", "public-development"],
        ["create", "x" * 129, "--origin-id", "b"],
        ["create", "assets", "--origin-id", " "],
        ["create", "assets", "--origin-id", "b", "--origin-type", "s3"],
        ["update", "dist-1", "--cache-policy", "forever"],
        ["generate-url", "b", "k", "--disposition", "download"],
        ["generate-url", "b", "k", "--expires-in", "0"],
        ["metrics", "dist-1", "--range", "1h"],
        ["website", "set", "dist-1", "--index-document", "/index.html"],
        ["website", "set", "dist-1", "--index-document", "a/../b.html"],
        ["domains", "create", "dist-1", "example"],
        ["domains", "create", "dist-1", "bad_domain.example.com"],
    ],
)
def test_cdn_validation_runs_before_any_request(gw, args):
    result = run(["cdn", *args])
    assert result.exit_code == 2, result.output
    assert gw.calls == []


def test_cdn_update_requires_an_option(gw):
    result = run(["cdn", "update", "dist-1"])
    assert result.exit_code == 2
    assert "Provide at least one update option" in plain(result)


def test_cdn_routes_are_url_encoded(gw):
    gw.on("GET", "cdn/distributions/dist/one", distribution())
    result = run(["cdn", "get", "dist/one"])
    assert result.exit_code == 0, result.output
    assert gw.calls[0].raw_path.startswith("/v1/cdn/distributions/dist%2Fone")


def test_cdn_delete_confirmation_mentions_side_effects(gw):
    result = run(["cdn", "delete", "dist-1"], input="n\n")
    assert result.exit_code == 1
    assert "removes its DNS record, all custom domains and purges its cache" in plain(result)
    gw.on("DELETE", D, {"message": "deleted"})
    assert run(["cdn", "delete", "dist-1", "--yes"]).exit_code == 0


def test_cdn_cache_policies_and_metrics(gw):
    gw.on("GET", "cdn/distributions/cache-policies", {"policies": [{"id": "media"}]})
    gw.on("GET", f"{D}/metrics", {"traffic": {}})
    assert run(["cdn", "cache-policies"]).exit_code == 0
    result = run(["cdn", "metrics", "dist-1", "--range", "7d"])
    assert result.exit_code == 0, result.output
    assert gw.last("GET", f"{D}/metrics").params["range"] == "7d"
    help_text = plain(net.runner.invoke(net.app, ["cdn", "metrics", "--help"]))
    assert "Not yet part of the published API contract" in help_text


def test_cdn_generate_url_request(gw):
    gw.on("POST", "cdn/generate-url", {"cdn_url": "https://x", "expires_at": None})
    result = run(["cdn", "generate-url", "assets", "images/logo.png", "--expires-in", "60", "--disposition", "inline"])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", "cdn/generate-url").json == {
        "bucket_name": "assets", "object_key": "images/logo.png", "expires_in": 60, "disposition": "inline"}


def test_website_get_missing_config_means_disabled(gw):
    gw.on("GET", f"{D}/website-config", (404, {"detail": "Website config not found"}))
    gw.on("GET", D, distribution())
    result = run(["cdn", "website", "get", "dist-1"])
    assert result.exit_code == 0, result.output
    assert "Website hosting is not configured (disabled)." in plain(result)


def test_website_get_missing_distribution_is_404(gw):
    gw.on("GET", f"{D}/website-config", (404, {"detail": "not found"}))
    gw.on("GET", D, (404, {"detail": "Distribution not found"}))
    result = run(["cdn", "website", "get", "dist-1"])
    assert result.exit_code == 1
    assert "Not found (404)" in plain(result)


def test_website_set_and_delete(gw):
    gw.on("PUT", f"{D}/website-config", {"enabled": True, "index_document": "home.html"})
    gw.on("DELETE", f"{D}/website-config", {"enabled": False})
    assert run(["cdn", "website", "set", "dist-1", "--index-document", " home.html "]).exit_code == 0
    assert gw.last("PUT", f"{D}/website-config").json == {"index_document": "home.html"}
    assert run(["cdn", "website", "delete", "dist-1", "--yes"]).exit_code == 0


def test_domain_create_normalises_and_prints_cname(gw):
    gw.on("POST", f"{D}/custom-domains", {
        "domain": "cdn.example.com", "status": "pending_validation",
        "validation": {"cname_record": {"type": "CNAME", "name": "cdn.example.com", "value": "dl-x.cdn.example"}},
        "instructions": ["Create the CNAME record at your DNS provider."],
    })
    result = run(["cdn", "domains", "create", "dist-1", " CDN.Example.com "])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", f"{D}/custom-domains").json == {"domain": "cdn.example.com"}
    output = plain(result)
    assert "CNAME cdn.example.com -> dl-x.cdn.example" in output
    assert "ibee cdn domains verify dist-1 cdn.example.com --wait" in output


def test_domain_create_check_billing_uses_custom_domain_sku(gw):
    gw.on("POST", "billing/resource-eligibility", {"allowed": True, "reason": "ok", "organization_id": "org-1", "sku_code": "CUSTOMDO-STD"})
    gw.on("POST", f"{D}/custom-domains", {"domain": "cdn.example.com", "status": "pending_validation"})
    result = run(["--check-billing", "cdn", "domains", "create", "dist-1", "cdn.example.com"])
    assert result.exit_code == 0, result.output
    assert not any(c.path == "billing/resource-eligibility" for c in gw.calls)


def test_domain_path_is_normalised(gw):
    gw.on("GET", f"{D}/custom-domains/cdn.example.com", {"domain": "cdn.example.com"})
    assert run(["cdn", "domains", "get", "dist-1", " CDN.example.com "]).exit_code == 0


def test_domain_delete_confirmation(gw):
    result = run(["cdn", "domains", "delete", "dist-1", "cdn.example.com"], input="n\n")
    assert result.exit_code == 1
    assert "Also delete its CNAME record at your DNS provider" in plain(result)


def test_domain_verify_pending_prints_portal_message(gw):
    gw.on("POST", f"{D}/custom-domains/cdn.example.com/verify", {"domain": "cdn.example.com", "status": "pending_tls"})
    result = run(["cdn", "domains", "verify", "dist-1", "cdn.example.com"])
    assert result.exit_code == 0, result.output
    assert "DNS records not yet propagated" in plain(result)


def test_domain_verify_wait_until_active(gw, monkeypatch):
    from ibee import operations

    monkeypatch.setattr(operations, "_clock", iter(range(0, 10000, 5)).__next__, raising=False)
    gw.on("POST", f"{D}/custom-domains/cdn.example.com/verify",
          {"status": "pending_validation"}, {"status": "active"})
    result = run(["cdn", "domains", "verify", "dist-1", "cdn.example.com", "--wait"])
    assert result.exit_code == 0, result.output
    assert len(gw.calls) == 2
    assert "Custom domain is active" in plain(result)


def test_domain_verify_wait_failed_exits_1(gw):
    gw.on("POST", f"{D}/custom-domains/cdn.example.com/verify", {"status": "failed", "message": "CAA blocks"})
    result = run(["cdn", "domains", "verify", "dist-1", "cdn.example.com", "--wait"])
    assert result.exit_code == 1
    assert "CAA blocks" in plain(result)


def test_domain_verify_wait_timeout_exits_3(gw, monkeypatch):
    from ibee import operations

    monkeypatch.setattr(operations, "_clock", iter(range(0, 10000, 30)).__next__, raising=False)
    gw.on("POST", f"{D}/custom-domains/cdn.example.com/verify", {"status": "pending_validation"})
    result = run(["cdn", "domains", "verify", "dist-1", "cdn.example.com", "--wait", "--timeout", "60"])
    assert result.exit_code == 3, result.output
    assert "check again with: ibee cdn domains verify dist-1 cdn.example.com --wait" in plain(result)


def test_purge_url_normalises_paths(gw):
    gw.on("POST", f"{D}/purge", {"success": True, "mode": "url", "purged": ["/a.css"]})
    result = run(["cdn", "purge", "dist-1", "--mode", "url", "--path", "a.css,/b.js", "--path", "https://cdn.example.com/c"])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", f"{D}/purge").json == {
        "mode": "url", "paths": ["/a.css", "/b.js", "https://cdn.example.com/c"]}


def test_purge_hostnames_are_lowercased(gw):
    gw.on("POST", f"{D}/purge", {"success": True, "mode": "hostname"})
    assert run(["cdn", "purge", "dist-1", "--mode", "hostname", "--hostname", "CDN.Example.com"]).exit_code == 0
    assert gw.last("POST", f"{D}/purge").json == {"mode": "hostname", "hostnames": ["cdn.example.com"]}


def test_purge_mode_is_required(gw):
    result = run(["cdn", "purge", "dist-1"])
    assert result.exit_code == 2
    assert "--mode" in plain(result)
    assert gw.calls == []


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--mode", "url"], "requires at least one --path"),
        (["--mode", "url", "--path", "http://cdn.example.com/a"], "https://"),
        (["--mode", "prefix", "--prefix", "/img?x=1"], "query string"),
        (["--mode", "tag", "--tag", "t", "--path", "/a"], "cannot be used with mode 'tag'"),
        (["--mode", "all", "--tag", "t"], "cannot be used with mode 'all'"),
        (["--mode", "everything"], "mode"),
    ],
)
def test_purge_selector_validation_prevents_request(gw, args, expected):
    result = run(["cdn", "purge", "dist-1", *args, "--yes"])
    assert result.exit_code == 2
    assert expected in plain(result)
    assert gw.calls == []


def test_purge_all_requires_confirmation(gw):
    result = run(["cdn", "purge", "dist-1", "--mode", "all"], input="n\n")
    assert result.exit_code == 1
    assert "purge all cached data for every hostname" in plain(result)
    assert gw.calls == []
    gw.on("POST", f"{D}/purge", {"success": True, "mode": "all"})
    assert run(["cdn", "purge", "dist-1", "--mode", "all", "--yes"]).exit_code == 0
    assert gw.last("POST", f"{D}/purge").json == {"mode": "all"}


def test_purge_success_false_exits_1(gw):
    gw.on("POST", f"{D}/purge", {"success": False, "mode": "prefix", "message": "Cache purge failed"})
    result = run(["cdn", "purge", "dist-1", "--mode", "prefix", "--prefix", "/img/"])
    assert result.exit_code == 1
    output = plain(result)
    assert "Cache purge failed (mode prefix)" in output
    assert "Nothing was purged" in output


def test_cdn_create_check_billing_denial_stops_before_create(gw):
    gw.on("GET", "object-storage/buckets/b1", {"name": "b1", "region": "r", "is_public": True})
    gw.on("POST", "cdn/distributions", (402, {"error": "billing_denied", "billing_reason": "insufficient_balance"}))
    result = run(["--check-billing", "cdn", "create", "site-cdn", "--origin-id", "b1"])
    assert result.exit_code == 1
    assert any(c.path == "cdn/distributions" for c in gw.calls)
    assert not any(c.path == "billing/resource-eligibility" for c in gw.calls)


def test_cdn_edge_billing_denial_prints_portal_copy(gw):
    gw.on("POST", "cdn/distributions", (402, {
        "error": "billing_denied", "billing_reason": "insufficient_balance",
        "billing_sku_code": "CDN-1", "admission_context_id": "adm-9"}))
    result = run(["cdn", "create", "site-cdn", "--origin-id", "b1", "--no-check-origin"])
    assert result.exit_code == 1
    output = plain(result)
    assert "wallet balance does not cover this CDN distribution" in output
    assert "admission_context_id=adm-9" in output


@pytest.mark.parametrize("group", ["block-storage", "cdn"])
def test_groups_preserve_workspace_validation(group):
    result = net.runner.invoke(net.app, ["--token", "test-token", "--workspace", "not-a-workspace", group, "list"])
    assert result.exit_code == 2
    assert "workspace_id must be a positive numeric string" in result.output
