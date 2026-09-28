"""Review fixes for 0.4.0 (SDK mocked or scripted gateway; no network)."""

from __future__ import annotations

import json

import pytest
from ibee.core.api_error import ApiError
from ibee.errors import error_from_response

import _net_fixtures as net
from _net_fixtures import FW, RIP, V, WS, gateway_record, group, plain, rip, run, summary, vip, vpc
from ibee_cli import helpers, render
from ibee_cli.context import CliApiError, Settings
from test_vm_parity_commands import CREATE, VM_ID, invoke, names, sdk  # noqa: F401 (fixture)

VOL = "64f1c2a9b8e7d6c5b4a39281"
BV = f"block-storage/volumes/{VOL}"
SEC = "secret-store/secrets"
SCOPE_403 = lambda scope: (403, {"error": "insufficient_scope", "required_scope": scope})  # noqa: E731


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    import time

    monkeypatch.setattr(helpers, "_sleep", lambda _s: None)
    monkeypatch.setattr(time, "sleep", lambda _s: None)


# ---------------------------------------------------------------------------
# Waiting, output and compatibility
# ---------------------------------------------------------------------------


def test_wait_poll_failure_prints_operation_and_resume_hint(sdk):
    sdk.returns["start_cloud_vm"] = {"operation_id": "op-123"}
    sdk.returns["get_compute_operation"] = error_from_response(503, {"error": "service_unavailable", "message": "down"})
    result = invoke(["vms", "start", VM_ID, "--wait"])
    assert result.exit_code == 1
    output = plain(result)
    assert "operation op-123" in output
    assert "ibee ops wait op-123" in output
    assert names(sdk).count("get_compute_operation") == 3


def test_batch_wait_poll_failure_names_the_operation(sdk):
    sdk.returns["get_compute_operation"] = error_from_response(503, {"error": "service_unavailable", "message": "down"})
    result = invoke([*CREATE, "--count", "2", "--wait"])
    assert result.exit_code == 1
    assert "ibee ops wait op-create_cloud_vm" in plain(result)


def test_yaml_emitter_quotes_ambiguous_strings():
    data = {"created_at": "2026-09-28T10:00:00Z", "t": "12:30", "x": ".inf", "h": "0x1A", "d": "2026-09-28",
            "a": "@foo", "sp": "abc ", "ok": "web-1", "path": "a/b.c", "none": None, "b": True}
    lines = render._yaml_lines(data, 0)
    assert lines == [
        'created_at: "2026-09-28T10:00:00Z"',
        't: "12:30"',
        'x: ".inf"',
        'h: "0x1A"',
        'd: "2026-09-28"',
        'a: "@foo"',
        'sp: "abc "',
        "ok: web-1",
        "path: a/b.c",
        "none: null",
        "b: true",
    ]


def test_firewalls_list_json_keeps_iso_timestamps(gw):
    gw.on("GET", FW, [group()])
    result = run(["--json", "firewalls", "list"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data[0]["created_at"] == net.TS
    assert "+00:00" not in result.output


def test_settings_and_cli_api_error_accept_the_030_forms():
    settings = Settings("t", WS, False, None, True)
    assert settings.output == "json" and settings.as_json is True
    assert Settings("t", WS, False, None, as_json=True).output == "json"
    assert Settings("t", WS, False, None, False).output is None
    error = CliApiError(404, {"detail": "missing"})
    assert isinstance(error, ApiError) and error.status_code == 404 and error.body == {"detail": "missing"}


def test_ops_get_honours_table_output(sdk, monkeypatch):
    from ibee_cli.commands import ops, vm_lifecycle

    monkeypatch.setattr(ops, "get_client", vm_lifecycle.get_client)  # the sdk fixture's fake client
    op_id = "op_" + "a" * 24
    result = invoke(["-o", "table", "ops", "get", op_id])
    assert result.exit_code == 0, result.output
    assert "┏" in result.output and "succeeded" in result.output
    assert not result.output.lstrip().startswith("{")
    result = invoke(["ops", "get", op_id])
    assert json.loads(result.output)["status"] == "succeeded"


def test_console_create_hides_the_connect_url_unless_asked(sdk):
    session = {"session_id": "cs-1", "connect_url": "https://console.example/?token=secret", "status": "active"}
    sdk.returns["create_vm_console_session"] = session
    result = invoke(["console", "create", VM_ID])
    assert result.exit_code == 0, result.output
    assert "token=secret" not in result.output
    assert "--show-url" in result.output
    for extra in (["console", "create", VM_ID, "--show-url"], ["--json", "console", "create", VM_ID]):
        result = invoke(extra)
        assert result.exit_code == 0, result.output
        assert "token=secret" in result.output


# ---------------------------------------------------------------------------
# VMs
# ---------------------------------------------------------------------------


def test_delete_without_state_check_still_asks_about_the_public_ip(sdk):
    sdk.returns["get_cloud_vm"] = {"_id": VM_ID, "name": "web", "status": "deleting", "public_ip": "203.0.113.9"}
    result = invoke(["vms", "delete", VM_ID, "--no-check-state"], input="y\ny\n")
    # The state rule is skipped; the reserve question is asked (and needs the SKU).
    assert "Reserve public IP 203.0.113.9" in plain(result)
    assert result.exit_code == 2
    assert "delete_cloud_vm" not in names(sdk)


def test_backup_policy_update_without_state_check_needs_the_full_schedule(sdk):
    result = invoke(["vms", "backup-policy", "update", VM_ID, "--hour", "5", "--no-check-state"])
    assert result.exit_code == 2
    assert "pass the full schedule" in plain(result)
    assert sdk.calls == []
    full = ["--frequency", "weekly", "--hour", "5", "--minute", "0", "--timezone", "UTC", "--window-minutes", "30"]
    assert invoke(["vms", "backup-policy", "update", VM_ID, *full, "--no-check-state"]).exit_code == 2
    result = invoke(["vms", "backup-policy", "update", VM_ID, *full, "--day-of-week", "6", "--no-check-state"])
    assert result.exit_code == 0, result.output
    assert invoke(["vms", "backup-policy", "update", VM_ID, "--retention-days", "9", "--no-check-state"]).exit_code == 0


def test_more_instance_names_than_count_is_a_usage_error(sdk):
    result = invoke([*CREATE, "--count", "2", "--instance-name", "a", "--instance-name", "b",
                     "--instance-name", "c"])
    assert result.exit_code == 2
    assert "given 3 times but --count is 2" in plain(result)
    assert sdk.calls == []


def test_ssh_key_secret_ref_needs_either_identifier(sdk):
    result = invoke(["vms", "access-update", VM_ID, "--ssh-key-mode", "add",
                     "--ssh-key-secret-ref", json.dumps({"secret_name": "deploy"})])
    assert result.exit_code == 0, result.output
    result = invoke(["vms", "access-update", VM_ID, "--ssh-key-mode", "add",
                     "--ssh-key-secret-ref", json.dumps({"store_key": "ssh-keys"})])
    assert result.exit_code == 2


@pytest.mark.parametrize("command", [["volume-attach", VM_ID, "vol-1"],
                                     ["volume-detach", VM_ID, "vol-1", "--confirm-unmounted", "--yes"]])
def test_vm_volume_wait_uses_the_portal_cadence(sdk, monkeypatch, command):
    seen = []
    monkeypatch.setattr(
        "ibee_cli.commands.vm_lifecycle.finish_operation",
        lambda *args, **kwargs: seen.append(args[6]),
    )
    assert invoke(["vms", *command, "--wait"]).exit_code == 0
    assert (seen[-1].timeout, seen[-1].poll_interval) == (120.0, 2.0)
    assert invoke(["vms", *command, "--wait", "--timeout", "600", "--poll-interval", "10"]).exit_code == 0
    assert (seen[-1].timeout, seen[-1].poll_interval) == (600.0, 10.0)


# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------


def test_vpc_delete_exits_1_while_the_nat_gateway_reconciles(gw):
    # The SDK raises a plain IbeeError (nat_gateway_deleting): server state, so exit 1 with the hint.
    gw.on("GET", V, vpc(nat_gateways=[gateway_record()]))
    gw.on("GET", f"{V}/virtual-ips", [])
    gw.on("GET", f"{V}/nat-gateways", [gateway_record()])
    gw.on("DELETE", f"{V}/nat-gateways/nat-1", gateway_record(status="deleting"))
    result = run(["vpcs", "delete", "vpc-1", "--delete-nat-gateway", "--nat-ip-action", "release", "--yes"])
    assert result.exit_code == 1, result.output
    assert "still reconciling" in plain(result)
    assert "Retry 'ibee vpcs delete vpc-1' shortly (check with: ibee vpcs nat list vpc-1)." in plain(result)
    assert ("DELETE", V) not in [(c.method, c.path) for c in gw.calls]


def test_firewall_summary_page_of_exactly_100_checks_for_more(gw):
    gw.on("GET", FW, [summary(i) for i in range(100)], [])
    result = run(["firewalls", "list", "--summary", "--limit", "100"])
    assert result.exit_code == 0, result.output
    assert "More results" not in plain(result)
    assert gw.calls[-1].params["offset"] == "100" and gw.calls[-1].params["limit"] == "1"
    gw.routes[("GET", FW)] = [[summary(i) for i in range(100)], [summary(100)]]
    assert "More results: use --offset 100" in plain(run(["firewalls", "list", "--summary", "--limit", "100"]))


def test_reserved_ip_convert_without_billing_scope_points_at_the_flag(gw):
    gw.on("POST", "billing/resource-eligibility", SCOPE_403("billing.read"))
    result = run(["reserved-ips", "convert", "--vm-id", "vm-1", "--site-id", "site-1"])
    assert result.exit_code == 1
    assert "--no-billing-check" in plain(result)
    assert all(c.path != "networking/reserved-ips/convert" for c in gw.calls)


@pytest.mark.parametrize(
    "record,text",
    [(rip(site_id="site-2"), "same site"), (rip(status="releasing"), "not available")],
)
def test_virtual_ip_attach_checks_reserved_ip_site_and_status(gw, record, text):
    gw.on("GET", f"{V}/virtual-ips", [vip()])
    gw.on("GET", V, vpc(nat_gateways=[gateway_record()]))
    gw.on("GET", RIP, record)
    result = run(["vpcs", "virtual-ips", "attach-ip", "vpc-1", "pvip-1", "--reserved-ip-id", "rip-1"])
    assert result.exit_code == 2, result.output
    assert text in plain(result)
    assert gw.writes() == []


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def test_s3_credential_id_output_keeps_the_secret(gw):
    from test_object_storage_commands import credential

    gw.on("POST", "object-storage/credentials",
          {**credential(), "access_key_id": "AKID", "secret_access_key": "s3cr3t", "organization_id": "org",
           "workspace_id": WS})
    result = run(["-o", "id", "buckets", "credentials", "create"])
    assert result.exit_code == 0, result.output
    assert "s3cr3t" in result.output and "AKID" in result.output


def test_cdn_create_skips_origin_check_without_bucket_read_scope(gw):
    gw.on("GET", "object-storage/buckets/site-assets", SCOPE_403("object-storage.read"))
    gw.on("POST", "cdn/distributions", {"id": "d1", "name": "assets"})
    result = run(["cdn", "create", "assets", "--origin-id", "site-assets"])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", "cdn/distributions").json["origin_id"] == "site-assets"


def test_block_storage_create_validates_before_billing_preflight(gw):
    result = run(["--check-billing", "block-storage", "create", "Bad_Name", "--size-gb", "5", "--site-id", "s1"])
    assert result.exit_code == 2
    assert gw.calls == []


@pytest.mark.parametrize(
    "response,text",
    [
        ((400, {"detail": "size_gb is not available for the selected plan"}), "pass --sku-code"),
        ((502, {"error": "ambiguous_block_storage_plan", "message": "several plans"}), "Contact support"),
    ],
)
def test_block_storage_create_plan_error_guidance(gw, response, text):
    gw.on("POST", "block-storage/volumes", response)
    result = run(["block-storage", "create", "data", "--size-gb", "20", "--site-id", "site-1", "--no-check-site"])
    assert result.exit_code == 1
    assert text in plain(result)


def _volume(**overrides):
    record = {"id": VOL, "name": "data", "size_gb": 100, "state": "ready", "site_id": "site-1",
              "vm_type": "cloud", "attachments": []}
    record.update(overrides)
    return record


@pytest.mark.parametrize(
    "command",
    [["block-storage", "detach-vm", VOL, "--confirm-unmounted"],
     ["block-storage", "detach", VOL, "--confirm-unmounted"]],
)
def test_detach_of_unattached_volume_fails_before_the_prompt(gw, command):
    gw.on("GET", BV, _volume())
    result = run(command, input="y\n")
    assert result.exit_code == 2
    output = plain(result)
    assert "not attached" in output
    assert "[y/N]" not in output
    assert gw.writes() == []


def test_node_detach_prompt_names_the_node(gw):
    gw.on("GET", BV, _volume(vm_type="gpu", attachments=[{"node_name": "node-a"}]))
    gw.on("POST", f"{BV}/detach", {"id": VOL})
    result = run(["block-storage", "detach", VOL, "--confirm-unmounted"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "from node 'node-a'" in plain(result)
    assert gw.last("POST", f"{BV}/detach").json["vm_type"] == "gpu"


# ---------------------------------------------------------------------------
# Secret Store
# ---------------------------------------------------------------------------


def test_undelete_without_version_read_asks_for_versions(gw):
    gw.on("GET", f"{SEC}/sec-1/versions", SCOPE_403("secret-store.read"))
    result = run(["secrets", "undelete", "sec-1"])
    assert result.exit_code == 2
    assert "Pass --versions N" in plain(result)
    assert gw.writes() == []


STATUS = {"id": "sec-1", "status": "deleted"}


@pytest.mark.parametrize(
    "method,path,command,payload",
    [
        ("DELETE", f"{SEC}/sec-1/permanent", ["secrets", "delete-permanent", "sec-1", "--yes", "--no-check-state"], STATUS),
        ("POST", f"{SEC}/sec-1/destroy", ["secrets", "destroy-versions", "sec-1", "--versions", "1", "--yes"], STATUS),
        ("DELETE", "secret-store/identities/idn-1", ["secrets", "identities", "delete", "idn-1", "--yes"], {"status": "deleted"}),
        ("DELETE", "secret-store/scopes/scp-1", ["secrets", "identities", "scopes", "delete", "scp-1", "--yes"],
         {"status": "deleted"}),
    ],
)
def test_secret_deletes_print_json_results(gw, method, path, command, payload):
    gw.on(method, path, payload)
    result = run(["-o", "json", *command])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["status"] == "deleted"


def test_store_page_hint_only_when_more_rows_follow(gw):
    stores = {"stores": [{"id": "store-1", "organization_id": "o", "workspace_id": WS, "name": "p", "store_key": "p",
                          "description": "", "status": "active", "created_at": net.TS, "updated_at": net.TS}],
              "total": 101, "page": 2, "limit": 100}
    gw.on("GET", "secret-store/stores", stores)
    assert "Showing" not in plain(run(["secrets", "stores", "list", "--page", "2"]))
    gw.routes[("GET", "secret-store/stores")] = [dict(stores, stores=stores["stores"] * 2, page=1)]
    assert "use --page 2 or --all" in plain(run(["secrets", "stores", "list"]))
