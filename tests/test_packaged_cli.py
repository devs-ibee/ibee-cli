"""Invoke the installed console script against localhost, with the real SDK.

IBEE_TEST_CLI may select a wheel-installed entry point. Otherwise use the entry
point in the running Python environment. No credentials or endpoints are read
from the caller's IBEE environment. The SDK default is two retries (three
attempts); shared fixture expectations use an explicitly configured three.
"""

import json
import os
from pathlib import Path
import subprocess
import sysconfig
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest

WS = "710995"
VM = "0123456789abcdef01234567"
ACCEPTED = {"operation_id": f"op_{VM}", "vm_id": VM, "status": "accepted", "submitted_at": "2026-10-04T00:00:00Z"}
CATALOG = {"sku_id": 11, "sku_code": "STANDARD-2-8-50"}


def cases():
    path = os.environ.get("IBEE_CONFORMANCE_FIXTURES")
    if path is None:
        return [pytest.param(None, marks=pytest.mark.skip(reason="IBEE_CONFORMANCE_FIXTURES is unset"))]
    fixture = json.loads(Path(path).read_text())
    assert fixture["version"] == 1 and fixture["cases"]
    return [pytest.param(case, id=case["name"]) for case in fixture["cases"]]


@pytest.fixture
def gateway():
    calls = []
    state = {"respond": lambda call: (500, {"detail": "unexpected request"}, {})}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            call = {"method": self.command, "path": parsed.path, "query": parse_qs(parsed.query),
                    "body": json.loads(body) if body else None, "headers": self.headers}
            calls.append(call)
            status, data, headers = state["respond"](call)
            raw = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Retry-After", "0")
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(raw)

        do_POST = do_GET

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    state.update(calls=calls, base_url=f"http://127.0.0.1:{server.server_port}/v1")
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run_cli(gateway, tmp_path, *args):
    executable = os.environ.get("IBEE_TEST_CLI", str(Path(sysconfig.get_path("scripts")) / "ibee"))
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("IBEE_", "PYTHON")) and key.lower() not in
           ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}
    env.update(NO_PROXY="127.0.0.1,localhost", NO_COLOR="1", TERM="dumb", COLUMNS="240")
    result = subprocess.run([executable, "--token", "fixture", "--workspace", WS,
                             "--base-url", gateway["base_url"], "--json", *args],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=20)
    assert "Traceback" not in result.stderr, result.stderr
    return result


def command(case):
    path = case["path"]
    if path.endswith("/actions/start"):
        return ["vms", "start", VM, "--no-check-state", "--idempotency-key", "stable-key"]
    if case["method"] == "GET" and path == "/compute/cloud-vms":
        return ["vms", "list", "--limit", "10"]
    if path == "/secret-store/identities/id-1/access":
        return ["secrets", "identities", "access", "id-1", "--show-sensitive"]
    if case["method"] == "POST" and path in ("/compute/cloud-vms", "/compute/gpu-vms"):
        args = ["gpus" if path.endswith("gpu-vms") else "vms", "create", "fixture-vm",
                "--site-id", "site-1", "--plan-id", "plan-1", "--template-id", "image-1",
                "--cpu", "2", "--ram-mb", "8192", "--disk-gb", "50", "--os-type", "linux",
                "--os-distro", "ubuntu", "--billing-catalog", json.dumps(CATALOG),
                "--preflight-billing", "--idempotency-key", "stable-key"]
        if path.endswith("gpu-vms"):
            args += ["--gpu-count", "1", "--gpu-model", "L4"]
        return args
    raise AssertionError(f"unmapped CLI fixture: {case['name']}")


@pytest.mark.parametrize("case", cases())
def test_installed_cli_shared_policy(case, gateway, tmp_path):
    if case["path"] == "/future-product/resources":
        pytest.skip("future-product has no CLI command; covered by SDK transport conformance")
    headers = {"X-Request-ID": case["request_id"]} if "request_id" in case else {}
    gateway["respond"] = lambda call: (case["status"], case["response"], headers)
    result = run_cli(gateway, tmp_path, *command(case))
    assert result.returncode == 1, result.stdout + result.stderr
    output = result.stdout + result.stderr
    for value in [case.get("request_id"), *case["response"].values()]:
        if value and case["status"] in (402, 403, 423):
            assert value in output, output
    calls = gateway["calls"]
    assert len(calls) == min(case["attempts"], 3)
    for call in calls:
        assert call["method"] == case["method"]
        assert call["path"] == "/v1" + case["path"].replace("vm-1", VM)
        assert call["query"]["workspace_id"] == [WS]
        assert call["headers"]["Authorization"] == "Bearer fixture"
        for name, value in case.get("headers", {}).items():
            assert call["headers"][name] == value
        assert call["body"] == calls[0]["body"]


def test_installed_cli_action_recovers(gateway, tmp_path):
    gateway["respond"] = lambda call: ((503, {"detail": "busy"}, {}) if len(gateway["calls"]) == 1
                                        else (202, ACCEPTED, {}))
    result = run_cli(gateway, tmp_path, "vms", "start", VM, "--no-check-state")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["operation_id"] == ACCEPTED["operation_id"]
    assert len(gateway["calls"]) == 2
    keys = [call["headers"]["X-Idempotency-Key"] for call in gateway["calls"]]
    assert keys[0] and keys[0] == keys[1]


@pytest.mark.parametrize("require,exit_code", [(False, 0), (True, 1)])
def test_installed_cli_explicit_eligibility_denial(gateway, tmp_path, require, exit_code):
    payload = {"organization_id": "org", "allowed": False, "reason": "insufficient_balance", "operation": "CREATE_RESOURCE"}
    gateway["respond"] = lambda call: (200, payload, {})
    args = ["billing", "eligibility", "--operation", "CREATE_RESOURCE"] + (["--require"] if require else [])
    result = run_cli(gateway, tmp_path, *args)
    assert result.returncode == exit_code, result.stderr
    assert json.loads(result.stdout)["allowed"] is False
    assert len(gateway["calls"]) == 1
    assert gateway["calls"][0]["path"] == "/v1/billing/resource-eligibility"
    assert gateway["calls"][0]["body"] == {"operation": "CREATE_RESOURCE"}


def test_installed_cli_catalog_create(gateway, tmp_path):
    def respond(call):
        if call["path"] == "/v1/compute/plans":
            return 200, {"plans": [{"plan_id": "plan-1", "vm_type": "cloud", "name": "Standard", "code": "STD",
                "cpu": 2, "ram_mb": 8192, "disk_gb": 50, "selectable": True, "pricing_status": "priced",
                "site_id": "site-1", "billing_catalog": {**CATALOG, "billing_options": [
                    {"billing_interval": "HOURLY", "unit_price_minor": 500}]}}]}, {}
        if call["path"] == "/v1/compute/images":
            return 200, {"images": [{"template_id": "image-1", "os_type": "linux", "os_distro": "ubuntu",
                                     "compatible_vm_types": ["cloud"], "site_ids": []}]}, {}
        if call["path"] == "/v1/compute/cloud-vms" and call["method"] == "POST":
            return 202, ACCEPTED, {}
        return 500, {"detail": "unexpected request"}, {}

    gateway["respond"] = respond
    result = run_cli(gateway, tmp_path, "--check-billing", "vms", "create", "fixture-vm",
                     "--site-id", "site-1", "--plan-id", "plan-1", "--template-id", "image-1", "--preflight-billing")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["operation_id"] == ACCEPTED["operation_id"]
    assert [(c["method"], c["path"]) for c in gateway["calls"]] == [
        ("GET", "/v1/compute/plans"), ("GET", "/v1/compute/images"), ("POST", "/v1/compute/cloud-vms")]
    body = gateway["calls"][-1]["body"]
    assert body["cpu"] == 2 and body["disk_gb"] == 50
    assert body["billing_catalog"]["sku_code"] == CATALOG["sku_code"]
    assert body["billing_catalog"]["billing_interval"] == "HOURLY"


@pytest.mark.parametrize("currency", [None, "USD", "INR"])
def test_installed_cli_catalog_currency(gateway, tmp_path, currency):
    # Simulate a USD organization; an explicit mismatch remains an API error.
    payload = {"plans": [], "count": 0, "vm_type": "cloud", "currency": "USD", "billing_interval": "HOURLY"}
    mismatch = {"detail": "Currency must match organization profile currency"}
    gateway["respond"] = lambda call: (400, mismatch, {}) if currency == "INR" else (200, payload, {})
    args = ["compute", "plans", "--vm-type", "cloud"]
    if currency is not None:
        args += ["--currency", currency]
    result = run_cli(gateway, tmp_path, *args)
    assert result.returncode == (1 if currency == "INR" else 0), result.stderr
    if currency == "INR":
        assert mismatch["detail"] in result.stderr
    else:
        assert json.loads(result.stdout)["currency"] == "USD"
    assert len(gateway["calls"]) == 1
    call = gateway["calls"][0]
    assert call["path"] == "/v1/compute/plans" and call["method"] == "GET"
    expected = {"workspace_id": [WS], "vm_type": ["cloud"]}
    if currency is not None:
        expected["currency"] = [currency]
    assert call["query"] == expected
