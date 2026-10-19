"""``ibee vpcs``, ``reserved-ips``, ``firewalls`` and ``load-balancers`` against a scripted gateway.

Each command makes one SDK call and sends only what was typed; the server's answer
(or error) is printed as received.
"""

from __future__ import annotations

import json

import pytest
from click import unstyle
from _net_fixtures import (
    FW, LB, NAT_CATALOG, RIP, RIP_CATALOG, V, WS, gateway_record, group, lb, pf_rule, plain, rip, run, subnet, vpc,
)

from ibee_cli.render import EXIT_CODES, identifiers

BACKENDS = [{"type": "ip", "target": "10.0.0.5", "port": 80, "weight": 5}, {"target": "web.internal", "port": 8080}]


def _json(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


# -- lists: table and -o json ---------------------------------------------------------


@pytest.mark.parametrize(
    "args, path, record, needle",
    [
        (["vpcs", "list"], "networking/vpcs", vpc(), "vpc-1"),
        (["reserved-ips", "list"], "networking/reserved-ips", rip(), "rip-1"),
        (["load-balancers", "list"], LB, lb(), "lb-1"),
    ],
)
def test_list_table_and_json(gw, args, path, record, needle):
    gw.on("GET", path, (200, [record]))

    table = run(["-o", "table", *args])
    assert table.exit_code == 0, table.output
    assert needle in plain(table)

    assert _json(run(["-o", "json", *args])) == json.loads(json.dumps([record]))
    assert [(c.method, c.path, c.params) for c in gw.calls] == [("GET", path, {"workspace_id": WS})] * 2


def test_firewall_list_collects_every_page_and_limit_sends_one_page(gw):
    gw.on("GET", FW, (200, [group()]))

    table = run(["-o", "table", "firewalls", "list"])
    assert table.exit_code == 0, table.output
    assert "fw-1" in plain(table)
    assert gw.calls[-1].params == {"workspace_id": WS, "limit": "100", "offset": "0"}

    assert _json(run(["-o", "json", "firewalls", "list", "--limit", "5"]))[0]["firewall_group_id"] == "fw-1"
    assert gw.calls[-1].params == {"workspace_id": WS, "limit": "5"}


def test_firewall_list_all_with_limit_is_a_usage_error(gw):
    result = run(["firewalls", "list", "--all", "--limit", "5"])
    assert result.exit_code == 2
    assert gw.calls == []


# -- VPCs and NAT gateways ------------------------------------------------------------


def test_vpc_create_sends_only_typed_fields(gw):
    gw.on("POST", "networking/vpcs", (201, vpc()))

    assert _json(run(["vpcs", "create", "net", "--site-id", "site-1"]))["vpc_id"] == "vpc-1"
    assert gw.last("POST", "networking/vpcs").json == {"name": "net", "site_id": "site-1"}


def test_vpc_create_with_nat_billing_catalog(gw, tmp_path):
    gw.on("POST", "networking/vpcs", (201, vpc()))
    args = ["vpcs", "create", "net", "--site-id", "site-1", "--connectivity", "nat_gateway"]

    _json(run([*args, "--nat-billing-catalog", json.dumps(NAT_CATALOG)]))
    expected = {"name": "net", "site_id": "site-1", "connectivity_type": "nat_gateway", "nat_billing_catalog": NAT_CATALOG}
    assert gw.last("POST", "networking/vpcs").json == expected

    path = tmp_path / "nat.json"
    path.write_text(json.dumps(NAT_CATALOG))
    _json(run([*args, "--nat-billing-catalog-file", str(path)]))
    assert gw.last("POST", "networking/vpcs").json == expected
    assert len(gw.calls) == 2


def test_nat_create_passes_billing_catalog_in_one_call(gw):
    gw.on("POST", f"{V}/nat-gateways", (201, gateway_record()))

    result = run(["vpcs", "nat", "create", "vpc-1", "--billing-catalog", json.dumps(NAT_CATALOG)])
    assert _json(result)["nat_gateway_id"] == "nat-1"
    assert [(c.method, c.path, c.json) for c in gw.calls] == [("POST", f"{V}/nat-gateways", {"billing_catalog": NAT_CATALOG})]


def test_nat_delete_passes_public_ip_action_and_catalog(gw):
    gw.on("DELETE", f"{V}/nat-gateways/nat-1", (204, None))

    result = run([
        "vpcs", "nat", "delete", "vpc-1", "nat-1", "--ip-action", "reserve",
        "--billing-catalog", json.dumps(RIP_CATALOG), "--yes",
    ])
    assert result.exit_code == 0, result.output
    assert "deletion accepted" in plain(result)
    assert [(c.method, c.path, c.json) for c in gw.calls] == [
        ("DELETE", f"{V}/nat-gateways/nat-1", {"public_ip_action": "reserve", "billing_catalog": RIP_CATALOG})
    ]


@pytest.mark.parametrize("command, enabled", [("enable", True), ("disable", False)])
def test_forwarding_enable_disable_is_one_update(gw, command, enabled):
    path = f"{V}/nat-gateways/nat-1/port-forwarding-rules/natpf-1"
    gw.on("PATCH", path, (200, pf_rule(enabled=enabled)))

    assert _json(run(["vpcs", "forwarding", command, "vpc-1", "nat-1", "natpf-1"]))["enabled"] is enabled
    assert [(c.method, c.path, c.json) for c in gw.calls] == [("PATCH", path, {"enabled": enabled})]


# -- firewall rules, reserved IPs, load balancers -------------------------------------


def test_firewall_rule_create_is_sent_as_typed(gw):
    gw.on("POST", f"{FW}/fw-1/rules", (201, group()))

    result = run([
        "firewalls", "rules", "create", "fw-1", "--protocol", "tcp", "--port", "70000-80000",
        "--source", "10.0.0.0/8,192.168.1.1", "--source", "bad-source",
    ])
    assert _json(result)["firewall_group_id"] == "fw-1"
    assert gw.last("POST", f"{FW}/fw-1/rules").json == {
        "protocol": "tcp",
        "port_start": 70000,
        "port_end": 80000,
        "remote_targets": ["10.0.0.0/8", "192.168.1.1", "bad-source"],
    }
    assert len(gw.calls) == 1


def test_reserved_ip_attach_is_one_call(gw):
    gw.on("POST", f"{RIP}/attach", (200, rip(status="attached", attached_resource_id="vm-1")))

    assert _json(run(["reserved-ips", "attach", "rip-1", "vm-1", "--vpc-id", "vpc-1"]))["public_ip_id"] == "rip-1"
    assert [(c.method, c.path, c.json) for c in gw.calls] == [("POST", f"{RIP}/attach", {"vm_id": "vm-1", "vpc_id": "vpc-1"})]


def test_load_balancer_create_sends_json_backends_unchanged(gw):
    gw.on("POST", f"{LB}/l7", (201, lb()))

    result = run(["load-balancers", "create-l7", "web", "--protocol", "https", "--backends", json.dumps(BACKENDS)])
    assert _json(result)["lb_id"] == "lb-1"
    assert gw.last("POST", f"{LB}/l7").json == {"name": "web", "protocol": "https", "backends": BACKENDS}


L7_RULES = [{"priority": 1, "path_prefix": "/api", "backends": BACKENDS[:1]}]


@pytest.mark.parametrize(
    "args, method, path, record, expected",
    [
        (["firewalls", "create", "web"], "POST", FW, group(), "fw-1"),
        (["firewalls", "get", "fw-1"], "GET", f"{FW}/fw-1", group(), "fw-1"),
        (["load-balancers", "create-l7", "web", "--backends", json.dumps(BACKENDS)], "POST", f"{LB}/l7",
         lb(backends=BACKENDS, rules=L7_RULES), "lb-1"),
        (["load-balancers", "create-l7", "web", "--backends", "[]"], "POST", f"{LB}/l7", lb(rules=[]), "lb-1"),
        (["vpcs", "get", "vpc-1"], "GET", V, vpc(subnets=[subnet(), subnet(subnet_id="sub-2")]), "vpc-1"),
    ],
)
def test_id_output_prints_the_object_id_not_its_nested_list(gw, args, method, path, record, expected):
    gw.on(method, path, (200, record))

    result = run(["-o", "id", *args])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [expected]


def test_id_output_still_prints_one_id_per_collection_item():
    groups = {"groups": [group(), group(firewall_group_id="fw-2")]}
    assert identifiers(groups, "firewall_group_id") == ["fw-1", "fw-2"]
    assert identifiers({"rules": group()["rules"]}) == ["sys-1", "r-1"]


def test_load_balancer_repeatable_backend_and_routing(gw):
    gw.on("POST", f"{LB}/l4", (201, lb(layer="l4", protocol="tcp")))

    result = run([
        "load-balancers", "create-l4", "tcp-lb", "--backend", json.dumps(BACKENDS[0]),
        "--backend", json.dumps(BACKENDS[1]), "--algorithm", "least_request",
    ])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", f"{LB}/l4").json == {
        "name": "tcp-lb", "protocol": "tcp", "backends": BACKENDS, "routing": {"algorithm": "least_request"},
    }


def test_load_balancer_backend_and_backends_conflict_sends_nothing(gw):
    result = run(["load-balancers", "create-l4", "x", "--backends", "[]", "--backend", "{}"])
    assert result.exit_code == 2
    assert gw.calls == []


# -- confirmations ---------------------------------------------------------------------


def test_delete_with_yes_sends_the_delete(gw):
    gw.on("DELETE", V, (204, None))

    result = run(["vpcs", "delete", "vpc-1", "--yes"])
    assert result.exit_code == 0, result.output
    assert "VPC 'vpc-1' deleted." in plain(result)
    assert [(c.method, c.path) for c in gw.calls] == [("DELETE", V)]


def test_delete_without_yes_prompts_and_declining_sends_nothing(gw):
    result = run(["load-balancers", "delete", "lb-1"], input="n\n")
    assert result.exit_code == 1
    assert "Delete load balancer 'lb-1'?" in result.output
    assert gw.calls == []

    gw.on("DELETE", f"{LB}/lb-1", (204, None))
    accepted = run(["load-balancers", "delete", "lb-1"], input="y\n")
    assert accepted.exit_code == 0, accepted.output
    assert [(c.method, c.path) for c in gw.calls] == [("DELETE", f"{LB}/lb-1")]


# -- errors and removed commands --------------------------------------------------------


@pytest.mark.parametrize("status", [404, 409, 422, 503])
def test_api_error_prints_the_server_answer_with_its_exit_code(gw, status):
    body = {"error": "vpc_has_nodes", "detail": "Detach nodes before deleting this VPC."}
    gw.on("DELETE", V, (status, body, {"x-request-id": "req-42"}))

    result = run(["vpcs", "delete", "vpc-1", "--yes"])
    assert result.exit_code == (EXIT_CODES.get(status) or 19)
    err = " ".join(unstyle(result.stderr).split())
    for text in (f"HTTP {status}", "code: vpc_has_nodes", "message: Detach nodes before deleting this VPC.", "request_id: req-42"):
        assert text in err


@pytest.mark.parametrize(
    "args",
    [
        ["vpcs", "virtual-ips", "list", "vpc-1"],
        ["vpcs", "nat", "replace-ip", "vpc-1", "nat-1", "--reserved-ip-id", "rip-1"],
        ["reserved-ips", "convert", "--vm-id", "vm-1", "--site-id", "site-1"],
        ["reserved-ips", "attach-virtual-ip", "rip-1", "--virtual-ip-id", "pvip-1"],
        ["firewalls", "list", "--summary"],
        ["vpcs", "delete", "vpc-1", "--delete-nat-gateway", "--yes"],
        ["vpcs", "subnets", "create", "vpc-1", "app", "--check-state"],
        ["load-balancers", "create-l7", "web", "--backends", "[]", "--health-check-type", "http"],
    ],
)
def test_removed_commands_and_flags_are_usage_errors(gw, args):
    result = run(args)
    assert result.exit_code == 2, result.output
    assert gw.calls == []


def test_reserve_passes_the_billing_catalog_through(gw):
    gw.on("POST", "networking/reserved-ips", (201, rip()))
    sku = {"sku_id": 3, "sku_code": "RESERVED-IP"}
    result = run(["reserved-ips", "reserve", "--site-id", "site-1", "--billing-catalog", json.dumps(sku)])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", "networking/reserved-ips").json == {"site_id": "site-1", "billing_catalog": sku}
