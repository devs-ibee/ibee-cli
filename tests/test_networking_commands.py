"""Request-shape tests for public networking CLI commands."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from ibee_cli.main import app

runner = CliRunner()
BASE_ARGS = [
    "--token",
    "test-token",
    "--workspace",
    "973318",
    "--base-url",
    "https://gateway.example/v1",
]


class FakeResponse:
    def __init__(self, payload=None, status_code: int = 200):
        self.payload = {} if payload is None else payload
        self.status_code = status_code
        self.content = b"" if status_code == 204 else b"{}"
        self.text = "" if status_code == 204 else "{}"

    def json(self):
        return self.payload


@pytest.fixture
def requests(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        status = 204 if method == "DELETE" else 200
        return FakeResponse(status_code=status)

    monkeypatch.setattr("ibee_cli.context.httpx.request", fake_request)
    return calls


def invoke(requests, args):
    result = runner.invoke(app, [*BASE_ARGS, *args])
    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    call = requests[0]
    assert call["params"]["workspace_id"] == "973318"
    assert call["headers"]["Authorization"] == "Bearer test-token"
    assert call["timeout"] == 30
    return call


def test_create_vpc_request(requests):
    call = invoke(
        requests,
        [
            "vpcs",
            "create",
            "private-apps",
            "--site-id",
            "blr-1",
            "--cidr",
            "10.44.0.0/22",
            "--no-auto-cidr",
            "--connectivity",
            "nat_gateway",
        ],
    )
    assert call["method"] == "POST"
    assert call["url"] == "https://gateway.example/v1/networking/vpcs"
    assert call["json"]["name"] == "private-apps"
    assert call["json"]["site_id"] == "blr-1"
    assert call["json"]["cidr"] == "10.44.0.0/22"
    assert call["json"]["auto_cidr"] is False
    assert call["json"]["connectivity_type"] == "nat_gateway"


def test_update_subnet_request(requests):
    call = invoke(
        requests,
        [
            "vpcs",
            "subnets",
            "update",
            "vpc-1",
            "subnet-1",
            "--name",
            "services",
            "--dns",
            "1.1.1.1",
            "--dns",
            "8.8.8.8",
        ],
    )
    assert call["method"] == "PATCH"
    assert call["url"].endswith("/networking/vpcs/vpc-1/subnets/subnet-1")
    assert call["json"] == {
        "name": "services",
        "dns": ["1.1.1.1", "8.8.8.8"],
    }


def test_delete_vpc_request_and_empty_response(requests):
    call = invoke(requests, ["vpcs", "delete", "vpc-1", "--yes"])
    assert call["method"] == "DELETE"
    assert call["url"].endswith("/networking/vpcs/vpc-1")
    assert call["json"] is None


def test_attach_node_request(requests):
    call = invoke(
        requests,
        [
            "vpcs",
            "nodes",
            "attach",
            "vpc-1",
            "vm-1",
            "--subnet-id",
            "subnet-1",
            "--connectivity",
            "public_ip",
            "--reserved-ip-id",
            "ip-1",
        ],
    )
    assert call["method"] == "POST"
    assert call["url"].endswith("/networking/vpcs/vpc-1/nodes")
    assert call["json"] == {
        "vm_id": "vm-1",
        "subnet_id": "subnet-1",
        "connectivity": "public_ip",
        "reserved_public_ip_id": "ip-1",
    }


@pytest.mark.parametrize(
    ("args", "method", "path_suffix", "payload"),
    [
        (
            [
                "vpcs",
                "nat",
                "create",
                "vpc-1",
                "--name",
                "egress",
                "--reserved-ip-id",
                "ip-1",
            ],
            "POST",
            "/networking/vpcs/vpc-1/nat-gateways",
            {"name": "egress", "reserved_public_ip_id": "ip-1"},
        ),
        (
            [
                "vpcs",
                "forwarding",
                "create",
                "vpc-1",
                "nat-1",
                "ssh",
                "--external-port",
                "2222",
                "--internal-ip",
                "10.44.0.10",
                "--internal-port",
                "22",
            ],
            "POST",
            (
                "/networking/vpcs/vpc-1/nat-gateways/nat-1"
                "/port-forwarding-rules"
            ),
            {
                "name": "ssh",
                "external_port": 2222,
                "internal_ip": "10.44.0.10",
                "internal_port": 22,
                "protocol": "tcp",
                "note": "",
                "enabled": True,
            },
        ),
        (
            [
                "vpcs",
                "forwarding",
                "update",
                "vpc-1",
                "nat-1",
                "rule-1",
                "--external-port",
                "2200",
                "--disabled",
            ],
            "PATCH",
            (
                "/networking/vpcs/vpc-1/nat-gateways/nat-1"
                "/port-forwarding-rules/rule-1"
            ),
            {"external_port": 2200, "enabled": False},
        ),
    ],
)
def test_nat_and_forwarding_requests(
    requests, args, method, path_suffix, payload
):
    call = invoke(requests, args)
    assert call["method"] == method
    assert call["url"].endswith(path_suffix)
    assert call["json"] == payload


def test_reserved_ip_attach_request(requests):
    call = invoke(
        requests,
        [
            "reserved-ips",
            "attach",
            "ip-1",
            "vm-1",
            "--vpc-id",
            "vpc-1",
            "--subnet-id",
            "subnet-1",
        ],
    )
    assert call["method"] == "POST"
    assert call["url"].endswith("/networking/reserved-ips/ip-1/attach")
    assert call["json"] == {
        "vm_id": "vm-1",
        "vpc_id": "vpc-1",
        "subnet_id": "subnet-1",
    }


def test_reserved_ip_move_request(requests):
    call = invoke(
        requests,
        [
            "reserved-ips",
            "move",
            "ip-1",
            "vm-2",
            "--vpc-id",
            "vpc-2",
            "--subnet-id",
            "subnet-2",
        ],
    )
    assert call["method"] == "POST"
    assert call["url"].endswith("/networking/reserved-ips/ip-1/move")
    assert call["json"] == {
        "vm_id": "vm-2",
        "vpc_id": "vpc-2",
        "subnet_id": "subnet-2",
    }


def test_firewall_rule_request(requests):
    call = invoke(
        requests,
        [
            "firewalls",
            "rules",
            "create",
            "fw-1",
            "--protocol",
            "tcp",
            "--port-start",
            "443",
            "--port-end",
            "443",
            "--remote-target",
            "0.0.0.0/0",
            "--action",
            "allow",
        ],
    )
    assert call["method"] == "POST"
    assert call["url"].endswith("/networking/firewall-groups/fw-1/rules")
    assert call["json"] == {
        "direction": "ingress",
        "protocol": "tcp",
        "port_start": 443,
        "port_end": 443,
        "remote_targets": ["0.0.0.0/0"],
        "action": "allow",
    }


def test_firewall_attachment_request(requests):
    call = invoke(
        requests,
        ["firewalls", "attachments", "attach", "fw-1", "vm-1"],
    )
    assert call["method"] == "POST"
    assert call["url"].endswith("/networking/firewall-groups/fw-1/attachments")
    assert call["json"] == {"vm_id": "vm-1"}


def test_create_l7_load_balancer_request(requests):
    call = invoke(
        requests,
        [
            "load-balancers",
            "create-l7",
            "web",
            "--protocol",
            "https",
            "--backends",
            '[{"type":"ip","target":"10.0.0.5","port":443}]',
            "--routing",
            '{"algorithm":"least_request"}',
            "--custom-domain",
            "app.example.com",
        ],
    )
    assert call["method"] == "POST"
    assert call["url"].endswith("/networking/load-balancers/l7")
    assert call["json"] == {
        "name": "web",
        "protocol": "https",
        "backends": [{"type": "ip", "target": "10.0.0.5", "port": 443}],
        "routing": {"algorithm": "least_request"},
        "custom_domain": {"hostname": "app.example.com"},
    }


def test_update_and_status_load_balancer_requests(requests):
    call = invoke(
        requests,
        [
            "load-balancers",
            "update-l4",
            "lb-1",
            "--name",
            "edge",
            "--backends",
            '[{"type":"ip","target":"10.0.0.6","port":80}]',
        ],
    )
    assert call["method"] == "PATCH"
    assert call["url"].endswith("/networking/load-balancers/l4/lb-1")
    assert call["json"]["name"] == "edge"

    requests.clear()
    call = invoke(requests, ["load-balancers", "status", "lb-1"])
    assert call["method"] == "GET"
    assert call["url"].endswith("/networking/load-balancers/lb-1/status")


@pytest.mark.parametrize(
    "args",
    [
        ["vpcs", "update", "vpc-1"],
        ["vpcs", "subnets", "update", "vpc-1", "subnet-1"],
        ["firewalls", "rules", "update", "fw-1", "rule-1"],
        ["load-balancers", "update-l4", "lb-1"],
    ],
)
def test_update_commands_require_changes(requests, args):
    result = runner.invoke(app, [*BASE_ARGS, *args])
    assert result.exit_code != 0
    assert "at least one update option" in result.output
    assert requests == []
