"""Request-shape tests for public networking CLI commands (real SDK, scripted gateway)."""

from __future__ import annotations

import pytest

from _net_fixtures import (
    FW,
    LB,
    V,
    WS,
    gateway_record,
    group,
    lb,
    node,
    pf_rule,
    rip,
    run,
    subnet,
    vpc,
)


def ok(result):
    assert result.exit_code == 0, result.output
    return result


def test_create_vpc_request(gw):
    gw.on("GET", "networking/sites", [{"site_id": "blr-1", "site_name": "BLR", "available": True}])
    gw.on("POST", "networking/vpcs", vpc(site_id="blr-1", cidr="10.44.0.0/22"))
    ok(run(["vpcs", "create", "private-apps", "--site-id", "blr-1", "--cidr", "10.44.0.0/22", "--no-auto-cidr",
            "--connectivity", "nat_gateway", "--nat-billing-catalog", '{"sku_code":"NAT-GATEWAY","sku_id":7}']))
    call = gw.last("POST", "networking/vpcs")
    assert call.params == {"workspace_id": WS}
    assert call.json == {
        "name": "private-apps",
        "site_id": "blr-1",
        "connectivity_type": "nat_gateway",
        "cidr": "10.44.0.0/22",
        "auto_cidr": False,
        "create_default_subnet": True,
        "nat_billing_catalog": {"sku_code": "NAT-GATEWAY", "sku_id": 7},
    }


def test_update_subnet_request(gw):
    gw.on("PATCH", f"{V}/subnets/subnet-1", subnet(subnet_id="subnet-1"))
    ok(run(["vpcs", "subnets", "update", "vpc-1", "subnet-1", "--name", "services", "--dns", "1.1.1.1",
            "--dns", "8.8.8.8"]))
    assert gw.last("PATCH", f"{V}/subnets/subnet-1").json == {"name": "services", "dns": ["1.1.1.1", "8.8.8.8"]}


def test_delete_vpc_request_and_empty_response(gw):
    gw.on("GET", V, vpc()).on("GET", f"{V}/virtual-ips", []).on("DELETE", V)
    result = ok(run(["vpcs", "delete", "vpc-1", "--yes"]))
    delete = gw.last("DELETE", V)
    assert delete.json is None
    assert "VPC 'vpc-1' deleted." in result.output


def test_attach_node_request(gw):
    gw.on("GET", V, vpc(connectivity_type="private"))
    gw.on("POST", f"{V}/nodes", node(connectivity="public_ip", vm_id="vm-1"))
    ok(run(["vpcs", "nodes", "attach", "vpc-1", "vm-1", "--subnet-id", "subnet-1", "--connectivity", "public_ip",
            "--reserved-ip-id", "ip-1"]))
    assert gw.last("POST", f"{V}/nodes").json == {
        "vm_id": "vm-1",
        "subnet_id": "subnet-1",
        "connectivity": "public_ip",
        "reserved_public_ip_id": "ip-1",
    }


def test_nat_create_request(gw):
    gw.on("GET", V, vpc()).on("GET", "networking/reserved-ips/ip-1", rip(public_ip_id="ip-1"))
    gw.on("POST", f"{V}/nat-gateways", gateway_record(name="egress", public_ip_source="reserved"))
    ok(run(["vpcs", "nat", "create", "vpc-1", "--name", "egress", "--reserved-ip-id", "ip-1",
            "--billing-catalog", '{"sku_code":"NAT-GATEWAY"}']))
    assert gw.last("POST", f"{V}/nat-gateways").json == {
        "name": "egress",
        "reserved_public_ip_id": "ip-1",
        "billing_catalog": {"sku_code": "NAT-GATEWAY"},
    }


def test_forwarding_create_and_update_requests(gw):
    rules = f"{V}/nat-gateways/nat-1/port-forwarding-rules"
    gw.on("GET", f"{V}/nat-gateways", [gateway_record()])
    gw.on("GET", rules, [])
    gw.on("GET", f"{V}/nodes", [node(ip="10.44.0.10")])
    gw.on("POST", rules, pf_rule())
    ok(run(["vpcs", "forwarding", "create", "vpc-1", "nat-1", "ssh", "--external-port", "2222", "--internal-ip",
            "10.44.0.10", "--internal-port", "22"]))
    assert gw.last("POST", rules).json == {
        "name": "ssh",
        "protocol": "tcp",
        "external_port": 2222,
        "internal_ip": "10.44.0.10",
        "internal_port": 22,
        "target_type": "vm",
        "target_vm_ids": [],
        "note": "",
        "enabled": True,
    }

    gw.routes[("GET", rules)] = [[pf_rule(port_forward_rule_id="rule-1")]]
    gw.on("PATCH", f"{rules}/rule-1", pf_rule(port_forward_rule_id="rule-1"))
    ok(run(["vpcs", "forwarding", "update", "vpc-1", "nat-1", "rule-1", "--external-port", "2200", "--disabled"]))
    assert gw.last("PATCH", f"{rules}/rule-1").json == {"external_port": 2200, "enabled": False}


def test_reserved_ip_attach_request(gw):
    gw.on("GET", "networking/reserved-ips/ip-1", rip(public_ip_id="ip-1"))
    gw.on("POST", "networking/reserved-ips/ip-1/attach", rip(public_ip_id="ip-1", status="attached"))
    ok(run(["reserved-ips", "attach", "ip-1", "vm-1", "--vpc-id", "vpc-1", "--subnet-id", "subnet-1"]))
    assert gw.last("POST", "networking/reserved-ips/ip-1/attach").json == {
        "vm_id": "vm-1",
        "vpc_id": "vpc-1",
        "subnet_id": "subnet-1",
    }


def test_reserved_ip_move_request(gw):
    attached = rip(public_ip_id="ip-1", attached_resource_type="vm", attached_resource_id="vm-1",
                   attached_allocation_id="alloc-1")
    gw.on("GET", "networking/reserved-ips/ip-1", attached)
    gw.on("POST", "networking/reserved-ips/ip-1/move", attached)
    ok(run(["reserved-ips", "move", "ip-1", "vm-2", "--vpc-id", "vpc-2", "--subnet-id", "subnet-2"]))
    assert gw.last("POST", "networking/reserved-ips/ip-1/move").json == {
        "vm_id": "vm-2",
        "vpc_id": "vpc-2",
        "subnet_id": "subnet-2",
    }


def test_firewall_rule_request(gw):
    gw.on("POST", f"{FW}/fw-1/rules", group())
    ok(run(["firewalls", "rules", "create", "fw-1", "--protocol", "tcp", "--port-start", "443", "--port-end", "443",
            "--remote-target", "0.0.0.0/0", "--action", "allow"]))
    assert gw.last("POST", f"{FW}/fw-1/rules").json == {
        "direction": "ingress",
        "protocol": "tcp",
        "port_start": 443,
        "port_end": 443,
        "remote_targets": ["0.0.0.0/0"],
        "action": "allow",
    }


def test_firewall_attachment_request(gw):
    gw.on("POST", f"{FW}/fw-1/attachments", group())
    ok(run(["firewalls", "attachments", "attach", "fw-1", " vm-1 "]))
    assert gw.last("POST", f"{FW}/fw-1/attachments").json == {"vm_id": "vm-1"}


def test_create_l7_load_balancer_request(gw):
    gw.on("POST", f"{LB}/l7", lb())
    ok(run(["load-balancers", "create-l7", "web", "--protocol", "https", "--backends",
            '[{"type":"ip","target":"10.0.0.5","port":443}]', "--routing", '{"algorithm":"least_request"}',
            "--custom-domain", "App.Example.com."]))
    assert gw.last("POST", f"{LB}/l7").json == {
        "name": "web",
        "protocol": "https",
        "backends": [{"type": "ip", "target": "10.0.0.5", "port": 443, "weight": 100, "tls": False}],
        "routing": {"algorithm": "least_request"},
        "tls": {"mode": "terminate", "certificate_source": "managed"},
        "custom_domain": {"hostname": "app.example.com"},
    }


def test_update_and_status_load_balancer_requests(gw):
    gw.on("PATCH", f"{LB}/l4/lb-1", lb(layer="l4", protocol="tcp"))
    ok(run(["load-balancers", "update-l4", "lb-1", "--name", "edge", "--backends",
            '[{"type":"ip","target":"10.0.0.6","port":80}]']))
    assert gw.last("PATCH", f"{LB}/l4/lb-1").json["name"] == "edge"

    gw.on("GET", f"{LB}/lb-1/status", {"lb_id": "lb-1", "status": "active", "endpoint": {"host": "x", "port": 1},
                                        "url": "u", "endpoint_url": "u", "conditions": [], "updated_at": "2026-09-01T10:00:00Z"})
    ok(run(["load-balancers", "status", "lb-1"]))
    assert gw.calls[-1].method == "GET" and gw.calls[-1].path == f"{LB}/lb-1/status"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["vpcs", "update", "vpc-1"], "At least one VPC field"),
        (["vpcs", "subnets", "update", "vpc-1", "subnet-1"], "At least one subnet field"),
        (["firewalls", "rules", "update", "fw-1", "rule-1", "--no-check-state"], "at least one"),
        (["load-balancers", "update-l4", "lb-1"], "At least one load balancer field"),
        (["reserved-ips", "update", "rip-1"], "At least one Reserved IP field"),
    ],
)
def test_update_commands_require_changes(gw, args, message):
    result = run(args)
    assert result.exit_code == 2, result.output
    assert message.lower() in result.output.lower()
    assert gw.calls == []
