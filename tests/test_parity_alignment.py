"""0.4.0 cross-client alignment: the CLI leaves the dependency and origin checks to the
Python SDK, maps SDK server-state errors to exit 1, and documents backend availability
(real SDK behind a scripted gateway, no network)."""

from __future__ import annotations

import time

import pytest
import typer
from ibee.errors import IbeeError
from ibee.validation import IbeeValidationError

from _net_fixtures import V, gateway_record, pf_rule, plain, run, vip, vpc
from ibee_cli.main import app
from ibee_cli.render import handle_api_errors
from typer.testing import CliRunner

RULES = f"{V}/nat-gateways/nat-1/port-forwarding-rules"
BUCKET = "object-storage/buckets/site-assets"
SCOPE_403 = lambda scope: (403, {"error": "insufficient_scope", "required_scope": scope})  # noqa: E731


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


def gets(gw, path):
    return [c for c in gw.calls if c.method == "GET" and c.path == path]


# ---------------------------------------------------------------------------
# Exit codes for SDK errors
# ---------------------------------------------------------------------------


def _raise(exc):
    @handle_api_errors
    def command():
        raise exc

    with pytest.raises(typer.Exit) as info:
        command()
    return info.value.exit_code


def test_server_state_sdk_error_exits_1_and_client_validation_exits_2(capsys):
    error = IbeeError("still reconciling", code="nat_gateway_deleting")
    error.cli_hint = "Retry shortly."
    assert _raise(error) == 1
    err = capsys.readouterr().err
    assert "still reconciling" in err and "Retry shortly." in err
    assert _raise(IbeeValidationError("bad", code="invalid_name", field="name")) == 2


# ---------------------------------------------------------------------------
# VPC delete: the SDK runs the node, NAT and virtual-IP checks
# ---------------------------------------------------------------------------


def test_vpc_delete_reads_the_state_once(gw):
    gw.on("GET", V, vpc()).on("GET", f"{V}/virtual-ips", []).on("DELETE", V)
    result = run(["vpcs", "delete", "vpc-1", "--yes"])
    assert result.exit_code == 0, result.output
    assert len(gets(gw, V)) == 1 and len(gets(gw, f"{V}/virtual-ips")) == 1
    assert [(c.method, c.path) for c in gw.writes()] == [("DELETE", V)]


def test_vpc_delete_keeps_the_cli_wording_for_a_nat_gateway(gw):
    gw.on("GET", V, vpc(nat_gateways=[gateway_record()]))
    result = run(["vpcs", "delete", "vpc-1", "--yes"])
    assert result.exit_code == 2, result.output
    assert "Delete the NAT gateway first (or pass --delete-nat-gateway)." in plain(result)
    assert gw.writes() == []


def test_vpc_delete_with_nat_gateway_refuses_virtual_ips_before_deleting_the_gateway(gw):
    gw.on("GET", V, vpc(nat_gateways=[gateway_record()]))
    gw.on("GET", f"{V}/virtual-ips", [vip()])
    result = run(["vpcs", "delete", "vpc-1", "--delete-nat-gateway", "--nat-ip-action", "release", "--yes"])
    assert result.exit_code == 2, result.output
    assert "Delete all virtual IP reservations before deleting the VPC." in plain(result)
    assert gw.writes() == []


def test_vpc_delete_with_check_state_fails_when_virtual_ips_cannot_be_read(gw):
    gw.on("GET", V, vpc()).on("GET", f"{V}/virtual-ips", SCOPE_403("network.read"))
    result = run(["vpcs", "delete", "vpc-1", "--yes"])
    assert result.exit_code == 1, result.output
    assert gw.writes() == []


def test_vpc_delete_no_check_state_sends_only_the_delete(gw):
    gw.on("DELETE", V)
    result = run(["vpcs", "delete", "vpc-1", "--no-check-state", "--yes"])
    assert result.exit_code == 0, result.output
    assert [(c.method, c.path) for c in gw.calls] == [("DELETE", V)]


@pytest.mark.parametrize(
    ("extra", "prompt"),
    [
        ([], "Delete VPC 'vpc-1'?"),
        (["--delete-nat-gateway"], "Delete NAT gateway(s) and then VPC 'vpc-1'?"),
    ],
)
def test_vpc_delete_prompts_before_any_request(gw, extra, prompt):
    result = run(["vpcs", "delete", "vpc-1", *extra], input="n\n")
    assert result.exit_code == 1
    assert prompt in plain(result)
    assert gw.calls == []


# ---------------------------------------------------------------------------
# Virtual IP delete: the SDK runs the Reserved IP and forwarding-rule checks
# ---------------------------------------------------------------------------


def test_virtual_ip_delete_checks_once_through_the_sdk(gw):
    gw.on("GET", f"{V}/virtual-ips", [vip()])
    gw.on("GET", f"{V}/nat-gateways", [gateway_record()])
    gw.on("GET", RULES, [pf_rule()])
    gw.on("DELETE", f"{V}/virtual-ips/pvip-1")
    result = run(["vpcs", "virtual-ips", "delete", "vpc-1", "pvip-1", "--yes"])
    assert result.exit_code == 0, result.output
    assert len(gets(gw, f"{V}/nat-gateways")) == 1 and len(gets(gw, RULES)) == 1
    assert [(c.method, c.path) for c in gw.writes()] == [("DELETE", f"{V}/virtual-ips/pvip-1")]


def test_virtual_ip_delete_with_check_state_fails_when_rules_cannot_be_read(gw):
    gw.on("GET", f"{V}/virtual-ips", [vip()])
    gw.on("GET", f"{V}/nat-gateways", [gateway_record()])
    gw.on("GET", RULES, SCOPE_403("network.read"))
    result = run(["vpcs", "virtual-ips", "delete", "vpc-1", "pvip-1", "--yes"])
    assert result.exit_code == 1, result.output
    assert gw.writes() == []


def test_virtual_ip_delete_no_check_state_and_decline(gw):
    result = run(["vpcs", "virtual-ips", "delete", "vpc-1", "pvip-1"], input="n\n")
    assert result.exit_code == 1 and "Delete virtual IP 'pvip-1'?" in plain(result)
    assert gw.calls == []
    gw.on("DELETE", f"{V}/virtual-ips/pvip-1")
    result = run(["vpcs", "virtual-ips", "delete", "vpc-1", "pvip-1", "--no-check-state", "--yes"])
    assert result.exit_code == 0, result.output
    assert [(c.method, c.path) for c in gw.calls] == [("DELETE", f"{V}/virtual-ips/pvip-1")]


# ---------------------------------------------------------------------------
# CDN create: the SDK reads the origin bucket (403 and 404 are non-fatal)
# ---------------------------------------------------------------------------


def test_cdn_create_origin_check_reads_the_bucket_once(gw):
    gw.on("GET", BUCKET, {"name": "site-assets", "region": "r", "is_public": True})
    gw.on("POST", "cdn/distributions", {"id": "d1", "name": "assets"})
    result = run(["cdn", "create", "assets", "--origin-id", "site-assets"])
    assert result.exit_code == 0, result.output
    assert len(gets(gw, BUCKET)) == 1


@pytest.mark.parametrize("status", [403, 404])
def test_cdn_create_unreadable_origin_is_left_to_the_api(gw, status):
    gw.on("GET", BUCKET, (status, {"detail": "no"}))
    gw.on("POST", "cdn/distributions", {"id": "d1", "name": "assets"})
    result = run(["cdn", "create", "assets", "--origin-id", "site-assets"])
    assert result.exit_code == 0, result.output


def test_cdn_create_private_origin_exits_2_before_billing_and_create(gw):
    gw.on("GET", BUCKET, {"name": "site-assets", "region": "r", "is_public": False})
    result = run(["--check-billing", "cdn", "create", "assets", "--origin-id", "site-assets"])
    assert result.exit_code == 2, result.output
    assert "Only public buckets can be used as CDN origins" in plain(result)
    assert [c.path for c in gw.calls] == [BUCKET]


def test_cdn_create_invalid_request_sends_nothing(gw):
    result = run(["cdn", "create", "assets", "--origin-id", "site-assets", "--cache-policy", "forever"])
    assert result.exit_code == 2, result.output
    assert gw.calls == []


# ---------------------------------------------------------------------------
# Help text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("group", ["vms", "gpus"])
@pytest.mark.parametrize("command", ["delete", "list-all"])
def test_backup_run_delete_and_list_all_state_backend_availability(group, command):
    result = CliRunner().invoke(app, [group, "backups", command, "--help"], env={"COLUMNS": "400"})
    assert result.exit_code == 0, result.output
    text = plain(result)
    assert "Needs the backend release that provides this operation" in text
    assert "currently available on the development environment" in text
    assert "production returns 404/405 until then" in text
    assert "Not yet part of the published API contract" in text


def test_backup_restore_help_accepts_a_run_id():
    result = CliRunner().invoke(app, ["vms", "backups", "restore", "--help"], env={"COLUMNS": "400"})
    assert "Backup run ID or recovery point ID" in plain(result)
