"""Standalone Block Storage lifecycle commands."""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote

import typer

from ..context import api_request, get_client, get_settings, require_workspace
from ..helpers import (
    compact_payload,
    confirm_destructive,
    idempotency_key_option,
    preflight_billing,
    resolve_idempotency_key,
)
from ..render import handle_api_errors, print_json

app = typer.Typer(help="Standalone persistent Block Storage volumes", no_args_is_help=True)


def _segment(value: str) -> str:
    return quote(value, safe="")


def _call(ctx: typer.Context, method: str, path: str, *, payload: dict | None = None,
          params: dict | None = None) -> None:
    result = api_request(get_settings(ctx), method, path, params=params, json_body=payload)
    if result is not None:
        print_json(result)


@app.command("list")
@handle_api_errors
def list_volumes(ctx: typer.Context) -> None:
    """List standalone volumes in the workspace."""
    _call(ctx, "GET", "block-storage/volumes")


@app.command("create")
@handle_api_errors
def create_volume(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    size_gb: int = typer.Option(..., "--size-gb", min=1, max=10000),
    site_id: str = typer.Option(..., "--site-id", help="Placement site ID"),
    site_name: Optional[str] = typer.Option(None, "--site-name"),
    sku_code: Optional[str] = typer.Option(None, "--sku-code", help="Optional server-catalog SKU selector"),
    volume_class: str = typer.Option("balanced", "--class"),
    replica_count: int = typer.Option(2, "--replicas", min=1, max=5),
    backup_enabled: bool = typer.Option(
        True, "--backup-enabled/--no-backup", help="Enable volume backups"
    ),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Create a volume; billing metadata is always resolved by the server."""
    key = resolve_idempotency_key(idempotency_key, "block-create", name)
    settings = get_settings(ctx)
    if settings.check_billing:
        preflight_billing(
            settings,
            get_client(settings),
            require_workspace(settings),
            sku_code=sku_code,
            resource_type="block_storage",
        )
    _call(ctx, "POST", "block-storage/volumes", payload=compact_payload(
        name=name, size_gb=size_gb, site_id=site_id, site_name=site_name,
        sku_code=sku_code,
        volume_class=volume_class, replica_count=replica_count,
        backup_enabled=backup_enabled,
        idempotency_key=key,
    ))


@app.command("get")
@handle_api_errors
def get_volume(ctx: typer.Context, volume_id: str = typer.Argument(...)) -> None:
    _call(ctx, "GET", f"block-storage/volumes/{_segment(volume_id)}")


@app.command("operations")
@handle_api_errors
def list_operations(ctx: typer.Context, volume_id: str = typer.Argument(...)) -> None:
    _call(ctx, "GET", f"block-storage/volumes/{_segment(volume_id)}/operations")


@app.command("attach")
@handle_api_errors
def attach_volume(
    ctx: typer.Context,
    volume_id: str = typer.Argument(...),
    node_name: str = typer.Option(..., "--node-name"),
    mode: str = typer.Option("single-writer", "--mode"),
    vm_id: Optional[str] = typer.Option(None, "--vm-id"),
    vm_name: Optional[str] = typer.Option(None, "--vm-name"),
    vm_state: Optional[str] = typer.Option(None, "--vm-state"),
    vm_site_id: Optional[str] = typer.Option(None, "--vm-site-id"),
    vm_type: str = typer.Option("cloud", "--vm-type"),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    key = resolve_idempotency_key(idempotency_key, "block-attach", volume_id)
    _call(ctx, "POST", f"block-storage/volumes/{_segment(volume_id)}/attachments", payload=compact_payload(
        node_name=node_name, mode=mode, vm_id=vm_id, vm_name=vm_name,
        vm_state=vm_state, vm_site_id=vm_site_id, vm_type=vm_type,
        idempotency_key=key,
    ))


@app.command("detach")
@handle_api_errors
def detach_volume(
    ctx: typer.Context,
    volume_id: str = typer.Argument(...),
    node_name: str = typer.Option(..., "--node-name"),
    force: bool = typer.Option(False, "--force"),
    confirm_unmounted: bool = typer.Option(False, "--confirm-unmounted"),
    vm_state: Optional[str] = typer.Option(None, "--vm-state"),
    vm_type: str = typer.Option("cloud", "--vm-type"),
    reason: Optional[str] = typer.Option(None, "--reason"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    key = resolve_idempotency_key(idempotency_key, "block-detach", volume_id)
    confirm_destructive(get_settings(ctx), f"Detach Block Storage volume '{volume_id}'?", yes)
    _call(ctx, "POST", f"block-storage/volumes/{_segment(volume_id)}/detach", payload=compact_payload(
        node_name=node_name, force=force, confirm_unmounted=confirm_unmounted,
        vm_state=vm_state, vm_type=vm_type, reason=reason,
        idempotency_key=key,
    ))


@app.command("resize")
@handle_api_errors
def resize_volume(
    ctx: typer.Context,
    volume_id: str = typer.Argument(...),
    new_size_gb: int = typer.Option(..., "--new-size-gb", min=1, max=10000),
    allow_online: bool = typer.Option(False, "--allow-online"),
    vm_state: Optional[str] = typer.Option(None, "--vm-state"),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    key = resolve_idempotency_key(idempotency_key, "block-resize", volume_id)
    _call(ctx, "POST", f"block-storage/volumes/{_segment(volume_id)}/resize", payload=compact_payload(
        new_size_gb=new_size_gb, allow_online=allow_online, vm_state=vm_state,
        idempotency_key=key,
    ))


@app.command("delete")
@handle_api_errors
def delete_volume(
    ctx: typer.Context,
    volume_id: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Delete a volume.

    The idempotency key is sent as a query parameter, which is not yet part of the
    published API contract; behaviour may change.
    """
    key = resolve_idempotency_key(idempotency_key, "block-delete", volume_id)
    confirm_destructive(get_settings(ctx), f"Delete Block Storage volume '{volume_id}'?", yes)
    _call(
        ctx,
        "DELETE",
        f"block-storage/volumes/{_segment(volume_id)}",
        params={"force": force, "idempotency_key": key},
    )
