"""Billing ownership coverage for explicit preview and product writes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from ibee_cli.commands import billing, secrets, vm_lifecycle, vms, gpus
from ibee_cli.main import app

import _net_fixtures as _net

runner = CliRunner()
BASE_ARGS = ["--token", "test-token", "--workspace", "973318"]


@pytest.mark.parametrize("flag", [[], ["--check-billing"]])
@pytest.mark.parametrize("status,code", [
    (402, "billing_denied"), (403, "organization_restricted"),
    (423, "organization_suspended"), (403, "key_revoked"),
    (403, "workspace_not_allowed"), (403, "insufficient_scope"),
])
def test_mutation_errors_are_upstream_and_suspension_is_source_neutral(gw, flag, status, code):
    gw.on("POST", "secret-store/stores", (status, {
        "error": code, "message": "Admin lifecycle decision",
        "enforcement_source": "ADMIN", "billing_reason": "insufficient_balance",
        "required_scope": "secret_store.write",
    }))
    result = runner.invoke(app, [*BASE_ARGS, *flag, "secrets", "stores", "create", "app"])
    assert result.exit_code == 1, result.output
    assert [c.path for c in gw.writes()] == ["secret-store/stores"]
    if status == 423:
        assert "Organization suspended (423)" in result.output
        assert "billing has suspended" not in result.output.lower()


@pytest.mark.parametrize("operation", ["REVOKE_CREDENTIAL", "SECURITY_RECOVERY"])
def test_explicit_lifecycle_eligibility_returns_denial_as_data(gw, monkeypatch, operation):
    monkeypatch.setattr(billing, "get_client", lambda _settings: gw.client())
    gw.on("POST", "billing/resource-eligibility", {
        "organization_id": "org", "allowed": False, "reason": "upstream", "operation": operation,
    })
    result = runner.invoke(app, [*BASE_ARGS, "billing", "eligibility", "--operation", operation])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", "billing/resource-eligibility").json == {"operation": operation}


class Billing:
    def __init__(self, calls, response=None):
        self.calls = calls
        self.response = response or SimpleNamespace(allowed=True, reason="ok")

    def check_resource_eligibility(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_manual_billing_eligibility_uses_typed_sdk(monkeypatch):
    calls = []
    client = SimpleNamespace(billing=Billing(calls))
    monkeypatch.setattr(billing, "get_client", lambda _settings: client)
    result = runner.invoke(
        app,
        [
            *BASE_ARGS,
            "billing",
            "eligibility",
            "--sku-code",
            "STANDARD-2-4",
            "--estimated-cost-minor",
            "12500",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "workspace_id": "973318",
            "sku_code": "STANDARD-2-4",
            "estimated_cost_minor": 12500,
        }
    ]


def test_manual_billing_command_reports_old_sdk(monkeypatch):
    monkeypatch.setattr(billing, "get_client", lambda _settings: SimpleNamespace())
    result = runner.invoke(app, [*BASE_ARGS, "billing", "eligibility"])
    assert result.exit_code == 1
    assert "Upgrade the SDK" in result.output


@pytest.mark.parametrize("group,family", [("vms", "cloud"), ("gpus", "gpu")])
@pytest.mark.parametrize("denied", [False, True])
def test_vm_preflight_uses_upstream_status_and_preserves_create_denial(gw, monkeypatch, group, family, denied):
    for module in (vm_lifecycle, vms, gpus):
        monkeypatch.setattr(module, "get_client", lambda _settings: gw.client())
    gw.on("GET", "compute/plans", {"plans": [{
        "plan_id": "plan", "vm_type": family, "selectable": True, "pricing_status": "priced",
        "cpu": 2, "ram_mb": 4096, "disk_gb": 50, "gpu_count": 1, "gpu_model": "L4",
        "billing_catalog": {"sku_id": 1, "sku_code": "VM-1", "billing_options": [
            {"billing_interval": "HOURLY", "unit_price_minor": 3500, "committed": False},
        ]},
    }]})
    gw.on("GET", "compute/images", {"images": [{
        "template_id": "image", "os_type": "linux", "os_distro": "ubuntu",
        "gpu_compatible": True, "compatible_vm_types": [family], "site_ids": [],
    }]})
    gw.on("POST", "billing/resource-eligibility", {
        "organization_id": "org", "allowed": True, "reason": "status_only",
        "effective_balance_minor": 3500,
    })
    response = (402, {"error": "billing_denied", "billing_reason": "insufficient_balance",
                      "billing_sku_code": "VM-1", "admission_context_id": "adm_upstream"}) if denied else (202, {
        "operation_id": "op_65f0c0ffee0000000000abcd", "vm_id": "65f0c0ffee0000000000abcd",
        "status": "accepted", "submitted_at": "2026-09-29T00:00:00Z",
    })
    gw.on("POST", f"compute/{family}-vms", response)
    result = runner.invoke(app, [*BASE_ARGS, "--check-billing", group, "create", "test",
                                "--site-id", "site-1", "--plan-id", "plan", "--template-id", "image",
                                "--billing-term", "HOURLY"])
    assert result.exit_code == (1 if denied else 0), result.output
    assert not any(c.path == "billing/resource-eligibility" for c in gw.calls)
    creates = [c for c in gw.calls if c.method == "POST" and c.path == f"compute/{family}-vms"]
    assert len(creates) == 1
    assert creates[0].json["billing_catalog"]["billing_interval"] == "HOURLY"
    if denied:
        assert "balance" in result.output.lower()


@pytest.mark.parametrize(
    ("args", "script", "post_path"),
    [
        (
            ["vpcs", "nat", "create", "vpc-1", "--billing-catalog", '{"sku_code":"NAT-GATEWAY"}'],
            [("GET", "networking/vpcs/vpc-1", "vpc")],
            "networking/vpcs/vpc-1/nat-gateways",
        ),
        (["reserved-ips", "reserve", "--site-id", "site-1"], [], "networking/reserved-ips"),
        (
            ["load-balancers", "create-l4", "edge", "--backends", '[{"type":"ip","target":"10.0.0.5","port":443}]'],
            [],
            "networking/load-balancers/l4",
        ),
    ],
)
def test_billable_creates_send_one_product_request_for_edge_admission(gw, args, script, post_path):
    """Without --check-billing no billing call is made and exactly one create is sent
    (read-only portal pre-steps such as reading the VPC may come first)."""

    records = {"vpc": _net.vpc(), "rip": _net.rip(), "lb": _net.lb(layer="l4", protocol="tcp")}
    for method, path, name in script:
        gw.on(method, path, records[name])
    response = {
        "networking/vpcs/vpc-1/nat-gateways": _net.gateway_record(),
        "networking/reserved-ips": _net.rip(),
        "networking/load-balancers/l4": records["lb"],
    }[post_path]
    gw.on("POST", post_path, response)
    result = runner.invoke(app, [*BASE_ARGS, *args])
    assert result.exit_code == 0, result.output
    assert [(c.method, c.path) for c in gw.writes()] == [("POST", post_path)]
    assert not any(c.path.startswith("billing") for c in gw.calls)


def test_secret_store_and_secret_create_run_the_portal_billing_preflight(monkeypatch):
    """The portal checks SECRETMA-STD before a store or secret create; the CLI asks the
    SDK to do the same by default, and --no-billing-check turns it off."""

    resource_calls = []

    class SecretStore:
        def create_secret_store(self, **kwargs):
            resource_calls.append(("store", kwargs))
            return SimpleNamespace(id="store-1")

        def create_secret(self, *args, **kwargs):
            resource_calls.append(("secret", kwargs))
            return SimpleNamespace(id="secret-1")

    client = SimpleNamespace(billing=Billing([]), secret_store=SecretStore())
    monkeypatch.setattr(secrets, "get_client", lambda _settings: client)

    secret_args = ["secrets", "create", "--store-id", "store-1", "--name", "db-url", "--value", "url=postgres://db"]
    for extra in ([], ["--no-billing-check"]):
        result = runner.invoke(app, [*BASE_ARGS, "secrets", "stores", "create", "production", *extra])
        assert result.exit_code == 0, result.output
        result = runner.invoke(app, [*BASE_ARGS, *secret_args, *extra])
        assert result.exit_code == 0, result.output
    assert [(name, kwargs["preflight_billing"]) for name, kwargs in resource_calls] == [
        ("store", False),
        ("secret", False),
        ("store", False),
        ("secret", False),
    ]
    # The global --check-billing forces the check even with --no-billing-check.
    resource_calls.clear()
    result = runner.invoke(
        app, [*BASE_ARGS, "--check-billing", "secrets", "stores", "create", "production", "--no-billing-check"]
    )
    assert result.exit_code == 0, result.output
    assert resource_calls[0][1]["preflight_billing"] is False
