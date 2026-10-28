"""What the commands do around their one SDK call: the VM billing_catalog convenience,
JSON options, confirmations, output modes and downloads."""

from __future__ import annotations

import json

import pytest
import yaml
from _net_fixtures import TS, WS, plain, run

HOURLY = {"billing_interval": "HOURLY", "committed": False, "commitment_period": "HOURLY", "unit_price_minor": 82}
MONTHLY = {
    "billing_interval": "MONTHLY", "committed": True, "commitment_period": "MONTHLY", "commitment_months": 1,
    "committed_hours": 731, "unit_price_minor": 66, "price_unit": "HOUR", "discount_percent": 19.51,
}
CATALOG = {
    "source": "billing_v2", "product_id": "p_rp274o", "sku_id": "sku-1", "sku_code": "PERFORMA-1-2-50",
    "plan_id": "plan_default", "plan_version": 117, "price_currency": "INR", "hourly_price_minor": 82,
    "billing_options": [HOURLY, MONTHLY],
    "attached_skus": {"bandwidth": {"sku_id": "sku-bw", "sku_code": "BW-1", "billing_options": [HOURLY]}},
}
ACCEPTED = {"operation_id": "op-1", "vm_id": "vm-1", "status": "accepted", "submitted_at": TS}
CREATE = ["--name", "web-1", "--site-id", "site-1", "--os-distro", "ubuntu", "--os-type", "linux",
          "--template-id", "tmpl-1", "--cpu", "1", "--ram-mb", "2048", "--plan-id", "plan-1"]


def plan(plan_id: str = "plan-1", vm_type: str = "cloud", **extra) -> dict:
    return {"plan_id": plan_id, "vm_type": vm_type, "name": "perf.1.c1.m2", "code": "PERFORMA-1-2-50", "cpu": 1,
            "ram_mb": 2048, "disk_gb": 50, "gpu_count": 0, "selectable": True, "pricing_status": "priced",
            "currency": "INR", "billing_interval": "MONTHLY", "billing_catalog": CATALOG, **extra}


def plans(*items, vm_type: str = "cloud") -> dict:
    return {"plans": list(items), "count": len(items), "vm_type": vm_type, "site_id": "site-1", "currency": "INR",
            "billing_interval": "MONTHLY"}


# ---------------------------------------------------------------------------
# vms/gpus create: billing_catalog from GET /compute/plans
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("group", "vm_type", "path"), [("vms", "cloud", "compute/cloud-vms"), ("gpus", "gpu", "compute/gpu-vms")])
@pytest.mark.parametrize("interval", ["HOURLY", "MONTHLY", "monthly"])
def test_billing_interval_merges_the_plans_option_into_its_catalog(gw, group, vm_type, path, interval) -> None:
    gw.on("GET", "compute/plans", plans(plan("other", vm_type), plan("plan-1", vm_type), vm_type=vm_type))
    gw.on("POST", path, (202, ACCEPTED))
    gpu = ["--gpu-count", "1", "--gpu-model", "L40S"] if group == "gpus" else []
    result = run([group, "create", *CREATE, *gpu, "--billing-interval", interval])
    assert result.exit_code == 0, result.output
    lookup, create = gw.calls
    assert (lookup.method, lookup.path) == ("GET", "compute/plans")
    assert lookup.params == {"workspace_id": [WS], "vm_type": [vm_type], "site_id": ["site-1"]}
    option = HOURLY if interval == "HOURLY" else MONTHLY
    assert create.json["billing_catalog"] == {**CATALOG, **option}
    assert create.json["plan_id"] == "plan-1" and create.json["cpu"] == 1
    assert json.loads(result.output) == ACCEPTED


def test_billing_catalog_is_sent_as_given_without_a_lookup(gw, tmp_path) -> None:
    gw.on("POST", "compute/cloud-vms", (202, ACCEPTED))
    selection = {**CATALOG, **HOURLY}
    source = tmp_path / "catalog.json"
    source.write_text(json.dumps(selection))
    result = run(["vms", "create", *CREATE, "--billing-catalog", f"@{source}"])
    assert result.exit_code == 0, result.output
    assert [c.method for c in gw.calls] == ["POST"]
    assert gw.calls[0].json["billing_catalog"] == selection


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ([], "--billing-interval"),
        (["--billing-interval", "HOURLY", "--billing-catalog", "{}"], "not both"),
    ],
)
def test_billing_catalog_needs_one_source(gw, extra, message) -> None:
    result = run(["vms", "create", *CREATE, *extra])
    assert result.exit_code == 2 and message in plain(result)
    assert gw.calls == []


def test_plan_or_interval_not_in_the_catalog_sends_no_create(gw) -> None:
    gw.on("GET", "compute/plans", plans(plan("other")))
    result = run(["vms", "create", *CREATE, "--billing-interval", "HOURLY"])
    assert result.exit_code == 2 and "Plan plan-1 is not in GET /compute/plans" in plain(result)
    hourly_only = plan(billing_catalog={**CATALOG, "billing_options": [HOURLY]})
    gw.routes.clear()
    gw.on("GET", "compute/plans", plans(hourly_only))
    result = run(["vms", "create", *CREATE, "--billing-interval", "MONTHLY"])
    assert result.exit_code == 2 and "no MONTHLY entry" in plain(result) and "offered: HOURLY" in plain(result)
    assert gw.writes() == []


def test_vm_create_fields_are_required_and_not_filled_in(gw) -> None:
    """No plan or image defaults: CPU, RAM and OS come from the caller (the API answers otherwise)."""
    result = run(["vms", "create", "--name", "web", "--site-id", "s", "--plan-id", "p", "--billing-catalog", "{}"])
    assert result.exit_code == 2
    assert gw.calls == []


def test_vm_writes_carry_an_idempotency_key(gw) -> None:
    gw.on("POST", "compute/cloud-vms/vm-1/actions/start", (202, ACCEPTED))
    assert run(["vms", "start", "vm-1"]).exit_code == 0
    assert gw.calls[-1].headers["x-idempotency-key"]
    assert run(["vms", "start", "vm-1", "--idempotency-key", "k-9"]).exit_code == 0
    assert gw.calls[-1].headers["x-idempotency-key"] == "k-9"


@pytest.mark.parametrize("group", ["vms", "gpus"])
def test_backups_disable_sends_no_body(gw, group) -> None:
    """Contract 0.6.3: the operation has no request body, and the server accepts none."""
    path = f"compute/{'cloud' if group == 'vms' else 'gpu'}-vms/vm-1/backups/disable"
    gw.on("POST", path, (418, {"error": "teapot"}))
    run([group, "backup-policy", "disable", "vm-1"])
    assert gw.last("POST", path).json is None


# ---------------------------------------------------------------------------
# JSON options and confirmations
# ---------------------------------------------------------------------------


def test_json_options_read_text_files_and_stdin(gw) -> None:
    gw.default = (418, {"error": "teapot"})
    rules = [{"allowed_origins": ["*"], "allowed_methods": ["GET"]}]
    run(["buckets", "cors", "set", "b", "--rules", json.dumps(rules)])
    assert gw.calls[-1].json == {"rules": rules}
    run(["buckets", "cors", "set", "b", "--rules", "@-"], input=json.dumps(rules))
    assert gw.calls[-1].json == {"rules": rules}
    assert len(gw.calls) == 2


@pytest.mark.parametrize("value", ["{not json", "@/no/such/file.json"])
def test_bad_json_option_sends_nothing(gw, value) -> None:
    result = run(["buckets", "cors", "set", "b", "--rules", value])
    assert result.exit_code == 2 and "--rules" in plain(result)
    assert gw.calls == []


def test_global_yes_and_env_skip_confirmation(gw, monkeypatch) -> None:
    gw.on("DELETE", "networking/reserved-ips/rip-1", None)
    assert run(["--yes", "reserved-ips", "release", "rip-1"]).exit_code == 0
    monkeypatch.setenv("IBEE_ASSUME_YES", "1")
    assert run(["reserved-ips", "release", "rip-1"]).exit_code == 0
    monkeypatch.delenv("IBEE_ASSUME_YES")
    assert run(["reserved-ips", "release", "rip-1"], input="y\n").exit_code == 0
    assert len(gw.calls) == 3


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

SITES = {"sites": [{"site_id": "s1", "name": "Bengaluru", "extra": {"a": 1}}, {"site_id": "s2", "name": "Chennai"}],
         "count": 2}


def test_json_is_the_default_and_unchanged(gw) -> None:
    gw.on("GET", "compute/sites", SITES)
    result = run(["compute", "sites"])
    assert result.exit_code == 0 and json.loads(result.output) == SITES


def test_output_modes(gw) -> None:
    gw.on("GET", "compute/sites", SITES)
    assert run(["-o", "id", "compute", "sites"]).output.split() == ["s1", "s2"]
    assert yaml.safe_load(run(["-o", "yaml", "compute", "sites"]).output) == SITES
    table = plain(run(["-o", "table", "compute", "sites"]))
    assert "site_id" in table and "Bengaluru" in table and "Chennai" in table
    assert json.loads(run(["--json", "compute", "sites"]).output) == SITES


def test_ids_of_bare_lists_and_objects(gw) -> None:
    gw.on("GET", "networking/reserved-ips", [
        {"public_ip_id": "rip-1", "address": "203.0.113.1", "site_id": "s1", "status": "reserved", "created_at": TS, "updated_at": TS},
    ])
    assert run(["-o", "id", "reserved-ips", "list"]).output.split() == ["rip-1"]
    gw.on("POST", "compute/cloud-vms/vm-1/actions/stop", (202, ACCEPTED))
    assert run(["-o", "id", "vms", "stop", "vm-1"]).output.split() == ["op-1"]
    table = plain(run(["-o", "table", "vms", "stop", "vm-1"]))
    assert "operation_id" in table and "op-1" in table


def test_empty_answer_prints_nothing(gw) -> None:
    gw.on("DELETE", "networking/vpcs/vpc-1", None)
    result = run(["vpcs", "delete", "vpc-1", "--yes"])
    assert result.exit_code == 0 and result.output == ""


def test_sbom_download_writes_the_document(gw) -> None:
    document = b'{"spdxVersion": "SPDX-2.3"}'
    gw.on("GET", "container-registry/registries/r-1/security/sboms/sha256:abc/download", (200, document))
    result = run(["registries", "sboms", "download", "r-1", "sha256:abc", "--repository-name", "api"])
    assert result.exit_code == 0 and result.stdout_bytes == document
    call = gw.calls[-1]
    assert call.params == {"workspace_id": [WS], "repository_name": ["api"]}
