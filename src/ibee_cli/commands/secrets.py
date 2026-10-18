"""Secret Store commands: stores, secrets, application identities and their scopes.

Each command makes one SDK call and prints the server's answer. The server decides
every rule (names, values, states, permissions and billing).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import typer

from ..context import get_client, get_settings
from ..helpers import compact_payload, confirm_destructive, field, parse_json, parse_value, response_items
from ..render import cell, emit, handle_api_errors, print_json, print_table

app = typer.Typer(help="Secret Store: stores, secrets, and application identities", no_args_is_help=True)
stores_app = typer.Typer(help="Manage secret stores", no_args_is_help=True)
identities_app = typer.Typer(help="Manage AppRole and Kubernetes application identities", no_args_is_help=True)
identity_scopes_app = typer.Typer(help="Manage store access granted to application identities", no_args_is_help=True)
app.add_typer(stores_app, name="stores")
app.add_typer(identities_app, name="identities")
identities_app.add_typer(identity_scopes_app, name="scopes")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _api(ctx: typer.Context) -> tuple[Any, Any]:
    """``(settings, client.secret_store)`` for this invocation."""
    settings = get_settings(ctx)
    return settings, get_client(settings).secret_store


def _show(item: Any, name: str) -> Any:
    value = cell(item, name)
    return str(value)[:19] if name.endswith("_at") and value else value


def _print_list(settings: Any, result: Any, title: str, key: str, columns: dict[str, str]) -> None:
    """A table of ``result.<key>`` (``columns`` maps header to field); -o json|yaml|id prints the answer."""
    if settings.structured_output:
        print_json(result)
        return
    rows = [[_show(item, name) for name in columns.values()] for item in response_items(result, key)]
    print_table(title, list(columns), rows)


def _confirm_on_terminal(settings: Any, prompt: str, yes: bool) -> None:
    """Ask only on a terminal (as in 0.4), so unattended scripts keep working."""
    try:
        interactive = sys.stdin.isatty()
    except (AttributeError, ValueError):  # pragma: no cover - closed or replaced stdin
        interactive = False
    if interactive:
        confirm_destructive(settings, prompt, yes)


def _require_show_sensitive(settings: Any, show_sensitive: bool) -> None:
    """The answer carries credentials: send only with --show-sensitive or -o json|yaml|id."""
    if not (show_sensitive or settings.structured_output):
        raise typer.BadParameter(
            "this command prints credentials; pass --show-sensitive (or -o json|yaml|id).",
            param_hint="--show-sensitive",
        )


def _versions(raw: str) -> list[int]:
    try:
        return [int(part) for part in raw.split(",") if part.strip()]
    except ValueError as exc:
        raise typer.BadParameter("expects comma-separated integers, for example 1,2", param_hint="--versions") from exc


def _yes() -> Any:
    return typer.Option(False, "--yes", "-y", help="Skip confirmation")


def _show_sensitive() -> Any:
    return typer.Option(False, "--show-sensitive", help="Print the credentials in the answer")


def _store_id() -> Any:
    return typer.Option(..., "--store-id", "-s", help="Secret store ID")


# ---------------------------------------------------------------------------
# Stores: `ibee secrets stores <verb>`
# ---------------------------------------------------------------------------


@stores_app.command("list")
@handle_api_errors
def list_stores(
    ctx: typer.Context,
    include_archived: bool = typer.Option(
        True, "--include-archived/--active-only", help="Include archived stores (default) or list active stores only"
    ),
    page: Optional[int] = typer.Option(None, "--page", help="Page number"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Stores per page"),
) -> None:
    """List secret stores in the workspace."""
    settings, api = _api(ctx)
    result = api.list_secret_stores(**compact_payload(include_archived=include_archived, page=page, limit=limit))
    columns = {"ID": "id", "Name": "name", "Store key": "store_key", "Status": "status", "Updated": "updated_at"}
    _print_list(settings, result, "Secret stores", "stores", columns)


@stores_app.command("create")
@handle_api_errors
def create_store(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Store name"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Store description"),
) -> None:
    """Create a secret store."""
    _settings, api = _api(ctx)
    print_json(api.create_secret_store(name=name, **compact_payload(description=description)))


@stores_app.command("get")
@handle_api_errors
def get_store(ctx: typer.Context, store_id: str = typer.Argument(..., help="Store ID")) -> None:
    """Show one secret store."""
    _settings, api = _api(ctx)
    print_json(api.get_secret_store(store_id))


@stores_app.command("update")
@handle_api_errors
def update_store(
    ctx: typer.Context,
    store_id: str = typer.Argument(..., help="Store ID"),
    name: Optional[str] = typer.Option(None, "--name", help="New name"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="New description"),
) -> None:
    """Update a secret store's name or description."""
    _settings, api = _api(ctx)
    print_json(api.update_secret_store(store_id, **compact_payload(name=name, description=description)))


@stores_app.command("archive")
@handle_api_errors
def archive_store(ctx: typer.Context, store_id: str = typer.Argument(..., help="Store ID"), yes: bool = _yes()) -> None:
    """Archive a secret store."""
    settings, api = _api(ctx)
    confirm_destructive(settings, f"Archive secret store '{store_id}'?", yes)
    print_json(api.archive_secret_store(store_id))


@stores_app.command("unarchive")
@handle_api_errors
def unarchive_store(
    ctx: typer.Context,
    store_id: str = typer.Argument(..., help="Store ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation (asked only on a terminal)"),
) -> None:
    """Restore an archived secret store."""
    settings, api = _api(ctx)
    _confirm_on_terminal(settings, f"Restore secret store '{store_id}'?", yes)
    print_json(api.unarchive_secret_store(store_id))


@stores_app.command("delete-permanent")
@handle_api_errors
def permanently_delete_store(
    ctx: typer.Context,
    store_id: str = typer.Argument(..., help="Store ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm irreversible deletion"),
) -> None:
    """Permanently delete a store, its secrets and its access entries."""
    settings, api = _api(ctx)
    confirm_destructive(settings, f"Permanently delete secret store '{store_id}'? This cannot be undone.", yes)
    print_json(api.permanently_delete_secret_store(store_id))


# ---------------------------------------------------------------------------
# Application identities: `ibee secrets identities <verb>`
# ---------------------------------------------------------------------------


@identities_app.command("list")
@handle_api_errors
def list_identities(ctx: typer.Context, store_id: str = _store_id()) -> None:
    """List application identities bound to a store."""
    settings, api = _api(ctx)
    columns = {
        "ID": "id", "Name": "name", "Auth method": "auth_method", "Policy": "token_policy_mode",
        "Status": "status", "Updated": "updated_at",
    }
    _print_list(settings, api.list_secret_identities(store_id), "Secret Store identities", "identities", columns)


@identities_app.command("create")
@handle_api_errors
def create_identity(
    ctx: typer.Context,
    store_id: str = typer.Option(..., "--store-id", "-s", help="Initial secret store ID"),
    name: str = typer.Option(..., "--name", "-n", help="Application identity name"),
    auth_method: str = typer.Option(..., "--auth-method", help="approle or kubernetes"),
    token_policy_mode: Optional[str] = typer.Option(None, "--token-policy-mode", help="read_only or read_write"),
    k8s_namespace: Optional[str] = typer.Option(None, "--k8s-namespace", help="Kubernetes namespace"),
    k8s_service_account: Optional[str] = typer.Option(None, "--k8s-service-account", help="Kubernetes service account"),
) -> None:
    """Create an AppRole or Kubernetes application identity (login details: `identities access`)."""
    _settings, api = _api(ctx)
    options = compact_payload(
        token_policy_mode=token_policy_mode, k8s_namespace=k8s_namespace, k8s_service_account=k8s_service_account
    )
    print_json(api.create_secret_identity(store_id, auth_method=auth_method, name=name, **options))


@identities_app.command("get")
@handle_api_errors
def get_identity(ctx: typer.Context, identity_id: str = typer.Argument(..., help="Application identity ID")) -> None:
    """Show application identity metadata."""
    _settings, api = _api(ctx)
    print_json(api.get_secret_identity(identity_id))


@identities_app.command("update")
@handle_api_errors
def update_identity(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    token_policy_mode: str = typer.Option(..., "--token-policy-mode", help="read_only or read_write"),
) -> None:
    """Update an identity's token policy mode."""
    _settings, api = _api(ctx)
    print_json(api.update_secret_identity(identity_id, token_policy_mode=token_policy_mode))


@identities_app.command("disable")
@handle_api_errors
def disable_identity(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation (asked only on a terminal)"),
) -> None:
    """Disable an application identity."""
    settings, api = _api(ctx)
    _confirm_on_terminal(settings, f"Disable identity '{identity_id}'?", yes)
    print_json(api.disable_secret_identity(identity_id))


@identities_app.command("enable")
@handle_api_errors
def enable_identity(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation (asked only on a terminal)"),
) -> None:
    """Enable a disabled application identity."""
    settings, api = _api(ctx)
    _confirm_on_terminal(settings, f"Enable identity '{identity_id}'?", yes)
    print_json(api.enable_secret_identity(identity_id))


@identities_app.command("access")
@handle_api_errors
def get_identity_access(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    show_sensitive: bool = _show_sensitive(),
) -> None:
    """Print workload login details (may contain credentials; never retried automatically)."""
    settings, api = _api(ctx)
    _require_show_sensitive(settings, show_sensitive)
    print_json(api.get_secret_identity_access(identity_id), id_field="identity_id")


@identities_app.command("rotate-secret-id")
@handle_api_errors
def rotate_identity_secret_id(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="AppRole application identity ID"),
    show_sensitive: bool = _show_sensitive(),
) -> None:
    """Generate a fresh AppRole secret ID and print it once (never retried automatically)."""
    settings, api = _api(ctx)
    _require_show_sensitive(settings, show_sensitive)
    print_json(api.rotate_secret_identity_secret_id(identity_id), id_field="identity_id")


@identities_app.command("revoke-sessions")
@handle_api_errors
def revoke_identity_sessions(
    ctx: typer.Context, identity_id: str = typer.Argument(..., help="Application identity ID"), yes: bool = _yes()
) -> None:
    """Revoke an identity's active sessions."""
    settings, api = _api(ctx)
    confirm_destructive(settings, f"Revoke all active sessions of identity '{identity_id}'?", yes)
    print_json(api.revoke_secret_identity_sessions(identity_id))


@identities_app.command("delete")
@handle_api_errors
def delete_identity(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm permanent deletion"),
) -> None:
    """Permanently delete an application identity."""
    settings, api = _api(ctx)
    confirm_destructive(settings, f"Delete identity '{identity_id}'? This cannot be undone.", yes)
    print_json(api.delete_secret_identity(identity_id))


# ---------------------------------------------------------------------------
# Identity scopes: `ibee secrets identities scopes <verb>`
# ---------------------------------------------------------------------------


@identity_scopes_app.command("list")
@handle_api_errors
def list_identity_scopes(
    ctx: typer.Context, identity_id: str = typer.Argument(..., help="Application identity ID")
) -> None:
    """List store access granted to an application identity."""
    settings, api = _api(ctx)
    columns = {
        "ID": "id", "Store": "store_id", "Access": "access_mode", "Version read": "allow_version_read",
        "Rollback": "allow_rollback", "Destroy": "allow_destroy",
    }
    _print_list(settings, api.list_secret_identity_scopes(identity_id), "Identity scopes", "scopes", columns)


@identity_scopes_app.command("create")
@handle_api_errors
def create_identity_scope(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    store_id: str = typer.Option(..., "--store-id", "-s", help="Secret store to grant"),
    access_mode: Optional[str] = typer.Option(None, "--access-mode", help="read_only or read_write"),
    allow_version_read: Optional[bool] = typer.Option(
        None, "--allow-version-read/--deny-version-read", help="Allow or deny reading historical versions"
    ),
    allow_rollback: bool = typer.Option(False, "--allow-rollback", help="Allow rollback"),
    allow_destroy: bool = typer.Option(False, "--allow-destroy", help="Allow irreversible version destruction"),
) -> None:
    """Grant an application identity access to a secret store."""
    _settings, api = _api(ctx)
    options = compact_payload(
        access_mode=access_mode,
        allow_version_read=allow_version_read,
        allow_rollback=allow_rollback or None,
        allow_destroy=allow_destroy or None,
    )
    print_json(api.create_secret_identity_scope(identity_id, store_id=store_id, **options))


@identity_scopes_app.command("update")
@handle_api_errors
def update_identity_scope(
    ctx: typer.Context,
    scope_id: str = typer.Argument(..., help="Identity scope ID"),
    access_mode: Optional[str] = typer.Option(None, "--access-mode", help="read_only or read_write"),
    allow_version_read: Optional[bool] = typer.Option(None, "--allow-version-read/--deny-version-read"),
    allow_rollback: Optional[bool] = typer.Option(None, "--allow-rollback/--deny-rollback"),
    allow_destroy: Optional[bool] = typer.Option(None, "--allow-destroy/--deny-destroy"),
) -> None:
    """Update store access and version permissions of an identity scope."""
    _settings, api = _api(ctx)
    options = compact_payload(
        access_mode=access_mode,
        allow_version_read=allow_version_read,
        allow_rollback=allow_rollback,
        allow_destroy=allow_destroy,
    )
    print_json(api.update_secret_identity_scope(scope_id, **options))


@identity_scopes_app.command("delete")
@handle_api_errors
def delete_identity_scope(
    ctx: typer.Context,
    scope_id: str = typer.Argument(..., help="Identity scope ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm scope deletion"),
) -> None:
    """Delete an identity's access to a secret store."""
    settings, api = _api(ctx)
    confirm_destructive(settings, f"Delete identity scope '{scope_id}'?", yes)
    print_json(api.delete_secret_identity_scope(scope_id))


# ---------------------------------------------------------------------------
# Secrets: `ibee secrets <verb>`
# ---------------------------------------------------------------------------


@app.command("list")
@handle_api_errors
def list_secrets(
    ctx: typer.Context,
    store_id: str = _store_id(),
    query: Optional[str] = typer.Option(None, "--query", "-q", help="Name search"),
    page: Optional[int] = typer.Option(None, "--page", help="Page number"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Secrets per page"),
) -> None:
    """List secrets in a store (metadata only)."""
    settings, api = _api(ctx)
    result = api.list_secrets(store_id, **compact_payload(q=query, page=page, limit=limit))
    columns = {"ID": "id", "Name": "secret_name", "Status": "status", "Updated": "updated_at"}
    _print_list(settings, result, "Secrets", "secrets", columns)


@app.command("create")
@handle_api_errors
def create_secret(
    ctx: typer.Context,
    store_id: str = _store_id(),
    name: str = typer.Option(..., "--name", "-n", help="Secret name (sent as typed)"),
    value: str = typer.Option(..., "--value", help="Value: a JSON object or key=value,key=value"),
) -> None:
    """Create a secret in a store."""
    _settings, api = _api(ctx)
    print_json(api.create_secret(store_id, secret_name=name, value=parse_value(value)))


@app.command("batch-create")
@handle_api_errors
def batch_create_secrets(
    ctx: typer.Context,
    store_id: str = _store_id(),
    file: Path = typer.Option(
        ..., "--file", "-f", exists=True, readable=True, dir_okay=False,
        help='JSON file: an array, or {"secrets": [...]}, of {secret_name, value}',
    ),
) -> None:
    """Create many secrets in one request; existing names are skipped by the server.

    Exits 1 when the server reports any failed secret.
    """
    payload = parse_json(file.read_text(encoding="utf-8"), "--file")
    items = payload.get("secrets") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise typer.BadParameter('expects a JSON array or {"secrets": [...]}', param_hint="--file")
    settings, api = _api(ctx)
    result = api.batch_create_secrets(store_id, secrets=items)
    title = (
        f"Batch: {field(result, 'created_count')} created, {field(result, 'skipped_count')} skipped, "
        f"{field(result, 'failed_count')} failed"
    )
    _print_list(settings, result, title, "results", {"Name": "secret_name", "Status": "status", "Error": "error"})
    if field(result, "failed_count"):
        raise typer.Exit(code=1)


@app.command("get")
@handle_api_errors
def get_secret(ctx: typer.Context, secret_id: str = typer.Argument(..., help="Secret ID")) -> None:
    """Show one secret's metadata (the value: `secrets value`)."""
    _settings, api = _api(ctx)
    print_json(api.get_secret(secret_id))


@app.command("value")
@handle_api_errors
def get_value(ctx: typer.Context, secret_id: str = typer.Argument(..., help="Secret ID")) -> None:
    """Print a secret's current value."""
    _settings, api = _api(ctx)
    print_json(api.get_secret_value(secret_id))


@app.command("set-value")
@handle_api_errors
def set_value(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    value: str = typer.Option(..., "--value", help="New full value: a JSON object or key=value,key=value"),
    cas: Optional[int] = typer.Option(None, "--cas", help="Check-and-set: the version the write expects"),
) -> None:
    """Replace a secret's value with a new version."""
    _settings, api = _api(ctx)
    print_json(api.update_secret_value(secret_id, value=parse_value(value), **compact_payload(cas=cas)))


@app.command("patch-value")
@handle_api_errors
def patch_value(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    value: str = typer.Option(..., "--value", help="Keys to merge: a JSON object (null deletes a key) or key=value pairs"),
) -> None:
    """Merge keys into a secret's value as a new version."""
    _settings, api = _api(ctx)
    print_json(api.patch_secret_value(secret_id, value=parse_value(value)))


@app.command("versions")
@handle_api_errors
def list_versions(ctx: typer.Context, secret_id: str = typer.Argument(..., help="Secret ID")) -> None:
    """List version metadata (JSON by default; -o table lists newest first)."""
    settings, api = _api(ctx)
    result = api.list_secret_versions(secret_id)
    summaries = sorted((field(result, "versions") or {}).values(), key=lambda v: field(v, "version") or 0, reverse=True)
    columns = ("version", "created_time", "deletion_time", "destroyed")
    emit(
        result,
        settings=settings,
        default="json",
        title=f"Versions of {field(result, 'secret_name')} (current {field(result, 'current_version')})",
        headers=["Version", "Created", "Deleted", "Destroyed"],
        rows=[[cell(summary, column) for column in columns] for summary in summaries],
        id_field="secret_id",
    )


@app.command("version")
@handle_api_errors
def get_version(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    version: int = typer.Argument(..., help="Version number"),
) -> None:
    """Print one secret version, including its value."""
    _settings, api = _api(ctx)
    print_json(api.get_secret_version(secret_id, version))


@app.command("rollback")
@handle_api_errors
def rollback_secret(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    version: int = typer.Option(..., "--version", help="Version to restore"),
) -> None:
    """Copy a previous version's value into a new current version."""
    _settings, api = _api(ctx)
    print_json(api.rollback_secret(secret_id, version=version))


@app.command("undelete")
@handle_api_errors
def undelete_secret(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    versions: str = typer.Option(..., "--versions", help="Comma-separated versions to restore"),
) -> None:
    """Restore soft-deleted versions of a secret."""
    parsed = _versions(versions)
    _settings, api = _api(ctx)
    print_json(api.undelete_secret(secret_id, versions=parsed))


@app.command("destroy-versions")
@handle_api_errors
def destroy_versions(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    versions: str = typer.Option(..., "--versions", help="Comma-separated versions to destroy"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm irreversible destruction"),
) -> None:
    """Irreversibly destroy selected secret versions."""
    parsed = _versions(versions)
    settings, api = _api(ctx)
    confirm_destructive(settings, f"Destroy versions {versions} of secret '{secret_id}'? This cannot be undone.", yes)
    print_json(api.destroy_secret_versions(secret_id, versions=parsed))


@app.command("delete-permanent")
@handle_api_errors
def permanently_delete_secret(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm irreversible deletion"),
) -> None:
    """Permanently delete a secret, all its versions and metadata."""
    settings, api = _api(ctx)
    confirm_destructive(settings, f"Permanently delete secret '{secret_id}'? This cannot be undone.", yes)
    print_json(api.permanently_delete_secret(secret_id))


@app.command("delete")
@handle_api_errors
def delete_secret(ctx: typer.Context, secret_id: str = typer.Argument(..., help="Secret ID"), yes: bool = _yes()) -> None:
    """Soft-delete a secret (undo with `ibee secrets undelete`)."""
    settings, api = _api(ctx)
    confirm_destructive(settings, f"Delete secret '{secret_id}'?", yes)
    print_json(api.delete_secret(secret_id))
