"""Secret Store commands."""

from __future__ import annotations

import typer

from ..context import get_client, get_settings, require_workspace
from ..render import handle_api_errors, print_json, print_table

app = typer.Typer(help="Secret Store: stores and secrets", no_args_is_help=True)


@app.command("stores")
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


@app.command("list")
@handle_api_errors
def list_secrets(
    ctx: typer.Context,
    store_id: str = typer.Option(..., "--store-id", "-s", help="Secret store ID"),
) -> None:
    """List secrets inside a store."""
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
