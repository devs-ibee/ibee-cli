"""Standalone Block Storage volumes (``ibee block-storage``).

Each command makes one SDK call and prints the server's answer; the server checks
names, sizes, plans, states and billing. To attach a volume to a VM use
``ibee vms|gpus volume-attach`` and ``volume-detach``.
"""

from __future__ import annotations

from typing import Optional

import typer

from ..context import get_client, get_settings
from ..helpers import compact_payload, confirm_destructive, field, idempotency_key_option
from ..render import cell, emit, handle_api_errors, print_json

app = typer.Typer(
    help="Standalone Block Storage volumes. Attach them to VMs with 'ibee vms volume-attach'.",
    no_args_is_help=True,
)

ID_FIELD = "id"
LIST_HEADERS = ("ID", "Name", "Size (GB)", "State", "Site", "Attached to")
NODE_LEVEL_NOTE = (
    "Advanced: records a storage-node attachment only. Use 'ibee vms volume-attach' / 'volume-detach' for VMs."
)


def _block_storage(ctx: typer.Context):
    settings = get_settings(ctx)
    return settings, get_client(settings).block_storage


def _first(item, *names):
    """The first of ``names`` that has a value (display only)."""
    return next((value for value in (cell(item, name) for name in names) if value not in (None, "")), None)


def _row(volume) -> tuple:
    attached = ", ".join(str(_first(a, "vm_name", "vm_id", "node_name")) for a in cell(volume, "attachments") or [])
    columns = ("id", "name", "size_gb", "state")
    return (*(cell(volume, c) for c in columns), _first(volume, "site_name", "site_id"), attached or None)


@app.command("list")
@handle_api_errors
def list_volumes(ctx: typer.Context) -> None:
    """List volumes in the workspace (-o table shows a summary table)."""
    settings, storage = _block_storage(ctx)
    volumes = list(storage.list_block_volumes() or [])
    emit(volumes, settings=settings, default="json", headers=LIST_HEADERS, rows=[_row(v) for v in volumes],
         title="Block Storage volumes", id_field=ID_FIELD)


@app.command("create")
@handle_api_errors
def create_volume(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Volume name"),
    size_gb: int = typer.Option(..., "--size-gb", help="Size in GB"),
    site_id: str = typer.Option(..., "--site-id", help="Placement site ID (see 'ibee compute sites')"),
    site_name: Optional[str] = typer.Option(None, "--site-name", help="Site display name"),
    sku_code: Optional[str] = typer.Option(None, "--sku-code", help="Block Storage SKU from the server's catalog"),
    volume_class: Optional[str] = typer.Option(None, "--class", help="capacity, balanced or performance"),
    replica_count: Optional[int] = typer.Option(None, "--replicas", help="Replica count"),
    backup_enabled: Optional[bool] = typer.Option(None, "--backup-enabled/--no-backup", help="Volume backups"),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Create a volume. The server resolves the plan and billing."""
    settings, storage = _block_storage(ctx)
    result = storage.create_block_volume(
        name=name,
        size_gb=size_gb,
        site_id=site_id,
        **compact_payload(
            site_name=site_name,
            sku_code=sku_code,
            volume_class=volume_class,
            replica_count=replica_count,
            backup_enabled=backup_enabled,
            idempotency_key=idempotency_key,
        ),
    )
    if settings.output == "id":
        typer.echo(str(field(field(result, "volume"), "id") or ""))
        return
    print_json(result)


@app.command("get")
@handle_api_errors
def get_volume(ctx: typer.Context, volume_id: str = typer.Argument(..., help="Volume ID")) -> None:
    """Show a volume and its attachments."""
    _settings, storage = _block_storage(ctx)
    print_json(storage.get_block_volume(volume_id), id_field=ID_FIELD)


@app.command("operations")
@handle_api_errors
def list_operations(ctx: typer.Context, volume_id: str = typer.Argument(..., help="Volume ID")) -> None:
    """List a volume's operations."""
    _settings, storage = _block_storage(ctx)
    print_json(storage.list_block_volume_operations(volume_id), id_field=ID_FIELD)


@app.command("attach", help=f"Attach a volume to a storage node. {NODE_LEVEL_NOTE}")
@handle_api_errors
def attach_volume(
    ctx: typer.Context,
    volume_id: str = typer.Argument(..., help="Volume ID"),
    node_name: str = typer.Option(..., "--node-name", help="Storage node name"),
    mode: Optional[str] = typer.Option(None, "--mode", help="single-writer or multi-writer"),
    vm_id: Optional[str] = typer.Option(None, "--vm-id"),
    vm_name: Optional[str] = typer.Option(None, "--vm-name"),
    vm_state: Optional[str] = typer.Option(None, "--vm-state"),
    vm_site_id: Optional[str] = typer.Option(None, "--vm-site-id"),
    vm_type: Optional[str] = typer.Option(None, "--vm-type", help="cloud or gpu"),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    _settings, storage = _block_storage(ctx)
    print_json(storage.attach_block_volume(
        volume_id,
        node_name=node_name,
        **compact_payload(mode=mode, vm_id=vm_id, vm_name=vm_name, vm_state=vm_state, vm_site_id=vm_site_id,
                          vm_type=vm_type, idempotency_key=idempotency_key),
    ))


@app.command("detach", help=f"Detach a volume from a storage node. {NODE_LEVEL_NOTE}")
@handle_api_errors
def detach_volume(
    ctx: typer.Context,
    volume_id: str = typer.Argument(..., help="Volume ID"),
    node_name: str = typer.Option(..., "--node-name", help="Storage node name"),
    force: Optional[bool] = typer.Option(None, "--force", help="Ask the server to skip its unmount check"),
    confirm_unmounted: Optional[bool] = typer.Option(
        None, "--confirm-unmounted", help="I have unmounted this volume from the server"
    ),
    vm_state: Optional[str] = typer.Option(None, "--vm-state"),
    vm_type: Optional[str] = typer.Option(None, "--vm-type", help="cloud or gpu"),
    reason: Optional[str] = typer.Option(None, "--reason"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    settings, storage = _block_storage(ctx)
    confirm_destructive(settings, f"Detach volume '{volume_id}' from node '{node_name}'?", yes)
    print_json(storage.detach_block_volume(
        volume_id,
        node_name=node_name,
        **compact_payload(force=force, confirm_unmounted=confirm_unmounted, vm_state=vm_state, vm_type=vm_type,
                          reason=reason, idempotency_key=idempotency_key),
    ))


@app.command("resize")
@handle_api_errors
def resize_volume(
    ctx: typer.Context,
    volume_id: str = typer.Argument(..., help="Volume ID"),
    new_size_gb: int = typer.Option(..., "--new-size-gb", help="New size in GB"),
    allow_online: Optional[bool] = typer.Option(None, "--allow-online", help="Resize while attached to a running VM"),
    vm_state: Optional[str] = typer.Option(None, "--vm-state", help="State of the attached VM"),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Resize a volume. After growing it, extend the filesystem inside the VM."""
    _settings, storage = _block_storage(ctx)
    print_json(storage.resize_block_volume(
        volume_id,
        new_size_gb=new_size_gb,
        **compact_payload(allow_online=allow_online, vm_state=vm_state, idempotency_key=idempotency_key),
    ))


@app.command("delete")
@handle_api_errors
def delete_volume(
    ctx: typer.Context,
    volume_id: str = typer.Argument(..., help="Volume ID"),
    force: Optional[bool] = typer.Option(None, "--force", help="Ask the server to detach and delete anyway"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a volume and its data."""
    settings, storage = _block_storage(ctx)
    confirm_destructive(settings, f"Delete Block Storage volume '{volume_id}'?", yes)
    print_json(storage.delete_block_volume(volume_id, **compact_payload(force=force)), id_field=ID_FIELD)
