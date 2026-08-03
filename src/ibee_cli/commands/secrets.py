"""Secret Store commands: stores (`ibee secrets stores ...`) and secrets."""

from __future__ import annotations

from typing import Optional

import typer

from ..context import get_client, get_settings, require_workspace
from ..helpers import parse_value, require_billing_eligibility
from ..render import handle_api_errors, print_json, print_table

app = typer.Typer(help="Secret Store: stores and secrets", no_args_is_help=True)

# ── stores sub-group: `ibee secrets stores <verb>` ──────────────────────────
stores_app = typer.Typer(help="Manage secret stores", no_args_is_help=True)
app.add_typer(stores_app, name="stores")


@stores_app.command("list")
@handle_api_errors
def list_stores(ctx: typer.Context) -> None:
    """List secret stores in the workspace."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.secret_store.list_secret_stores(workspace_id=require_workspace(settings))
    if settings.as_json:
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
    require_billing_eligibility(
        client, workspace, sku_code="SECRETMA-STD"
    )
    result = client.secret_store.create_secret_store(
        workspace_id=workspace, name=name, description=description
    )
    if settings.as_json:
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
    if settings.as_json:
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
    if not yes:
        typer.confirm(f"Archive secret store '{store_id}'?", abort=True)
    client = get_client(settings)
    client.secret_store.archive_secret_store(
        workspace_id=require_workspace(settings), store_id=store_id
    )
    typer.secho(f"Store '{store_id}' archived.", fg=typer.colors.GREEN)


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
    if settings.as_json:
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
    require_billing_eligibility(
        client, workspace, sku_code="SECRETMA-STD"
    )
    result = client.secret_store.create_secret(
        workspace_id=workspace,
        store_id=store_id,
        secret_name=name,
        value=parse_value(value),
    )
    if settings.as_json:
        print_json(result)
        return
    typer.secho(f"Secret '{name}' created (id {getattr(result, 'id', '?')}).", fg=typer.colors.GREEN)


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
    if settings.as_json:
        print_json(result)
        return
    typer.secho(f"Secret '{secret_id}' value updated.", fg=typer.colors.GREEN)


@app.command("delete")
@handle_api_errors
def delete_secret(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete (soft) a secret."""
    settings = get_settings(ctx)
    if not yes:
        typer.confirm(f"Delete secret '{secret_id}'?", abort=True)
    client = get_client(settings)
    client.secret_store.delete_secret(
        workspace_id=require_workspace(settings), secret_id=secret_id
    )
    typer.secho(f"Secret '{secret_id}' deleted.", fg=typer.colors.GREEN)
