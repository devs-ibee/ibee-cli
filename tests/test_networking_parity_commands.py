"""0.4.0 portal-parity networking commands: VPCs, NAT, virtual IPs, Reserved IPs,
firewalls and load balancers (real SDK, scripted gateway, no network)."""

from __future__ import annotations

import json
import time

import pytest

from _net_fixtures import (
    FW,
    LB,
    NAT_CATALOG,
    RIP,
    RIP_CATALOG,
    V,
    gateway_record,
    group,
    lb,
    node,
    pf_rule,
    plain,
    rip,
    run,
    site,
    subnet,
    summary,
    vip,
    vpc,
)
from ibee.errors import NotFoundError, ReservedIpTargetUnsupportedError

from ibee_cli.render import api_error_lines

RULES = f"{V}/nat-gateways/nat-1/port-forwarding-rules"
def decision(sku, allowed=True, reason="ok"):
    return {"allowed": allowed, "organization_id": "o", "reason": reason, "sku_code": sku}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


def ok(result):
    assert result.exit_code == 0, result.output
    return result


def usage(result, text, gw_):
    assert result.exit_code == 2, result.output
    assert text.lower() in plain(result).lower()
    return result


# ---------------------------------------------------------------------------
# Sites and VPC create
# ---------------------------------------------------------------------------


def test_sites_available_only_and_table(gw):
    gw.on("GET", "networking/sites", [site(), site(site_id="site-2", available=False, message="Coming soon")])
    result = ok(run(["vpcs", "sites", "--available-only"]))
    data = json.loads(result.output)
    assert [item["site_id"] for item in data] == ["site-1"]
    result = ok(run(["-o", "table", "vpcs", "sites"]))
    assert "Coming soon" in plain(result)


def test_vpc_create_uses_portal_defaults(gw):
    gw.on("GET", "networking/sites", [site()])
    gw.on("POST", "networking/vpcs", vpc(connectivity_type="private"))
    ok(run(["vpcs", "create", "  apps  ", "--site-id", "site-1", "--description", "  "]))
    assert gw.last("POST", "networking/vpcs").json == {
        "name": "apps",
        "site_id": "site-1",
        "connectivity_type": "private",
        "auto_cidr": True,
        "create_default_subnet": True,
    }


def test_vpc_create_checks_the_site_first(gw):
    gw.on("GET", "networking/sites", [site(available=False, message="VPCs are not enabled in this site")])
    result = run(["vpcs", "create", "apps", "--site-id", "site-1"])
    usage(result, "VPCs are not enabled in this site", gw)
    assert gw.writes() == []

    gw.calls.clear()
    gw.on("POST", "networking/vpcs", vpc())
    ok(run(["vpcs", "create", "apps", "--site-id", "site-1", "--no-check-site"]))
    assert [c.method for c in gw.calls] == ["POST"]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--cidr", "10.20.1.0/22"], "10.20.0.0/22"),
        (["--cidr", "8.8.8.0/24"], "RFC1918"),
        (["--cidr", "10.0.0.0/20"], "22"),
        (["--cidr", "10.20.0.0/24", "--auto-cidr"], "auto_cidr=false"),
        (["--no-auto-cidr"], "cidr is required"),
        (["--nat-billing-catalog", '{"sku_code":"NAT-GATEWAY"}'], "nat_gateway"),
        (["--connectivity", "internet"], "connectivity_type"),
        (["--connectivity", "nat_gateway", "--nat-billing-catalog", "{}"], "sku_code"),
    ],
)
def test_vpc_create_validation_exits_2_before_any_request(gw, args, message):
    result = run(["vpcs", "create", "apps", "--site-id", "site-1", *args])
    usage(result, message, gw)
    assert gw.calls == []


def test_vpc_create_prints_sdk_warnings(gw):
    gw.on("GET", "networking/sites", [site()])
    gw.on("POST", "networking/vpcs", vpc())
    result = ok(run(["vpcs", "create", "apps", "--site-id", "site-1", "--connectivity", "nat_gateway"]))
    assert "Warning:" in plain(result) and "billing catalog" in plain(result)
    result = ok(run(["vpcs", "create", "apps", "--site-id", "site-1", "--connectivity", "public"]))
    assert "deprecated" in plain(result)


def test_vpc_create_reads_catalog_file(gw, tmp_path):
    path = tmp_path / "nat.json"
    path.write_text(json.dumps(NAT_CATALOG))
    gw.on("POST", "networking/vpcs", vpc())
    result = ok(run(["vpcs", "create", "apps", "--site-id", "site-1", "--no-check-site", "--connectivity",
                     "nat_gateway", "--nat-billing-catalog-file", str(path)]))
    assert "Warning" not in plain(result)
    assert gw.last("POST", "networking/vpcs").json["nat_billing_catalog"] == NAT_CATALOG


# ---------------------------------------------------------------------------
# VPC delete
# ---------------------------------------------------------------------------


def test_vpc_delete_refuses_with_attached_nodes(gw):
    gw.on("GET", V, vpc(node_count=2))
    usage(run(["vpcs", "delete", "vpc-1", "--yes"]), "Detach 2 attached node(s)", gw)
    assert gw.writes() == []


def test_vpc_delete_refuses_with_nat_gateway_and_virtual_ips(gw):
    gw.on("GET", V, vpc(nat_gateways=[gateway_record()]))
    usage(run(["vpcs", "delete", "vpc-1", "--yes"]), "--delete-nat-gateway", gw)

    gw.routes[("GET", V)] = [vpc()]
    gw.on("GET", f"{V}/virtual-ips", [vip()])
    usage(run(["vpcs", "delete", "vpc-1", "--yes"]), "Delete all virtual IP reservations", gw)
    assert gw.writes() == []


def test_vpc_delete_with_nat_gateway_deletes_it_first_and_waits(gw):
    with_gateway = vpc(nat_gateways=[gateway_record()])
    gw.on("GET", V, with_gateway, with_gateway, with_gateway, vpc())
    gw.on("GET", f"{V}/virtual-ips", [])
    gw.on("DELETE", f"{V}/nat-gateways/nat-1")
    gw.on("DELETE", V)
    ok(run(["vpcs", "delete", "vpc-1", "--delete-nat-gateway", "--nat-ip-action", "release", "--yes"]))
    writes = [(c.method, c.path, c.json) for c in gw.writes()]
    assert writes == [
        ("DELETE", f"{V}/nat-gateways/nat-1", {"public_ip_action": "release"}),
        ("DELETE", V, None),
    ]


def test_vpc_delete_nat_options_need_delete_nat_gateway(gw):
    usage(run(["vpcs", "delete", "vpc-1", "--nat-ip-action", "release", "--yes"]), "--delete-nat-gateway", gw)
    usage(run(["vpcs", "delete", "vpc-1", "--delete-nat-gateway", "--nat-ip-action", "keep", "--yes"]),
          "reserve", gw)
    assert gw.calls == []


def test_vpc_delete_conflict_prints_portal_guidance(gw):
    gw.on("DELETE", V, (409, {"detail": "VPC still has subnets in use"}))
    result = run(["vpcs", "delete", "vpc-1", "--yes", "--no-check-state"])
    assert result.exit_code == 1
    assert "Conflict (409)" in plain(result)
    assert "Detach nodes and remove subnets before deleting this VPC." in plain(result)


def test_vpc_delete_declined_sends_nothing(gw):
    gw.on("GET", V, vpc()).on("GET", f"{V}/virtual-ips", [])
    result = run(["vpcs", "delete", "vpc-1"], input="n\n")
    assert result.exit_code == 1
    assert gw.writes() == []


# ---------------------------------------------------------------------------
# Subnets and nodes
# ---------------------------------------------------------------------------


def test_subnet_create_checks_the_vpc_cidr(gw):
    gw.on("GET", V, vpc(subnets=[subnet(cidr="10.20.0.0/24")]))
    usage(run(["vpcs", "subnets", "create", "vpc-1", "apps", "--cidr", "10.30.0.0/24"]), "10.20.0.0/22", gw)
    usage(run(["vpcs", "subnets", "create", "vpc-1", "apps", "--cidr", "10.20.0.0/25"]), "overlap", gw)
    assert gw.writes() == []

    gw.on("POST", f"{V}/subnets", subnet(subnet_id="sub-2", cidr="10.20.1.0/24"))
    ok(run(["vpcs", "subnets", "create", "vpc-1", "apps", "--cidr", "10.20.1.0/24"]))
    assert gw.last("POST", f"{V}/subnets").json == {"name": "apps", "cidr": "10.20.1.0/24", "auto_cidr": False}


def test_subnet_create_cidr_and_prefix_length_are_exclusive(gw):
    usage(run(["vpcs", "subnets", "create", "vpc-1", "apps", "--cidr", "10.20.1.0/24", "--prefix-length", "24",
               "--no-check-state"]), "prefix_length", gw)
    assert gw.calls == []


def test_subnet_delete_conflict_hint(gw):
    gw.on("DELETE", f"{V}/subnets/sub-1", (409, {"detail": "Subnet has allocations"}))
    result = run(["vpcs", "subnets", "delete", "vpc-1", "sub-1", "--yes"])
    assert result.exit_code == 1
    assert "Detach nodes before deleting this subnet." in plain(result)


def test_node_attach_requested_private_ip(gw):
    gw.on("GET", f"{V}/subnets/sub-1", subnet())
    gw.on("GET", V, vpc())
    usage(run(["vpcs", "nodes", "attach", "vpc-1", "vm-1", "--subnet-id", "sub-1", "--private-ip", "10.20.0.1"]),
          "reserved for the subnet gateway", gw)
    usage(run(["vpcs", "nodes", "attach", "vpc-1", "vm-1", "--subnet-id", "sub-1", "--private-ip", "10.20.0.255"]),
          "network or broadcast", gw)
    usage(run(["vpcs", "nodes", "attach", "vpc-1", "vm-1", "--subnet-id", "sub-1", "--private-ip", "10.20.5.4"]),
          "inside 10.20.0.0/24", gw)
    assert gw.writes() == []

    gw.on("POST", f"{V}/nodes", node())
    ok(run(["vpcs", "nodes", "attach", "vpc-1", "vm-1", "--subnet-id", "sub-1", "--private-ip", " 10.20.0.10 "]))
    # connectivity is left to the backend (nat in a nat_gateway VPC), as the portal resolves it.
    assert gw.last("POST", f"{V}/nodes").json == {
        "vm_id": "vm-1",
        "subnet_id": "sub-1",
        "requested_private_ip": "10.20.0.10",
    }


def test_node_attach_connectivity_rules(gw):
    gw.on("GET", V, vpc(connectivity_type="private"))
    usage(run(["vpcs", "nodes", "attach", "vpc-1", "vm-1", "--subnet-id", "sub-1", "--connectivity", "nat"]),
          "nat_gateway VPC", gw)
    usage(run(["vpcs", "nodes", "attach", "vpc-1", "vm-1", "--subnet-id", "sub-1", "--connectivity", "public_ip"]),
          "reserved_public_ip_id", gw)
    gw.routes[("GET", V)] = [vpc()]
    usage(run(["vpcs", "nodes", "attach", "vpc-1", "vm-1", "--subnet-id", "sub-1", "--connectivity", "public_ip"]),
          "not available for nat_gateway VPCs", gw)
    usage(run(["vpcs", "nodes", "attach", "vpc-1", "vm-1", "--subnet-id", "sub-1", "--connectivity", "nat"]),
          "available NAT gateway", gw)
    assert gw.writes() == []


def test_node_detach_conflict_explains_vm_side_detach(gw):
    gw.on("DELETE", f"{V}/nodes/vm-1", (409, {"detail": "VM networking still references this allocation"}))
    result = run(["vpcs", "nodes", "detach", "vpc-1", "vm-1", "--yes"])
    assert result.exit_code == 1
    assert "not yet available in the public API" in plain(result)


# ---------------------------------------------------------------------------
# NAT gateways
# ---------------------------------------------------------------------------


def test_nat_create_only_in_nat_vpcs(gw):
    gw.on("GET", V, vpc(connectivity_type="private"))
    usage(run(["vpcs", "nat", "create", "vpc-1"]), "only be created for nat_gateway VPCs", gw)
    assert gw.writes() == []


def test_nat_create_returns_existing_gateway(gw):
    gw.on("GET", V, vpc(nat_gateways=[gateway_record()]))
    result = ok(run(["vpcs", "nat", "create", "vpc-1"]))
    assert "NAT gateway already exists" in plain(result)
    assert gw.writes() == []


def test_nat_create_reserved_ip_eligibility(gw):
    gw.on("GET", V, vpc())
    gw.on("GET", "networking/reserved-ips/rip-9", rip(public_ip_id="rip-9", site_id="site-2"))
    usage(run(["vpcs", "nat", "create", "vpc-1", "--reserved-ip-id", "rip-9"]), "same site", gw)
    assert gw.writes() == []


def test_nat_create_warns_without_catalog_and_runs_preflight(gw):
    gw.on("GET", V, vpc())
    gw.on("POST", "billing/resource-eligibility", decision("NAT-GATEWAY"))
    gw.on("POST", f"{V}/nat-gateways", gateway_record())
    result = ok(run(["vpcs", "nat", "create", "vpc-1", "--preflight"]))
    assert "not be metered" in plain(result)
    assert [(c.method, c.path) for c in gw.writes()] == [
        ("POST", "billing/resource-eligibility"),
        ("POST", f"{V}/nat-gateways"),
    ]
    assert gw.writes()[0].json == {"sku_code": "NAT-GATEWAY"}
    assert gw.last("POST", f"{V}/nat-gateways").json == {"name": "NAT Gateway"}


def test_nat_create_global_check_billing_denial(gw):
    gw.on("GET", V, vpc())
    gw.on("POST", "billing/resource-eligibility", decision("NAT-GATEWAY", False, "insufficient_balance"))
    result = run(["--check-billing", "vpcs", "nat", "create", "vpc-1", "--billing-catalog", json.dumps(NAT_CATALOG)])
    assert result.exit_code == 1, result.output
    assert "Add credits" in plain(result)
    assert not gw.requests("POST")[1:]


def _nat_delete_state(gw, **gateway):
    gw.on("GET", f"{V}/nat-gateways", [gateway_record(**gateway)])
    gw.on("GET", f"{V}/virtual-ips", [vip()])
    gw.on("GET", f"{V}/nodes", [node(), node("vm-2", "10.20.0.11", connectivity="private")])
    gw.on("GET", RULES, [pf_rule(), pf_rule(port_forward_rule_id="natpf-2", external_port=8080)])
    gw.on("DELETE", f"{V}/nat-gateways/nat-1")


def test_nat_delete_shows_impact_before_confirming(gw):
    _nat_delete_state(gw)
    result = run(["vpcs", "nat", "delete", "vpc-1", "nat-1"], input="n\n")
    assert result.exit_code == 1
    assert "1 VM(s) will lose managed outbound internet, and 2 port forwarding rule(s)" in plain(result)
    assert gw.writes() == []


def test_nat_delete_public_ip_action_rules(gw):
    _nat_delete_state(gw)
    usage(run(["vpcs", "nat", "delete", "vpc-1", "nat-1", "--ip-action", "reserve", "--yes"]),
          "RESERVED-IP billing_catalog", gw)
    usage(run(["vpcs", "nat", "delete", "vpc-1", "nat-1", "--ip-action", "release", "--billing-catalog",
               json.dumps(RIP_CATALOG), "--yes"]), "only allowed with public_ip_action='reserve'", gw)
    assert gw.writes() == []

    ok(run(["vpcs", "nat", "delete", "vpc-1", "nat-1", "--ip-action", "reserve", "--billing-catalog",
            json.dumps(RIP_CATALOG), "--yes"]))
    assert gw.writes()[-1].json == {"public_ip_action": "reserve", "billing_catalog": RIP_CATALOG}


def test_nat_delete_reserve_is_free_for_reserved_ip_gateways(gw):
    _nat_delete_state(gw, public_ip_source="reserved")
    ok(run(["vpcs", "nat", "delete", "vpc-1", "nat-1", "--ip-action", "reserve", "--yes"]))
    assert gw.writes()[-1].json == {"public_ip_action": "reserve"}


def test_nat_delete_refuses_while_a_virtual_ip_holds_a_reserved_ip(gw):
    _nat_delete_state(gw)
    gw.routes[("GET", f"{V}/virtual-ips")] = [[vip(public_ip_id="rip-1")]]
    usage(run(["vpcs", "nat", "delete", "vpc-1", "nat-1", "--yes"]), "Detach virtual-IP Reserved Public IPs", gw)
    assert gw.writes() == []


def test_nat_delete_unknown_gateway(gw):
    gw.on("GET", f"{V}/nat-gateways", [])
    usage(run(["vpcs", "nat", "delete", "vpc-1", "nat-1", "--yes"]), "was not found", gw)


def test_nat_delete_wait(gw):
    gw.on("GET", V, vpc(nat_gateways=[gateway_record()]), vpc())
    gw.on("DELETE", f"{V}/nat-gateways/nat-1")
    result = ok(run(["vpcs", "nat", "delete", "vpc-1", "nat-1", "--yes", "--no-check-state", "--wait"]))
    assert "NAT gateway 'nat-1' deleted." in plain(result)
    assert gw.last("DELETE", f"{V}/nat-gateways/nat-1").json is None

    gw.routes[("GET", V)] = [vpc(nat_gateways=[gateway_record(status="deleting")])]
    result = run(["vpcs", "nat", "delete", "vpc-1", "nat-1", "--yes", "--no-check-state", "--wait"])
    assert result.exit_code == 3
    assert "still reconciling" in plain(result)


def test_nat_delete_without_wait_says_reconciling(gw):
    gw.on("DELETE", f"{V}/nat-gateways/nat-1")
    result = ok(run(["vpcs", "nat", "delete", "vpc-1", "nat-1", "--yes", "--no-check-state"]))
    assert "still reconciling" in plain(result)


def test_nat_replace_ip(gw):
    gw.on("GET", V, vpc(nat_gateways=[gateway_record()]))
    gw.on("GET", RIP, rip())
    gw.on("PUT", f"{V}/nat-gateways/nat-1/public-ip", gateway_record(public_ip_source="reserved"))
    ok(run(["vpcs", "nat", "replace-ip", "vpc-1", "nat-1", "--reserved-ip-id", " rip-1 "]))
    assert gw.last("PUT", f"{V}/nat-gateways/nat-1/public-ip").json == {"reserved_public_ip_id": "rip-1"}

    gw.routes[("GET", RIP)] = [rip(attached_resource_type="vm", attached_resource_id="vm-9")]
    usage(run(["vpcs", "nat", "replace-ip", "vpc-1", "nat-1", "--reserved-ip-id", "rip-1"]), "already attached", gw)


# ---------------------------------------------------------------------------
# Port forwarding
# ---------------------------------------------------------------------------


def _pf_state(gw, rules=None, **gateway):
    gw.on("GET", f"{V}/nat-gateways", [gateway_record(**gateway)])
    gw.on("GET", RULES, rules if rules is not None else [pf_rule()])
    gw.on("GET", f"{V}/nodes", [node(), node("vm-2", "10.20.0.11")])
    gw.on("GET", f"{V}/virtual-ips", [vip()])
    gw.on("POST", RULES, pf_rule())


PF = ["vpcs", "forwarding", "create", "vpc-1", "nat-1", "web"]


def test_forwarding_duplicate_port_and_port_range(gw):
    _pf_state(gw)
    usage(run([*PF, "--external-port", "2222", "--internal-ip", "10.20.0.10", "--internal-port", "22"]),
          "TCP external port 2222 already exists", gw)
    usage(run([*PF, "--external-port", "70000", "--internal-ip", "10.20.0.10", "--internal-port", "22"]),
          "1 to 65535", gw)
    usage(run([*PF, "--external-port", "80", "--internal-ip", "10.20.0.10", "--internal-port", "22",
               "--protocol", "icmp"]), "tcp", gw)
    assert gw.writes() == []


def test_forwarding_requires_available_gateway_and_nat_target(gw):
    _pf_state(gw, status="provisioning")
    usage(run([*PF, "--external-port", "80", "--internal-ip", "10.20.0.10", "--internal-port", "80"]),
          "An active NAT gateway is required.", gw)
    gw.routes[("GET", f"{V}/nat-gateways")] = [[gateway_record()]]
    usage(run([*PF, "--external-port", "80", "--internal-ip", "10.20.0.99", "--internal-port", "80"]),
          "NAT", gw)
    assert gw.writes() == []


def test_forwarding_vip_target_uses_announcers(gw):
    _pf_state(gw)
    ok(run([*PF, "--external-port", "443", "--internal-ip", "10.20.0.50", "--internal-port", "443", "--target",
            "vip"]))
    body = gw.last("POST", RULES).json
    assert body["target_type"] == "vip" and body["target_vm_ids"] == ["vm-1", "vm-2"]

    usage(run([*PF, "--external-port", "444", "--internal-ip", "10.20.0.50", "--internal-port", "443", "--target",
               "vip", "--target-vm-id", "vm-1"]), "announcer", gw)


def test_forwarding_update_target_and_toggles(gw):
    gw.on("GET", f"{V}/nat-gateways", [gateway_record()])
    gw.on("GET", RULES, [pf_rule()])
    gw.on("PATCH", f"{RULES}/natpf-1", pf_rule())
    ok(run(["vpcs", "forwarding", "update", "vpc-1", "nat-1", "natpf-1", "--target", "vm"]))
    assert gw.last("PATCH", f"{RULES}/natpf-1").json == {"target_type": "vm", "target_vm_ids": []}
    usage(run(["vpcs", "forwarding", "update", "vpc-1", "nat-1", "natpf-1", "--target-vm-id", "vm-1"]),
          "target_type", gw)

    ok(run(["vpcs", "forwarding", "disable", "vpc-1", "nat-1", "natpf-1"]))
    assert gw.last("PATCH", f"{RULES}/natpf-1").json == {"enabled": False}
    ok(run(["vpcs", "forwarding", "enable", "vpc-1", "nat-1", "natpf-1"]))
    assert gw.last("PATCH", f"{RULES}/natpf-1").json == {"enabled": True}

    gw.routes[("GET", f"{V}/nat-gateways")] = [[gateway_record(status="error")]]
    usage(run(["vpcs", "forwarding", "enable", "vpc-1", "nat-1", "natpf-1"]), "active NAT gateway", gw)


# ---------------------------------------------------------------------------
# Virtual IPs
# ---------------------------------------------------------------------------


def test_virtual_ip_list_get_and_missing(gw):
    gw.on("GET", f"{V}/virtual-ips", [vip()])
    result = ok(run(["-o", "id", "vpcs", "virtual-ips", "list", "vpc-1"]))
    assert result.output.strip() == "pvip-1"
    result = ok(run(["vpcs", "virtual-ips", "get", "vpc-1", "pvip-1"]))
    assert json.loads(result.output)["private_ip"] == "10.20.0.50"
    result = run(["vpcs", "virtual-ips", "get", "vpc-1", "pvip-9"])
    assert result.exit_code == 1 and "Not found (404)" in plain(result)


def test_virtual_ip_create_rules(gw):
    gw.on("GET", f"{V}/subnets/sub-1", subnet())
    gw.on("GET", f"{V}/nodes", [node(), node("vm-3", "10.20.0.12", connectivity="private")])
    create = ["vpcs", "virtual-ips", "create", "vpc-1", "--subnet-id", "sub-1"]
    usage(run([*create, "--private-ip", "10.20.0.1", "--announcer-vm-id", "vm-1"]), "subnet gateway", gw)
    usage(run([*create, "--private-ip", "10.20.0.50"]), "announcer", gw)
    usage(run([*create, "--private-ip", "10.20.0.50", "--announcer-vm-id", "vm-3"]), "NAT", gw)
    assert gw.writes() == []

    gw.on("POST", f"{V}/virtual-ips", vip())
    ok(run([*create, "--private-ip", "10.20.0.50", "--announcer-vm-id", "vm-1", "--announcer-vm-id", "vm-1"]))
    assert gw.last("POST", f"{V}/virtual-ips").json == {
        "subnet_id": "sub-1",
        "private_ip": "10.20.0.50",
        "purpose": "metallb",
        "announcer_vm_ids": ["vm-1"],
    }


def test_virtual_ip_delete_guards(gw):
    gw.on("GET", f"{V}/virtual-ips", [vip(public_ip_id="rip-1")])
    usage(run(["vpcs", "virtual-ips", "delete", "vpc-1", "pvip-1", "--yes"]), "Detach the Reserved IP", gw)

    gw.routes[("GET", f"{V}/virtual-ips")] = [[vip()]]
    gw.on("GET", f"{V}/nat-gateways", [gateway_record()])
    gw.on("GET", RULES, [pf_rule(internal_ip="10.20.0.50")])
    usage(run(["vpcs", "virtual-ips", "delete", "vpc-1", "pvip-1", "--yes"]), "port-forwarding rules", gw)
    assert gw.writes() == []

    gw.routes[("GET", RULES)] = [[]]
    gw.on("DELETE", f"{V}/virtual-ips/pvip-1")
    ok(run(["vpcs", "virtual-ips", "delete", "vpc-1", "pvip-1", "--yes"]))
    assert [(c.method, c.path) for c in gw.writes()] == [("DELETE", f"{V}/virtual-ips/pvip-1")]


def test_virtual_ip_attach_and_detach_reserved_ip(gw):
    gw.on("GET", f"{V}/virtual-ips", [vip()])
    gw.on("GET", V, vpc(nat_gateways=[gateway_record()]))
    gw.on("GET", RIP, rip())
    gw.on("POST", f"{RIP}/attach-virtual-ip", rip(attached_resource_type="vpc_virtual_ip"))
    ok(run(["vpcs", "virtual-ips", "attach-ip", "vpc-1", "pvip-1", "--reserved-ip-id", "rip-1"]))
    assert gw.last("POST", f"{RIP}/attach-virtual-ip").json == {"virtual_ip_id": "pvip-1"}

    gw.routes[("GET", V)] = [vpc()]
    usage(run(["vpcs", "virtual-ips", "attach-ip", "vpc-1", "pvip-1", "--reserved-ip-id", "rip-1"]),
          "available NAT gateway", gw)

    usage(run(["vpcs", "virtual-ips", "detach-ip", "vpc-1", "pvip-1", "--yes"]), "no Reserved IP attached", gw)
    gw.routes[("GET", f"{V}/virtual-ips")] = [[vip(public_ip_id="rip-1")]]
    gw.on("POST", f"{RIP}/detach", rip())
    ok(run(["vpcs", "virtual-ips", "detach-ip", "vpc-1", "pvip-1", "--yes"]))
    assert gw.writes()[-1].path == f"{RIP}/detach"


# ---------------------------------------------------------------------------
# Reserved IPs
# ---------------------------------------------------------------------------


def test_reserve_ip_body_and_billing_check(gw):
    gw.on("POST", "billing/resource-eligibility", decision("RESERVED-IP"))
    gw.on("POST", "networking/reserved-ips", rip())
    result = ok(run(["reserved-ips", "reserve", "--site-id", " site-1 ", "--label", "  edge  ", "--billing-catalog",
                     json.dumps({**RIP_CATALOG, "billing_options": [1]}), "--check-billing"]))
    assert json.loads(result.output)["public_ip_id"] == "rip-1"
    assert gw.writes()[0].json == {"sku_code": "RESERVED-IP"}
    assert gw.last("POST", "networking/reserved-ips").json == {
        "site_id": "site-1",
        "label": "edge",
        "billing_catalog": RIP_CATALOG,
    }


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--site-id", "  "], "Choose a location for the Reserved IP."),
        (["--site-id", "s", "--label", "x" * 121], "120"),
        (["--site-id", "s", "--billing-catalog", '{"sku_code":"RESERVED-IP"}'], "sku_id"),
    ],
)
def test_reserve_ip_validation(gw, args, message):
    usage(run(["reserved-ips", "reserve", *args]), message, gw)
    assert gw.calls == []


def test_reserve_ip_edge_denial_names_the_reserved_ip(gw):
    gw.on("POST", "networking/reserved-ips", (402, {"error": "billing_denied", "billing_reason": "insufficient_balance",
                                                     "billing_sku_code": "RESERVED-IP"}))
    result = run(["reserved-ips", "reserve", "--site-id", "site-1"])
    assert result.exit_code == 1
    assert "does not cover this Reserved IP" in plain(result)
    assert "Add credits in the IBEE portal" in plain(result)


def test_convert_runs_billing_check_by_default(gw):
    gw.on("POST", "billing/resource-eligibility", decision("RESERVED-IP"))
    gw.on("POST", "networking/reserved-ips/convert", rip(allocation_method="converted"))
    ok(run(["reserved-ips", "convert", "--vm-id", "vm-1", "--site-id", "site-1", "--label", "web"]))
    assert [c.path for c in gw.writes()] == ["billing/resource-eligibility", "networking/reserved-ips/convert"]
    assert gw.last("POST", "networking/reserved-ips/convert").json == {
        "vm_id": "vm-1", "site_id": "site-1", "label": "web"
    }
    gw.calls.clear()
    ok(run(["reserved-ips", "convert", "--vm-id", "vm-1", "--site-id", "site-1", "--no-billing-check"]))
    assert [c.path for c in gw.writes()] == ["networking/reserved-ips/convert"]


def test_convert_billing_denial_stops_before_convert(gw):
    gw.on("POST", "billing/resource-eligibility", decision("RESERVED-IP", False, "initial_topup_required"))
    result = run(["reserved-ips", "convert", "--vm-id", "vm-1", "--site-id", "site-1"])
    assert result.exit_code == 1
    assert [c.path for c in gw.writes()] == ["billing/resource-eligibility"]


def test_release_refuses_while_attached(gw):
    gw.on("GET", RIP, rip(attached_resource_type="nat_gateway", attached_resource_id="nat-1"))
    usage(run(["reserved-ips", "release", "rip-1", "--yes"]), "Change or delete the NAT Gateway", gw)
    gw.routes[("GET", RIP)] = [rip(attached_resource_type="vm", attached_resource_id="vm-1")]
    usage(run(["reserved-ips", "release", "rip-1", "--yes"]), "Detach this IP before releasing it.", gw)
    assert gw.writes() == []

    gw.routes[("GET", RIP)] = [rip()]
    gw.on("DELETE", RIP)
    result = ok(run(["reserved-ips", "release", "rip-1", "--yes"]))
    assert "released" in plain(result)


def test_update_reverse_dns_rules(gw):
    usage(run(["reserved-ips", "update", "rip-1", "--reverse-dns", "bad_host!.example.com"]), "valid FQDN", gw)
    assert gw.calls == []
    gw.on("PATCH", RIP, rip())
    ok(run(["reserved-ips", "update", "rip-1", "--reverse-dns", ""]))
    assert gw.last("PATCH", RIP).json == {"reverse_dns": ""}


def test_attach_rules_and_detach_from_service(gw):
    gw.on("GET", RIP, rip(attached_resource_type="nat_gateway", attached_resource_id="nat-1"))
    usage(run(["reserved-ips", "attach", "rip-1", "vm-1"]), "detach_from_service", gw)
    gw.routes[("GET", RIP)] = [rip(attached_resource_type="vm", attached_resource_id="vm-2")]
    usage(run(["reserved-ips", "attach", "rip-1", "vm-1"]), "use move", gw)
    assert gw.writes() == []

    gw.routes[("GET", RIP)] = [rip(attached_resource_type="nat_gateway", attached_resource_id="nat-1")]
    gw.on("POST", f"{RIP}/detach", rip()).on("POST", f"{RIP}/attach", rip(status="attached"))
    ok(run(["reserved-ips", "attach", "rip-1", "vm-1", "--detach-from-service"]))
    assert [c.path for c in gw.writes()] == [f"{RIP}/detach", f"{RIP}/attach"]


def test_attach_to_a_vm_outside_a_vpc_points_at_convert(gw):
    gw.on("POST", f"{RIP}/attach", (404, {"detail": "Reserved IP attach and move require a VPC network allocation"}))
    result = run(["reserved-ips", "attach", "rip-1", "vm-1", "--no-check-state"])
    assert result.exit_code == 1
    assert "ibee reserved-ips convert" in plain(result)


def test_move_and_detach_rules(gw):
    gw.on("GET", RIP, rip())
    usage(run(["reserved-ips", "move", "rip-1", "vm-2"]), "use attach", gw)
    gw.routes[("GET", RIP)] = [rip(attached_resource_type="vm", attached_resource_id="vm-2",
                                   attached_allocation_id="alloc-1")]
    usage(run(["reserved-ips", "move", "rip-1", "vm-2"]), "already attached to that VM", gw)

    # An unattached IP is returned as-is without a detach request.
    gw.routes[("GET", RIP)] = [rip()]
    ok(run(["reserved-ips", "detach", "rip-1"]))
    gw.routes[("GET", RIP)] = [rip(attached_resource_type="vm", attached_resource_id="vm-2",
                                   allocation_method="converted")]
    usage(run(["reserved-ips", "detach", "rip-1"]), "active public IP", gw)
    assert gw.writes() == []


def test_attach_virtual_ip_and_hidden_alias(gw):
    gw.on("GET", RIP, rip())
    gw.on("POST", f"{RIP}/attach-virtual-ip", rip(attached_resource_type="vpc_virtual_ip"))
    ok(run(["reserved-ips", "attach-virtual-ip", "rip-1", "--virtual-ip-id", "pvip-1"]))
    ok(run(["reserved-ips", "attach-vip", "rip-1", "--virtual-ip-id", "pvip-1"]))
    assert [c.json for c in gw.writes()] == [{"virtual_ip_id": "pvip-1"}] * 2
    gw.routes[("GET", RIP)] = [rip(attached_resource_type="vm", attached_resource_id="vm-1")]
    usage(run(["reserved-ips", "attach-virtual-ip", "rip-1", "--virtual-ip-id", "pvip-1"]), "not available", gw)


# ---------------------------------------------------------------------------
# Firewalls
# ---------------------------------------------------------------------------


def test_firewall_summary_list_pages_like_the_portal(gw):
    gw.on("GET", FW, [summary(i) for i in range(6)])
    result = ok(run(["-o", "table", "firewalls", "list", "--summary", "--limit", "5"]))
    assert gw.calls[0].params == {"workspace_id": "973318", "summary": "true", "limit": "6", "offset": "0"}
    assert "More results: use --offset 5" in plain(result)
    assert "fw-4" in plain(result) and "fw-5" not in plain(result)
    usage(run(["firewalls", "list", "--all", "--limit", "5"]), "--all", gw)
    usage(run(["firewalls", "list", "--limit", "101"]), "limit", gw)


def test_firewall_create_rules(gw):
    gw.on("GET", FW, [summary(1, "Web")])
    usage(run(["firewalls", "create", "  web "]), "already exists", gw)
    usage(run(["firewalls", "create", "x" * 121]), "120", gw)
    usage(run(["firewalls", "create", "db", "--default", "--no-check-state"]), "platform", gw)
    assert gw.writes() == []
    gw.on("POST", FW, group(name="db"))
    ok(run(["firewalls", "create", "db"]))
    assert gw.last("POST", FW).json == {"name": "db"}


def test_firewall_rule_port_range_and_sources(gw):
    gw.on("POST", f"{FW}/fw-1/rules", group())
    ok(run(["firewalls", "rules", "create", "fw-1", "--port", "8000-8080", "--source", "10.0.0.5, 10.1.2.0/16",
            "--source", "10.0.0.5"]))
    assert gw.last("POST", f"{FW}/fw-1/rules").json == {
        "direction": "ingress",
        "protocol": "tcp",
        "port_start": 8000,
        "port_end": 8080,
        "remote_targets": ["10.0.0.5/32", "10.1.0.0/16"],
        "action": "allow",
    }
    ok(run(["firewalls", "rules", "create", "fw-1", "--protocol", "icmp"]))
    assert gw.last("POST", f"{FW}/fw-1/rules").json["remote_targets"] == ["0.0.0.0/0"]
    assert "port_start" not in gw.last("POST", f"{FW}/fw-1/rules").json


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--protocol", "tcp"], "Port is required for TCP and UDP rules."),
        (["--protocol", "icmp", "--port", "22"], "port"),
        (["--protocol", "gre"], "Any, TCP, UDP, and ICMP"),
        (["--port", "22", "--port-start", "22"], "not both"),
        (["--port", "90-80"], "greater than or equal"),
        (["--port", "abc"], "single port like 22"),
        (["--port", "22", "--source", "2001:db8::/32"], "IPv4"),
        (["--port", "22", "--action", "reject"], "action"),
    ],
)
def test_firewall_rule_validation(gw, args, message):
    usage(run(["firewalls", "rules", "create", "fw-1", *args]), message, gw)
    assert gw.calls == []


def test_firewall_system_managed_rules_are_read_only(gw):
    gw.on("GET", f"{FW}/fw-1", group())
    usage(run(["firewalls", "rules", "update", "fw-1", "sys-1", "--disabled"]), "System-managed", gw)
    usage(run(["firewalls", "rules", "delete", "fw-1", "sys-1", "--yes"]), "System-managed", gw)
    assert gw.writes() == []
    gw.on("DELETE", f"{FW}/fw-1/rules/r-1", group())
    ok(run(["firewalls", "rules", "delete", "fw-1", "r-1", "--yes"]))


def test_firewall_attachments_paging_and_attach_hint(gw):
    usage(run(["firewalls", "attachments", "list", "fw-1", "--limit", "501"]), "limit", gw)
    gw.on("GET", f"{FW}/fw-1/attachments", [{"network_id": "n-1", "vm_id": "vm-1"}])
    ok(run(["firewalls", "attachments", "list", "fw-1", "--limit", "500", "--skip", "0"]))
    assert gw.calls[-1].params == {"workspace_id": "973318", "limit": "500", "skip": "0"}

    gw.on("POST", f"{FW}/fw-1/attachments",
          (400, {"detail": "Only OVS/OVN-backed VM networks can attach firewall groups"}))
    result = run(["firewalls", "attachments", "attach", "fw-1", "vm-1"])
    assert result.exit_code == 1
    assert "OVS/OVN-backed and active" in plain(result)


# ---------------------------------------------------------------------------
# Load balancers
# ---------------------------------------------------------------------------


def test_create_l4_from_flags_uses_portal_shapes(gw):
    gw.on("POST", f"{LB}/l4", lb(layer="l4", protocol="tls_passthrough"))
    ok(run(["load-balancers", "create-l4", "edge", "--protocol", "tls_passthrough",
            "--backend", "ip:10.0.0.5:443", "--backend", "ip:[2001:db8::1]:443:50", "--backend",
            "hostname:api.example.com:8443:10:tls", "--algorithm", "least_request", "--retries", "3",
            "--health-check-type", "tcp", "--logs"]))
    assert gw.last("POST", f"{LB}/l4").json == {
        "name": "edge",
        "protocol": "tls_passthrough",
        "backends": [
            {"type": "ip", "target": "10.0.0.5", "port": 443, "weight": 100, "tls": False},
            {"type": "ip", "target": "2001:db8::1", "port": 443, "weight": 50, "tls": False},
            {"type": "hostname", "target": "api.example.com", "port": 8443, "weight": 10, "tls": True},
        ],
        "routing": {"algorithm": "least_request"},
        "policy": {
            "timeout_ms": 30000,
            "retries": {"attempts": 3, "per_retry_timeout_ms": 5000, "on": ["5xx", "reset", "connect-failure"]},
            "proxy_protocol_enabled": False,
        },
        "health_check": {"active": {"type": "tcp", "interval_ms": 10000, "timeout_ms": 2000,
                                    "healthy_threshold": 2, "unhealthy_threshold": 3}},
        "tls": {"mode": "passthrough", "certificate_source": "managed"},
        "observability": {"logs_enabled": True},
    }


def test_create_l7_rules_sticky_and_billing_check(gw):
    gw.on("POST", "billing/resource-eligibility", decision("LOADBALA-STD"))
    gw.on("POST", f"{LB}/l7", lb())
    ok(run(["--check-billing", "load-balancers", "create-l7", "web", "--protocol", "https", "--backend",
            "service:api:8080", "--sticky-header", "X-User-ID", "--rule", "1:/", "--rule", "2:/api:X-Env=beta",
            "--health-check-path", "/ready"]))
    assert gw.writes()[0].json == {"sku_code": "LOADBALA-STD"}
    body = gw.last("POST", f"{LB}/l7").json
    assert body["routing"] == {"sticky_header": "X-User-ID"}
    assert body["rules"] == [
        {"priority": 1, "path_prefix": "/"},
        {"priority": 2, "path_prefix": "/api", "headers": {"X-Env": "beta"}},
    ]
    assert body["health_check"]["active"]["type"] == "http"
    assert body["health_check"]["active"]["path"] == "/ready"
    assert body["tls"] == {"mode": "terminate", "certificate_source": "managed"}


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["create-l7", "web", "--backend", "ip:10.0.0.5:80", "--custom-domain", "a.example.com"], "https"),
        (["create-l7", "web", "--protocol", "https", "--backend", "ip:10.0.0.5:80", "--tls",
          '{"mode":"terminate","certificate_source":"custom"}'], "Custom certificates"),
        (["create-l4", "edge", "--backend", "service:api/v1:80"], "Kubernetes service name"),
        (["create-l4", "edge", "--backend", "ip:10.0.0.5:80:5000"], "weight"),
        (["create-l4", "edge", "--backend", "ip:10.0.0.5"], "TYPE:TARGET:PORT"),
        (["create-l4", "edge"], "--backend"),
        (["create-l4", "edge", "--backend", "ip:10.0.0.5:80", "--backends", "[]"], "not both"),
        (["create-l4", "edge", "--backend", "ip:10.0.0.5:80", "--routing", '{"sticky_header":"X"}'], "L7"),
        (["create-l4", "edge", "--backend", "ip:10.0.0.5:80", "--health-check-type", "tcp",
          "--health-check-path", "/x"], "tcp"),
        (["create-l4", "edge", "--backend", "ip:10.0.0.5:80", "--timeout-ms", "50"], "timeout_ms"),
        (["create-l7", "web", "--backend", "ip:10.0.0.5:80", "--rule", "0:/"], "positive"),
        (["create-l7", "web", "--backend", "ip:10.0.0.5:80", "--rule", "1:api"], "start with '/'"),
    ],
)
def test_load_balancer_validation_exits_2(gw, args, message):
    usage(run(["load-balancers", *args]), message, gw)
    assert gw.calls == []


def test_update_l7_custom_domain_clear_and_policy(gw):
    gw.on("PATCH", f"{LB}/l7/lb-1", lb())
    ok(run(["load-balancers", "update-l7", "lb-1", "--clear-custom-domain", "--timeout-ms", "60000"]))
    assert gw.last("PATCH", f"{LB}/l7/lb-1").json == {
        "policy": {"timeout_ms": 60000, "proxy_protocol_enabled": False},
        "custom_domain": None,
    }
    usage(run(["load-balancers", "update-l7", "lb-1", "--clear-custom-domain", "--custom-domain", "a.example.com"]),
          "not both", gw)


def test_list_and_get_include_deleted(gw):
    gw.on("GET", LB, [lb(status="deleted")])
    result = ok(run(["-o", "id", "load-balancers", "list", "--status", "deleted"]))
    assert result.output.strip() == "lb-1"
    assert gw.calls[-1].params == {"workspace_id": "973318", "status": "deleted", "include_deleted": "true"}
    usage(run(["load-balancers", "list", "--layer", "l4", "--protocol", "https"]), "protocol", gw)

    gw.on("GET", f"{LB}/lb-1", lb())
    ok(run(["load-balancers", "get", "lb-1", "--include-deleted"]))
    assert gw.calls[-1].params == {"workspace_id": "973318", "include_deleted": "true"}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_reserved_ip_target_error_lines_point_at_the_cli_command():
    error = ReservedIpTargetUnsupportedError(headers={}, body={"detail": "require a VPC network allocation"})
    assert "ibee reserved-ips convert" in api_error_lines(error)[0]
    assert api_error_lines(NotFoundError(headers={}, body={"detail": "x"}))[0].startswith("Not found (404)")
