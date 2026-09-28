"""Secret Store commands for stores, secrets, application identities, and scopes."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Optional

import typer

from ..context import get_client, get_settings, require_workspace
from ..helpers import confirm_destructive, parse_value, preflight_create
from ..render import handle_api_errors, print_json, print_table

app = typer.Typer(
    help="Secret Store: stores, secrets, and application identities",
    no_args_is_help=True,
)

# ── stores sub-group: `ibee secrets stores <verb>` ──────────────────────────
stores_app = typer.Typer(help="Manage secret stores", no_args_is_help=True)
app.add_typer(stores_app, name="stores")

# Application identities authenticate workloads directly to Secret Store. Scope
# management stays nested under identities so it cannot be confused with API
# token scopes: `ibee secrets identities scopes <verb>`.
identities_app = typer.Typer(
    help="Manage AppRole and Kubernetes application identities",
    no_args_is_help=True,
)
identity_scopes_app = typer.Typer(
    help="Manage store access granted to application identities",
    no_args_is_help=True,
)
app.add_typer(identities_app, name="identities")
identities_app.add_typer(identity_scopes_app, name="scopes")


class AuthMethod(str, Enum):
    APPROLE = "approle"
    KUBERNETES = "kubernetes"


class AccessMode(str, Enum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


def _require_sensitive_output(show_sensitive: bool) -> None:
    """Require an explicit opt-in before printing generated credentials."""
    if not show_sensitive:
        raise typer.BadParameter(
            "This command can generate and print credentials. "
            "Pass --show-sensitive only when the terminal and output handling are secure."
        )


def _parse_versions(raw: str) -> list[int]:
    """Parse a comma-separated, positive version list."""
    try:
        versions = [int(part.strip()) for part in raw.split(",") if part.strip()]
    except ValueError as exc:
        raise typer.BadParameter("versions must be comma-separated integers") from exc
    if not versions or any(version < 1 for version in versions):
        raise typer.BadParameter("versions must contain positive integers")
    if len(versions) > 100:
        raise typer.BadParameter("at most 100 versions can be changed at once")
    return versions


def _load_batch(path: Path) -> list[dict]:
    """Read and validate a Secret Store batch-ingest file."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"batch file is not valid JSON: {exc}") from exc
    secrets = payload.get("secrets") if isinstance(payload, dict) else payload
    if not isinstance(secrets, list) or not 1 <= len(secrets) <= 500:
        raise typer.BadParameter("batch file must contain 1 to 500 secrets")
    for index, item in enumerate(secrets):
        if not isinstance(item, dict) or not isinstance(item.get("secret_name"), str):
            raise typer.BadParameter(f"secrets[{index}].secret_name must be a string")
        if not isinstance(item.get("value"), dict):
            raise typer.BadParameter(f"secrets[{index}].value must be a JSON object")
    return secrets


@stores_app.command("list")
@handle_api_errors
def list_stores(ctx: typer.Context) -> None:
    """List secret stores in the workspace."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.list_secret_stores(workspace_id=require_workspace(settings))
    if settings.structured_output:
        print_json(result)
        return
    stores = result.stores or []
    print_table(
        "Secret stores",
        ["ID", "Name", "Store key", "Status", "Updated"],
        [
            (s.id, s.name, s.store_key, getattr(s.status, "value", s.status), str(s.updated_at or "")[:19])
            for s in stores
        ],
    )


@stores_app.command("create")
@handle_api_errors
def create_store(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Store name"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Store description"),
) -> None:
    """Create a secret store."""
    settings = get_settings(ctx)
    client = get_client(settings)
    workspace = require_workspace(settings)
    preflight_create(settings, "secret_store", client=client)
    result = client.secret_store.create_secret_store(
        workspace_id=workspace, name=name, description=description
    )
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Store '{name}' created (id {getattr(result, 'id', '?')}).", fg=typer.colors.GREEN)


@stores_app.command("get")
@handle_api_errors
def get_store(ctx: typer.Context, store_id: str = typer.Argument(..., help="Store ID")) -> None:
    """Show one secret store."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.get_secret_store(
        workspace_id=require_workspace(settings), store_id=store_id
    )
    print_json(result)


@stores_app.command("update")
@handle_api_errors
def update_store(
    ctx: typer.Context,
    store_id: str = typer.Argument(..., help="Store ID"),
    name: Optional[str] = typer.Option(None, "--name", help="New name"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="New description"),
) -> None:
    """Update a secret store's name or description."""
    if name is None and description is None:
        raise typer.BadParameter("Provide --name and/or --description to update.")
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.update_secret_store(
        workspace_id=require_workspace(settings), store_id=store_id, name=name, description=description
    )
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Store '{store_id}' updated.", fg=typer.colors.GREEN)


@stores_app.command("archive")
@handle_api_errors
def archive_store(
    ctx: typer.Context,
    store_id: str = typer.Argument(..., help="Store ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Archive a secret store."""
    settings = get_settings(ctx)
    confirm_destructive(get_settings(ctx), f"Archive secret store '{store_id}'?", yes)
    client = get_client(settings)
    client.secret_store.archive_secret_store(
        workspace_id=require_workspace(settings), store_id=store_id
    )
    typer.secho(f"Store '{store_id}' archived.", fg=typer.colors.GREEN)


@stores_app.command("unarchive")
@handle_api_errors
def unarchive_store(
    ctx: typer.Context,
    store_id: str = typer.Argument(..., help="Store ID"),
) -> None:
    """Reactivate an archived secret store."""
    settings = get_settings(ctx)
    client = get_client(settings)
    client.secret_store.unarchive_secret_store(
        store_id, workspace_id=require_workspace(settings)
    )
    typer.secho(f"Store '{store_id}' unarchived.", fg=typer.colors.GREEN)


@stores_app.command("delete-permanent")
@handle_api_errors
def permanently_delete_store(
    ctx: typer.Context,
    store_id: str = typer.Argument(..., help="Secret store ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm irreversible deletion"),
) -> None:
    """Permanently delete a store and all store-scoped resources."""
    settings = get_settings(ctx)
    confirm_destructive(
        get_settings(ctx),
        f"Permanently delete secret store '{store_id}' and all of its secrets, "
        "versions, identities, and policies? This cannot be undone.",
        yes,
    )
    client = get_client(settings)
    client.secret_store.permanently_delete_secret_store(
        store_id, workspace_id=require_workspace(settings)
    )
    typer.secho(f"Store '{store_id}' permanently deleted.", fg=typer.colors.GREEN)


# ── application identities: `ibee secrets identities <verb>` ───────────────

@identities_app.command("list")
@handle_api_errors
def list_identities(
    ctx: typer.Context,
    store_id: str = typer.Option(..., "--store-id", "-s", help="Secret store ID"),
) -> None:
    """List application identities bound to a store."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.list_secret_identities(
        store_id,
        workspace_id=require_workspace(settings),
    )
    if settings.structured_output:
        print_json(result)
        return
    identities = getattr(result, "identities", None) or []
    print_table(
        "Secret Store identities",
        ["ID", "Name", "Auth method", "Policy", "Status", "Updated"],
        [
            (
                identity.id,
                identity.name,
                getattr(identity.auth_method, "value", identity.auth_method),
                getattr(identity.token_policy_mode, "value", identity.token_policy_mode),
                getattr(identity.status, "value", identity.status),
                str(getattr(identity, "updated_at", "") or "")[:19],
            )
            for identity in identities
        ],
    )


@identities_app.command("create")
@handle_api_errors
def create_identity(
    ctx: typer.Context,
    store_id: str = typer.Option(..., "--store-id", "-s", help="Initial secret store ID"),
    name: str = typer.Option(..., "--name", "-n", help="Application identity name"),
    auth_method: AuthMethod = typer.Option(..., "--auth-method", help="approle or kubernetes"),
    token_policy_mode: AccessMode = typer.Option(
        AccessMode.READ_ONLY,
        "--token-policy-mode",
        help="Default read_only or read_write access policy",
    ),
    k8s_namespace: Optional[str] = typer.Option(
        None,
        "--k8s-namespace",
        help="Required for kubernetes identities",
    ),
    k8s_service_account: Optional[str] = typer.Option(
        None,
        "--k8s-service-account",
        help="Required for kubernetes identities",
    ),
) -> None:
    """Create an AppRole or Kubernetes application identity."""
    if auth_method is AuthMethod.KUBERNETES:
        if not k8s_namespace or not k8s_service_account:
            raise typer.BadParameter(
                "Kubernetes identities require --k8s-namespace and --k8s-service-account."
            )
    elif k8s_namespace is not None or k8s_service_account is not None:
        raise typer.BadParameter(
            "--k8s-namespace and --k8s-service-account apply only to kubernetes identities."
        )

    settings = get_settings(ctx)
    client = get_client(settings)
    binding: dict[str, str] = {}
    if k8s_namespace is not None:
        binding["k8s_namespace"] = k8s_namespace
    if k8s_service_account is not None:
        binding["k8s_service_account"] = k8s_service_account
    result = client.secret_store.create_secret_identity(
        store_id,
        workspace_id=require_workspace(settings),
        auth_method=auth_method.value,
        name=name,
        token_policy_mode=token_policy_mode.value,
        **binding,
    )
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(
        f"Identity '{name}' created (id {getattr(result, 'id', '?')}). "
        "Use `ibee secrets identities access ID --show-sensitive` to obtain access details.",
        fg=typer.colors.GREEN,
    )


@identities_app.command("get")
@handle_api_errors
def get_identity(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
) -> None:
    """Show application identity metadata without credentials."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.get_secret_identity(
        identity_id,
        workspace_id=require_workspace(settings),
    )
    print_json(result)


@identities_app.command("update")
@handle_api_errors
def update_identity(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    token_policy_mode: AccessMode = typer.Option(
        ...,
        "--token-policy-mode",
        help="Set read_only or read_write and revoke active sessions",
    ),
) -> None:
    """Update an identity's token policy mode and revoke active sessions."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.update_secret_identity(
        identity_id,
        workspace_id=require_workspace(settings),
        token_policy_mode=token_policy_mode.value,
    )
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Identity '{identity_id}' updated.", fg=typer.colors.GREEN)


@identities_app.command("disable")
@handle_api_errors
def disable_identity(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
) -> None:
    """Disable an identity and revoke its active sessions."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.disable_secret_identity(
        identity_id,
        workspace_id=require_workspace(settings),
    )
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Identity '{identity_id}' disabled.", fg=typer.colors.GREEN)


@identities_app.command("enable")
@handle_api_errors
def enable_identity(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
) -> None:
    """Re-enable a disabled identity."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.enable_secret_identity(
        identity_id,
        workspace_id=require_workspace(settings),
    )
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Identity '{identity_id}' enabled.", fg=typer.colors.GREEN)


@identities_app.command("access")
@handle_api_errors
def get_identity_access(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    show_sensitive: bool = typer.Option(
        False,
        "--show-sensitive",
        help="Acknowledge that generated credentials will be printed",
    ),
) -> None:
    """Generate or return workload access details, which may contain credentials."""
    _require_sensitive_output(show_sensitive)
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.get_secret_identity_access(
        identity_id,
        workspace_id=require_workspace(settings),
    )
    print_json(result)


@identities_app.command("rotate-secret-id")
@handle_api_errors
def rotate_identity_secret_id(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="AppRole application identity ID"),
    show_sensitive: bool = typer.Option(
        False,
        "--show-sensitive",
        help="Acknowledge that fresh AppRole credentials will be printed",
    ),
) -> None:
    """Generate fresh AppRole credentials and print them once."""
    _require_sensitive_output(show_sensitive)
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.rotate_secret_identity_secret_id(
        identity_id,
        workspace_id=require_workspace(settings),
    )
    print_json(result)


@identities_app.command("revoke-sessions")
@handle_api_errors
def revoke_identity_sessions(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Revoke active sessions without deleting or disabling the identity."""
    settings = get_settings(ctx)
    confirm_destructive(get_settings(ctx), f"Revoke all active sessions for identity '{identity_id}'?", yes)
    client = get_client(settings)
    result = client.secret_store.revoke_secret_identity_sessions(
        identity_id,
        workspace_id=require_workspace(settings),
    )
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Identity '{identity_id}' sessions revoked.", fg=typer.colors.GREEN)


@identities_app.command("delete")
@handle_api_errors
def delete_identity(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm permanent deletion"),
) -> None:
    """Permanently delete an identity, its scopes, policy, role, and sessions."""
    settings = get_settings(ctx)
    confirm_destructive(
        get_settings(ctx),
        f"Permanently delete identity '{identity_id}', all scopes, and all sessions? "
        "This cannot be undone.",
        yes,
    )
    client = get_client(settings)
    client.secret_store.delete_secret_identity(
        identity_id,
        workspace_id=require_workspace(settings),
    )
    typer.secho(f"Identity '{identity_id}' permanently deleted.", fg=typer.colors.GREEN)


# ── identity scopes: `ibee secrets identities scopes <verb>` ────────────────

@identity_scopes_app.command("list")
@handle_api_errors
def list_identity_scopes(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
) -> None:
    """List store access granted to an application identity."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.list_secret_identity_scopes(
        identity_id,
        workspace_id=require_workspace(settings),
    )
    if settings.structured_output:
        print_json(result)
        return
    scopes = getattr(result, "scopes", None) or []
    print_table(
        "Identity scopes",
        ["ID", "Store", "Access", "Version read", "Rollback", "Destroy"],
        [
            (
                scope.id,
                scope.store_id,
                getattr(scope.access_mode, "value", scope.access_mode),
                scope.allow_version_read,
                scope.allow_rollback,
                scope.allow_destroy,
            )
            for scope in scopes
        ],
    )


@identity_scopes_app.command("create")
@handle_api_errors
def create_identity_scope(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    store_id: str = typer.Option(..., "--store-id", "-s", help="Secret store to grant"),
    access_mode: AccessMode = typer.Option(
        AccessMode.READ_ONLY,
        "--access-mode",
        help="read_only or read_write",
    ),
    allow_version_read: bool = typer.Option(
        False,
        "--allow-version-read",
        help="Allow reading historical version values",
    ),
    allow_rollback: bool = typer.Option(False, "--allow-rollback", help="Allow rollback"),
    allow_destroy: bool = typer.Option(
        False,
        "--allow-destroy",
        help="Allow irreversible version destruction",
    ),
) -> None:
    """Grant an application identity access to another secret store."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.create_secret_identity_scope(
        identity_id,
        workspace_id=require_workspace(settings),
        store_id=store_id,
        access_mode=access_mode.value,
        allow_version_read=allow_version_read,
        allow_rollback=allow_rollback,
        allow_destroy=allow_destroy,
    )
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(
        f"Scope created (id {getattr(result, 'id', '?')}) for identity '{identity_id}'.",
        fg=typer.colors.GREEN,
    )


@identity_scopes_app.command("update")
@handle_api_errors
def update_identity_scope(
    ctx: typer.Context,
    scope_id: str = typer.Argument(..., help="Identity scope ID"),
    access_mode: Optional[AccessMode] = typer.Option(
        None,
        "--access-mode",
        help="Set read_only or read_write",
    ),
    allow_version_read: Optional[bool] = typer.Option(
        None,
        "--allow-version-read/--deny-version-read",
        help="Allow or deny historical version reads",
    ),
    allow_rollback: Optional[bool] = typer.Option(
        None,
        "--allow-rollback/--deny-rollback",
        help="Allow or deny rollback",
    ),
    allow_destroy: Optional[bool] = typer.Option(
        None,
        "--allow-destroy/--deny-destroy",
        help="Allow or deny irreversible version destruction",
    ),
) -> None:
    """Update store access and version permissions for an identity scope."""
    if (
        access_mode is None
        and allow_version_read is None
        and allow_rollback is None
        and allow_destroy is None
    ):
        raise typer.BadParameter("Provide at least one access or permission option to update.")
    settings = get_settings(ctx)
    client = get_client(settings)
    changes: dict[str, str | bool] = {}
    if access_mode is not None:
        changes["access_mode"] = access_mode.value
    if allow_version_read is not None:
        changes["allow_version_read"] = allow_version_read
    if allow_rollback is not None:
        changes["allow_rollback"] = allow_rollback
    if allow_destroy is not None:
        changes["allow_destroy"] = allow_destroy
    result = client.secret_store.update_secret_identity_scope(
        scope_id,
        workspace_id=require_workspace(settings),
        **changes,
    )
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Scope '{scope_id}' updated.", fg=typer.colors.GREEN)


@identity_scopes_app.command("delete")
@handle_api_errors
def delete_identity_scope(
    ctx: typer.Context,
    scope_id: str = typer.Argument(..., help="Identity scope ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm scope deletion"),
) -> None:
    """Delete an identity's access to a secret store."""
    settings = get_settings(ctx)
    confirm_destructive(
        get_settings(ctx),
        f"Delete identity scope '{scope_id}' and remove its store access?",
        yes,
    )
    client = get_client(settings)
    client.secret_store.delete_secret_identity_scope(
        scope_id,
        workspace_id=require_workspace(settings),
    )
    typer.secho(f"Scope '{scope_id}' deleted.", fg=typer.colors.GREEN)


# ── secrets: `ibee secrets <verb>` ──────────────────────────────────────────

@app.command("list")
@handle_api_errors
def list_secrets(
    ctx: typer.Context,
    store_id: str = typer.Option(..., "--store-id", "-s", help="Secret store ID"),
) -> None:
    """List secrets inside a store (metadata only)."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.list_secrets(
        workspace_id=require_workspace(settings), store_id=store_id
    )
    if settings.structured_output:
        print_json(result)
        return
    secrets = getattr(result, "secrets", None) or []
    print_table(
        "Secrets",
        ["ID", "Name", "Status", "Updated"],
        [
            (s.id, s.name, getattr(s.status, "value", s.status), str(getattr(s, "updated_at", "") or "")[:19])
            for s in secrets
        ],
    )


@app.command("create")
@handle_api_errors
def create_secret(
    ctx: typer.Context,
    store_id: str = typer.Option(..., "--store-id", "-s", help="Secret store ID"),
    name: str = typer.Option(..., "--name", "-n", help="Secret name (lowercase, hyphens)"),
    value: str = typer.Option(..., "--value", help="Value: JSON object or key=value,key=value"),
) -> None:
    """Create a secret in a store."""
    settings = get_settings(ctx)
    client = get_client(settings)
    workspace = require_workspace(settings)
    preflight_create(settings, "secret", client=client)
    result = client.secret_store.create_secret(
        workspace_id=workspace,
        store_id=store_id,
        secret_name=name,
        value=parse_value(value),
    )
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Secret '{name}' created (id {getattr(result, 'id', '?')}).", fg=typer.colors.GREEN)


@app.command("batch-create")
@handle_api_errors
def batch_create_secrets(
    ctx: typer.Context,
    store_id: str = typer.Option(..., "--store-id", "-s", help="Secret store ID"),
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        exists=True,
        readable=True,
        dir_okay=False,
        resolve_path=True,
        help="JSON file containing an array or {\"secrets\": [...]} (maximum 500)",
    ),
) -> None:
    """Create many secrets without overwriting names that already exist."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.batch_create_secrets(
        store_id,
        workspace_id=require_workspace(settings),
        secrets=_load_batch(file),
    )
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(
        "Batch complete: "
        f"{getattr(result, 'created_count', 0)} created, "
        f"{getattr(result, 'skipped_count', 0)} skipped, "
        f"{getattr(result, 'failed_count', 0)} failed.",
        fg=typer.colors.GREEN,
    )


@app.command("get")
@handle_api_errors
def get_secret(ctx: typer.Context, secret_id: str = typer.Argument(..., help="Secret ID")) -> None:
    """Show one secret's metadata (value not included — use `secrets value`)."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.get_secret(
        workspace_id=require_workspace(settings), secret_id=secret_id
    )
    print_json(result)


@app.command("value")
@handle_api_errors
def get_value(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
) -> None:
    """Print a secret's current value as JSON. Requires secret-store.read scope."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.get_secret_value(
        workspace_id=require_workspace(settings), secret_id=secret_id
    )
    print_json(result)


@app.command("set-value")
@handle_api_errors
def set_value(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    value: str = typer.Option(..., "--value", help="New value: JSON object or key=value,key=value"),
    cas: Optional[int] = typer.Option(None, "--cas", help="Check-and-set: only update if current version matches"),
) -> None:
    """Create a new version of a secret's value."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.update_secret_value(
        workspace_id=require_workspace(settings),
        secret_id=secret_id,
        value=parse_value(value),
        cas=cas,
    )
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Secret '{secret_id}' value updated.", fg=typer.colors.GREEN)


@app.command("patch-value")
@handle_api_errors
def patch_value(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    value: str = typer.Option(..., "--value", help="Keys to merge: JSON object or key=value pairs"),
) -> None:
    """Merge keys into a secret and create a new version."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.patch_secret_value(
        secret_id,
        workspace_id=require_workspace(settings),
        value=parse_value(value),
    )
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Secret '{secret_id}' value patched.", fg=typer.colors.GREEN)


@app.command("versions")
@handle_api_errors
def list_versions(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
) -> None:
    """List version metadata without returning secret values."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.list_secret_versions(
        secret_id, workspace_id=require_workspace(settings)
    )
    print_json(result)


@app.command("version")
@handle_api_errors
def get_version(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    version: int = typer.Argument(..., min=1, help="Version number"),
) -> None:
    """Print one secret version, including its sensitive value."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.get_secret_version(
        secret_id, version, workspace_id=require_workspace(settings)
    )
    print_json(result)


@app.command("rollback")
@handle_api_errors
def rollback_secret(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    version: int = typer.Option(..., "--version", min=1, help="Previous version to restore"),
) -> None:
    """Copy a previous value into a new current version."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.rollback_secret(
        secret_id,
        workspace_id=require_workspace(settings),
        version=version,
    )
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Secret '{secret_id}' rolled back from version {version}.", fg=typer.colors.GREEN)


@app.command("undelete")
@handle_api_errors
def undelete_secret(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    versions: str = typer.Option(..., "--versions", help="Comma-separated versions to restore"),
) -> None:
    """Restore soft-deleted versions and reactivate a secret."""
    settings = get_settings(ctx)
    client = get_client(settings)
    client.secret_store.undelete_secret(
        secret_id,
        workspace_id=require_workspace(settings),
        versions=_parse_versions(versions),
    )
    typer.secho(f"Secret '{secret_id}' restored.", fg=typer.colors.GREEN)


@app.command("destroy-versions")
@handle_api_errors
def destroy_versions(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    versions: str = typer.Option(..., "--versions", help="Comma-separated versions to destroy"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm irreversible destruction"),
) -> None:
    """Irreversibly destroy selected secret versions."""
    parsed_versions = _parse_versions(versions)
    settings = get_settings(ctx)
    confirm_destructive(
        get_settings(ctx),
        f"Destroy versions {parsed_versions} of secret '{secret_id}'? This cannot be undone.",
        yes,
    )
    client = get_client(settings)
    client.secret_store.destroy_secret_versions(
        secret_id,
        workspace_id=require_workspace(settings),
        versions=parsed_versions,
    )
    typer.secho(f"Secret '{secret_id}' versions destroyed.", fg=typer.colors.GREEN)


@app.command("delete-permanent")
@handle_api_errors
def permanently_delete_secret(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm irreversible deletion"),
) -> None:
    """Permanently delete all versions and metadata. This cannot be undone."""
    settings = get_settings(ctx)
    confirm_destructive(
        get_settings(ctx),
        f"Permanently delete secret '{secret_id}' and all versions? This cannot be undone.",
        yes,
    )
    client = get_client(settings)
    client.secret_store.permanently_delete_secret(
        secret_id, workspace_id=require_workspace(settings)
    )
    typer.secho(f"Secret '{secret_id}' permanently deleted.", fg=typer.colors.GREEN)


@app.command("delete")
@handle_api_errors
def delete_secret(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete (soft) a secret."""
    settings = get_settings(ctx)
    confirm_destructive(get_settings(ctx), f"Delete secret '{secret_id}'?", yes)
    client = get_client(settings)
    client.secret_store.delete_secret(
        workspace_id=require_workspace(settings), secret_id=secret_id
    )
    typer.secho(f"Secret '{secret_id}' deleted.", fg=typer.colors.GREEN)
