"""CLI surface tests — no network required."""

from types import SimpleNamespace

from typer.testing import CliRunner
from typer.main import get_command

from ibee_cli.commands import compute, vms
from ibee_cli.main import app

runner = CliRunner()


def test_help_lists_command_groups():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for group in (
        "buckets",
        "secrets",
        "vms",
        "gpus",
        "vpcs",
        "reserved-ips",
        "firewalls",
        "load-balancers",
    ):
        assert group in result.output


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "ibee-cli 0.3.0" in result.output


def test_subcommand_help():
    for args in (
        ["buckets", "--help"],
        ["secrets", "--help"],
        ["vms", "--help"],
        ["vpcs", "--help"],
        ["reserved-ips", "--help"],
        ["firewalls", "--help"],
        ["load-balancers", "--help"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0


def test_nested_networking_command_registration():
    expected = {
        ("vpcs",): ("sites", "subnets", "nodes", "nat", "forwarding"),
        ("vpcs", "subnets"): ("list", "create", "get", "update", "delete"),
        ("vpcs", "nodes"): ("list", "attach", "detach"),
        ("vpcs", "nat"): ("list", "create", "delete"),
        ("vpcs", "forwarding"): ("list", "create", "update", "delete"),
        ("firewalls",): ("list", "create", "get", "delete", "rules", "attachments"),
        ("firewalls", "rules"): ("create", "update", "delete"),
        ("firewalls", "attachments"): ("list", "attach", "detach"),
        ("reserved-ips",): (
            "list",
            "reserve",
            "get",
            "update",
            "attach",
            "detach",
            "move",
            "release",
        ),
        ("load-balancers",): (
            "list",
            "create-l4",
            "create-l7",
            "get",
            "update-l4",
            "update-l7",
            "delete",
            "status",
        ),
    }
    for group, commands in expected.items():
        result = runner.invoke(app, [*group, "--help"])
        assert result.exit_code == 0
        for command in commands:
            assert command in result.output


def test_bucket_and_credential_command_registration():
    result = runner.invoke(app, ["buckets", "--help"])
    assert result.exit_code == 0
    for command in ("list", "create", "get", "update", "delete", "credentials"):
        assert command in result.output

    result = runner.invoke(app, ["buckets", "credentials", "--help"])
    assert result.exit_code == 0
    for command in ("list", "create", "get", "revoke"):
        assert command in result.output


def test_vm_groups_only_advertise_supported_commands():
    root = get_command(app)
    expected = {"list", "get", "create", "delete", "metrics", "start", "stop", "reboot"}
    assert set(root.commands["vms"].commands) == expected
    assert set(root.commands["gpus"].commands) == expected
    assert "update" not in root.commands["vms"].commands
    assert "network-interfaces" not in root.commands["vms"].commands
    assert "update" not in root.commands["gpus"].commands
    assert "network-interfaces" not in root.commands["gpus"].commands


def test_compute_catalog_forwards_only_supported_image_filters(monkeypatch):
    calls = []

    class Catalog:
        def list_compute_images(self, **kwargs):
            calls.append(kwargs)
            return {"images": [], "count": 0, "vm_type": kwargs["vm_type"]}

    monkeypatch.setattr(
        compute,
        "get_client",
        lambda settings: SimpleNamespace(compute_catalog=Catalog()),
    )
    result = runner.invoke(
        app,
        [
            "--token",
            "test-token",
            "--workspace",
            "607005",
            "compute",
            "images",
            "--vm-type",
            "gpu",
            "--site-id",
            "site-1",
        ],
    )
    assert result.exit_code == 0
    assert calls == [
        {"workspace_id": "607005", "vm_type": "gpu", "site_id": "site-1"}
    ]


def test_vm_create_forwards_required_catalog_ids(monkeypatch):
    calls = []

    class CloudVms:
        def create_cloud_vm(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(operation_id="op-1")

    monkeypatch.setattr(
        vms,
        "get_client",
        lambda settings: SimpleNamespace(cloud_vms=CloudVms()),
    )
    monkeypatch.setattr(vms, "finish_operation", lambda *args: None)
    result = runner.invoke(
        app,
        [
            "--token",
            "test-token",
            "--workspace",
            "607005",
            "vms",
            "create",
            "web",
            "--site-id",
            "site-1",
            "--plan-id",
            "plan-1",
            "--template-id",
            "image-1",
        ],
    )
    assert result.exit_code == 0
    assert calls[0]["site_id"] == "site-1"
    assert calls[0]["plan_id"] == "plan-1"
    assert calls[0]["template_id"] == "image-1"


def test_missing_token_is_clean_error(monkeypatch):
    for var in ("IBEE_TOKEN", "IBEE_API_TOKEN", "IBEE_WORKSPACE_ID"):
        monkeypatch.delenv(var, raising=False)
    result = runner.invoke(app, ["buckets", "list"])
    assert result.exit_code == 2
    assert "No API token" in result.output
