"""Cloud VM commands."""

from __future__ import annotations

import uuid

import typer

from ..context import get_client, get_settings, require_workspace
from ..render import handle_api_errors, print_json, print_table

app = typer.Typer(help="Cloud VMs", no_args_is_help=True)


@app.command("list")
@handle_api_errors
def list_vms(ctx: typer.Context) -> None:
    """List cloud VMs in the workspace."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.cloud_vms.list_cloud_vms(workspace_id=require_workspace(settings))
    if settings.as_json:
        print_json(result)
        return
    vms = getattr(result, "items", None) or getattr(result, "vms", None) or []
    print_table(
        "Cloud VMs",
        ["ID", "Name", "Status", "CPU", "RAM (MB)", "Public IP"],
        [
            (v.id, v.name, getattr(v.status, "value", v.status), v.cpu, v.ram_mb, v.public_ip)
            for v in vms
        ],
    )


@app.command("get")
@handle_api_errors
def get_vm(ctx: typer.Context, vm_id: str = typer.Argument(..., help="VM ID")) -> None:
    """Show one cloud VM."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.cloud_vms.get_cloud_vm(workspace_id=require_workspace(settings), vm_id=vm_id)
    print_json(result)


def _power_action(ctx: typer.Context, vm_id: str, action: str) -> None:
    settings = get_settings(ctx)
    client = get_client(settings)
    method = getattr(client.cloud_vms, f"{action}_cloud_vm")
    result = method(
        workspace_id=require_workspace(settings),
        vm_id=vm_id,
        idempotency_key=f"cli-{action}-{vm_id}-{uuid.uuid4().hex[:8]}",
    )
    if settings.as_json:
        print_json(result)
        return
    op_id = getattr(result, "operation_id", None) or getattr(result, "id", None)
    typer.secho(
        f"{action.capitalize()} accepted for VM {vm_id}"
        + (f" (operation {op_id})" if op_id else ""),
        fg=typer.colors.GREEN,
    )


@app.command("start")
@handle_api_errors
def start_vm(ctx: typer.Context, vm_id: str = typer.Argument(...)) -> None:
    """Start a cloud VM."""
    _power_action(ctx, vm_id, "start")


@app.command("stop")
@handle_api_errors
def stop_vm(ctx: typer.Context, vm_id: str = typer.Argument(...)) -> None:
    """Stop a cloud VM."""
    _power_action(ctx, vm_id, "stop")


@app.command("reboot")
@handle_api_errors
def reboot_vm(ctx: typer.Context, vm_id: str = typer.Argument(...)) -> None:
    """Reboot a cloud VM."""
    _power_action(ctx, vm_id, "reboot")
