"""A scripted public gateway for networking CLI tests.

Commands run against the real Python SDK (``Ibee``) whose HTTP client uses an
``httpx.MockTransport``, so the SDK's portal pre-steps and validations run for real.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import httpx
from click import unstyle
from ibee import Ibee
from typer.testing import CliRunner

from ibee_cli import context
from ibee_cli.commands import firewalls, load_balancers, networking, reserved_ips
from ibee_cli.main import app

WS = "973318"
BASE_URL = "https://gateway.example/v1"
BASE = ["--token", "test-token", "--workspace", WS]
TS = "2026-09-01T10:00:00Z"
V = "networking/vpcs/vpc-1"
RIP = "networking/reserved-ips/rip-1"
FW = "networking/firewall-groups"
LB = "networking/load-balancers"
NAT_CATALOG = {"sku_id": 7, "sku_code": "NAT-GATEWAY", "unit_price_minor": 500}
RIP_CATALOG = {"sku_id": 3, "sku_code": "RESERVED-IP", "unit_price_minor": 100}

runner = CliRunner()


class Gateway:
    """Scripted responses keyed by (METHOD, path relative to /v1)."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list] = {}
        self.calls: list[SimpleNamespace] = []

    def on(self, method: str, path: str, *responses) -> "Gateway":
        self.routes.setdefault((method.upper(), path), []).extend(responses or [None])
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/v1/"):
            path = path[len("/v1/"):]
        body = json.loads(request.content) if request.content else None
        self.calls.append(
            SimpleNamespace(method=request.method, path=path, params=dict(request.url.params), json=body)
        )
        queue = self.routes.get((request.method, path))
        if not queue:
            raise AssertionError(f"unexpected request {request.method} {path}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        status, payload = item if isinstance(item, tuple) else (200, item)
        if payload is None:
            return httpx.Response(204 if status == 200 else status)
        return httpx.Response(status, json=payload)

    def client(self) -> Ibee:
        return Ibee(
            token="test-token",
            base_url=BASE_URL,
            httpx_client=httpx.Client(transport=httpx.MockTransport(self.handler)),
            max_retries=0,
        )

    def requests(self, method: str | None = None) -> list[SimpleNamespace]:
        return [call for call in self.calls if method is None or call.method == method]

    def writes(self) -> list[SimpleNamespace]:
        return [call for call in self.calls if call.method != "GET"]

    def last(self, method: str, path: str) -> SimpleNamespace:
        matches = [c for c in self.calls if c.method == method and c.path == path]
        assert matches, f"no {method} {path} in {[(c.method, c.path) for c in self.calls]}"
        return matches[-1]


def install(monkeypatch) -> Gateway:
    """Route every networking command's SDK client to a new scripted gateway."""

    gateway = Gateway()
    factory = lambda _settings: gateway.client()  # noqa: E731
    for module in (networking, reserved_ips, firewalls, load_balancers, context):
        monkeypatch.setattr(module, "get_client", factory)
    return gateway


def run(args, **kwargs):
    return runner.invoke(app, [*BASE, *args], **kwargs)


def plain(result) -> str:
    return re.sub(r"\s+", " ", unstyle(result.output))


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


def vpc(**overrides):
    record = {
        "vpc_id": "vpc-1",
        "organization_id": "org",
        "workspace_id": WS,
        "site_id": "site-1",
        "name": "net",
        "cidr": "10.20.0.0/22",
        "status": "available",
        "connectivity_type": "nat_gateway",
        "created_at": TS,
        "updated_at": TS,
        "node_count": 0,
        "subnets": [],
        "nat_gateways": [],
        "attached_nodes": [],
    }
    record.update(overrides)
    return record


def gateway_record(**overrides):
    record = {
        "nat_gateway_id": "nat-1",
        "vpc_id": "vpc-1",
        "site_id": "site-1",
        "name": "NAT Gateway",
        "public_ip_id": "pip-1",
        "public_ip": "203.0.113.5",
        "status": "available",
        "public_ip_source": "automatic",
    }
    record.update(overrides)
    return record


def subnet(**overrides):
    record = {
        "subnet_id": "sub-1",
        "vpc_id": "vpc-1",
        "site_id": "site-1",
        "name": "default",
        "cidr": "10.20.0.0/24",
        "gateway": "10.20.0.1",
        "dns": ["1.1.1.1"],
        "status": "available",
        "created_at": TS,
        "updated_at": TS,
    }
    record.update(overrides)
    return record


def node(vm_id="vm-1", ip="10.20.0.10", **overrides):
    record = {
        "allocation_id": f"alloc-{vm_id}",
        "vpc_id": "vpc-1",
        "subnet_id": "sub-1",
        "vm_id": vm_id,
        "connectivity": "nat",
        "private_ip": ip,
        "prefix_length": 24,
        "subnet_mask": "255.255.255.0",
        "gateway": "10.20.0.1",
        "dns": [],
        "nat_gateway_id": "nat-1",
        "status": "active",
    }
    record.update(overrides)
    return record


def pf_rule(**overrides):
    record = {
        "port_forward_rule_id": "natpf-1",
        "vpc_id": "vpc-1",
        "nat_gateway_id": "nat-1",
        "name": "ssh",
        "protocol": "tcp",
        "external_port": 2222,
        "internal_ip": "10.20.0.10",
        "internal_port": 22,
        "status": "available",
        "created_at": TS,
        "updated_at": TS,
    }
    record.update(overrides)
    return record


def vip(**overrides):
    record = {
        "virtual_ip_id": "pvip-1",
        "vpc_id": "vpc-1",
        "subnet_id": "sub-1",
        "private_ip": "10.20.0.50",
        "purpose": "metallb",
        "announcer_vm_ids": ["vm-1", "vm-2"],
        "status": "available",
    }
    record.update(overrides)
    return record


def rip(**overrides):
    record = {
        "public_ip_id": "rip-1",
        "address": "203.0.113.10",
        "site_id": "site-1",
        "status": "reserved",
        "reservation_type": "user_reserved",
        "created_at": TS,
        "updated_at": TS,
    }
    record.update(overrides)
    return record


def site(**overrides):
    record = {"site_id": "site-1", "site_name": "Bengaluru", "available": True}
    record.update(overrides)
    return record


def summary(i: int, name: str | None = None):
    return {
        "firewall_group_id": f"fw-{i}",
        "name": name or f"group-{i}",
        "status": "active",
        "is_default": False,
        "rule_count": 2,
        "linked_instance_count": 0,
    }


def group(**overrides):
    record = {
        "firewall_group_id": "fw-1",
        "organization_id": "o",
        "workspace_id": WS,
        "name": "web",
        "is_default": False,
        "status": "active",
        "rules": [
            {"rule_id": "sys-1", "protocol": "tcp", "port_start": 22, "system_managed": True, "enabled": True,
             "created_at": TS, "updated_at": TS},
            {"rule_id": "r-1", "protocol": "tcp", "port_start": 80, "system_managed": False, "enabled": True,
             "created_at": TS, "updated_at": TS},
        ],
        "created_at": TS,
        "updated_at": TS,
    }
    record.update(overrides)
    return record


def lb(**overrides):
    record = {
        "lb_id": "lb-1",
        "organization_id": "o",
        "workspace_id": WS,
        "name": "web",
        "layer": "l7",
        "protocol": "https",
        "status": "active",
        "endpoint": {"host": "x", "port": 443},
        "url": "https://x",
        "endpoint_url": "https://x",
        "backends": [],
        "created_at": TS,
        "updated_at": TS,
    }
    record.update(overrides)
    return record
