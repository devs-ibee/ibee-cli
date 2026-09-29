"""CLI surface tests — no network required."""

from types import SimpleNamespace

import typer
from click import unstyle
from ibee.core.api_error import ApiError
from typer.testing import CliRunner

from ibee_cli.commands import secrets
from ibee_cli.main import app
from ibee_cli.render import handle_api_errors

runner = CliRunner()


def test_help_lists_command_groups():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for group in (
        "billing",
        "block-storage",
        "buckets",
        "cdn",
        "secrets",
        "vms",
        "gpus",
        "console",
        "vpcs",
        "reserved-ips",
        "firewalls",
        "load-balancers",
    ):
        assert group in result.output


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "ibee-cli 0.4.1" in result.output


def test_subcommand_help():
    for args in (
        ["billing", "--help"],
        ["block-storage", "--help"],
        ["buckets", "--help"],
        ["cdn", "--help"],
        ["secrets", "--help"],
        ["vms", "--help"],
        ["gpus", "--help"],
        ["console", "--help"],
        ["vpcs", "--help"],
        ["reserved-ips", "--help"],
        ["firewalls", "--help"],
        ["load-balancers", "--help"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0


def test_missing_token_is_clean_error(monkeypatch):
    for var in ("IBEE_TOKEN", "IBEE_API_TOKEN", "IBEE_WORKSPACE_ID"):
        monkeypatch.delenv(var, raising=False)
    result = runner.invoke(app, ["buckets", "list"])
    assert result.exit_code == 2
    assert "No API token" in result.output


def test_invalid_workspace_is_rejected_before_api_request():
    message = "workspace_id must be a positive numeric string (for example, '710995')."
    for workspace_id in ("0", "01", "-1", "abc", "1.0"):
        result = runner.invoke(
            app,
            ["--workspace", workspace_id, "buckets", "list"],
            env={"IBEE_TOKEN": "test-token"},
        )
        assert result.exit_code == 2
        assert message in result.output


def test_secret_store_help_lists_complete_lifecycle():
    result = runner.invoke(app, ["secrets", "--help"])
    assert result.exit_code == 0
    for command in (
        "batch-create",
        "patch-value",
        "versions",
        "version",
        "rollback",
        "undelete",
        "destroy-versions",
        "delete-permanent",
    ):
        assert command in result.output

    stores = runner.invoke(app, ["secrets", "stores", "--help"])
    assert stores.exit_code == 0
    assert "unarchive" in stores.output
    assert "delete-permanent" in stores.output

    identities = runner.invoke(app, ["secrets", "identities", "--help"])
    assert identities.exit_code == 0
    for command in (
        "list",
        "create",
        "get",
        "update",
        "disable",
        "enable",
        "access",
        "rotate-secret-id",
        "revoke-sessions",
        "delete",
        "scopes",
    ):
        assert command in identities.output

    scopes = runner.invoke(app, ["secrets", "identities", "scopes", "--help"])
    assert scopes.exit_code == 0
    for command in ("list", "create", "update", "delete"):
        assert command in scopes.output


def test_new_secret_store_commands_call_matching_sdk_methods(monkeypatch, tmp_path):
    calls = []

    class FakeSecretStore:
        def __getattr__(self, name):
            def call(*args, **kwargs):
                calls.append((name, args, kwargs))
                return SimpleNamespace(
                    created_count=1,
                    skipped_count=0,
                    failed_count=0,
                )

            return call

    fake_client = SimpleNamespace(secret_store=FakeSecretStore())
    monkeypatch.setattr(secrets, "get_client", lambda settings: fake_client)
    env = {"IBEE_TOKEN": "test-token", "IBEE_WORKSPACE_ID": "973318"}
    batch_file = tmp_path / "secrets.json"
    batch_file.write_text(
        '{"secrets":[{"secret_name":"api-key","value":{"key":"redacted"}}]}',
        encoding="utf-8",
    )

    commands = [
        ["secrets", "stores", "unarchive", "store-1", "--no-check-state"],
        ["secrets", "stores", "delete-permanent", "store-1", "--yes", "--no-check-state"],
        ["secrets", "batch-create", "--store-id", "store-1", "--file", str(batch_file)],
        ["secrets", "patch-value", "secret-1", "--value", '{"user":"ibee"}'],
        ["secrets", "versions", "secret-1"],
        ["secrets", "version", "secret-1", "1"],
        ["secrets", "rollback", "secret-1", "--version", "1"],
        ["secrets", "undelete", "secret-1", "--versions", "1,2"],
        ["secrets", "destroy-versions", "secret-1", "--versions", "1", "--yes"],
        ["secrets", "delete-permanent", "secret-1", "--yes", "--no-check-state"],
    ]
    for command in commands:
        result = runner.invoke(app, command, env=env)
        assert result.exit_code == 0, result.output

    assert [call[0] for call in calls] == [
        "unarchive_secret_store",
        "permanently_delete_secret_store",
        "batch_create_secrets",
        "patch_secret_value",
        "list_secret_versions",
        "get_secret_version",
        "rollback_secret",
        "undelete_secret",
        "destroy_secret_versions",
        "permanently_delete_secret",
    ]
    assert calls[2][2]["secrets"][0]["secret_name"] == "api-key"
    assert calls[7][2]["versions"] == [1, 2]


def test_workspace_ownership_403_has_actionable_message():
    @handle_api_errors
    def fail():
        raise ApiError(
            status_code=403,
            body={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Secret 'secret-1' does not belong to workspace '973318'",
                }
            },
        )

    command = typer.Typer()
    command.command()(fail)
    result = runner.invoke(command, [])
    assert result.exit_code == 1
    assert "different workspace" in result.output
    assert "IBEE_WORKSPACE_ID" in result.output


def test_identity_and_scope_commands_call_matching_sdk_methods(monkeypatch):
    calls = []

    class FakeSecretStore:
        def __getattr__(self, name):
            def call(*args, **kwargs):
                calls.append((name, args, kwargs))
                return SimpleNamespace()

            return call

    fake_client = SimpleNamespace(secret_store=FakeSecretStore())
    monkeypatch.setattr(secrets, "get_client", lambda settings: fake_client)
    env = {"IBEE_TOKEN": "test-token", "IBEE_WORKSPACE_ID": "973318"}

    commands = [
        ["secrets", "identities", "list", "--store-id", "store-1"],
        [
            "secrets",
            "identities",
            "create",
            "--store-id",
            "store-1",
            "--name",
            "worker",
            "--auth-method",
            "approle",
            "--token-policy-mode",
            "read_write",
        ],
        ["secrets", "identities", "get", "identity-1"],
        [
            "secrets",
            "identities",
            "update",
            "identity-1",
            "--token-policy-mode",
            "read_only",
        ],
        ["secrets", "identities", "disable", "identity-1"],
        ["secrets", "identities", "enable", "identity-1"],
        ["secrets", "identities", "access", "identity-1", "--show-sensitive"],
        [
            "secrets",
            "identities",
            "rotate-secret-id",
            "identity-1",
            "--show-sensitive",
        ],
        ["secrets", "identities", "revoke-sessions", "identity-1", "--yes"],
        ["secrets", "identities", "delete", "identity-1", "--yes"],
        ["secrets", "identities", "scopes", "list", "identity-1"],
        [
            "secrets",
            "identities",
            "scopes",
            "create",
            "identity-1",
            "--store-id",
            "store-2",
            "--access-mode",
            "read_write",
            "--allow-version-read",
            "--allow-rollback",
        ],
        [
            "secrets",
            "identities",
            "scopes",
            "update",
            "scope-1",
            "--access-mode",
            "read_write",
            "--deny-version-read",
            "--deny-rollback",
            "--allow-destroy",
        ],
        ["secrets", "identities", "scopes", "delete", "scope-1", "--yes"],
    ]
    for command in commands:
        result = runner.invoke(app, command, env=env)
        assert result.exit_code == 0, result.output

    assert [call[0] for call in calls] == [
        "list_secret_identities",
        "create_secret_identity",
        "get_secret_identity",
        "update_secret_identity",
        "disable_secret_identity",
        "enable_secret_identity",
        "get_secret_identity_access",
        "rotate_secret_identity_secret_id",
        "revoke_secret_identity_sessions",
        "delete_secret_identity",
        "list_secret_identity_scopes",
        "create_secret_identity_scope",
        "update_secret_identity_scope",
        "delete_secret_identity_scope",
    ]
    assert calls[1][2] == {
        "workspace_id": "973318",
        "auth_method": "approle",
        "name": "worker",
        "token_policy_mode": "read_write",
    }
    assert calls[11][2]["allow_version_read"] is True
    assert calls[11][2]["allow_rollback"] is True
    assert calls[11][2]["allow_destroy"] is False
    assert calls[12][2]["allow_version_read"] is False
    assert calls[12][2]["allow_rollback"] is False
    assert calls[12][2]["allow_destroy"] is True


def test_sensitive_identity_credentials_require_explicit_opt_in(monkeypatch):
    called = False

    def fail_if_called(settings):
        nonlocal called
        called = True
        return SimpleNamespace()

    monkeypatch.setattr(secrets, "get_client", fail_if_called)
    env = {"IBEE_TOKEN": "test-token", "IBEE_WORKSPACE_ID": "973318"}

    for command in ("access", "rotate-secret-id"):
        result = runner.invoke(
            app,
            ["secrets", "identities", command, "identity-1"],
            env=env,
            color=True,
        )
        assert result.exit_code == 2
        assert "--show-sensitive" in unstyle(result.output)
    assert called is False


def test_kubernetes_identity_requires_binding_fields(monkeypatch):
    called = False

    def fail_if_called(settings):
        nonlocal called
        called = True
        return SimpleNamespace()

    monkeypatch.setattr(secrets, "get_client", fail_if_called)
    result = runner.invoke(
        app,
        [
            "secrets",
            "identities",
            "create",
            "--store-id",
            "store-1",
            "--name",
            "worker",
            "--auth-method",
            "kubernetes",
        ],
        env={"IBEE_TOKEN": "test-token", "IBEE_WORKSPACE_ID": "973318"},
        color=True,
    )
    assert result.exit_code == 2
    output = unstyle(result.output)
    assert "--k8s-namespace" in output
    assert "--k8s-service-account" in output
    assert called is False


def test_identity_and_scope_deletion_prompt_before_api_call(monkeypatch):
    calls = []

    class FakeSecretStore:
        def __getattr__(self, name):
            def call(*args, **kwargs):
                calls.append(name)

            return call

    monkeypatch.setattr(
        secrets,
        "get_client",
        lambda settings: SimpleNamespace(secret_store=FakeSecretStore()),
    )
    env = {"IBEE_TOKEN": "test-token", "IBEE_WORKSPACE_ID": "973318"}

    identity = runner.invoke(
        app,
        ["secrets", "identities", "delete", "identity-1"],
        input="n\n",
        env=env,
    )
    scope = runner.invoke(
        app,
        ["secrets", "identities", "scopes", "delete", "scope-1"],
        input="n\n",
        env=env,
    )
    assert identity.exit_code == 1
    assert scope.exit_code == 1
    assert calls == []
